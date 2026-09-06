"""Human review queue: one screen per item, single keystroke adjudication.

Only the items QA routed appear here, roughly 15 percent of the corpus. That
ratio is the point of the system: a queue holding everything has no value, and
a queue holding nothing has no safety.

Elapsed seconds are recorded per item, because a claim about human review cost
is worthless without a measured time.

The queue is resumable. Relaunching skips ticket ids already adjudicated, so
the pass can be done in several sittings.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import induce, paths, store, yamlio

console = Console()

ACTIONS = {
    "l": ("accept_llm", "accept the LLM label"),
    "v": ("accept_vendor", "accept the vendor label"),
    "h": ("accept_heuristic", "accept the heuristic label"),
    "o": ("other_leaf", "pick a different leaf"),
    "b": ("ambiguous_both", "genuinely ambiguous, keep both labels"),
    "d": ("duplicate", "confirm this is a duplicate"),
    "p": ("pii_confirmed", "confirm PII is present"),
    "s": ("skip", "skip, decide later"),
}

# Fast-adjudication guard.
#
# A reviewer once accepted 22 items in five seconds by holding a key down. The
# decisions recorded cleanly and nothing complained, which is a real hole in a
# tool whose entire output is human judgement.
#
# The guard confirms rather than discards. Silently throwing away a reviewer's
# input has its own failure mode, and a genuinely obvious item can be decided
# quickly, so the run has to be both fast AND identical AND sustained before
# anything interrupts.
FAST_RUN_LENGTH = 3          # identical decisions in a row before asking
FAST_FLOOR_DEFAULT = 1.0     # seconds
FAST_FLOOR_BOUNDS = (0.5, 3.0)

REASON_LABEL = {
    "suspected_label_error": "two annotators agree against the vendor label",
    "ambiguity": "the model was unsure or all three annotators disagree",
    "duplicate": "representative of a near-duplicate cluster",
}


def fast_floor(conn) -> float:
    """The threshold below which a decision looks like a keypress, not a call.

    Derived from the reviewer's own history rather than hardcoded: the 5th
    percentile of past adjudication times, clamped to a sane range. With little
    history it falls back to one second, which sat in a wide empty gap in the
    observed data (real decisions from 1.4s upward, a key-mash run at 0.16s to
    0.60s).
    """
    times = sorted(
        r["seconds"] for r in conn.execute("SELECT seconds FROM adjudications")
    )
    if len(times) < 20:
        return FAST_FLOOR_DEFAULT
    p5 = times[max(0, int(len(times) * 0.05) - 1)]
    low, high = FAST_FLOOR_BOUNDS
    return min(high, max(low, p5))


def load_queue(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT ticket_id, flag_type, detail, rank FROM qa_flags
           WHERE routed_to_human = 1 ORDER BY
             CASE flag_type
               WHEN 'suspected_label_error' THEN 0
               WHEN 'ambiguity' THEN 1
               ELSE 2 END,
             rank, ticket_id"""
    ).fetchall()
    return [
        {"ticket_id": r["ticket_id"], "flag_type": r["flag_type"],
         "detail": json.loads(r["detail"]) if r["detail"] else {}}
        for r in rows
    ]


def already_done() -> set[str]:
    if not paths.ADJUDICATIONS.exists():
        return set()
    done = set()
    for line in paths.ADJUDICATIONS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("decision") != "skip":
                done.add(record["ticket_id"])
    return done


def _all_flags(conn, ticket_id: str) -> list[str]:
    return [
        r["flag_type"]
        for r in conn.execute(
            "SELECT DISTINCT flag_type FROM qa_flags WHERE ticket_id = ?", (ticket_id,)
        )
    ]


