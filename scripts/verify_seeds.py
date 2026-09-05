#!/usr/bin/env python3
"""Assert that every seeded phenomenon claimed in the manifest is actually
planted in the sealed ground truth, and is actually detectable.

BUILD_CONTRACT C3 carve out 2: this tool reads data/ground_truth.jsonl. It is a
verification tool, not a pipeline component. No labeling, QA, review,
improvement, report, or export code imports it.

Counting is not enough. A seeded phenomenon that no detector could ever catch
would make recall meaningless, so this also checks that the plants are well
formed: PII spans land on the real substring, duplicate pairs are actually
near duplicates by the same measure QA uses, and seeded label errors are
plausible confusions rather than random leaves.

Exits non zero on any mismatch.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dql import paths, templates as T  # noqa: E402

GROUND_TRUTH_PATH = paths.DATA / "ground_truth.jsonl"

ARABIC = re.compile(r"[؀-ۿ]")

failures: list[str] = []
checks: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str) -> None:
    checks.append((name, detail))
    if not condition:
        failures.append(f"{name}: {detail}")


def main() -> int:
    if not GROUND_TRUTH_PATH.exists():
        print("ground truth is missing. Run `python -m dql generate` first.")
        return 1
    if not paths.SEED_MANIFEST.exists():
        print("seed manifest is missing. Run `python -m dql generate` first.")
        return 1

    manifest = json.loads(paths.SEED_MANIFEST.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in GROUND_TRUTH_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    intended = manifest["seeded"]

    conn = sqlite3.connect(paths.DB)
    conn.row_factory = sqlite3.Row
    bodies = {
        r["ticket_id"]: r["body"]
        for r in conn.execute("SELECT ticket_id, body FROM tickets")
    }
    subjects = {
        r["ticket_id"]: r["subject"]
        for r in conn.execute("SELECT ticket_id, subject FROM tickets")
    }
    conn.close()

    # -- corpus size --------------------------------------------------------
    check(
        "corpus size",
        len(records) == manifest["count"],
        f"manifest claims {manifest['count']}, ground truth holds {len(records)}",
    )
    check(
        "tickets match ground truth",
        len(bodies) == len(records),
        f"{len(bodies)} tickets in the database, {len(records)} ground-truth records",
    )

    # -- label errors -------------------------------------------------------
    errors = [r for r in records if r["seeded_label_error"]]
    check(
        "label errors planted",
        len(errors) == intended["label_errors"],
        f"manifest claims {intended['label_errors']}, found {len(errors)}",
    )
    wrong_direction = [
        r for r in errors if r["vendor_label"] == r["true_label"]
    ]
    check(
        "label errors are actually wrong",
        not wrong_direction,
        f"{len(wrong_direction)} seeded errors carry the correct vendor label",
    )
    implausible = [
        r["ticket_id"] for r in errors
        if r["vendor_label"] not in T.CONFUSION_MAP.get(r["true_label"], [])
    ]
    check(
        "label errors are plausible confusions",
        not implausible,
        f"{len(implausible)} seeded errors are not drawn from the confusion map",
    )
    clean_mismatch = [
        r["ticket_id"] for r in records
        if not r["seeded_label_error"] and r["vendor_label"] != r["true_label"]
    ]
    check(
        "no unplanted label errors",
        not clean_mismatch,
        f"{len(clean_mismatch)} tickets disagree with truth without being seeded",
    )

    # -- duplicates ---------------------------------------------------------
    pair_ids = {r["duplicate_pair_id"] for r in records if r["duplicate_pair_id"]}
    check(
        "duplicate pairs planted",
        len(pair_ids) == intended["duplicate_pairs"],
        f"manifest claims {intended['duplicate_pairs']}, found {len(pair_ids)}",
    )
    members = [r for r in records if r["duplicate_pair_id"]]
    check(
        "duplicate pairs have two members each",
        len(members) == intended["duplicate_pairs"] * 2,
        f"expected {intended['duplicate_pairs'] * 2} members, found {len(members)}",
    )
    canonicals = [r for r in members if r["is_canonical"]]
    check(
        "one canonical per pair",
        len(canonicals) == intended["duplicate_pairs"],
        f"expected {intended['duplicate_pairs']} canonical members, found {len(canonicals)}",
    )

    from rapidfuzz import fuzz

    by_pair: dict[str, list[dict]] = {}
    for r in members:
        by_pair.setdefault(r["duplicate_pair_id"], []).append(r)

    ratios = []
    label_mismatch = []
    for pair_id, pair in sorted(by_pair.items()):
        a, b = pair[0], pair[1]
        if a["true_label"] != b["true_label"]:
            label_mismatch.append(pair_id)
        text_a = subjects[a["ticket_id"]] + " " + bodies[a["ticket_id"]]
        text_b = subjects[b["ticket_id"]] + " " + bodies[b["ticket_id"]]
        ratios.append((pair_id, fuzz.token_set_ratio(text_a.lower(), text_b.lower())))

    check(
        "duplicate members share a true label",
        not label_mismatch,
        f"{len(label_mismatch)} pairs disagree on the true label",
    )
    # Deliberately NOT asserted against the QA threshold. Requiring every
    # seeded pair to clear the detector's threshold would tune the seed set to
    # the detector, force duplicate recall to 100 percent by construction, and
    # make the metric meaningless. What must hold is that the pairs are genuine
    # near duplicates and are clearly separated from unrelated tickets. Whether
    # the detector then catches all of them is the eval's question, not this
    # tool's.
    worst = min(ratios, key=lambda kv: kv[1]) if ratios else ("none", 0)
    check(
        "duplicate pairs are genuine near duplicates",
        worst[1] >= 70,
        f"lowest token_set_ratio is {worst[1]:.1f} on {worst[0]}, floor for a real paraphrase is 70",
    )

    # Separation from a random baseline: unrelated tickets must score clearly
    # below the weakest planted pair, otherwise the phenomenon is not a
    # phenomenon.
    import random as _random

    baseline_rng = _random.Random(7)
    pair_members = {r["ticket_id"] for r in members}
    unrelated = sorted(set(bodies) - pair_members)
    baseline = []
    for _ in range(400):
        a, b = baseline_rng.sample(unrelated, 2)
        baseline.append(
            fuzz.token_set_ratio(
                (subjects[a] + " " + bodies[a]).lower(),
                (subjects[b] + " " + bodies[b]).lower(),
            )
        )
    baseline.sort()
    baseline_p99 = baseline[int(len(baseline) * 0.99) - 1]
    check(
        "planted pairs separate from unrelated tickets",
        worst[1] > baseline_p99,
        f"weakest pair {worst[1]:.1f} vs 99th percentile of unrelated pairs {baseline_p99:.1f}",
    )

    # -- ambiguous ----------------------------------------------------------
    ambiguous = [r for r in records if r["is_ambiguous"]]
    check(
        "ambiguous tickets planted",
        len(ambiguous) == intended["ambiguous"],
        f"manifest claims {intended['ambiguous']}, found {len(ambiguous)}",
    )
    missing_alt = [r["ticket_id"] for r in ambiguous if not r["acceptable_alt"]]
    check(
        "ambiguous tickets carry an acceptable alternate",
        not missing_alt,
        f"{len(missing_alt)} ambiguous tickets have no acceptable_alt",
    )
    same_alt = [
        r["ticket_id"] for r in ambiguous if r["acceptable_alt"] == r["true_label"]
    ]
    check(
        "acceptable alternate differs from the primary",
        not same_alt,
        f"{len(same_alt)} ambiguous tickets list the primary as its own alternate",
    )
    stray_alt = [
        r["ticket_id"] for r in records
        if not r["is_ambiguous"] and r["acceptable_alt"]
    ]
    check(
        "no alternate on unambiguous tickets",
        not stray_alt,
        f"{len(stray_alt)} unambiguous tickets carry an acceptable_alt",
    )

    # -- PII ----------------------------------------------------------------
    pii = [r for r in records if r["has_pii"]]
    check(
        "PII tickets planted",
        len(pii) == intended["pii"],
        f"manifest claims {intended['pii']}, found {len(pii)}",
    )
    no_spans = [r["ticket_id"] for r in pii if not r["pii_spans"]]
    check(
        "PII tickets record at least one span",
        not no_spans,
        f"{len(no_spans)} PII tickets have no recorded span",
    )
    bad_spans = []
    for r in pii:
        body = bodies[r["ticket_id"]]
        for span in r["pii_spans"]:
            if body[span["start"]:span["end"]] != span["value"]:
                bad_spans.append(f"{r['ticket_id']}:{span['type']}")
    check(
        "PII spans land on the planted value",
        not bad_spans,
        f"{len(bad_spans)} spans do not match the substring they point at",
    )
    stray_spans = [
        r["ticket_id"] for r in records if not r["has_pii"] and r["pii_spans"]
    ]
    check(
        "no spans on clean tickets",
        not stray_spans,
        f"{len(stray_spans)} non-PII tickets carry spans",
    )

    # -- multilingual -------------------------------------------------------
    multilingual = [r for r in records if r["is_multilingual"]]
    check(
        "multilingual tickets planted",
        len(multilingual) == intended["multilingual"],
        f"manifest claims {intended['multilingual']}, found {len(multilingual)}",
    )
    no_arabic = [
        r["ticket_id"] for r in multilingual if not ARABIC.search(bodies[r["ticket_id"]])
    ]
    check(
        "multilingual tickets contain Arabic script",
        not no_arabic,
        f"{len(no_arabic)} multilingual tickets contain no Arabic characters",
    )
    stray_arabic = [
        r["ticket_id"] for r in records
        if not r["is_multilingual"] and ARABIC.search(bodies[r["ticket_id"]])
    ]
    check(
        "no Arabic outside the multilingual set",
        not stray_arabic,
        f"{len(stray_arabic)} unmarked tickets contain Arabic characters",
    )

    # -- disjointness -------------------------------------------------------
    sets = {
        "label_error": {r["ticket_id"] for r in errors},
        "duplicate": {r["ticket_id"] for r in members},
        "ambiguous": {r["ticket_id"] for r in ambiguous},
        "pii": {r["ticket_id"] for r in pii},
        "multilingual": {r["ticket_id"] for r in multilingual},
    }
    overlaps = []
    names = sorted(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = sets[a] & sets[b]
            if shared:
                overlaps.append(f"{a} and {b} share {len(shared)}")
    check(
        "seeded phenomena are disjoint",
        not overlaps,
        "; ".join(overlaps) if overlaps else "all five sets are mutually exclusive",
    )

    # -- report -------------------------------------------------------------
    width = max(len(name) for name, _ in checks)
    print("verify_seeds")
    print("=" * (width + 60))
    for name, detail in checks:
        failed = any(f.startswith(name + ":") for f in failures)
        mark = "FAIL" if failed else "ok  "
        print(f"  {mark}  {name.ljust(width)}  {detail}")
    print("=" * (width + 60))
    if ratios:
        lo = min(r for _, r in ratios)
        hi = max(r for _, r in ratios)
        above = sum(1 for _, r in ratios if r >= 90)
        print(f"  duplicate token_set_ratio range: {lo:.1f} to {hi:.1f}")
        print(
            f"  pairs at or above the QA threshold of 90: {above} of {len(ratios)} "
            "(not asserted, this is what duplicate recall measures)"
        )
    if failures:
        print(f"\nverify_seeds: {len(failures)} failure(s)")
        return 1
    print(f"\nverify_seeds: green, {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
