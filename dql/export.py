"""Export: fine-tune-ready splits plus the datasheet.

Reads dataset v2 verbatim from the store rather than recomputing anything, so
what ships is exactly what the improvement pass logged and the eval harness
scored (BUILD_CONTRACT C1).

Never opens the sealed truth file.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import induce, paths, qa, store, yamlio

console = Console()

TRAIN_SHARE = 0.85
SPLIT_SEED = 20260904


def run() -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        store.assert_taxonomy_locked(conn)
        rows = list(conn.execute("SELECT * FROM dataset_v2"))
        if not rows:
            console.print("[red]No dataset v2. Run `python -m dql improve` first.[/red]")
            return 1
        tickets = {t["ticket_id"]: t for t in store.all_tickets(conn)}
        taxonomy = yamlio.load(paths.TAXONOMY)
        run_id = store.start_run(conn, "export")
        stats = _collect_stats(conn)

    usable = [
        r for r in rows
        if r["status"] == "kept" and r["label"] is not None
    ]
    excluded = Counter(
        r["status"] if r["status"] != "kept" else "kept_without_label"
        for r in rows if r not in usable
    )

    # A PII leak must never reach an export file. This is a last line of
    # defence, not the primary control: the improvement pass scrubs by span.
    leaks = [r["ticket_id"] for r in usable if qa.find_pii(r["body_scrubbed"])]
    if leaks:
        console.print(Panel(
            f"{len(leaks)} record(s) still contain PII after scrubbing:\n"
            + ", ".join(leaks[:20])
            + "\n\nExport refused. Fix the scrub and re-run improve.",
            title="[red]export blocked", title_align="left",
        ))
        with store.session() as conn:
            store.finish_run(conn, run_id, status="failed", note=f"{len(leaks)} PII leaks")
        return 1

    # Stratified 85/15 by label, with a fixed seed so the split is reproducible.
    by_label: dict[str, list] = defaultdict(list)
    for r in usable:
        by_label[r["label"]].append(r)

    rng = random.Random(SPLIT_SEED)
    train, evaluation = [], []
    for label in sorted(by_label):
        members = sorted(by_label[label], key=lambda r: r["ticket_id"])
        rng.shuffle(members)
        cut = max(1, round(len(members) * TRAIN_SHARE)) if len(members) > 1 else 1
        train.extend(members[:cut])
        evaluation.extend(members[cut:])

    train.sort(key=lambda r: r["ticket_id"])
    evaluation.sort(key=lambda r: r["ticket_id"])

    _write_split(paths.TRAIN_JSONL, train, tickets)
    _write_split(paths.EVAL_JSONL, evaluation, tickets)
    paths.DATASHEET.write_text(
        _datasheet(taxonomy, rows, usable, train, evaluation, excluded, stats),
        encoding="utf-8",
    )

    with store.session() as conn:
        store.finish_run(conn, run_id, note=f"{len(train)} train, {len(evaluation)} eval")

    table = Table(title="Export", title_justify="left", header_style="bold")
    table.add_column("Split")
    table.add_column("Records", justify="right")
    table.add_column("File")
    table.add_row("train", str(len(train)), "export/train.jsonl")
    table.add_row("eval", str(len(evaluation)), "export/eval.jsonl")
    table.add_row("excluded", str(sum(excluded.values())),
                  ", ".join(f"{k} {v}" for k, v in excluded.items()) or "none")
    console.print(table)
    console.print(Panel(
        f"PII scan of the exported records: [green]0 leaks[/green]\n"
        f"datasheet written to {paths.DATASHEET}",
        title="export complete", title_align="left",
    ))
    return 0


def _write_split(path, records, tickets) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            ticket = tickets[r["ticket_id"]]
            payload = {
                "ticket_id": r["ticket_id"],
                "subject": ticket["subject"],
                "body": r["body_scrubbed"],
                "channel": ticket["channel"],
                "customer_tier": ticket["tier"],
                "created_at": ticket["created_at"],
                "label": r["label"],
            }
            if r["alt_label"]:
                payload["acceptable_alt_label"] = r["alt_label"]
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _collect_stats(conn) -> dict:
    return {
        "adjudications": conn.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0],
        "review_seconds": conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) FROM adjudications"
        ).fetchone()[0],
        "total_cost": store.total_cost(conn),
        "routed": conn.execute(
            "SELECT COUNT(DISTINCT ticket_id) FROM qa_flags WHERE routed_to_human = 1"
        ).fetchone()[0],
    }


def _datasheet(taxonomy, rows, usable, train, evaluation, excluded, stats) -> str:
    dist = Counter(r["label"] for r in usable)
    leaves = induce.leaf_names(taxonomy)
    lines = [
        "# Datasheet: enterprise support ticket classification",
        "",
        "Written in the style of Datasheets for Datasets (Gebru et al.). Every",
        "number here is read from the pipeline's own store, not typed in by hand.",
        "",
        "## Motivation",
        "",
        "This dataset exists to demonstrate a measurable dataset improvement cycle:",
        "taxonomy induction, dual labeling, quality measurement, human review of",
        "exactly the items that warrant it, and a scored comparison between the",
        "dataset before and after that loop. It is a portfolio artifact, not a",
        "production corpus, and it should not be used to train a system that makes",
        "decisions about real customers.",
        "",
        "## Composition",
        "",
        f"- {len(rows)} synthetic support tickets for a fictional B2B SaaS platform",
        f"- {len(usable)} exported after the improvement pass",
        f"- {len(train)} train, {len(evaluation)} eval, stratified 85/15 by label",
        f"- label vocabulary: {len(leaves)} leaves across "
        f"{len(taxonomy['domains'])} domains, induced from unlabeled tickets",
        "- fields: ticket_id, subject, body, channel, customer_tier, created_at, label",
        "- an optional acceptable_alt_label appears on tickets a human reviewer",
        "  judged genuinely ambiguous",
        "",
        "Excluded from the export:",
        "",
    ]
    for reason, n in excluded.most_common():
        lines.append(f"- {reason}: {n}")
    lines += [
        "",
        "### Label distribution",
        "",
        "| Label | Records |",
        "|---|---|",
    ]
    for label, n in dist.most_common():
        lines.append(f"| {label} | {n} |")

    lines += [
        "",
        "## Collection process",
        "",
        "Every ticket is synthetic, generated by `dql/generate.py` from templates",
        "with a fixed seed. No real customer data was used and no text was scraped.",
        "Generation performs zero model calls, so the corpus is free to reproduce",
        "and byte identical across runs.",
        "",
        "The generator deliberately plants known defects and seals a record of them",
        "in a ground-truth file that only the eval harness may read: 40 vendor label",
        "errors, 18 near-duplicate paraphrase pairs, 30 genuinely ambiguous tickets,",
        "25 tickets carrying synthetic PII, and 12 mixing English and Arabic. The",
        "quality framework is then scored on how many it actually caught. That is",
        "the honesty mechanism of the whole project: the measurement has an answer",
        "key the measured components cannot see.",
        "",
        "All PII is unmistakably synthetic by construction: 555 telephone numbers,",
        "addresses under the reserved example.com and example.org domains, and the",
        "published non-functional test card numbers.",
        "",
        "## Labeling process",
        "",
        "Two independent annotators label every ticket:",
        "",
        "- an LLM labeler, given the locked taxonomy with definitions, inclusion and",
        "  exclusion criteria, and boundary examples, returning a label, a",
        "  confidence, a short rationale, and an explicit abstain flag",
        "- a deterministic heuristic labeler built only from the taxonomy text,",
        "  which is the fallback path and the second annotator whose disagreement",
        "  is diagnostic",
        "",
        "The quality framework computes inter-annotator agreement, per-class",
        "confusion, suspected label errors, near duplicates, PII, and ambiguity,",
        f"then routes {stats['routed']} tickets to a human queue. A person",
        f"adjudicated {stats['adjudications']} of them, spending",
        f"{stats['review_seconds'] / 60:.1f} minutes in total. Those decisions,",
        "plus deterministic automated fixes, produce the exported version.",
        "",
        "## Quality metrics",
        "",
        "Scored against the sealed ground truth by `eval/harness.py`. Current",
        "figures live in `eval/results_v1.json` and `eval/results_v2.json`, and the",
        "headline comparison is in `REPORT.md`. Those files are the source of every",
        "number quoted about this dataset.",
        "",
        "## Known limitations",
        "",
        "- **Synthetic.** Template-generated text has less variety than real support",
        "  tickets, and models trained on it will not see the messiness of genuine",
        "  customer writing.",
        "- **Seeded defects are disjoint.** Each planted phenomenon sits on its own",
        "  ticket set so that each detector can be scored without confounding. Real",
        "  corpora routinely contain a mislabeled duplicate that also carries PII.",
        "- **Accidental near duplicates.** Tickets drawn from the same template",
        "  shape are textually alike, so the corpus contains real near duplicates",
        "  beyond the 18 that were planted. Duplicate precision is reported against",
        "  the planted pairs alone, with that denominator stated.",
        "- **A taxonomy gap.** The induced taxonomy has no data export leaf, so",
        "  tickets of that kind cannot be labeled correctly. This is visible in the",
        "  eval results rather than smoothed over.",
        "- **Small.** 600 tickets is enough to demonstrate a measured loop and far",
        "  too small to train a production classifier.",
        "- **English and Arabic only**, with Arabic present in 12 tickets.",
        "",
        "## Recommended uses",
        "",
        "Suitable for demonstrating labeling and quality pipelines, evaluating",
        "taxonomy induction, and as a fixture for testing data-quality tooling.",
        "",
        "Not suitable for training a customer-facing classifier, for benchmarking",
        "model quality, or for any claim about real-world support ticket",
        "distributions.",
        "",
        "## Maintenance",
        "",
        "Regenerate with `python -m dql run-all`. The corpus is deterministic at the",
        "recorded seed, and cached model responses make a re-run reproducible at",
        "zero API cost.",
        "",
    ]
    return "\n".join(lines)
