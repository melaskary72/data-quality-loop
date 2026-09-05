"""Corpus generator: synthesize tickets and seal the ground truth.

Deterministic and offline. No model calls, ever (Req 1.3). Two runs at the same
seed produce byte identical output (Req 1.4).

The five seeded phenomena are planted on mutually disjoint ticket sets so each
detector can be scored without confounding. That is a deliberate simplification
and it is restated in the risk register and in the README Known Gaps: in the
real world a mislabeled duplicate containing PII is entirely normal.

This module is the one carve out to BUILD_CONTRACT C3: it *writes*
data/ground_truth.jsonl and never reads it back.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from . import paths, store, templates as T

console = Console()

N_LABEL_ERRORS = 40
N_DUPLICATE_PAIRS = 18
N_AMBIGUOUS = 30
N_PII = 25
N_MULTILINGUAL = 12

CHANNELS = ["email", "chat", "portal"]
CHANNEL_WEIGHTS = [0.5, 0.25, 0.25]
TIERS = ["free", "growth", "enterprise"]
TIER_WEIGHTS = [0.2, 0.5, 0.3]

BASE_DATE = datetime(2026, 3, 1, 9, 0, 0)


# --------------------------------------------------------------------------
# text construction
# --------------------------------------------------------------------------

def _context(rng: random.Random) -> dict[str, str]:
    """One filler value per slot, per ticket.

    Resolving each placeholder independently produced incoherent tickets: a
    subject asking about Salesforce above a body describing SAP Concur. A
    ticket is one incident, so it gets one context and the subject and body
    share it.
    """
    return {key: rng.choice(options) for key, options in T.FILLERS.items()}


def _fill(text: str, ctx: dict[str, str]) -> str:
    """Resolve {slot} placeholders from this ticket's context."""
    out = text
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", value)
    return out


def _wrap(body: str, ctx: dict[str, str], rng: random.Random) -> str:
    """Add a natural opener, one or two detail sentences, and a closer.

    The detail sentences are drawn independently of the template, which is what
    keeps two unrelated tickets sharing a leaf and a variant from reading as
    near duplicates of each other.
    """
    parts = []
    if rng.random() < 0.65:
        parts.append(rng.choice(T.OPENERS))
    parts.append(body)
    for detail in rng.sample(T.DETAIL_SENTENCES, k=rng.choice([1, 2, 2])):
        parts.append(_fill(detail, ctx))
    if rng.random() < 0.55:
        parts.append(rng.choice(T.CLOSERS))
    return " ".join(parts)


def _paraphrase(body: str, rng: random.Random) -> str:
    """Paraphrase transform for the duplicate member of a pair.

    Reorders clauses and swaps a couple of surface forms while keeping the
    incident, and most of the token set, identical. That is what makes these
    genuine near duplicates rather than either copies or different tickets.
    """
    # Strip the original opener and closer first. Reordering before stripping
    # moved the closer into the middle of the text, where it no longer matched
    # and left a doubled full stop behind.
    text = body
    for opener in T.OPENERS:
        text = text.replace(opener + " ", "")
    for closer in T.CLOSERS:
        text = text.replace(" " + closer, "").replace(closer, "")
    text = text.strip()

    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    if len(sentences) >= 2:
        sentences = [sentences[-1]] + sentences[:-1]
    text = ". ".join(s.rstrip(".") for s in sentences)
    if text and not text.endswith("."):
        text += "."

    swaps = 0
    for old_form, new_form in T.SYNONYMS:
        if swaps >= 2:
            break
        if old_form in text:
            text = text.replace(old_form, new_form, 1)
            swaps += 1

    lead = rng.choice(T.OPENERS)
    tail = rng.choice(T.CLOSERS)
    return f"{lead} {text} {tail}"


def _add_arabic(body: str, rng: random.Random) -> str:
    """Interleave Arabic sentences describing the same issue."""
    fragments = rng.sample(T.ARABIC_FRAGMENTS, k=rng.choice([2, 3]))
    sentences = [s.strip() for s in body.split(". ") if s.strip()]
    if len(sentences) >= 2:
        cut = len(sentences) // 2
        head = ". ".join(sentences[:cut])
        tail = ". ".join(sentences[cut:])
        if not head.endswith("."):
            head += "."
        return f"{head} {fragments[0]} {tail} {' '.join(fragments[1:])}".strip()
    return f"{body} {' '.join(fragments)}"


