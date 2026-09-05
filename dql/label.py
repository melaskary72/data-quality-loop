"""Labeling engine: two independent annotators over the locked taxonomy.

Two annotators, on purpose. Agreement between two independent annotators is
what makes a kappa meaningful, and a deterministic second annotator keeps the
system useful when the API is unavailable.

Neither labeler ever sees ground truth, and neither sees the vendor label.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import induce, llm, paths, store, yamlio

console = Console()

BATCH_SIZE = 10
MAX_RATIONALE_WORDS = 25

# Deliberately small and generic. A larger list starts encoding domain
# knowledge, and the heuristic labeler is supposed to derive its signal from
# the taxonomy text alone.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those there here of in on at to
for from with without by as is are was were be been being do does did doing have
has had having it its it's we our us you your they them their he she his her
not no nor so such can could should would may might must will shall about into
over under again further once when where why how all any both each few more most
other some only own same too very just also who whom which what while during
i me my mine yours ours theirs am been being had having does did doing
""".split())

TOKEN = re.compile(r"[a-z][a-z0-9_]+")

LABEL_SYSTEM = """You are labeling support tickets against a fixed taxonomy for \
a training-data pipeline. Your labels become training data, so consistency \
matters more than cleverness.

Rules:
- assign exactly one leaf per ticket, using the leaf name verbatim
- `confidence` is your honest probability that the label is correct, from 0 to 1
- `rationale` is at most 25 words and names the evidence in the ticket
- set `abstain` to true when no leaf genuinely fits, or when the ticket \
supports two leaves equally well and picking one would be arbitrary

Abstaining is a real answer, not a failure. A confident wrong label is far more \
expensive to us than an honest abstention, because it silently corrupts the \
dataset while an abstention gets routed to a human. Do not abstain to avoid \
effort, and do not force a label to look decisive.

Some tickets mix English and another language. Label them on their content, \
exactly as you would a monolingual ticket.

Here is the taxonomy:

{taxonomy}"""


