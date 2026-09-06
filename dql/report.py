"""REPORT.md: the v1 to v2 comparison, read from committed results files.

Every number here is read from eval/results_*.json or from the store. Nothing
is recomputed independently, so the report cannot disagree with the eval
(BUILD_CONTRACT C1).

Never opens the sealed truth file: it reads what the harness already wrote.
"""

from __future__ import annotations

import json
import os
from collections import Counter

from rich.console import Console
from rich.panel import Panel

from . import paths, store

console = Console()

RESULTS_V1 = paths.EVAL / "results_v1.json"
RESULTS_V2 = paths.EVAL / "results_v2.json"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def run(email: bool = False) -> int:
    if not RESULTS_V1.exists():
        console.print(
            "[red]eval/results_v1.json is missing. Run "
            "`python -m dql evaluate` first.[/red]"
        )
        return 1

    v1 = json.loads(RESULTS_V1.read_text(encoding="utf-8"))
    v2 = json.loads(RESULTS_V2.read_text(encoding="utf-8")) if RESULTS_V2.exists() else None

    with store.session() as conn:
        cost_rows = store.cost_breakdown(conn)
        total_cost = store.total_cost(conn)
        adjudications = list(conn.execute("SELECT * FROM adjudications"))
        routed = conn.execute(
            "SELECT COUNT(DISTINCT ticket_id) FROM qa_flags WHERE routed_to_human = 1"
        ).fetchone()[0]
        flag_counts = Counter(
            r["flag_type"]
            for r in conn.execute("SELECT flag_type, ticket_id FROM qa_flags")
        )
        corpus = store.ticket_count(conn)

    with store.session() as conn:
        run_id = store.start_run(conn, "report")

    paths.REPORT.write_text(
        _render(v1, v2, cost_rows, total_cost, adjudications, routed, flag_counts, corpus),
        encoding="utf-8",
    )
    with store.session() as conn:
        store.finish_run(conn, run_id, note=f"{total_cost:.4f} USD total spend")

    console.print(Panel(
        f"written to {paths.REPORT}\n"
        f"total API spend {total_cost:.4f} USD",
        title="report complete", title_align="left",
    ))

    if email:
        return _send_email(v1, v2, total_cost)
    return 0


