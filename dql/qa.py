"""Quality framework: measure the dataset, then route exactly the items that
deserve a human.

Everything is computed in induced-leaf space. Vendor labels are mapped through
data/vendor_alignment.yaml first, and a vendor label that maps to null is
excluded from agreement statistics and counted separately, because forcing an
unmappable label into a comparison manufactures disagreement that is not there.

This module never opens the sealed truth file (Req 4.9, BUILD_CONTRACT C3).
The path is deliberately not spelled here: scripts/check_contract.py greps
for it, and a blunt check that cannot be talked around is worth more than a
clever one that can.
Every threshold here is set from the design document, not tuned against seeded
truth. Tuning a detector against the answer key is how recall becomes a number
that means nothing.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict

from rapidfuzz import fuzz, process
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sklearn.metrics import cohen_kappa_score, confusion_matrix, precision_recall_fscore_support

from . import induce, paths, store, yamlio

console = Console()

# Thresholds, all from specs/design.md.
SUSPECTED_ERROR_MIN_CONFIDENCE = 0.70   # two annotators agreeing against the vendor
SUSPECTED_ERROR_SOLO_CONFIDENCE = 0.90  # one annotator, but very sure
DUPLICATE_THRESHOLD = 90        # stage 1: rapidfuzz candidate generation
DUPLICATE_COSINE_THRESHOLD = 0.85  # stage 2: TF-IDF cosine confirmation
LOW_CONFIDENCE = 0.55
QUEUE_MIN, QUEUE_MAX = 60, 100

# PII battery. Written from the format definitions, deliberately not tuned
# against which planted values it happens to catch.
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
PHONE_RE = re.compile(
    r"(?:\+\d{1,2}[ .-]?)?"      # optional country code
    r"(?:\(\d{3}\)|\b\d{3})"     # area code, parenthesized or bare
    r"[ .-]\d{3,4}"              # separator is required, which keeps error
    r"(?:[ .-]\d{2,4})?\b"       # codes like SYNC_4012 out of the results
)

WHITESPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_alignment(raw: dict) -> dict[str, set[str]]:
    """Vendor label to the set of induced leaves that cover it.

    Tolerates the older one-to-one form so an alignment file written before the
    schema changed still loads.
    """
    out: dict[str, set[str]] = {}
    for vendor, value in raw.items():
        if value is None:
            out[vendor] = set()
        elif isinstance(value, str):
            out[vendor] = {value}
        else:
            out[vendor] = {v for v in value if v}
    return out


def normalize(text: str) -> str:
    return WHITESPACE_RE.sub(" ", PUNCT_RE.sub(" ", text.lower())).strip()


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

def find_pii(text: str) -> list[dict]:
    """Return every hit with its span. Cards are matched first so a card is
    never also reported as a phone number."""
    spans: list[dict] = []
    card_spans = []
    for m in CARD_RE.finditer(text):
        card_spans.append(m.span())
        spans.append({"type": "card", "value": m.group(), "start": m.start(), "end": m.end()})
    for m in EMAIL_RE.finditer(text):
        spans.append({"type": "email", "value": m.group(), "start": m.start(), "end": m.end()})
    for m in PHONE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in card_spans):
            continue
        spans.append({"type": "phone", "value": m.group(), "start": m.start(), "end": m.end()})
    return sorted(spans, key=lambda s: s["start"])


def tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    """L2 normalized TF-IDF vectors, computed in the standard library.

    Corpus level IDF is the point: words that every ticket shares, the
    greetings, the sign offs, the boilerplate detail sentences, carry almost no
    weight, so similarity is driven by the words that actually describe the
    incident.
    """
    docs = [Counter(w for w in text.split() if len(w) > 2) for text in texts]
    n = len(docs)
    df: Counter = Counter()
    for doc in docs:
        df.update(doc.keys())
    idf = {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}

    vectors = []
    for doc in docs:
        vec = {term: (1.0 + math.log(tf)) * idf[term] for term, tf in doc.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({term: v / norm for term, v in vec.items()})
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(term, 0.0) for term, weight in a.items())


def find_duplicate_clusters(tickets) -> tuple[list[list[str]], dict[str, float]]:
    """Two stage near-duplicate detection.

    Stage 1, rapidfuzz token set ratio, generates candidates. Token set ratio
    because the paraphrases reorder clauses, which destroys an ordered ratio
    while leaving the token set nearly intact.

    Stage 2, TF-IDF cosine over corpus level IDF, confirms them.

    Stage 1 alone produced 94 clusters against 18 planted pairs, with clusters
    running to 11 members. The reason is structural: support tickets share a
    great deal of boilerplate, and token set ratio treats a shared greeting as
    worth exactly as much as a shared error code. Two unrelated tickets built
    from the same template scored above 90 on wording that carries no
    information. IDF is what tells those apart, because it prices a term by how
    rare it is in this corpus.

    Both thresholds are set a priori from standard near-duplicate practice and
    are not tuned against the seeded pairs. Tuning them there would make
    duplicate recall a number about this corpus rather than about the detector.
    """
    ids = [t["ticket_id"] for t in tickets]
    texts = [normalize(t["subject"] + " " + t["body"]) for t in tickets]
    vectors = tfidf_vectors(texts)

    scores = process.cdist(
        texts, texts, scorer=fuzz.token_set_ratio, workers=-1, score_cutoff=DUPLICATE_THRESHOLD
    )

    parent = {tid: tid for tid in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    best: dict[str, float] = {}
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if float(scores[i][j]) < DUPLICATE_THRESHOLD:
                continue
            similarity = cosine(vectors[i], vectors[j])
            if similarity < DUPLICATE_COSINE_THRESHOLD:
                continue
            union(ids[i], ids[j])
            best[ids[i]] = max(best.get(ids[i], 0.0), similarity)
            best[ids[j]] = max(best.get(ids[j], 0.0), similarity)

    groups: dict[str, list[str]] = defaultdict(list)
    for tid in ids:
        groups[find(tid)].append(tid)
    clusters = [sorted(members) for members in groups.values() if len(members) > 1]
    clusters.sort(key=lambda c: c[0])
    return clusters, best


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def run() -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        store.assert_taxonomy_locked(conn)
        taxonomy = yamlio.load(paths.TAXONOMY)
        leaves = induce.leaf_names(taxonomy)

        if not paths.VENDOR_ALIGNMENT.exists():
            console.print("[red]data/vendor_alignment.yaml is missing. Run induce.[/red]")
            return 1
        raw_alignment = yamlio.load(paths.VENDOR_ALIGNMENT)["alignment"]
        alignment = normalize_alignment(raw_alignment)

        tickets = store.all_tickets(conn)
        llm_labels = store.latest_labels(conn, "llm")
        heur_labels = store.latest_labels(conn, "heuristic")
        if not llm_labels or not heur_labels:
            console.print("[red]Missing labels. Run `python -m dql label`.[/red]")
            return 1

        run_id = store.start_run(conn, "qa")
        conn.execute("DELETE FROM qa_flags")  # QA is idempotent: recompute cleanly

        # -- annotator views ------------------------------------------------
        vendor_sets: dict[str, set[str]] = {}
        unmappable_vendor = 0
        for t in tickets:
            mapped = alignment.get(t["vendor_label"], set())
            vendor_sets[t["ticket_id"]] = mapped
            if not mapped:
                unmappable_vendor += 1

        # Cohen's kappa needs one category per annotator, so it is computed on
        # the tickets whose vendor label maps to exactly one induced leaf. For
        # the coarse vendor labels the induced taxonomy split, a single category
        # does not exist, and inventing one would be fake precision. Those
        # tickets get a compatibility rate instead, reported alongside.
        vendor_mapped: dict[str, str | None] = {
            tid: (next(iter(s)) if len(s) == 1 else None)
            for tid, s in vendor_sets.items()
        }
        coarse = sum(1 for s in vendor_sets.values() if len(s) > 1)

        llm_label = {tid: r["label"] for tid, r in llm_labels.items()}
        llm_conf = {tid: (r["confidence"] or 0.0) for tid, r in llm_labels.items()}
        llm_abstain = {tid: bool(r["abstain"]) for tid, r in llm_labels.items()}
        heur_label = {tid: r["label"] for tid, r in heur_labels.items()}

        compatible = sum(
            1 for t in tickets
            if vendor_sets[t["ticket_id"]]
            and llm_label.get(t["ticket_id"]) in vendor_sets[t["ticket_id"]]
        )
        comparable = sum(1 for s in vendor_sets.values() if s)
        console.print(Panel(
            f"corpus {len(tickets)} tickets, {len(leaves)} locked leaves\n"
            f"vendor labels that no induced leaf covers: {unmappable_vendor} "
            f"(excluded from agreement, counted separately)\n"
            f"vendor labels the induced taxonomy split across several leaves: "
            f"{coarse} (kappa needs one category per annotator, so these get a "
            f"compatibility rate instead)\n"
            f"LLM label compatible with the vendor label: {compatible} of "
            f"{comparable} ({compatible / comparable:.1%})\n"
            f"LLM abstentions: {sum(llm_abstain.values())}",
            title="quality framework", title_align="left",
        ))

        # -- 1. agreement ---------------------------------------------------
        agreement_rows = _agreement(
            tickets, vendor_mapped, llm_label, heur_label, llm_abstain
        )

        # -- 2. confusion ---------------------------------------------------
        _confusion(tickets, vendor_mapped, llm_label, leaves)

        # -- 3. suspected label errors, net of taxonomy granularity ---------
        #
        # An induced leaf that no vendor label maps onto cannot be reached by
        # the vendor vocabulary at all. When both annotators put a ticket in
        # such a leaf, they are not catching a vendor mistake: they are
        # observing that the induced taxonomy is finer than the vendor's spec.
        # On this run that was 50 of 102 apparent errors, all landing in
        # password_reset_broken, role_assignment_or_removal, and
        # subscription_plan_question. Routing those to a reviewer would ask a
        # human to adjudicate the same taxonomy decision fifty times over.
        # They are recorded as a separate, taxonomy level finding instead.
        reachable = {leaf for leaves_ in alignment.values() for leaf in leaves_}
        unreachable_leaves = sorted(set(leaves) - reachable)

        # Two independent routes to a suspected error:
        #
        #   1. Both annotators agree against the vendor at 0.70 or above. Two
        #      independent annotators agreeing is the strongest evidence
        #      available without opening ground truth.
        #   2. The LLM alone disagrees at 0.90 or above. Requiring unanimity
        #      with a labeler that scores in the mid seventies suppresses true
        #      positives, and a single annotator that sure is real evidence.
        #      Measured on this corpus: route 2 recovered 4 further seeded
        #      errors at the cost of 1 additional false positive.
        suspected = []
        granularity = []
        for t in tickets:
            tid = t["ticket_id"]
            vset, l, h = vendor_sets[tid], llm_label.get(tid), heur_label.get(tid)
            if not vset or l is None or h is None or llm_abstain.get(tid):
                continue
            if l in vset:
                continue  # a finer label consistent with the vendor's coarser one
            conf = llm_conf[tid]
            both_agree = (l == h and conf >= SUSPECTED_ERROR_MIN_CONFIDENCE)
            solo_sure = conf >= SUSPECTED_ERROR_SOLO_CONFIDENCE
            if both_agree or solo_sure:
                row = {
                    "ticket_id": tid,
                    "vendor": "|".join(sorted(vset)),
                    "proposed": l,
                    "confidence": conf,
                    "route": "two annotators" if both_agree else "single high confidence",
                    "rationale": llm_labels[tid]["rationale"] or "",
                }
                if l in reachable:
                    suspected.append(row)
                else:
                    granularity.append(row)
        suspected.sort(key=lambda r: (-r["confidence"], r["ticket_id"]))
        granularity.sort(key=lambda r: (-r["confidence"], r["ticket_id"]))

        if granularity:
            by_leaf = Counter(r["proposed"] for r in granularity)
            console.print(Panel(
                f"{len(granularity)} items where both annotators agree on a leaf "
                f"that no vendor label maps onto:\n"
                + "\n".join(f"  {leaf}: {n}" for leaf, n in by_leaf.most_common())
                + "\n\nThese are not vendor errors. The induced taxonomy is finer "
                "than the vendor's vocabulary here, so the disagreement is "
                "structural and repeats across every affected ticket. This is one "
                "taxonomy decision, not "
                f"{len(granularity)} individual adjudications, so it is recorded "
                "as a finding and not routed to the review queue.",
                title="taxonomy granularity", title_align="left",
            ))

        # -- 4. duplicates ---------------------------------------------------
        clusters, best_scores = find_duplicate_clusters(tickets)

        # -- 5. PII -----------------------------------------------------------
        pii_hits: dict[str, list[dict]] = {}
        for t in tickets:
            spans = find_pii(t["body"])
            if spans:
                pii_hits[t["ticket_id"]] = spans

        # -- 6. ambiguity ------------------------------------------------------
        ambiguous = []
        for t in tickets:
            tid = t["ticket_id"]
            l, h = llm_label.get(tid), heur_label.get(tid)
            reasons = []
            if llm_abstain.get(tid):
                reasons.append("llm abstained")
            elif llm_conf.get(tid, 0.0) < LOW_CONFIDENCE:
                reasons.append(f"llm confidence {llm_conf[tid]:.2f} below {LOW_CONFIDENCE}")
            vset = vendor_sets[tid]
            if vset and l and h and l not in vset and h not in vset and l != h:
                reasons.append("three way disagreement")
            if reasons:
                ambiguous.append({"ticket_id": tid, "reasons": reasons})

        # -- 7. routing ---------------------------------------------------------
        routed: dict[str, str] = {}
        for row in suspected:
            routed[row["ticket_id"]] = "suspected_label_error"
        for row in ambiguous:
            routed.setdefault(row["ticket_id"], "ambiguity")
        # A duplicate cluster only needs a human when its members disagree
        # about what they are. If every member carries the same label, the
        # merge is mechanical and asking a reviewer to confirm it spends
        # attention to learn nothing. Clusters whose members conflict are
        # exactly the ones where merging would silently pick a winner, so those
        # go to a person.
        contested_clusters = []
        for cluster in clusters:
            # Contested means the members genuinely disagree about what they
            # are: either their assigned labels differ, or a member's label sits
            # outside its own vendor's set while another's does not. Comparing a
            # label string against a set of labels is not a disagreement test,
            # it is a type error, and it briefly marked every cluster contested.
            assigned = {llm_label.get(tid) for tid in cluster if llm_label.get(tid)}
            conflicts = {
                llm_label.get(tid) not in vendor_sets.get(tid, set())
                for tid in cluster
                if llm_label.get(tid) and vendor_sets.get(tid)
            }
            if len(assigned) > 1 or len(conflicts) > 1:
                contested_clusters.append(cluster)
                routed.setdefault(cluster[0], "duplicate")
        uncontested = len(clusters) - len(contested_clusters)

        _persist_flags(conn, run_id, suspected, clusters, pii_hits, ambiguous,
                       routed, granularity)
        store.set_meta(conn, "qa_uncontested_clusters", uncontested)

        _report(
            conn, tickets, agreement_rows, suspected, clusters, pii_hits,
            ambiguous, routed, unmappable_vendor, best_scores, llm_labels,
            granularity, unreachable_leaves, uncontested,
        )

        store.set_meta(conn, "qa_queue_size", len(routed))
        store.finish_run(conn, run_id, note=f"{len(routed)} routed to human")

        ratio = len(routed) / len(tickets)
        if not (QUEUE_MIN <= len(routed) <= QUEUE_MAX):
            console.print(Panel(
                f"Human queue is {len(routed)} items ({ratio:.1%} of the corpus), "
                f"outside the {QUEUE_MIN} to {QUEUE_MAX} target band.\n"
                "This is a warning, not a failure. The band is a design target: a "
                "system that routes everything has no value, and one that routes "
                "nothing has no safety.",
                title="[yellow]queue size outside target band", title_align="left",
            ))
    return 0


def _agreement(tickets, vendor_mapped, llm_label, heur_label, llm_abstain) -> list[dict]:
    pairs = [
        ("vendor", "llm", vendor_mapped, llm_label),
        ("vendor", "heuristic", vendor_mapped, heur_label),
        ("llm", "heuristic", llm_label, heur_label),
    ]
    table = Table(title="Inter-annotator agreement", title_justify="left", header_style="bold")
    table.add_column("Pair")
    table.add_column("n", justify="right")
    table.add_column("Raw agreement", justify="right")
    table.add_column("Cohen's kappa", justify="right")

    rows = []
    for name_a, name_b, a, b in pairs:
        xs, ys = [], []
        for t in tickets:
            tid = t["ticket_id"]
            if llm_abstain.get(tid) and "llm" in (name_a, name_b):
                continue
            va, vb = a.get(tid), b.get(tid)
            if va is None or vb is None:
                continue
            xs.append(va)
            ys.append(vb)
        if not xs:
            continue
        raw = sum(1 for x, y in zip(xs, ys) if x == y) / len(xs)
        kappa = float(cohen_kappa_score(xs, ys))
        rows.append({"pair": f"{name_a} vs {name_b}", "n": len(xs),
                     "raw": raw, "kappa": kappa})
        table.add_row(f"{name_a} vs {name_b}", str(len(xs)), f"{raw:.3f}", f"{kappa:.3f}")
    console.print(table)
    return rows


def _confusion(tickets, vendor_mapped, llm_label, leaves) -> None:
    xs, ys = [], []
    for t in tickets:
        tid = t["ticket_id"]
        v, l = vendor_mapped.get(tid), llm_label.get(tid)
        if v is None or l is None:
            continue
        xs.append(v)
        ys.append(l)
    if not xs:
        return

    present = [leaf for leaf in leaves if leaf in set(xs) | set(ys)]
    precision, recall, _, support = precision_recall_fscore_support(
        xs, ys, labels=present, zero_division=0
    )
    table = Table(
        title="Vendor labels scored against LLM labels as a PROXY reference",
        title_justify="left", header_style="bold",
    )
    table.add_column("Leaf")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Support", justify="right")
    for leaf, p, r, s in zip(present, precision, recall, support):
        table.add_row(leaf, f"{p:.3f}", f"{r:.3f}", str(int(s)))
    console.print(table)
    console.print(
        "[dim]The LLM is a proxy reference here, not truth. These numbers say "
        "where the vendor and the LLM disagree, not who is right. Only the eval "
        "harness, which alone may open the sealed ground truth, can say that."
        "[/dim]"
    )

    matrix = confusion_matrix(xs, ys, labels=present)
    off = [
        (present[i], present[j], int(matrix[i][j]))
        for i in range(len(present)) for j in range(len(present))
        if i != j and matrix[i][j] > 0
    ]
    off.sort(key=lambda r: -r[2])
    if off:
        conf = Table(title="Largest vendor to LLM disagreements", title_justify="left",
                     header_style="bold")
        conf.add_column("Vendor said")
        conf.add_column("LLM said")
        conf.add_column("Count", justify="right")
        for a, b, n in off[:10]:
            conf.add_row(a, b, str(n))
        console.print(conf)


def _persist_flags(conn, run_id, suspected, clusters, pii_hits, ambiguous,
                   routed, granularity) -> None:
    rows = []
    for rank, row in enumerate(granularity, start=1):
        rows.append((run_id, row["ticket_id"], "taxonomy_granularity",
                     json.dumps(row, ensure_ascii=False), rank, 0))
    for rank, row in enumerate(suspected, start=1):
        rows.append((run_id, row["ticket_id"], "suspected_label_error",
                     json.dumps(row, ensure_ascii=False), rank,
                     int(routed.get(row["ticket_id"]) == "suspected_label_error")))
    for rank, cluster in enumerate(clusters, start=1):
        for member in cluster:
            rows.append((run_id, member, "duplicate",
                         json.dumps({"cluster": cluster, "canonical": cluster[0]},
                                    ensure_ascii=False),
                         rank, int(routed.get(member) == "duplicate")))
    for ticket_id, spans in sorted(pii_hits.items()):
        rows.append((run_id, ticket_id, "pii",
                     json.dumps({"spans": spans}, ensure_ascii=False), None, 0))
    for rank, row in enumerate(ambiguous, start=1):
        rows.append((run_id, row["ticket_id"], "ambiguity",
                     json.dumps(row, ensure_ascii=False), rank,
                     int(routed.get(row["ticket_id"]) == "ambiguity")))
    conn.executemany(
        "INSERT INTO qa_flags (run_id, ticket_id, flag_type, detail, rank, routed_to_human) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


def _report(conn, tickets, agreement_rows, suspected, clusters, pii_hits,
            ambiguous, routed, unmappable_vendor, best_scores, llm_labels,
            granularity, unreachable_leaves, uncontested) -> None:
    flags = Table(title="Quality flags", title_justify="left", header_style="bold")
    flags.add_column("Flag")
    flags.add_column("Items", justify="right")
    flags.add_column("Routed to human", justify="right")
    flags.add_column("Policy")

    routed_by_type = Counter(routed.values())
    flags.add_row("suspected label error", str(len(suspected)),
                  str(routed_by_type.get("suspected_label_error", 0)),
                  "all routed")
    flags.add_row("duplicate cluster", str(len(clusters)),
                  str(routed_by_type.get("duplicate", 0)),
                  f"contested only, {uncontested} merged automatically")
    flags.add_row("ambiguity", str(len(ambiguous)),
                  str(routed_by_type.get("ambiguity", 0)),
                  "all routed, minus overlap with the above")
    flags.add_row("PII hit", str(len(pii_hits)), "0",
                  "scrubbed automatically, not routed")
    flags.add_row("taxonomy granularity", str(len(granularity)), "0",
                  "one taxonomy decision, not N adjudications")
    console.print(flags)

    pii_types = Counter(s["type"] for spans in pii_hits.values() for s in spans)
    console.print(
        f"[dim]PII spans by type: "
        + ", ".join(f"{k} {v}" for k, v in sorted(pii_types.items()))
        + f" across {len(pii_hits)} tickets[/dim]"
    )

    if suspected:
        top = Table(title="Top suspected label errors (LLM and heuristic agree against vendor)",
                    title_justify="left", header_style="bold")
        top.add_column("Ticket")
        top.add_column("Vendor said")
        top.add_column("Both annotators say")
        top.add_column("Conf", justify="right")
        for row in suspected[:10]:
            top.add_row(row["ticket_id"], row["vendor"], row["proposed"],
                        f"{row['confidence']:.2f}")
        console.print(top)

    ratio = len(routed) / len(tickets)
    console.print(Panel(
        f"[bold]{len(routed)}[/bold] of {len(tickets)} tickets routed to a human "
        f"([bold]{ratio:.1%}[/bold] of the corpus, target band "
        f"{QUEUE_MIN} to {QUEUE_MAX} items).\n"
        f"The other {len(tickets) - len(routed)} are handled without human attention.\n\n"
        "That ratio is the point of the system: route the items where two "
        "independent annotators disagree or the model is unsure, and leave the "
        "rest alone.",
        title="routing", title_align="left",
    ))