def label_schema(valid_leaves: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "string"},
                        "label": {"type": "string", "enum": valid_leaves + ["none"]},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                        "abstain": {"type": "boolean"},
                    },
                    "required": ["ticket_id", "label", "confidence",
                                 "rationale", "abstain"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["labels"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# heuristic labeler
# --------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    return [
        token for token in TOKEN.findall(text.lower())
        if token not in STOPWORDS and len(token) > 2
    ]


class HeuristicLabeler:
    """Deterministic scorer built from the taxonomy text and nothing else.

    Each leaf becomes a weighted term vector drawn from its definition,
    inclusion criteria, and boundary examples. Term weights are inverse
    document frequency computed over the leaf descriptions themselves, so a
    word appearing in every leaf carries almost no signal while a word unique
    to one leaf carries a lot. Exclusion criteria contribute negative weight.

    It never sees ground truth and never sees the vendor label. Its accuracy is
    expected to sit clearly below the LLM's: it exists to establish the floor
    the LLM has to beat, and to be the second annotator whose disagreement is
    diagnostic.
    """

    POSITIVE_FIELDS = ("definition", "inclusion_criteria", "boundary_examples")
    SUBJECT_WEIGHT = 2.0
    EXCLUSION_WEIGHT = -0.6

    def __init__(self, taxonomy: dict) -> None:
        self.leaves = induce.leaf_names(taxonomy)
        self.positive: dict[str, Counter] = {}
        self.negative: dict[str, Counter] = {}

        for domain in taxonomy["domains"]:
            for leaf in domain["leaves"]:
                pos: Counter = Counter()
                for field in self.POSITIVE_FIELDS:
                    value = leaf.get(field, [])
                    parts = [value] if isinstance(value, str) else list(value)
                    for part in parts:
                        pos.update(tokenize(part))
                # The leaf name itself is strong evidence.
                pos.update(tokenize(leaf["name"].replace("_", " ")) * 3)
                self.positive[leaf["name"]] = pos

                neg: Counter = Counter()
                for part in leaf.get("exclusion_criteria", []):
                    neg.update(tokenize(part))
                self.negative[leaf["name"]] = neg

        # IDF over the leaf descriptions, not over the corpus: the corpus is
        # what we are labeling, and fitting term weights to it would be a
        # different (and less honest) system.
        n = len(self.leaves)
        df: Counter = Counter()
        for pos in self.positive.values():
            df.update(set(pos))
        self.idf = {
            term: math.log((n + 1) / (count + 1)) + 1.0
            for term, count in df.items()
        }
        self.norm = {
            leaf: math.sqrt(sum((tf * self.idf.get(term, 1.0)) ** 2
                                for term, tf in pos.items())) or 1.0
            for leaf, pos in self.positive.items()
        }

    def score(self, subject: str, body: str) -> dict[str, float]:
        terms: Counter = Counter()
        terms.update({t: self.SUBJECT_WEIGHT for t in tokenize(subject)})
        for token in tokenize(body):
            terms[token] += 1.0

        scores: dict[str, float] = {}
        for leaf in self.leaves:
            pos, neg = self.positive[leaf], self.negative[leaf]
            total = 0.0
            for term, count in terms.items():
                weight = self.idf.get(term, 0.0)
                if term in pos:
                    total += count * pos[term] * weight
                if term in neg:
                    total += count * neg[term] * weight * self.EXCLUSION_WEIGHT
            scores[leaf] = total / self.norm[leaf]
        return scores

    def label(self, subject: str, body: str) -> tuple[str, float]:
        """Return (leaf, score). Score is the normalized margin over the runner
        up, which is a usable confidence proxy without being a probability."""
        scores = self.score(subject, body)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        best_leaf, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best <= 0:
            return best_leaf, 0.0
        margin = (best - second) / best
        return best_leaf, round(min(1.0, max(0.0, margin)), 4)


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def run(source: str = "both", fresh: bool = False) -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        if store.ticket_count(conn) == 0:
            console.print("[red]No tickets. Run `python -m dql generate` first.[/red]")
            return 1
        store.assert_taxonomy_locked(conn)

        taxonomy = yamlio.load(paths.TAXONOMY)
        valid_leaves = induce.leaf_names(taxonomy)
        tickets = store.all_tickets(conn)
        console.print(
            f"Labeling [bold]{len(tickets)}[/bold] tickets against "
            f"[bold]{len(valid_leaves)}[/bold] locked leaves."
        )

        if source in ("heuristic", "both"):
            _run_heuristic(conn, taxonomy, tickets)
        if source in ("llm", "both"):
            code = _run_llm(conn, taxonomy, valid_leaves, tickets, fresh)
            if code != 0:
                return code

        _print_summary(conn, valid_leaves)
    return 0


def _run_heuristic(conn, taxonomy: dict, tickets) -> None:
    console.print("\n[bold]heuristic labeler[/bold] (deterministic, no API calls)")
    run_id = store.start_run(conn, "label", model="heuristic")
    labeler = HeuristicLabeler(taxonomy)
    rows = []
    for ticket in tickets:
        leaf, score = labeler.label(ticket["subject"], ticket["body"])
        rows.append({
            "ticket_id": ticket["ticket_id"],
            "source": "heuristic",
            "label": leaf,
            "confidence": score,
            "rationale": "deterministic term overlap against the taxonomy text",
            "abstain": False,
        })
    store.insert_labels(conn, run_id, rows)
    store.finish_run(conn, run_id, note=f"{len(rows)} heuristic labels")
    console.print(f"   {len(rows)} labels, 0 API calls, 0.0000 USD")


def _run_llm(conn, taxonomy: dict, valid_leaves: list[str], tickets, fresh: bool) -> int:
    console.print(f"\n[bold]LLM labeler[/bold] ({llm.get_model()}, "
                  f"batches of {BATCH_SIZE}{', cache bypassed' if fresh else ''})")
    run_id = store.start_run(conn, "label", model=llm.get_model())
    client = llm.Client(conn, run_id, fresh=fresh)

    system = LABEL_SYSTEM.format(taxonomy=induce.render_taxonomy(taxonomy))
    schema = label_schema(valid_leaves)

    rows: list[dict] = []
    batches = [tickets[i:i + BATCH_SIZE] for i in range(0, len(tickets), BATCH_SIZE)]
    for n, batch in enumerate(batches, start=1):
        batch_started = time.monotonic()
        payload = "\n\n".join(
            f"[{t['ticket_id']}]\nSubject: {t['subject']}\n{t['body']}" for t in batch
        )
        try:
            result = client.complete_json(
                system=system,
                user="Label each ticket.\n\n" + payload,
                schema=schema,
                max_tokens=3000,
                cache_system=True,
            )
        except llm.CostCapExceeded as exc:
            store.finish_run(conn, run_id, status="failed", note="cost cap")
            console.print(Panel(str(exc), title="[red]cost cap", title_align="left"))
            return 3

        seen = set()
        for item in result.data.get("labels", []):
            ticket_id = item["ticket_id"]
            seen.add(ticket_id)
            abstain = bool(item.get("abstain"))
            label = item.get("label", "none")
            if abstain or label == "none" or label not in valid_leaves:
                label, abstain = None, True
            words = (item.get("rationale") or "").split()
            rows.append({
                "ticket_id": ticket_id,
                "source": "llm",
                "label": label,
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.0)))),
                "rationale": " ".join(words[:MAX_RATIONALE_WORDS]),
                "abstain": abstain,
            })
        # A ticket the model silently dropped is an abstention, not a gap.
        for ticket in batch:
            if ticket["ticket_id"] not in seen:
                rows.append({
                    "ticket_id": ticket["ticket_id"], "source": "llm", "label": None,
                    "confidence": 0.0, "rationale": "no label returned for this ticket",
                    "abstain": True,
                })

        elapsed = time.monotonic() - batch_started
        if not result.cached:
            console.print(
                f"   batch {n:>2} of {len(batches)}: {elapsed:5.1f}s, "
                f"{client.calls_made} calls, {client.calls_cached} cached, "
                f"cumulative [bold]{client.spend():.4f} USD[/bold]"
            )
        elif n % 10 == 0:
            console.print(
                f"   batch {n:>2} of {len(batches)}: replayed from cache, "
                f"{client.calls_cached} cache hits, 0.0000 USD spent"
            )

    store.insert_labels(conn, run_id, rows)
    store.finish_run(conn, run_id, note=f"{len(rows)} llm labels")
    if client.cache_write_tokens or client.cache_read_tokens:
        console.print(
            f"   prompt cache: {client.cache_write_tokens:,} tokens written, "
            f"{client.cache_read_tokens:,} read"
        )
    else:
        console.print(
            "   [yellow]prompt cache did not engage[/yellow]: the taxonomy block is "
            "about 2.8k tokens and this model's minimum cacheable prefix is 4096, "
            "so the API accepts cache_control and silently ignores it. Measured, "
            "not assumed: see docs/06-CHANGE-LOG.md."
        )
    return 0