def _add_pii(body: str, rng: random.Random) -> tuple[str, list[dict]]:
    """Append synthetic PII and record the exact span of every planted value."""
    kinds = ["email", "phone", "card"]
    chosen = [rng.choice(kinds)]
    if rng.random() < 0.35:
        remaining = [k for k in kinds if k not in chosen]
        chosen.append(rng.choice(remaining))

    text = body
    spans: list[dict] = []
    for kind in chosen:
        pool = {"email": T.PII_EMAILS, "phone": T.PII_PHONES, "card": T.PII_CARDS}[kind]
        value = rng.choice(pool)
        carrier = rng.choice(T.PII_CARRIERS).format(value=value)
        text = f"{text} {carrier}"
        start = text.rfind(value)
        spans.append({
            "type": kind,
            "value": value,
            "start": start,
            "end": start + len(value),
        })
    return text, spans


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def build_corpus(seed: int, count: int) -> tuple[list[dict], list[dict], dict]:
    """Return (tickets, ground_truth, manifest). Pure and deterministic."""
    rng = random.Random(seed)

    indices = list(range(count))
    pool = indices[:]
    rng.shuffle(pool)

    def take(n: int) -> list[int]:
        chosen = sorted(pool[:n])
        del pool[:n]
        return chosen

    # Disjoint by construction, allocated in a fixed order.
    ambiguous_idx = take(N_AMBIGUOUS)
    duplicate_idx = take(N_DUPLICATE_PAIRS * 2)
    pii_idx = take(N_PII)
    multilingual_idx = take(N_MULTILINGUAL)
    label_error_idx = take(N_LABEL_ERRORS)

    ambiguous_set = set(ambiguous_idx)
    pii_set = set(pii_idx)
    multilingual_set = set(multilingual_idx)
    label_error_set = set(label_error_idx)

    # Pair up the duplicate slots: (canonical, paraphrase).
    duplicate_pairs = [
        (duplicate_idx[i], duplicate_idx[i + 1])
        for i in range(0, len(duplicate_idx), 2)
    ]
    dup_role: dict[int, tuple[str, bool]] = {}
    for pair_no, (a, b) in enumerate(duplicate_pairs, start=1):
        pair_id = f"DUP-{pair_no:02d}"
        dup_role[a] = (pair_id, True)
        dup_role[b] = (pair_id, False)

    weighted_leaves = list(T.CLASS_WEIGHTS.keys())
    weights = [T.CLASS_WEIGHTS[leaf] for leaf in weighted_leaves]

    # -- assign true labels -------------------------------------------------
    true_label: dict[int, str] = {}
    acceptable_alt: dict[int, str | None] = {}

    for slot, idx in enumerate(ambiguous_idx):
        blend = T.AMBIGUOUS_BLENDS[slot % len(T.AMBIGUOUS_BLENDS)]
        true_label[idx] = blend["primary"]
        acceptable_alt[idx] = blend["alt"]

    for a, b in duplicate_pairs:
        leaf = rng.choices(weighted_leaves, weights=weights, k=1)[0]
        true_label[a] = leaf
        true_label[b] = leaf

    for idx in indices:
        if idx not in true_label:
            true_label[idx] = rng.choices(weighted_leaves, weights=weights, k=1)[0]

    # -- render text --------------------------------------------------------
    subject: dict[int, str] = {}
    body: dict[int, str] = {}
    pii_spans: dict[int, list[dict]] = {}

    ambiguous_slot = {idx: i for i, idx in enumerate(ambiguous_idx)}

    for idx in indices:
        ctx = _context(rng)
        if idx in ambiguous_set:
            blend = T.AMBIGUOUS_BLENDS[ambiguous_slot[idx] % len(T.AMBIGUOUS_BLENDS)]
            subj = _fill(blend["subject"], ctx)
            text = _wrap(_fill(blend["body"], ctx), ctx, rng)
        else:
            leaf = true_label[idx]
            # Paired by index: templates are written so subjects[i] summarizes
            # bodies[i]. Drawing them independently produced tickets whose
            # subject and body described different incidents.
            variant = rng.randrange(len(T.TEMPLATES[leaf]["bodies"]))
            subj = _fill(T.TEMPLATES[leaf]["subjects"][variant], ctx)
            text = _wrap(_fill(T.TEMPLATES[leaf]["bodies"][variant], ctx), ctx, rng)
        subject[idx] = subj
        body[idx] = text

    # Duplicate members are derived from their canonical partner's final text.
    for a, b in duplicate_pairs:
        subject[b] = subject[a]
        body[b] = _paraphrase(body[a], rng)

    for idx in sorted(multilingual_set):
        body[idx] = _add_arabic(body[idx], rng)

    for idx in sorted(pii_set):
        body[idx], spans = _add_pii(body[idx], rng)
        pii_spans[idx] = spans

    # -- vendor labels, with the seeded errors ------------------------------
    vendor_label: dict[int, str] = {}
    seeded_error_true: dict[int, str] = {}
    for idx in indices:
        truth = true_label[idx]
        if idx in label_error_set:
            confusions = T.CONFUSION_MAP[truth]
            vendor_label[idx] = rng.choice(confusions)
            seeded_error_true[idx] = truth
        else:
            vendor_label[idx] = truth

    # -- assemble -----------------------------------------------------------
    tickets: list[dict] = []
    ground_truth: list[dict] = []

    for idx in indices:
        ticket_id = f"T-{idx + 1:04d}"
        created = BASE_DATE + timedelta(
            days=rng.randint(0, 179),
            hours=rng.randint(0, 10),
            minutes=rng.randint(0, 59),
        )
        tickets.append({
            "ticket_id": ticket_id,
            "subject": subject[idx],
            "body": body[idx],
            "channel": rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0],
            "tier": rng.choices(TIERS, weights=TIER_WEIGHTS, k=1)[0],
            "created_at": created.isoformat(timespec="seconds"),
            "vendor_label": vendor_label[idx],
        })

        pair_id, is_canonical = dup_role.get(idx, (None, None))
        ground_truth.append({
            "ticket_id": ticket_id,
            "true_label": true_label[idx],
            "acceptable_alt": acceptable_alt.get(idx),
            "vendor_label": vendor_label[idx],
            "seeded_label_error": idx in label_error_set,
            "seeded_error_true_label": seeded_error_true.get(idx),
            "is_ambiguous": idx in ambiguous_set,
            "has_pii": idx in pii_set,
            "pii_spans": pii_spans.get(idx, []),
            "duplicate_pair_id": pair_id,
            "is_canonical": is_canonical,
            "is_multilingual": idx in multilingual_set,
        })

    observed: dict[str, int] = {}
    for record in ground_truth:
        observed[record["true_label"]] = observed.get(record["true_label"], 0) + 1

    manifest = {
        "seed": seed,
        "count": count,
        "generator_version": "1.0",
        "ground_truth_taxonomy": T.GROUND_TRUTH_TAXONOMY,
        "seeded": {
            "label_errors": N_LABEL_ERRORS,
            "duplicate_pairs": N_DUPLICATE_PAIRS,
            "ambiguous": N_AMBIGUOUS,
            "pii": N_PII,
            "multilingual": N_MULTILINGUAL,
        },
        "phenomena_are_disjoint": True,
        "observed_class_distribution": dict(sorted(observed.items(), key=lambda kv: -kv[1])),
    }
    return tickets, ground_truth, manifest


