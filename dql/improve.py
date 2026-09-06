"""Improvement pass: produce dataset v2 from v1 plus adjudications plus
automated fixes.

Fully deterministic. Given the same v1 labels and the same adjudications file,
this produces the same v2 every time. No model calls.

Every change is logged to data/v1_to_v2_diff.jsonl with the field, the value
before, the value after, and the reason. A dataset improvement you cannot diff
is a dataset you cannot defend, and the diff is what makes the v1 to v2 delta
reviewable rather than asserted.

This module never opens the sealed truth file.
"""

from __future__ import annotations

import json
from collections import Counter

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import paths, qa, store, yamlio

console = Console()

PLACEHOLDER = {"email": "EMAIL", "phone": "PHONE", "card": "CARD"}


def scrub(body: str, spans: list[dict]) -> tuple[str, list[dict]]:
    """Replace every PII span with a typed placeholder.

    Spans are applied right to left so earlier offsets stay valid. Numbering is
    per ticket and per type, so a body with two emails yields [EMAIL_1] and
    [EMAIL_2] rather than two identical tokens.
    """
    ordered = sorted(spans, key=lambda s: s["start"])
    counters: Counter = Counter()
    assigned = []
    for span in ordered:
        counters[span["type"]] += 1
        assigned.append((span, f"[{PLACEHOLDER[span['type']]}_{counters[span['type']]}]"))

    out = body
    for span, token in reversed(assigned):
        out = out[:span["start"]] + token + out[span["end"]:]
    return out, [
        {"type": s["type"], "placeholder": token, "start": s["start"], "end": s["end"]}
        for s, token in assigned
    ]