def _print_summary(conn, valid_leaves: list[str]) -> None:
    llm_labels = store.latest_labels(conn, "llm")
    heur_labels = store.latest_labels(conn, "heuristic")

    # Vendor labels live in the vendor vocabulary, not in induced-leaf space.
    # Counting them directly against induced leaf names reported 0 for every
    # row except `feature_request`, which is simply the one string the two
    # vocabularies happen to share. Map them first.
    alignment = {}
    if paths.VENDOR_ALIGNMENT.exists():
        alignment = yamlio.load(paths.VENDOR_ALIGNMENT).get("alignment", {})

    table = Table(title="Label distribution", title_justify="left", header_style="bold")
    table.add_column("Leaf")
    table.add_column("Vendor", justify="right")
    table.add_column("LLM", justify="right")
    table.add_column("Heuristic", justify="right")

    vendor = Counter()
    vendor_unmapped = 0
    for r in conn.execute("SELECT vendor_label FROM tickets"):
        mapped = alignment.get(r["vendor_label"]) if alignment else None
        if mapped is None:
            vendor_unmapped += 1
        else:
            vendor[mapped] += 1
    llm_counts = Counter(r["label"] for r in llm_labels.values() if r["label"])
    heur_counts = Counter(r["label"] for r in heur_labels.values() if r["label"])

    for leaf in valid_leaves:
        table.add_row(leaf, str(vendor.get(leaf, 0)),
                      str(llm_counts.get(leaf, 0)), str(heur_counts.get(leaf, 0)))
    abstained = sum(1 for r in llm_labels.values() if r["abstain"])
    table.add_row("[dim]abstained[/dim]", "", str(abstained), "0")
    table.add_row("[dim]vendor label no leaf covers[/dim]", str(vendor_unmapped), "", "")
    console.print(table)
    console.print(
        "[dim]Vendor counts are mapped into induced-leaf space through "
        "data/vendor_alignment.yaml. Two vendor labels collapse onto "
        "billing_dispute_or_refund, which is why that row is larger than any "
        "single vendor class.[/dim]"
    )

    if llm_labels:
        confidences = [r["confidence"] for r in llm_labels.values() if not r["abstain"]]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        console.print(
            f"LLM mean confidence on non abstained items: [bold]{mean_conf:.3f}[/bold], "
            f"abstentions: [bold]{abstained}[/bold] of {len(llm_labels)}"
        )
    console.print(
        f"cumulative spend [bold]{store.total_cost(conn):.4f} USD[/bold] "
        f"of {llm.get_cost_cap():.2f} cap"
    )