def render_item(conn, item: dict, position: int, total: int, leaves: list[str]) -> None:
    ticket = conn.execute(
        "SELECT * FROM tickets WHERE ticket_id = ?", (item["ticket_id"],)
    ).fetchone()
    llm = conn.execute(
        """SELECT l.* FROM labels l JOIN runs r ON r.run_id = l.run_id
           WHERE l.ticket_id = ? AND l.source = 'llm' AND r.status = 'ok'
           ORDER BY r.started_at DESC LIMIT 1""",
        (item["ticket_id"],),
    ).fetchone()
    heur = conn.execute(
        """SELECT l.* FROM labels l JOIN runs r ON r.run_id = l.run_id
           WHERE l.ticket_id = ? AND l.source = 'heuristic' AND r.status = 'ok'
           ORDER BY r.started_at DESC LIMIT 1""",
        (item["ticket_id"],),
    ).fetchone()

    console.clear()
    console.rule(f"[bold]{item['ticket_id']}[/bold]  item {position} of {total}")

    header = Text()
    header.append(ticket["subject"] + "\n", style="bold")
    header.append(
        f"{ticket['channel']} | {ticket['tier']} | {ticket['created_at']}",
        style="dim",
    )
    console.print(Panel(header, title="ticket", title_align="left"))
    console.print(Panel(ticket["body"], title="body", title_align="left"))

    labels = Table(header_style="bold", title="what each annotator said",
                   title_justify="left")
    labels.add_column("Annotator")
    labels.add_column("Label")
    labels.add_column("Confidence", justify="right")
    labels.add_column("Rationale")
    labels.add_row("vendor", ticket["vendor_label"], "", "[dim]incoming label[/dim]")
    if llm:
        labels.add_row(
            "llm",
            "[yellow]abstained[/yellow]" if llm["abstain"] else (llm["label"] or ""),
            f"{llm['confidence']:.2f}" if llm["confidence"] is not None else "",
            llm["rationale"] or "",
        )
    if heur:
        labels.add_row("heuristic", heur["label"] or "",
                       f"{heur['confidence']:.2f}" if heur["confidence"] is not None else "",
                       "[dim]term overlap[/dim]")
    console.print(labels)

    flags = _all_flags(conn, item["ticket_id"])
    reason = REASON_LABEL.get(item["flag_type"], item["flag_type"])
    extra = ""
    if item["flag_type"] == "duplicate":
        cluster = item["detail"].get("cluster", [])
        extra = f"\ncluster members: {', '.join(cluster)}"
    if item["flag_type"] == "ambiguity":
        extra = "\n" + "; ".join(item["detail"].get("reasons", []))
    console.print(Panel(
        f"routed because: [bold]{reason}[/bold]{extra}\n"
        f"all flags on this ticket: {', '.join(flags)}",
        title="why this is in the queue", title_align="left",
    ))


def prompt_action() -> str:
    line = Table.grid(padding=(0, 2))
    line.add_column()
    line.add_column()
    for key, (_, description) in ACTIONS.items():
        line.add_row(f"[bold cyan]{key}[/bold cyan]", description)
    line.add_row("[bold cyan]t[/bold cyan]", "show the taxonomy")
    line.add_row("[bold cyan]q[/bold cyan]", "save and quit")
    console.print(line)
    return console.input("\n[bold]decision[/bold] > ").strip().lower()


def choose_leaf(leaves: list[str]) -> str | None:
    table = Table(header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Leaf")
    for i, leaf in enumerate(leaves, start=1):
        table.add_row(str(i), leaf)
    console.print(table)
    raw = console.input("leaf number (blank to cancel) > ").strip()
    if not raw.isdigit():
        return None
    index = int(raw)
    return leaves[index - 1] if 1 <= index <= len(leaves) else None


def show_taxonomy(taxonomy: dict) -> None:
    console.print(Panel(induce.render_taxonomy(taxonomy),
                        title="locked taxonomy", title_align="left"))
    console.input("\npress enter to go back ")


def record(conn, ticket_id: str, decision: str, final_label: str | None,
           reviewer: str, seconds: float) -> dict:
    entry = {
        "ticket_id": ticket_id,
        "decision": decision,
        "final_label": final_label,
        "reviewer": reviewer,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 2),
    }
    with paths.ADJUDICATIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    conn.execute(
        "INSERT OR REPLACE INTO adjudications VALUES (?,?,?,?,?,?)",
        (entry["ticket_id"], entry["decision"], entry["final_label"],
         entry["reviewer"], entry["ts"], entry["seconds"]),
    )
    conn.commit()
    return entry