def run() -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        store.assert_taxonomy_locked(conn)

        tickets = {t["ticket_id"]: t for t in store.all_tickets(conn)}
        if not tickets:
            console.print("[red]No tickets. Run generate first.[/red]")
            return 1

        llm = store.latest_labels(conn, "llm")
        heur = store.latest_labels(conn, "heuristic")
        if not llm:
            console.print("[red]No labels. Run `python -m dql label` first.[/red]")
            return 1

        flag_rows = conn.execute(
            "SELECT ticket_id, flag_type, detail FROM qa_flags"
        ).fetchall()
        if not flag_rows:
            console.print("[red]No QA flags. Run `python -m dql qa` first.[/red]")
            return 1

        alignment = qa.normalize_alignment(
            yamlio.load(paths.VENDOR_ALIGNMENT)["alignment"]
        )

        pii_spans: dict[str, list[dict]] = {}
        clusters: list[list[str]] = []
        seen_clusters: set[tuple[str, ...]] = set()
        for row in flag_rows:
            detail = json.loads(row["detail"]) if row["detail"] else {}
            if row["flag_type"] == "pii":
                pii_spans[row["ticket_id"]] = detail.get("spans", [])
            elif row["flag_type"] == "duplicate":
                cluster = tuple(detail.get("cluster", []))
                if cluster and cluster not in seen_clusters:
                    seen_clusters.add(cluster)
                    clusters.append(list(cluster))

        adjudications: dict[str, dict] = {}
        if paths.ADJUDICATIONS.exists():
            for line in paths.ADJUDICATIONS.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if record.get("decision") != "skip":
                        adjudications[record["ticket_id"]] = record

        run_id = store.start_run(conn, "improve")
        diff: list[dict] = []

        # -- v1: the pipeline's own automated output ------------------------
        v1: dict[str, str | None] = {
            tid: (None if llm[tid]["abstain"] else llm[tid]["label"])
            for tid in tickets
            if tid in llm
        }
        store.insert_labels(conn, run_id, [
            {"ticket_id": tid, "source": "v1", "label": label,
             "confidence": llm[tid]["confidence"], "rationale": "dataset v1",
             "abstain": label is None}
            for tid, label in v1.items()
        ])

        # -- v2: start from v1, then apply every fix ------------------------
        v2: dict[str, str | None] = dict(v1)
        alt: dict[str, str | None] = {}
        status: dict[str, str] = {tid: "kept" for tid in tickets}
        reason: dict[str, str] = {}

        # 1. adjudicated corrections
        applied = Counter()
        for tid, record in sorted(adjudications.items()):
            if tid not in tickets:
                continue
            decision = record["decision"]
            applied[decision] += 1
            before = v2.get(tid)

            if decision == "ambiguous_both":
                # Dual label: keep the primary, record the defensible alternate.
                second = heur.get(tid)
                alternate = second["label"] if second else None
                if alternate and alternate != before:
                    alt[tid] = alternate
                    reason[tid] = "adjudicated ambiguous, dual labeled"
                    diff.append({"ticket_id": tid, "field": "alt_label",
                                 "before": None, "after": alternate,
                                 "reason": "reviewer marked genuinely ambiguous"})
                else:
                    status[tid] = "quarantined"
                    reason[tid] = "adjudicated ambiguous with no distinct alternate"
                    diff.append({"ticket_id": tid, "field": "status",
                                 "before": "kept", "after": "quarantined",
                                 "reason": "reviewer marked ambiguous, no second label to keep"})
                continue

            after = record.get("final_label")
            if decision == "accept_vendor" and after:
                # The reviewer accepted the vendor's label, which may cover
                # several induced leaves. If the label already on the ticket is
                # inside that set it is a finer, compatible choice, so keep it.
                # Otherwise take the single covering leaf when there is one.
                covered = alignment.get(after, set())
                if before in covered:
                    after = before
                elif len(covered) == 1:
                    after = next(iter(covered))
                else:
                    after = before
            if after and after != before:
                v2[tid] = after
                reason[tid] = f"adjudicated: {decision}"
                diff.append({"ticket_id": tid, "field": "label", "before": before,
                             "after": after, "reason": f"reviewer decision: {decision}"})

        # 2. duplicate clusters collapse to their canonical member
        merged = 0
        for cluster in clusters:
            canonical = cluster[0]
            for member in cluster[1:]:
                if status.get(member) == "kept":
                    status[member] = "merged_duplicate"
                    reason[member] = f"near duplicate of {canonical}"
                    merged += 1
                    diff.append({"ticket_id": member, "field": "status",
                                 "before": "kept", "after": "merged_duplicate",
                                 "reason": f"collapsed into canonical member {canonical}"})

        # 3. PII scrubbed with typed placeholders
        scrubbed_body: dict[str, str] = {}
        scrubbed_count = 0
        for tid, ticket in tickets.items():
            spans = pii_spans.get(tid, [])
            if spans:
                body, tokens = scrub(ticket["body"], spans)
                scrubbed_body[tid] = body
                scrubbed_count += 1
                diff.append({"ticket_id": tid, "field": "body",
                             "before": f"{len(spans)} PII span(s)",
                             "after": ", ".join(t["placeholder"] for t in tokens),
                             "reason": "PII scrubbed with typed placeholders"})
            else:
                scrubbed_body[tid] = ticket["body"]

        # -- persist --------------------------------------------------------
        conn.execute("DELETE FROM dataset_v2")
        conn.executemany(
            "INSERT INTO dataset_v2 VALUES (?,?,?,?,?,?)",
            [
                (tid, v2.get(tid), alt.get(tid), scrubbed_body[tid],
                 status[tid], reason.get(tid))
                for tid in sorted(tickets)
            ],
        )
        store.insert_labels(conn, run_id, [
            {"ticket_id": tid, "source": "v2", "label": v2.get(tid),
             "confidence": None, "rationale": reason.get(tid, "unchanged from v1"),
             "abstain": v2.get(tid) is None}
            for tid in sorted(tickets)
        ])
        conn.commit()

        paths.V1_TO_V2_DIFF.write_text(
            "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in diff) + "\n",
            encoding="utf-8",
        )
        store.set_meta(conn, "v2_merged_duplicates", merged)
        store.set_meta(conn, "v2_quarantined",
                       sum(1 for s in status.values() if s == "quarantined"))
        store.finish_run(conn, run_id, note=f"{len(diff)} changes v1 to v2")

        _report(v1, v2, status, alt, applied, merged, scrubbed_count, diff)
    return 0


def _report(v1, v2, status, alt, applied, merged, scrubbed, diff) -> None:
    table = Table(title="Dataset v1 to v2", title_justify="left", header_style="bold")
    table.add_column("Change")
    table.add_column("Count", justify="right")
    table.add_row("labels corrected by a reviewer",
                  str(sum(1 for d in diff if d["field"] == "label")))
    table.add_row("dual labeled as ambiguous", str(len(alt)))
    table.add_row("quarantined",
                  str(sum(1 for s in status.values() if s == "quarantined")))
    table.add_row("merged as duplicates", str(merged))
    table.add_row("tickets with PII scrubbed", str(scrubbed))
    table.add_row("[bold]total logged changes", f"[bold]{len(diff)}")
    console.print(table)

    if applied:
        decisions = Table(title="Reviewer decisions applied", title_justify="left",
                          header_style="bold")
        decisions.add_column("Decision")
        decisions.add_column("Count", justify="right")
        for decision, n in applied.most_common():
            decisions.add_row(decision, str(n))
        console.print(decisions)

    kept = sum(1 for s in status.values() if s == "kept")
    console.print(Panel(
        f"v2 holds [bold]{kept}[/bold] usable tickets of {len(status)}\n"
        f"every change written to {paths.V1_TO_V2_DIFF}\n\n"
        "Next: [bold]python -m dql evaluate --version both[/bold]",
        title="improvement pass complete", title_align="left",
    ))
