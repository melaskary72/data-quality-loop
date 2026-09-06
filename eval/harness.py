"""Eval harness: score both dataset versions against sealed ground truth.

This is the ONLY module permitted to open data/ground_truth.jsonl
(BUILD_CONTRACT C3). Nothing in dql/ imports it.

Predictions live in the induced vocabulary and truth lives in the generator's
hidden vocabulary, so every comparison goes through the hand written alignment
in eval/taxonomy_alignment.yaml. That file is committed and small enough to
read in full, which is the point: an accuracy number computed through a private
mapping is not a number anyone should trust.

Release gates fail the build loudly. A gate that has never failed is a gate
nobody is reading.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from dql import paths, qa, store, yamlio  # noqa: E402

console = Console()

GROUND_TRUTH = paths.DATA / "ground_truth.jsonl"
ALIGNMENT_PATH = ROOT / "eval" / "taxonomy_alignment.yaml"

GATES = {
    "v2_accuracy_at_least_v1_plus_5": 5.0,
    "seeded_error_recall_min": 0.85,
    "pii_leaks_max": 0,
    "duplicate_recall_min": 0.80,
    "total_cost_max_usd": 3.00,
}


def load_ground_truth() -> dict[str, dict]:
    if not GROUND_TRUTH.exists():
        raise SystemExit("ground truth is missing. Run `python -m dql generate`.")
    records = {}
    for line in GROUND_TRUTH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["ticket_id"]] = record
    return records


def load_alignment() -> tuple[dict[str, list[str]], list[str]]:
    doc = yamlio.load(ALIGNMENT_PATH)
    return doc["alignment"], doc.get("unreachable_ground_truth_leaves", []) or []


def is_correct(predicted: str | None, truth: dict, alignment: dict) -> bool:
    """A prediction is correct when the ground-truth leaf, or the documented
    acceptable alternate on an ambiguous ticket, is covered by the induced leaf
    that was predicted."""
    if predicted is None:
        return False
    targets = alignment.get(predicted, [])
    if truth["true_label"] in targets:
        return True
    alt = truth.get("acceptable_alt")
    return bool(alt and alt in targets)


def accuracy(predictions: dict[str, str | None], gt: dict, alignment: dict,
             subset: set[str] | None = None) -> tuple[float, int, int]:
    ids = [t for t in predictions if t in gt and (subset is None or t in subset)]
    hits = sum(1 for t in ids if is_correct(predictions[t], gt[t], alignment))
    return (hits / len(ids) if ids else 0.0), hits, len(ids)


# --------------------------------------------------------------------------
# metric blocks
# --------------------------------------------------------------------------

def score_seeded_errors(gt, flags, v2_labels, alignment) -> dict:
    """Of the planted vendor label errors, how many QA flagged, and of those,
    how many v2 actually corrected."""
    seeded = [t for t, r in gt.items() if r["seeded_label_error"]]
    flagged_ids = {
        f["ticket_id"] for f in flags
        if f["flag_type"] in ("suspected_label_error", "taxonomy_granularity")
    }
    caught = [t for t in seeded if t in flagged_ids]
    corrected = [
        t for t in caught
        if v2_labels.get(t) is not None and is_correct(v2_labels[t], gt[t], alignment)
    ]
    return {
        "planted": len(seeded),
        "flagged_by_qa": len(caught),
        "recall": len(caught) / len(seeded) if seeded else 0.0,
        "corrected_in_v2": len(corrected),
        "correction_rate_of_caught": len(corrected) / len(caught) if caught else 0.0,
    }


def score_duplicates(gt, flags) -> dict:
    """Recall against the 18 planted paraphrase pairs, plus precision with the
    denominator stated honestly."""
    pairs: dict[str, list[str]] = {}
    for tid, record in gt.items():
        if record["duplicate_pair_id"]:
            pairs.setdefault(record["duplicate_pair_id"], []).append(tid)

    clusters: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for f in flags:
        if f["flag_type"] != "duplicate":
            continue
        cluster = tuple(json.loads(f["detail"])["cluster"])
        if cluster not in seen:
            seen.add(cluster)
            clusters.append(list(cluster))

    found = 0
    matched_clusters = set()
    for pair_id, members in pairs.items():
        for i, cluster in enumerate(clusters):
            if all(m in cluster for m in members):
                found += 1
                matched_clusters.add(i)
                break

    # A cluster containing no planted pair is a false positive against the
    # seeded denominator. Many are genuine textual near duplicates that were
    # never planted, so the shape_group diagnostic reports how many.
    unmatched = [c for i, c in enumerate(clusters) if i not in matched_clusters]
    shape_explained = 0
    for cluster in unmatched:
        shapes = {gt[t]["shape_group"] for t in cluster if t in gt}
        if len(shapes) == 1 and None not in shapes:
            shape_explained += 1

    return {
        "planted_pairs": len(pairs),
        "pairs_recovered": found,
        "recall": found / len(pairs) if pairs else 0.0,
        "clusters_flagged": len(clusters),
        "precision_against_planted_pairs": (
            len(matched_clusters) / len(clusters) if clusters else 0.0
        ),
        "unmatched_clusters": len(unmatched),
        "unmatched_explained_by_shared_template_shape": shape_explained,
        "precision_note": (
            "Precision is scored against the 18 planted pairs only. "
            f"{shape_explained} of {len(unmatched)} unmatched clusters consist "
            "entirely of tickets built from one template shape, so they are real "
            "textual near duplicates that were never planted. The denominator is "
            "stated rather than adjusted."
        ),
    }


def score_pii(gt, flags, v2_bodies) -> dict:
    planted = {t for t, r in gt.items() if r["has_pii"]}
    flagged = {f["ticket_id"] for f in flags if f["flag_type"] == "pii"}
    true_positive = planted & flagged

    planted_spans = sum(len(gt[t]["pii_spans"]) for t in planted)
    found_spans = sum(
        len(json.loads(f["detail"])["spans"]) for f in flags if f["flag_type"] == "pii"
    )

    leaks = []
    for tid, body in v2_bodies.items():
        if qa.find_pii(body):
            leaks.append(tid)

    return {
        "planted_tickets": len(planted),
        "flagged_tickets": len(flagged),
        "recall": len(true_positive) / len(planted) if planted else 0.0,
        "precision": len(true_positive) / len(flagged) if flagged else 0.0,
        "planted_spans": planted_spans,
        "flagged_spans": found_spans,
        "leaks_after_scrub": len(leaks),
        "leaking_tickets": sorted(leaks)[:20],
    }


def score_abstention(gt, llm_labels, heur_labels, alignment) -> dict:
    """Abstention is working when it lands on genuinely hard items.

    Accuracy on abstained items is not directly measurable, because an
    abstention carries no label. Two proxies are reported instead: whether
    abstentions concentrate on the planted ambiguous tickets, and whether the
    deterministic labeler does worse on them than on the rest.
    """
    abstained = {t for t, r in llm_labels.items() if r["abstain"]}
    answered = set(llm_labels) - abstained
    ambiguous = {t for t, r in gt.items() if r["is_ambiguous"]}

    base_rate = len(ambiguous) / len(gt) if gt else 0.0
    in_abstained = len(abstained & ambiguous) / len(abstained) if abstained else 0.0

    heur = {t: r["label"] for t, r in heur_labels.items()}
    acc_abstained, _, n_abs = accuracy(heur, gt, alignment, subset=abstained)
    acc_answered, _, n_ans = accuracy(heur, gt, alignment, subset=answered)

    return {
        "abstentions": len(abstained),
        "ambiguous_base_rate": base_rate,
        "ambiguous_share_of_abstentions": in_abstained,
        "concentration_ratio": (in_abstained / base_rate) if base_rate else 0.0,
        "heuristic_accuracy_on_abstained": acc_abstained,
        "heuristic_accuracy_on_answered": acc_answered,
        "n_abstained": n_abs,
        "n_answered": n_ans,
        "reading": (
            "Abstention is calibrated when the deterministic labeler scores "
            "materially lower on abstained items than on answered ones, which "
            "means the model abstained on genuinely hard tickets rather than at "
            "random."
        ),
    }


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def run(version: str = "both") -> int:
    gt = load_ground_truth()
    alignment, unreachable = load_alignment()

    with store.session() as conn:
        tickets = {t["ticket_id"]: t for t in store.all_tickets(conn)}
        llm = store.latest_labels(conn, "llm")
        heur = store.latest_labels(conn, "heuristic")
        flags = [
            {"ticket_id": r["ticket_id"], "flag_type": r["flag_type"], "detail": r["detail"]}
            for r in conn.execute("SELECT ticket_id, flag_type, detail FROM qa_flags")
        ]
        v2_rows = list(conn.execute("SELECT * FROM dataset_v2"))
        total_cost = store.total_cost(conn)

    if not llm:
        console.print("[red]No labels to score. Run `python -m dql label`.[/red]")
        return 1

    vendor = {t: tickets[t]["vendor_label"] for t in tickets}
    v1 = {t: (None if r["abstain"] else r["label"]) for t, r in llm.items()}
    heur_pred = {t: r["label"] for t, r in heur.items()}

    have_v2 = bool(v2_rows)
    v2 = {r["ticket_id"]: r["label"] for r in v2_rows} if have_v2 else {}
    v2_bodies = {r["ticket_id"]: r["body_scrubbed"] for r in v2_rows} if have_v2 else {}
    v2_status = {r["ticket_id"]: r["status"] for r in v2_rows} if have_v2 else {}

    # -- accuracy -----------------------------------------------------------
    v1_acc, v1_hits, v1_n = accuracy(v1, gt, alignment)
    heur_acc, _, _ = accuracy(heur_pred, gt, alignment)

    # The vendor labels are already in the ground-truth vocabulary, so they are
    # compared directly rather than through the alignment.
    vendor_hits = sum(
        1 for t in vendor
        if t in gt and (vendor[t] == gt[t]["true_label"] or vendor[t] == gt[t].get("acceptable_alt"))
    )
    vendor_acc = vendor_hits / len(vendor) if vendor else 0.0

    unreachable_count = sum(1 for r in gt.values() if r["true_label"] in unreachable)

    results = {
        "version": "v1",
        "generated_at": store.utcnow(),
        "corpus_size": len(gt),
        "alignment_file": "eval/taxonomy_alignment.yaml",
        "acceptable_alt_policy": (
            "A prediction matching the documented acceptable_alt on a planted "
            "ambiguous ticket counts as correct."
        ),
        "vendor_baseline_accuracy": vendor_acc,
        "v1_accuracy": v1_acc,
        "v1_correct": v1_hits,
        "v1_scored": v1_n,
        "llm_accuracy": v1_acc,
        "heuristic_accuracy": heur_acc,
        "unreachable_ground_truth_leaves": unreachable,
        "tickets_in_unreachable_classes": unreachable_count,
        "unreachable_note": (
            "The induced taxonomy has no counterpart for these ground-truth "
            "leaves, so every ticket in them is a guaranteed error. They are "
            f"{unreachable_count} of {len(gt)} tickets, and they are counted as "
            "errors rather than excluded."
        ),
        "seeded_errors": score_seeded_errors(gt, flags, v1, alignment),
        "duplicates": score_duplicates(gt, flags),
        "pii": score_pii(gt, flags, {t: tickets[t]["body"] for t in tickets}),
        "abstention": score_abstention(gt, llm, heur, alignment),
        "total_cost_usd": total_cost,
    }

    v2_results = None
    if have_v2:
        kept = {t for t, s in v2_status.items() if s != "merged_duplicate"}
        v2_acc, v2_hits, v2_n = accuracy(v2, gt, alignment, subset=kept)
        v2_results = dict(results)
        v2_results.update({
            "version": "v2",
            "v2_accuracy": v2_acc,
            "v2_correct": v2_hits,
            "v2_scored": v2_n,
            "v2_excludes_merged_duplicates": len(v2_status) - len(kept),
            "seeded_errors": score_seeded_errors(gt, flags, v2, alignment),
            "pii": score_pii(gt, flags, v2_bodies),
            "accuracy_delta_points": (v2_acc - v1_acc) * 100,
        })
        v2_results.pop("v1_accuracy", None)

    _write(results, v2_results)
    _print(results, v2_results)
    code = _gates(results, v2_results, total_cost)

    with store.session() as conn:
        run_id = store.start_run(conn, "evaluate")
        store.finish_run(
            conn, run_id,
            status="ok" if code == 0 else "failed",
            note="all gates pass" if code == 0 else "release gates failed",
        )
    return code


def _write(v1_results: dict, v2_results: dict | None) -> None:
    (ROOT / "eval" / "results_v1.json").write_text(
        json.dumps(v1_results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if v2_results:
        (ROOT / "eval" / "results_v2.json").write_text(
            json.dumps(v2_results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _print(r: dict, v2: dict | None) -> None:
    table = Table(title="Accuracy against sealed ground truth",
                  title_justify="left", header_style="bold")
    table.add_column("Measure")
    table.add_column("Value", justify="right")
    table.add_row("vendor labels as delivered", f"{r['vendor_baseline_accuracy']:.1%}")
    table.add_row("dataset v1 (LLM labels)", f"{r['v1_accuracy']:.1%}")
    table.add_row("heuristic labeler", f"{r['heuristic_accuracy']:.1%}")
    if v2:
        table.add_row("dataset v2 (after the loop)", f"{v2['v2_accuracy']:.1%}")
        table.add_row("[bold]v1 to v2 delta",
                      f"[bold]{v2['accuracy_delta_points']:+.1f} points")
    console.print(table)

    console.print(
        f"[dim]{r['unreachable_note']}[/dim]"
    )

    se = (v2 or r)["seeded_errors"]
    dup = r["duplicates"]
    pii = (v2 or r)["pii"]
    ab = r["abstention"]

    detail = Table(title="Seeded phenomena", title_justify="left", header_style="bold")
    detail.add_column("Phenomenon")
    detail.add_column("Planted", justify="right")
    detail.add_column("Caught", justify="right")
    detail.add_column("Recall", justify="right")
    detail.add_row("vendor label errors", str(se["planted"]),
                   str(se["flagged_by_qa"]), f"{se['recall']:.1%}")
    detail.add_row("duplicate pairs", str(dup["planted_pairs"]),
                   str(dup["pairs_recovered"]), f"{dup['recall']:.1%}")
    detail.add_row("PII tickets", str(pii["planted_tickets"]),
                   str(pii["flagged_tickets"]), f"{pii['recall']:.1%}")
    console.print(detail)

    console.print(
        f"[dim]duplicate precision against planted pairs: "
        f"{dup['precision_against_planted_pairs']:.1%}. "
        f"{dup['unmatched_explained_by_shared_template_shape']} of "
        f"{dup['unmatched_clusters']} unmatched clusters are tickets sharing one "
        f"template shape, that is real near duplicates that were never planted."
        f"[/dim]"
    )

    calib = Table(title="Abstention calibration", title_justify="left", header_style="bold")
    calib.add_column("Measure")
    calib.add_column("Value", justify="right")
    calib.add_row("abstentions", str(ab["abstentions"]))
    calib.add_row("ambiguous share of abstentions",
                  f"{ab['ambiguous_share_of_abstentions']:.1%}")
    calib.add_row("ambiguous base rate in corpus", f"{ab['ambiguous_base_rate']:.1%}")
    calib.add_row("heuristic accuracy on abstained items",
                  f"{ab['heuristic_accuracy_on_abstained']:.1%}")
    calib.add_row("heuristic accuracy on answered items",
                  f"{ab['heuristic_accuracy_on_answered']:.1%}")
    console.print(calib)
    console.print(f"[dim]{ab['reading']}[/dim]")


def _gates(r: dict, v2: dict | None, total_cost: float) -> int:
    table = Table(title="Release gates", title_justify="left", header_style="bold")
    table.add_column("Gate")
    table.add_column("Required", justify="right")
    table.add_column("Actual", justify="right")
    table.add_column("Result")

    failures = []

    def check(name: str, required: str, actual: str, ok: bool, evaluable: bool = True) -> None:
        if not evaluable:
            table.add_row(name, required, actual, "[yellow]not evaluable[/yellow]")
            return
        table.add_row(name, required, actual,
                      "[green]pass[/green]" if ok else "[red]FAIL[/red]")
        if not ok:
            failures.append(name)

    if v2:
        delta = v2["accuracy_delta_points"]
        check("v2 accuracy at least v1 plus 5 points", ">= +5.0 pts",
              f"{delta:+.1f} pts", delta >= GATES["v2_accuracy_at_least_v1_plus_5"])
    else:
        check("v2 accuracy at least v1 plus 5 points", ">= +5.0 pts",
              "no v2 yet", False, evaluable=False)

    se = (v2 or r)["seeded_errors"]
    check("seeded error recall", ">= 85%", f"{se['recall']:.1%}",
          se["recall"] >= GATES["seeded_error_recall_min"])

    pii = (v2 or r)["pii"]
    if v2:
        check("PII leaks in the exported dataset", "== 0",
              str(pii["leaks_after_scrub"]),
              pii["leaks_after_scrub"] <= GATES["pii_leaks_max"])
    else:
        check("PII leaks in the exported dataset", "== 0",
              "no v2 yet", False, evaluable=False)

    dup = r["duplicates"]
    check("duplicate recall", ">= 80%", f"{dup['recall']:.1%}",
          dup["recall"] >= GATES["duplicate_recall_min"])

    check("total API cost", "<= 3.00 USD", f"{total_cost:.4f} USD",
          total_cost <= GATES["total_cost_max_usd"])

    console.print(table)

    if failures:
        console.print(Panel(
            f"{len(failures)} release gate(s) failed:\n"
            + "\n".join(f"  - {f}" for f in failures)
            + "\n\nThe dataset does not ship in this state. Fix the cause, re-run, "
              "and record the incident in docs/06-CHANGE-LOG.md and the README "
              "Known Gaps.",
            title="[red]release blocked", title_align="left",
        ))
        return 1

    console.print(Panel(
        "All release gates pass. Results written to eval/results_v1.json"
        + (" and eval/results_v2.json" if v2 else ""),
        title="[green]release gates green", title_align="left",
    ))
    return 0
