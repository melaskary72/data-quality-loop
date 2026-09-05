"""Taxonomy induction: a Claude agent proposes the operating vocabulary.

The agent reads unlabeled tickets only. It never sees ground truth and never
sees the vendor label, so the induced taxonomy cannot inherit either the
generator's secret or the vendor's mistakes (Req 2.1).

Deterministic validators then decide whether the proposal is usable. A
proposal that reads fluently can still be structurally unusable, and only code
catches that reliably.

Output is locked by sha256 into the `artifacts` table. Every later stage
refuses to run on drift (BUILD_CONTRACT C2).
"""

from __future__ import annotations

import json
import random

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import llm, paths, store, yamlio

console = Console()

SAMPLE_SIZE = 120
PROPOSE_SIZE = 60
MAP_BATCH = 30
BODY_CHARS = 420

MIN_DOMAINS, MAX_DOMAINS = 2, 4
MIN_LEAVES, MAX_LEAVES = 8, 14
MIN_BOUNDARY_EXAMPLES = 2
MAX_UNMAPPABLE_RATE = 0.05
MAX_REPAIR_ATTEMPTS = 3

UNMAPPABLE = "unmappable"

TAXONOMY_SCHEMA = {
    "type": "object",
    "properties": {
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "leaves": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "definition": {"type": "string"},
                                "inclusion_criteria": {
                                    "type": "array", "items": {"type": "string"},
                                },
                                "exclusion_criteria": {
                                    "type": "array", "items": {"type": "string"},
                                },
                                "boundary_examples": {
                                    "type": "array", "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "name", "definition", "inclusion_criteria",
                                "exclusion_criteria", "boundary_examples",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "description", "leaves"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["domains", "rationale"],
    "additionalProperties": False,
}

def mapping_schema(valid_leaves: list[str]) -> dict:
    """Constrain `leaf` to an enum of the real leaf names.

    The first mapping run assigned a ticket to `product_and_feature_issues`,
    which is a domain name rather than a leaf. Listing the leaves in the prompt
    is a request; an enum in the schema is a guarantee. Constrain the output
    space rather than repairing bad output afterwards.
    """
    return {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "string"},
                        "leaf": {"type": "string", "enum": valid_leaves + [UNMAPPABLE]},
                    },
                    "required": ["ticket_id", "leaf"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["assignments"],
        "additionalProperties": False,
    }

def alignment_schema(valid_leaves: list[str]) -> dict:
    """Same guarantee for the vendor alignment: an induced leaf or "none"."""
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vendor_label": {"type": "string"},
                        "induced_leaf": {"type": "string", "enum": valid_leaves + ["none"]},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["vendor_label", "induced_leaf", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["mappings"],
        "additionalProperties": False,
    }

PROPOSE_SYSTEM = """You are a taxonomy designer working on a support ticket \
corpus for a B2B SaaS operations platform. You design label taxonomies that \
human annotators can apply consistently at scale.

Design a two level taxonomy from the tickets you are given.

Hard constraints:
- between 2 and 4 domains
- between 8 and 14 leaf categories in total, across all domains
- leaf names are lower_snake_case, specific, and mutually exclusive
- every leaf needs a definition, at least two inclusion criteria, at least one \
exclusion criterion, and at least two boundary examples

A boundary example is a short description of a ticket that sits near the edge \
of this leaf, stating which way it should be labeled and why. Boundary \
examples are what stop two annotators from drifting apart, so make them do \
real work: pick the confusion that would actually happen.

Design for the corpus you were given, not for support tickets in general. If a \
theme is absent from the sample, it does not get a leaf.

In `rationale`, explain the cuts you made: where you drew each boundary, which \
merges or splits you considered and rejected, and which leaves you expect to \
be confused with each other."""

REPAIR_SYSTEM = """You are revising a taxonomy you proposed. It failed \
deterministic validation.

Fix every problem listed. The constraints are not negotiable and are not being \
relaxed for you:
- between 2 and 4 domains
- between 8 and 14 leaf categories in total, across all domains
- no two leaves may share a name, anywhere in the taxonomy
- leaf names are lower_snake_case
- every leaf needs a definition, at least two inclusion criteria, at least one \
exclusion criterion, and at least two boundary examples

Do not drop coverage to get under the limits by brute force. Merge leaves that \
a human annotator would struggle to tell apart, and fold rare leaves into the \
nearest broader one. If you merge two leaves, say so in the rationale and make \
the surviving leaf's boundary examples cover the confusion the merge creates.

Return the complete corrected taxonomy, not a diff."""

MAP_SYSTEM = """You are applying a fixed taxonomy to support tickets.

Assign every ticket to exactly one leaf. Use the leaf names verbatim.

If a ticket genuinely does not fit any leaf, assign it the literal string \
"{unmappable}". Do not stretch a leaf to cover a ticket that does not belong \
to it: an honest unmappable rate is the measurement we need here, and a forced \
assignment destroys it.

Assign a LEAF name, never a domain name. The only values you may return are:

{leaf_list}

Here is the taxonomy:

{taxonomy}"""

ALIGN_SYSTEM = """You are aligning two label vocabularies.

The first is the vocabulary an outside labeling vendor used. The second is the \
taxonomy we induced from the raw tickets. They were designed independently and \
they do not match one to one.

For each vendor label, name the single induced leaf that best covers it. If no \
induced leaf covers it, return the literal string "none".

Judge by meaning, not by how similar the two strings look. Two labels with \
nearly identical names can mean different things, and two labels with \
different names can mean the same thing.

Here is the induced taxonomy:

{taxonomy}"""


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def sample_tickets(conn, seed: int, n: int) -> list[dict]:
    """Deterministic sample. Reads subject and body only: no vendor label, no
    ground truth (Req 2.1)."""
    rows = conn.execute(
        "SELECT ticket_id, subject, body FROM tickets ORDER BY ticket_id"
    ).fetchall()
    rng = random.Random(seed)
    chosen = rng.sample(list(rows), k=min(n, len(rows)))
    chosen.sort(key=lambda r: r["ticket_id"])
    return [
        {
            "ticket_id": r["ticket_id"],
            "subject": r["subject"],
            "body": r["body"][:BODY_CHARS],
        }
        for r in chosen
    ]


def render_tickets(tickets: list[dict]) -> str:
    return "\n\n".join(
        f"[{t['ticket_id']}]\nSubject: {t['subject']}\n{t['body']}" for t in tickets
    )


def render_taxonomy(taxonomy: dict) -> str:
    """The taxonomy block that grounds every downstream prompt."""
    lines = []
    for domain in taxonomy["domains"]:
        lines.append(f"## {domain['name']}: {domain.get('description', '')}".rstrip())
        for leaf in domain["leaves"]:
            lines.append(f"\n### {leaf['name']}")
            lines.append(f"{leaf['definition']}")
            if leaf.get("inclusion_criteria"):
                lines.append("Include when:")
                lines.extend(f"  - {c}" for c in leaf["inclusion_criteria"])
            if leaf.get("exclusion_criteria"):
                lines.append("Do not include when:")
                lines.extend(f"  - {c}" for c in leaf["exclusion_criteria"])
            if leaf.get("boundary_examples"):
                lines.append("Boundary cases:")
                lines.extend(f"  - {c}" for c in leaf["boundary_examples"])
        lines.append("")
    return "\n".join(lines)


def leaf_names(taxonomy: dict) -> list[str]:
    return [leaf["name"] for d in taxonomy["domains"] for leaf in d["leaves"]]


# --------------------------------------------------------------------------
# validators
# --------------------------------------------------------------------------

def validate_structure(taxonomy: dict) -> list[str]:
    """Deterministic structural validation. Returns a list of problems."""
    problems: list[str] = []
    domains = taxonomy.get("domains", [])

    if not (MIN_DOMAINS <= len(domains) <= MAX_DOMAINS):
        problems.append(
            f"domain count {len(domains)} is outside {MIN_DOMAINS} to {MAX_DOMAINS}"
        )

    names = leaf_names(taxonomy)
    if not (MIN_LEAVES <= len(names) <= MAX_LEAVES):
        problems.append(
            f"leaf count {len(names)} is outside {MIN_LEAVES} to {MAX_LEAVES}"
        )

    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        problems.append(f"duplicate leaf names: {duplicates}")

    domain_names = [d.get("name", "") for d in domains]
    dup_domains = sorted({n for n in domain_names if domain_names.count(n) > 1})
    if dup_domains:
        problems.append(f"duplicate domain names: {dup_domains}")

    for domain in domains:
        if not domain.get("leaves"):
            problems.append(f"domain {domain.get('name')!r} has no leaves")
        for leaf in domain.get("leaves", []):
            name = leaf.get("name", "<unnamed>")
            if not leaf.get("definition", "").strip():
                problems.append(f"leaf {name!r} has an empty definition")
            if len(leaf.get("inclusion_criteria", [])) < 2:
                problems.append(f"leaf {name!r} has fewer than 2 inclusion criteria")
            if len(leaf.get("exclusion_criteria", [])) < 1:
                problems.append(f"leaf {name!r} has no exclusion criteria")
            if len(leaf.get("boundary_examples", [])) < MIN_BOUNDARY_EXAMPLES:
                problems.append(
                    f"leaf {name!r} has fewer than {MIN_BOUNDARY_EXAMPLES} boundary examples"
                )
            if name != name.lower() or " " in name:
                problems.append(f"leaf {name!r} is not lower_snake_case")
    return problems


def validate_coverage(assignments: dict[str, str], sample: list[dict],
                      valid: set[str]) -> tuple[list[str], float, list[str]]:
    """Coverage validation: every sampled ticket assigned, few unmappable."""
    problems: list[str] = []
    missing = [t["ticket_id"] for t in sample if t["ticket_id"] not in assignments]
    if missing:
        problems.append(f"{len(missing)} sampled tickets were never assigned")

    unknown = sorted({
        leaf for leaf in assignments.values()
        if leaf != UNMAPPABLE and leaf not in valid
    })
    if unknown:
        problems.append(f"assignments used leaves outside the taxonomy: {unknown}")

    unmappable = [tid for tid, leaf in assignments.items() if leaf == UNMAPPABLE]
    rate = len(unmappable) / max(1, len(sample))
    if rate > MAX_UNMAPPABLE_RATE:
        problems.append(
            f"unmappable rate {rate:.1%} exceeds the {MAX_UNMAPPABLE_RATE:.0%} ceiling"
        )
    return problems, rate, unmappable


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def run(fresh: bool = False, relock: bool = False) -> int:
    paths.ensure_dirs()

    with store.session() as conn:
        if store.ticket_count(conn) == 0:
            console.print("[red]No tickets. Run `python -m dql generate` first.[/red]")
            return 1

        existing = store.get_artifact(conn, "taxonomy.yaml")
        if existing is not None and not relock:
            try:
                store.assert_taxonomy_locked(conn)
                console.print(Panel(
                    f"taxonomy.yaml is already locked at {existing['sha256'][:12]} "
                    f"({existing['locked_at']}).\n"
                    "Induction is idempotent, so there is nothing to do.\n"
                    "To induce again deliberately: `python -m dql induce --relock`.",
                    title="already locked", title_align="left",
                ))
                return 0
            except store.TaxonomyDrift as exc:
                console.print(Panel(str(exc), title="[red]drift", title_align="left"))
                return 2

        seed = store.get_meta(conn, "generator_seed", 20260904)
        sample = sample_tickets(conn, seed=seed, n=SAMPLE_SIZE)
        console.print(
            f"Sampled [bold]{len(sample)}[/bold] tickets "
            f"(subject and body only, no vendor label, no ground truth)."
        )

        run_id = store.start_run(conn, "induce", model=llm.get_model())
        client = llm.Client(conn, run_id, fresh=fresh)

        # -- 1. propose ----------------------------------------------------
        console.print("\n[bold]1. proposing a taxonomy[/bold]")
        proposal = client.complete_json(
            system=PROPOSE_SYSTEM,
            user="Here are support tickets sampled from the corpus.\n\n"
                 + render_tickets(sample[:PROPOSE_SIZE]),
            schema=TAXONOMY_SCHEMA,
            max_tokens=8000,
        )
        taxonomy = proposal.data
        console.print(
            f"   {'cache hit' if proposal.cached else 'API call'}, "
            f"{len(leaf_names(taxonomy))} leaves across "
            f"{len(taxonomy['domains'])} domains, "
            f"spend so far {client.spend():.4f} USD"
        )

        # -- 2. structural validation, with a bounded repair loop ----------
        #
        # The first proposal on this corpus came back with 5 domains, 17
        # leaves, and a duplicated leaf name. Rather than relax the bounds to
        # fit the output, which would make the constraints decorative, the
        # validator output is handed back to the agent as a repair task. The
        # constraints never move, the validators still gate, and the number of
        # attempts is recorded as a build fact.
        console.print("\n[bold]2. structural validation[/bold]")
        problems = validate_structure(taxonomy)
        attempts = 1
        while problems and attempts <= MAX_REPAIR_ATTEMPTS:
            for p in problems:
                console.print(f"   [yellow]invalid[/yellow] {p}")
            console.print(
                f"   repair attempt {attempts} of {MAX_REPAIR_ATTEMPTS}, "
                "constraints unchanged"
            )
            repair = client.complete_json(
                system=REPAIR_SYSTEM,
                user="Your proposal:\n\n"
                     + json.dumps(taxonomy, ensure_ascii=False, indent=2)
                     + "\n\nValidation problems to fix:\n"
                     + "\n".join(f"- {p}" for p in problems),
                schema=TAXONOMY_SCHEMA,
                max_tokens=8000,
            )
            taxonomy = repair.data
            problems = validate_structure(taxonomy)
            attempts += 1
            console.print(
                f"   now {len(leaf_names(taxonomy))} leaves across "
                f"{len(taxonomy['domains'])} domains, "
                f"{len(problems)} problem(s) remaining, "
                f"spend {client.spend():.4f} USD"
            )

        if problems:
            _write_failure_report(taxonomy, problems, None, None)
            for p in problems:
                console.print(f"   [red]FAIL[/red] {p}")
            store.finish_run(conn, run_id, status="failed",
                             note=f"{len(problems)} structural problems after "
                                  f"{attempts - 1} repair attempt(s)")
            console.print(Panel(
                f"Induction failed {len(problems)} structural check(s) after "
                f"{attempts - 1} repair attempt(s). The taxonomy was NOT locked.\n"
                f"Report: {paths.INDUCTION_REPORT}",
                title="[red]induction failed", title_align="left",
            ))
            return 1
        console.print(
            f"   [green]ok[/green] all structural checks passed"
            + (f" after {attempts - 1} repair attempt(s)" if attempts > 1 else "")
        )
        structural_attempts = attempts - 1

        # -- 3. map the full sample ----------------------------------------
        console.print("\n[bold]3. mapping the full sample[/bold]")
        taxonomy_block = render_taxonomy(taxonomy)
        valid_leaves = leaf_names(taxonomy)
        map_system = MAP_SYSTEM.format(
            unmappable=UNMAPPABLE,
            taxonomy=taxonomy_block,
            leaf_list="\n".join(f"- {name}" for name in valid_leaves),
        )
        map_schema = mapping_schema(valid_leaves)
        assignments: dict[str, str] = {}
        for start in range(0, len(sample), MAP_BATCH):
            batch = sample[start:start + MAP_BATCH]
            result = client.complete_json(
                system=map_system,
                user="Assign each ticket to one leaf.\n\n" + render_tickets(batch),
                schema=map_schema,
                max_tokens=4000,
            )
            for item in result.data.get("assignments", []):
                assignments[item["ticket_id"]] = item["leaf"].strip()
            console.print(
                f"   batch {start // MAP_BATCH + 1}: {len(batch)} tickets, "
                f"{'cache' if result.cached else 'API'}, "
                f"cumulative {client.spend():.4f} USD"
            )

        # -- 4. coverage validation ----------------------------------------
        console.print("\n[bold]4. coverage validation[/bold]")
        valid = set(valid_leaves)
        cov_problems, rate, unmappable = validate_coverage(assignments, sample, valid)
        console.print(f"   unmappable rate {rate:.1%} "
                      f"(ceiling {MAX_UNMAPPABLE_RATE:.0%}, "
                      f"{len(unmappable)} of {len(sample)})")
        if cov_problems:
            _write_failure_report(taxonomy, cov_problems, rate, unmappable)
            for p in cov_problems:
                console.print(f"   [red]FAIL[/red] {p}")
            store.finish_run(conn, run_id, status="failed",
                             note=f"coverage: {'; '.join(cov_problems)}")
            console.print(Panel(
                f"Induction failed coverage validation. The taxonomy was NOT locked.\n"
                f"Report: {paths.INDUCTION_REPORT}",
                title="[red]induction failed", title_align="left",
            ))
            return 1
        console.print("   [green]ok[/green] coverage is within bounds")

        # -- 5. vendor alignment -------------------------------------------
        console.print("\n[bold]5. aligning the vendor vocabulary[/bold]")
        vendor_labels = sorted({
            r["vendor_label"]
            for r in conn.execute("SELECT DISTINCT vendor_label FROM tickets")
        })
        align = client.complete_json(
            system=ALIGN_SYSTEM.format(taxonomy=taxonomy_block),
            user="Vendor labels to align:\n"
                 + "\n".join(f"- {v}" for v in vendor_labels),
            schema=alignment_schema(valid_leaves),
            max_tokens=4000,
        )
        alignment: dict[str, str | None] = {}
        reasoning: dict[str, str] = {}
        for item in align.data.get("mappings", []):
            leaf = item["induced_leaf"].strip()
            alignment[item["vendor_label"]] = None if leaf in ("none", "") else leaf
            reasoning[item["vendor_label"]] = item.get("reasoning", "")
        for vendor in vendor_labels:
            alignment.setdefault(vendor, None)

        stray = sorted({
            leaf for leaf in alignment.values() if leaf is not None and leaf not in valid
        })
        if stray:
            console.print(
                f"   [yellow]warn[/yellow] alignment named leaves outside the "
                f"taxonomy, dropped to null: {stray}"
            )
            for vendor, leaf in list(alignment.items()):
                if leaf in stray:
                    alignment[vendor] = None

        # -- 6. write and lock ---------------------------------------------
        console.print("\n[bold]6. writing artifacts and locking[/bold]")
        yamlio.dump(
            taxonomy,
            paths.TAXONOMY,
            header=(
                "Induced taxonomy: the operating vocabulary for this repo.\n"
                "Proposed by a Claude agent from unlabeled tickets, then validated\n"
                "deterministically. Locked by sha256 in the artifacts table: editing\n"
                "this file makes every downstream stage refuse to run until it is\n"
                "re-locked deliberately with `python -m dql induce --relock`.\n"
                f"Sample size {SAMPLE_SIZE}. Unmappable rate {rate:.1%}."
            ),
        )
        yamlio.dump(
            {"alignment": alignment, "reasoning": reasoning},
            paths.VENDOR_ALIGNMENT,
            header=(
                "Vendor vocabulary to induced leaf.\n"
                "Derived from public vendor label strings plus the induced taxonomy.\n"
                "Never derived from ground truth, so QA may use it without leaking.\n"
                "A null mapping means no induced leaf covers that vendor label, and\n"
                "those tickets are excluded from agreement statistics and counted\n"
                "separately."
            ),
        )
        paths.TAXONOMY_RATIONALE.write_text(
            _rationale_markdown(taxonomy, rate, unmappable, assignments,
                                repairs=structural_attempts),
            encoding="utf-8",
        )

        digest = store.lock_artifact(conn, "taxonomy.yaml", paths.TAXONOMY)
        store.set_meta(conn, "induction_unmappable_rate", rate)
        store.set_meta(conn, "induction_repair_attempts", structural_attempts)
        store.set_meta(conn, "induction_sample_size", len(sample))
        store.finish_run(
            conn, run_id,
            note=f"{len(valid)} leaves, locked {digest[:12]}, "
                 f"{structural_attempts} structural repair attempt(s)",
        )

        _print_summary(taxonomy, alignment, rate, digest, client)
    return 0


def _rationale_markdown(taxonomy: dict, rate: float, unmappable: list[str],
                        assignments: dict[str, str], repairs: int = 0) -> str:
    counts: dict[str, int] = {}
    for leaf in assignments.values():
        counts[leaf] = counts.get(leaf, 0) + 1

    lines = [
        "# Taxonomy rationale",
        "",
        "This is the agent's own reasoning, kept verbatim as a build artifact.",
        "It is not edited, because the point of keeping it is to show what the",
        "agent actually argued, including anything a reviewer would push back on.",
        "",
        "## The agent's reasoning",
        "",
        taxonomy.get("rationale", "").strip() or "_none returned_",
        "",
        "## Structure",
        "",
    ]
    for domain in taxonomy["domains"]:
        lines.append(f"### {domain['name']}")
        lines.append("")
        lines.append(domain.get("description", "").strip())
        lines.append("")
        for leaf in domain["leaves"]:
            lines.append(f"- **{leaf['name']}**: {leaf['definition']}")
        lines.append("")

    lines += [
        "## Coverage on the induction sample",
        "",
        f"- sample size: {sum(counts.values())}",
        f"- unmappable: {len(unmappable)} ({rate:.1%}, ceiling {MAX_UNMAPPABLE_RATE:.0%})",
        f"- structural repair attempts needed: {repairs}",
        "",
        "| Leaf | Tickets in sample |",
        "|---|---|",
    ]
    for leaf, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {leaf} | {n} |")
    lines.append("")
    return "\n".join(lines)


def _write_failure_report(taxonomy: dict, problems: list[str],
                          rate: float | None, unmappable: list[str] | None) -> None:
    lines = [
        "# Induction failure report",
        "",
        f"Induction failed {len(problems)} validation check(s). "
        "The taxonomy was not locked and no downstream stage will run.",
        "",
        "## Problems",
        "",
    ]
    lines += [f"- {p}" for p in problems]
    if rate is not None:
        lines += ["", f"Unmappable rate: {rate:.1%} over {len(unmappable or [])} tickets."]
    lines += ["", "## Proposal as returned", "", "```json",
              json.dumps(taxonomy, indent=2, ensure_ascii=False), "```", ""]
    paths.INDUCTION_REPORT.write_text("\n".join(lines), encoding="utf-8")


def _print_summary(taxonomy: dict, alignment: dict, rate: float,
                   digest: str, client: llm.Client) -> None:
    table = Table(title="Induced taxonomy", title_justify="left", header_style="bold")
    table.add_column("Domain")
    table.add_column("Leaf")
    table.add_column("Definition")
    for domain in taxonomy["domains"]:
        for i, leaf in enumerate(domain["leaves"]):
            definition = leaf["definition"]
            if len(definition) > 68:
                definition = definition[:65] + "..."
            table.add_row(domain["name"] if i == 0 else "", leaf["name"], definition)
    console.print(table)

    align_table = Table(title="Vendor vocabulary alignment",
                        title_justify="left", header_style="bold")
    align_table.add_column("Vendor label")
    align_table.add_column("Induced leaf")
    for vendor, leaf in sorted(alignment.items()):
        align_table.add_row(vendor, leaf or "[dim]none[/dim]")
    console.print(align_table)

    console.print(Panel(
        f"taxonomy.yaml locked at [bold]{digest[:16]}[/bold]\n"
        f"unmappable rate {rate:.1%}\n"
        f"API calls {client.calls_made}, cache hits {client.calls_cached}\n"
        f"cumulative spend [bold]{client.spend():.4f} USD[/bold] "
        f"of {client.cap:.2f} cap",
        title="induction complete", title_align="left",
    ))