def unrecord(conn, entries: list[dict]) -> None:
    """Remove specific adjudications from both stores.

    Both, always. Only the jsonl drives the resume logic, but `improve` reads
    the table, so leaving rows behind would let the improvement pass consume
    decisions the reviewer just rejected.
    """
    targets = {(e["ticket_id"], e["ts"]) for e in entries}
    kept = []
    for line in paths.ADJUDICATIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if (record["ticket_id"], record["ts"]) not in targets:
            kept.append(record)
    with paths.ADJUDICATIONS.open("w", encoding="utf-8") as fh:
        for record in kept:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    for ticket_id, ts in targets:
        conn.execute(
            "DELETE FROM adjudications WHERE ticket_id = ? AND ts = ?",
            (ticket_id, ts),
        )
    conn.commit()


def confirm_fast_run(conn, session_log: list[dict], floor: float) -> list[str]:
    """Interrupt on a sustained run of fast identical decisions.

    Returns ticket ids to put back in the queue. Confirming keeps everything.
    """
    tail = session_log[-FAST_RUN_LENGTH:]
    if len(tail) < FAST_RUN_LENGTH:
        return []
    if len({e["decision"] for e in tail}) != 1:
        return []
    if any(e["seconds"] >= floor for e in tail):
        return []

    # Extend backwards over the whole run, not just the trigger window.
    run = list(tail)
    for entry in reversed(session_log[:-FAST_RUN_LENGTH]):
        if entry["decision"] == tail[0]["decision"] and entry["seconds"] < floor:
            run.insert(0, entry)
        else:
            break

    table = Table(header_style="bold")
    table.add_column("Ticket")
    table.add_column("Decision")
    table.add_column("Seconds", justify="right")
    for entry in run:
        table.add_row(entry["ticket_id"], entry["decision"], f"{entry['seconds']:.2f}")

    console.print()
    console.print(Panel(
        f"[bold]{len(run)}[/bold] identical decisions in a row, each under "
        f"{floor:.2f}s.\n\n"
        "That is the signature of a held-down key rather than a series of "
        "judgements. These are recorded already, nothing has been thrown away.\n\n"
        "[bold]y[/bold] keep them, they were genuinely that obvious\n"
        "[bold]n[/bold] undo them and put those tickets back in the queue",
        title="[yellow]that was fast", title_align="left",
    ))
    console.print(table)

    answer = console.input("\n[bold]keep these decisions?[/bold] [y/N] > ").strip().lower()
    if answer == "y":
        console.print("[dim]kept.[/dim]")
        for entry in run:
            entry["confirmed_fast"] = True
        return []

    unrecord(conn, run)
    ids = [e["ticket_id"] for e in run]
    for entry in run:
        session_log.remove(entry)
    console.print(f"[dim]undone, {len(ids)} ticket(s) returned to the queue.[/dim]")
    console.input("press enter to continue ")
    return ids