def _render(v1, v2, cost_rows, total_cost, adjudications, routed, flags, corpus) -> str:
    seconds = sum(a["seconds"] for a in adjudications)
    decisions = Counter(a["decision"] for a in adjudications)

    lines = [
        "# REPORT: data-quality-loop",
        "",
        f"Generated from `eval/results_v1.json`"
        + (" and `eval/results_v2.json`" if v2 else "")
        + ". Every number below is read from those files or from the pipeline's",
        "own store. None of it is typed in by hand.",
        "",
        "## Headline",
        "",
        "| Metric | v1 (pipeline output) | v2 (after the loop) |",
        "|---|---|---|",
    ]

    v1_acc = v1["v1_accuracy"]
    if v2:
        v2_acc = v2["v2_accuracy"]
        delta = v2["accuracy_delta_points"]
        lines.append(f"| Label accuracy vs ground truth | {pct(v1_acc)} | "
                     f"{pct(v2_acc)} ({delta:+.1f} pts) |")
    else:
        lines.append(f"| Label accuracy vs ground truth | {pct(v1_acc)} | "
                     "not yet produced |")

    se1 = v1["seeded_errors"]
    se2 = (v2 or v1)["seeded_errors"]
    lines.append(
        f"| Seeded label errors caught (of {se1['planted']}) | "
        f"{se1['flagged_by_qa']} ({pct(se1['recall'])}) | "
        f"{se2['corrected_in_v2']} corrected |"
    )
    dup = v1["duplicates"]
    lines.append(
        f"| Duplicate pairs found (of {dup['planted_pairs']}) | "
        f"{dup['pairs_recovered']} ({pct(dup['recall'])}) | "
        f"{dup['pairs_recovered']} merged |"
    )
    pii1, pii2 = v1["pii"], (v2 or v1)["pii"]
    lines.append(
        f"| PII items caught (of {pii1['planted_tickets']}) / leaks in export | "
        f"{pii1['flagged_tickets']} ({pct(pii1['recall'])}) | "
        f"{pii2['leaks_after_scrub']} leaks |"
    )
    lines.append(f"| Items routed to human / total | {routed} / {corpus} "
                 f"({routed / corpus * 100:.1f}%) | |")
    lines.append(f"| Human review time | {seconds / 60:.1f} minutes over "
                 f"{len(adjudications)} items | |")
    lines.append(f"| Total API cost | {total_cost:.4f} USD | |")

    lines += [
        "",
        "## Accuracy detail",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Vendor labels as delivered | {pct(v1['vendor_baseline_accuracy'])} |",
        f"| LLM labeler | {pct(v1['llm_accuracy'])} |",
        f"| Heuristic labeler | {pct(v1['heuristic_accuracy'])} |",
    ]
    if v2:
        lines.append(f"| Dataset v2 | {pct(v2['v2_accuracy'])} |")
    lines += [
        "",
        f"{v1['unreachable_note']}",
        "",
        f"Acceptable-alternate policy: {v1['acceptable_alt_policy']}",
        "",
        "Predictions are in the induced vocabulary and truth is in the generator's",
        "hidden vocabulary, so every comparison passes through the hand written",
        "alignment in `eval/taxonomy_alignment.yaml`. That file is committed and",
        "short enough to read in full, which is the point.",
        "",
        "## Quality flags",
        "",
        "| Flag | Tickets |",
        "|---|---|",
    ]
    for flag, n in flags.most_common():
        lines.append(f"| {flag} | {n} |")

    lines += [
        "",
        "## Human review",
        "",
        f"- items routed: {routed} of {corpus} ({routed / corpus * 100:.1f}% of the corpus)",
        f"- items adjudicated: {len(adjudications)}",
        f"- total review time: {seconds / 60:.1f} minutes",
    ]
    if adjudications:
        lines.append(
            f"- median seconds per item: "
            f"{sorted(a['seconds'] for a in adjudications)[len(adjudications) // 2]:.1f}"
        )
        lines += ["", "| Decision | Count |", "|---|---|"]
        for decision, n in decisions.most_common():
            lines.append(f"| {decision} | {n} |")

    lines += [
        "",
        "## Abstention calibration",
        "",
    ]
    ab = v1["abstention"]
    lines += [
        f"- abstentions: {ab['abstentions']}",
        f"- share of abstentions that are planted-ambiguous tickets: "
        f"{pct(ab['ambiguous_share_of_abstentions'])}, against a base rate of "
        f"{pct(ab['ambiguous_base_rate'])}",
        f"- heuristic accuracy on abstained items: "
        f"{pct(ab['heuristic_accuracy_on_abstained'])}",
        f"- heuristic accuracy on answered items: "
        f"{pct(ab['heuristic_accuracy_on_answered'])}",
        "",
        ab["reading"],
        "",
        "## Cost accounting",
        "",
        "| Stage | Calls | Tokens in | Tokens out | USD |",
        "|---|---|---|---|---|",
    ]
    for row in cost_rows:
        lines.append(
            f"| {row['stage']} | {row['calls']} | {row['tokens_in']:,} | "
            f"{row['tokens_out']:,} | {row['usd']:.4f} |"
        )
    lines += [
        f"| **total** | | | | **{total_cost:.4f}** |",
        "",
        "Generation, the heuristic labeler, QA, the improvement pass, the eval",
        "harness, and export make no model calls at all. Only induction and LLM",
        "labeling spend anything.",
        "",
        "## Duplicate precision, stated honestly",
        "",
        dup["precision_note"],
        "",
    ]
    return "\n".join(lines)


def _send_email(v1, v2, total_cost) -> int:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    recipient = os.environ.get("DQL_EMAIL_TO", "").strip()
    if not api_key or not recipient:
        console.print(
            "[yellow]--email requested but RESEND_API_KEY or DQL_EMAIL_TO is "
            "unset. Skipping the send, the report is still written.[/yellow]"
        )
        return 0
    try:
        import resend
    except ImportError:
        console.print(
            "[yellow]--email requested but the optional `resend` package is not "
            "installed. Skipping the send.[/yellow]"
        )
        return 0

    acc = v2["v2_accuracy"] if v2 else v1["v1_accuracy"]
    severity = "green" if v2 and v2["accuracy_delta_points"] >= 5 else "attention"
    resend.api_key = api_key
    resend.Emails.send({
        "from": "data-quality-loop <onboarding@resend.dev>",
        "to": [recipient],
        "subject": f"[{severity}] data-quality-loop: accuracy {pct(acc)}, "
                   f"spend {total_cost:.2f} USD",
        "text": paths.REPORT.read_text(encoding="utf-8")[:8000],
    })
    console.print(f"[green]summary emailed to {recipient}[/green]")
    return 0