def run(seed: int = 20260904, count: int = 600) -> int:
    paths.ensure_dirs()
    tickets, ground_truth, manifest = build_corpus(seed, count)

    with store.session() as conn:
        run_id = store.start_run(conn, "generate")
        store.insert_tickets(conn, tickets)
        store.set_meta(conn, "generator_seed", seed)
        store.set_meta(conn, "corpus_count", count)

        # C3 carve out: the generator writes the sealed file and never reads it.
        gt_path = paths.DATA / "ground_truth.jsonl"
        gt_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in ground_truth) + "\n",
            encoding="utf-8",
        )
        paths.SEED_MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        store.finish_run(conn, run_id, note=f"{len(tickets)} tickets, seed {seed}")

    table = Table(title="Corpus generated", title_justify="left", header_style="bold")
    table.add_column("Property")
    table.add_column("Value", justify="right")
    table.add_row("tickets", str(len(tickets)))
    table.add_row("seed", str(seed))
    table.add_row("ground-truth leaves", str(len(T.LEAVES)))
    table.add_row("seeded label errors", str(N_LABEL_ERRORS))
    table.add_row("seeded duplicate pairs", str(N_DUPLICATE_PAIRS))
    table.add_row("seeded ambiguous", str(N_AMBIGUOUS))
    table.add_row("seeded PII tickets", str(N_PII))
    table.add_row("seeded multilingual", str(N_MULTILINGUAL))
    table.add_row("LLM calls", "0")
    table.add_row("cost", "0.0000 USD")
    console.print(table)

    dist = Table(title="Class distribution (hidden ground truth)", title_justify="left", header_style="bold")
    dist.add_column("Leaf")
    dist.add_column("Count", justify="right")
    dist.add_column("Share", justify="right")
    for leaf, n in manifest["observed_class_distribution"].items():
        dist.add_row(leaf, str(n), f"{n / count * 100:.1f} percent")
    console.print(dist)
    console.print(f"[dim]sealed ground truth written, read only by eval/[/dim]")
    return 0