def run(reviewer: str = "mohamed", limit: int | None = None) -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        store.assert_taxonomy_locked(conn)
        taxonomy = yamlio.load(paths.TAXONOMY)
        leaves = induce.leaf_names(taxonomy)

        queue = load_queue(conn)
        if not queue:
            console.print("[yellow]Nothing routed. Run `python -m dql qa` first.[/yellow]")
            return 1

        done = already_done()
        pending = [item for item in queue if item["ticket_id"] not in done]
        if limit:
            pending = pending[:limit]

        if not pending:
            console.print(Panel(
                f"All {len(queue)} routed items are already adjudicated.\n"
                f"Recorded in {paths.ADJUDICATIONS}.\n\n"
                "Next: [bold]python -m dql improve[/bold]",
                title="review complete", title_align="left",
            ))
            return 0

        console.print(Panel(
            f"{len(pending)} items to review ({len(done)} already done, "
            f"{len(queue)} routed in total).\n\n"
            "Every decision is timed and appended to data/adjudications.jsonl.\n"
            "You can quit at any point with [bold]q[/bold] and resume later: the "
            "queue skips what you have already decided.",
            title="human review queue", title_align="left",
        ))
        console.input("press enter to start ")

        run_id = store.start_run(conn, "review", model="human")
        reviewed = 0
        floor = fast_floor(conn)
        session_log: list[dict] = []
        queue = list(pending)
        cursor = 0
        while cursor < len(queue):
            item = queue[cursor]
            position = cursor + 1
            cursor += 1
            while True:
                render_item(conn, item, position, len(queue), leaves)
                started = time.monotonic()
                key = prompt_action()
                elapsed = time.monotonic() - started

                if key == "q":
                    store.finish_run(conn, run_id,
                                     note=f"{reviewed} adjudicated this session")
                    _summary(conn, reviewed, len(queue), session_log, floor)
                    return 0
                if key == "t":
                    show_taxonomy(taxonomy)
                    continue
                if key not in ACTIONS:
                    continue

                decision, _ = ACTIONS[key]
                final_label: str | None = None

                if decision == "accept_llm":
                    row = conn.execute(
                        """SELECT l.label FROM labels l JOIN runs r ON r.run_id = l.run_id
                           WHERE l.ticket_id=? AND l.source='llm' AND r.status='ok'
                           ORDER BY r.started_at DESC LIMIT 1""",
                        (item["ticket_id"],),
                    ).fetchone()
                    final_label = row["label"] if row else None
                    if final_label is None:
                        console.print("[yellow]The LLM abstained here, so there is "
                                      "no label to accept. Pick another action.[/yellow]")
                        console.input("press enter ")
                        continue
                elif decision == "accept_vendor":
                    row = conn.execute(
                        "SELECT vendor_label FROM tickets WHERE ticket_id=?",
                        (item["ticket_id"],),
                    ).fetchone()
                    final_label = row["vendor_label"]
                elif decision == "accept_heuristic":
                    row = conn.execute(
                        """SELECT l.label FROM labels l JOIN runs r ON r.run_id = l.run_id
                           WHERE l.ticket_id=? AND l.source='heuristic' AND r.status='ok'
                           ORDER BY r.started_at DESC LIMIT 1""",
                        (item["ticket_id"],),
                    ).fetchone()
                    final_label = row["label"] if row else None
                elif decision == "other_leaf":
                    final_label = choose_leaf(leaves)
                    if final_label is None:
                        continue

                entry = record(conn, item["ticket_id"], decision, final_label,
                               reviewer, elapsed)
                session_log.append(entry)
                reviewed += 1

                returned = confirm_fast_run(conn, session_log, floor)
                if returned:
                    reviewed -= len(returned)
                    by_id = {i["ticket_id"]: i for i in queue}
                    queue.extend(by_id[t] for t in returned if t in by_id)
                break

        store.finish_run(conn, run_id, note=f"{reviewed} adjudicated this session")
        _summary(conn, reviewed, len(queue), session_log, floor)
    return 0


def _summary(conn, reviewed: int, queue_size: int,
             session_log: list[dict] | None = None,
             floor: float = FAST_FLOOR_DEFAULT) -> None:
    rows = list(conn.execute(
        "SELECT decision, COUNT(*) n, SUM(seconds) total FROM adjudications GROUP BY decision"
    ))
    table = Table(title="Adjudications", title_justify="left", header_style="bold")
    table.add_column("Decision")
    table.add_column("Count", justify="right")
    table.add_column("Total seconds", justify="right")
    total_items = 0
    total_seconds = 0.0
    for r in rows:
        table.add_row(r["decision"], str(r["n"]), f"{r['total']:.1f}")
        total_items += r["n"]
        total_seconds += r["total"] or 0.0
    console.print(table)
    fast = [
        e for e in (session_log or [])
        if e["seconds"] < floor and not e.get("confirmed_fast")
    ]
    if fast:
        console.print(Panel(
            f"{len(fast)} decision(s) this session came in under {floor:.2f}s "
            "without being confirmed:\n"
            + ", ".join(f"{e['ticket_id']} ({e['seconds']:.2f}s)" for e in fast[:20])
            + "\n\nThey are recorded. Re-run review after deleting them from "
              "data/adjudications.jsonl if you want another look.",
            title="[yellow]fast decisions in this session", title_align="left",
        ))

    console.print(Panel(
        f"this session: [bold]{reviewed}[/bold] items\n"
        f"recorded overall: [bold]{total_items}[/bold] of {queue_size} routed\n"
        f"review time: [bold]{total_seconds / 60:.1f} minutes[/bold] "
        f"({total_seconds / max(1, total_items):.1f} seconds per item)\n\n"
        f"appended to {paths.ADJUDICATIONS}\n"
        "Next: [bold]python -m dql improve[/bold]",
        title="review session", title_align="left",
    ))
