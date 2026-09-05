# Design: data-quality-loop

This document is the technical design. It records component boundaries, data
flow, failure modes, and the reasoning behind each decision that a reviewer
would otherwise have to reverse engineer from the code.

---

## 1. Shape of the system

```
generate -> induce -> label -> qa -> review -> improve -> evaluate -> report -> export
```

Every stage is a CLI subcommand, is idempotent, and keeps its state in one
SQLite database at `data/dql.db`. Stages communicate through the database and
through a small number of committed artifacts on disk. No stage passes state to
another through memory or through a temporary file.

The central design tension is this: the system must demonstrate LLM judgment
where judgment is genuinely needed, while remaining reproducible enough that a
reviewer can re-run it and get the same numbers. The resolution is a hard split.

- **Deterministic rails**: corpus generation, the heuristic labeler, duplicate
  detection, the PII regex battery, routing policy, the improvement pass, the
  eval harness. Fixed seeds, no model calls, byte-identical across runs.
- **LLM judgment**: taxonomy induction, the LLM labeler, vendor label
  alignment. These are the three places where a rule would be a poor substitute
  for reading the text.

Every LLM response is cached to SQLite keyed by model plus content hash, so a
re-run without `--fresh` is fully reproducible and costs nothing. That gives us
the reviewer story: clone, run, get the same numbers, spend zero dollars,
because the cache is committed alongside the code.

---

## 2. Three vocabularies, and why that is deliberate

This is the single most important thing to understand about the repo, and the
part most likely to be misread as a bug.

There are three distinct label vocabularies in play.

1. **Ground-truth taxonomy** (12 leaves). What the generator actually labeled
   against. Sealed inside `data/ground_truth.jsonl`. Read only by `eval/`.
2. **Vendor vocabulary**. The labels shipped with the incoming tickets. In this
   simulation the vendor vocabulary happens to use the same leaf names as the
   ground-truth taxonomy, because a real labeling vendor is working from a spec
   that is close to correct. It is public: it sits in the `tickets` table and
   any component may read it. What is sealed is not the vendor's vocabulary but
   which vendor labels are *wrong*.
3. **Induced taxonomy** (8 to 14 leaves). What the Claude agent proposes after
   reading unlabeled tickets, locked into `taxonomy.yaml`. This is the operating
   vocabulary. Everything downstream labels into it.

The induced taxonomy will not exactly match the ground-truth taxonomy. It might
merge `invoice_dispute` and `refund_request`, or split `how_to_question` by
surface area. **That mismatch is the realistic case, not a defect.** A frontier
data team almost never gets to induce a taxonomy that is isomorphic to the
one the customer secretly wanted. Handling the mismatch explicitly, through a
recorded and reviewable alignment, is the point.

Two alignment tables therefore exist, and they are deliberately kept apart.

| Table | Direction | Written by | Read by | May see ground truth |
|---|---|---|---|---|
| `data/vendor_alignment.yaml` | vendor label to induced leaf | `induce` stage | `qa`, `improve` | No |
| `eval/taxonomy_alignment.yaml` | induced leaf to ground-truth leaf | a human, by hand, at build time | `eval/` only | Yes, it is part of the harness |

The vendor alignment is derived at induction time from public vendor label
strings plus the induced taxonomy definitions, using one LLM call whose output
is printed for human inspection and locked by hash. It never touches ground
truth, so QA can use it without leaking.

The eval alignment is written by hand during the build, after reading the
induced taxonomy, and lives in `eval/` where the contract check permits ground
truth access. It is committed so a reviewer can audit exactly how induced
leaves were credited against sealed truth. Where an induced leaf legitimately
covers two ground-truth leaves, the alignment records the set, and a prediction
counts as correct if the ground-truth leaf is in that set. That policy is
restated in the results files so no number is quotable without its caveat.

### Failure mode this creates

If the alignment is written sloppily or generously, v1 and v2 accuracy both
inflate. Mitigation: the alignment is committed, it is small enough to read in
full, and `docs/02-RISK-REGISTER.md` names this as an accepted risk with the
reasoning that a hand alignment reviewed in the open is more honest than an
automatic one that hides its choices.

---

## 3. Corpus generator

Templated and combinatorial synthesis, no model calls, fixed seed 20260904.
Roughly 600 tickets across 12 leaves with a deliberately uneven class
distribution, because uniform class balance is the least realistic thing a
support corpus could do and it would hide exactly the frequent-class bias the
QA layer is supposed to surface.

Each leaf owns a set of subject templates, body templates, and slot fillers
(product surface names, error codes, plan names, regions). A ticket is a seeded
draw over templates plus fillers, so the corpus has real lexical variety
without any model in the loop.

### Seeded phenomena are planted on disjoint ticket sets

The five phenomena, 40 label errors, 18 duplicate pairs (36 tickets), 30
ambiguous, 25 PII, 12 multilingual, are assigned to mutually exclusive slot
pools. 143 of 600 tickets carry exactly one planted phenomenon each.

Reasoning: in the real world these overlap constantly, and a production system
must handle a mislabeled duplicate containing PII. Overlapping them here would
make every recall number ambiguous, because a missed detection could be blamed
on interference between detectors. Since the headline claim of this repo is
recall against seeded truth, the measurement has to be clean before it is
realistic. This is a stated, deliberate simplification and it is repeated in
`docs/02-RISK-REGISTER.md` and in the README Known Gaps.

### How each phenomenon is planted

- **Label errors**: `vendor_label` is set to a plausible confusion of the true
  leaf, drawn from a hand written confusion map, for example
  `login_auth_failure` mislabeled `permissions_rbac`. Never a random leaf,
  because a random wrong label is trivially catchable and would inflate recall.
- **Near duplicates**: the second member is generated by a paraphrase transform
  over the first, reordering clauses and substituting synonyms while keeping the
  incident identical. Both members keep the same true label.
- **Ambiguous**: generated from blended templates that legitimately satisfy two
  leaves. Ground truth stores `true_label` plus `acceptable_alt`.
- **PII**: synthetic strings injected at recorded spans. All are unmistakably
  fake: 555 phone numbers, `example.com` and `example.org` mail domains, and
  card sequences using reserved test prefixes.
- **Multilingual**: body text alternates English and Arabic sentences describing
  the same issue. Included because multilingual data is a live frontier-data
  problem and because a detector battery tuned only on English is a real and
  common failure.

### Sealed output

`data/ground_truth.jsonl`, one record per ticket, carrying the true leaf, the
acceptable alternate, and every seeded flag with its spans and pair ids. It is
committed, because it is fully synthetic and a reviewer needs to audit it.
`data/seed_manifest.json` carries the intended counts, and
`scripts/verify_seeds.py` asserts intent equals reality.

---

## 4. Taxonomy induction

Sample 120 tickets under the fixed seed. Subject plus a truncated body only. The
sampler never reads ground truth and never reads `vendor_label`, so the induced
taxonomy cannot inherit the vendor's mistakes or the generator's secret.

Two model steps:

1. **Propose**: one structured-output call over a 60 ticket subset returns the
   taxonomy, plus a rationale paragraph per domain.
2. **Map**: the full 120 ticket sample is mapped to proposed leaves in batches,
   with an explicit `unmappable` option, to measure coverage honestly.

Then deterministic validators run: domain count 2 to 4, leaf count 8 to 14, no
duplicate names, required fields present on every leaf, at least two boundary
examples per leaf, and unmappable rate below 5 percent. Any failure exits non
zero with a written report. The validators exist because a proposal that reads
fluently can still be structurally unusable, and only code catches that
reliably.

Output: `taxonomy.yaml`, `taxonomy_rationale.md`, and `data/vendor_alignment.yaml`.
The sha256 of `taxonomy.yaml` is written to the `artifacts` table at lock time.
Every later stage recomputes and compares it, and refuses to run on drift. A
taxonomy that changes after labeling begins silently invalidates every
downstream number, so the check is a hard failure rather than a warning.

### YAML without a YAML dependency

`taxonomy.yaml`, `data/vendor_alignment.yaml`, and `eval/taxonomy_alignment.yaml`
are read and written by `dql/yamlio.py`, a small emitter and parser for a
restricted YAML subset: nested mappings, sequences of scalars, sequences of
mappings, and scalar strings, ints, floats, booleans, and null.

Reasoning: PyYAML is not on the dependency allowlist, and the alternative to a
100 line parser is either adding a dependency or storing the taxonomy as JSON.
The taxonomy is the one artifact a human is expected to read and hand edit, so
it should be YAML. Every file the parser reads is either emitted by the matching
emitter or hand written in the same restricted subset, so the parser never has
to survive arbitrary YAML. A round trip self test guards it.

---

## 5. Labeling

Two annotators, on purpose. Agreement between two independent annotators is
what makes a kappa meaningful, and a deterministic second annotator is what
keeps the system useful when the API is unavailable.

**LLM labeler.** The locked taxonomy, with definitions, inclusion criteria,
exclusion criteria, and boundary examples, is rendered into the system prompt.
That is the RAG-lite grounding: the model is not asked to recall a taxonomy, it
is handed one and asked to apply it. Tickets go out in batches of 10 under a
structured output schema returning `label`, `confidence`, `rationale` capped at
25 words, and `abstain`. The taxonomy block is placed first and marked for
prompt caching, since it is identical across all 60 batches.

Abstention is a first class output rather than a low confidence score, because
"this ticket does not belong to any leaf" and "this ticket is probably leaf X"
are different claims, and collapsing them is how ambiguity gets lost.

**Heuristic labeler.** Deterministic scorer built by tokenizing each leaf's
definition, inclusion criteria, and boundary examples into a weighted term set,
with an inverse document frequency weight computed over the leaf descriptions
themselves so that terms shared by every leaf carry little signal. A ticket is
scored per leaf by weighted term overlap over subject plus body, subject terms
counted double. Highest score wins, and the margin over the runner up becomes
the heuristic's score.

It is built from the taxonomy text and nothing else. It never sees ground
truth, and it never sees the vendor label. Its accuracy is expected to be
clearly below the LLM's. That is the intended result: it establishes the floor
that the LLM has to beat, and it is the second annotator whose disagreement
with the LLM is diagnostic.

**Versioning.** Every labeling run gets a `run_id`. Rows append. Nothing is
overwritten or deleted, so any past run remains auditable.

**Caching and cost.** Cache key is sha256 over model id, prompt template
version, and the exact ticket payload. `--fresh` bypasses reads but still
writes. Every call records tokens in, tokens out, and USD into `costs`.
Cumulative spend prints after every batch, and a call that would cross
`DQL_COST_CAP_USD` is refused before it is sent, not after.

---

## 6. Quality framework

Everything is computed in induced-leaf space. Vendor labels are mapped through
`data/vendor_alignment.yaml` first, and a vendor label that maps to `null` is
excluded from agreement statistics and counted separately, because forcing an
unmappable label into a comparison would silently manufacture disagreement.

- **Agreement**: Cohen's kappa plus raw agreement for all three pairs, via
  `sklearn.metrics.cohen_kappa_score`. scikit-learn is used for metrics only.
- **Confusion**: per-class confusion matrix and per-class precision and recall
  for vendor labels with LLM labels as the reference. Every surface that prints
  this states that the LLM is a proxy reference, not truth. Without that
  sentence the table reads like a vendor scorecard, which it is not.
- **Suspected label errors**: LLM and heuristic agree with each other, both
  disagree with the mapped vendor label, and LLM confidence is at or above 0.70.
  Two independent annotators agreeing against the vendor is the strongest
  evidence available without opening ground truth. Ranked by confidence.
- **Duplicates**: rapidfuzz `token_set_ratio` over normalized subject plus body,
  threshold 90, transitively clustered. Token set ratio is chosen because the
  planted paraphrases reorder clauses, which destroys ordered ratios while
  leaving the token set nearly intact.
- **PII**: regex battery for emails, phone numbers in several formats, and card
  like digit sequences. Every hit is logged with its character span so the
  improvement pass can scrub precisely rather than rewriting whole bodies.
- **Ambiguity**: LLM abstained, or LLM confidence below 0.55, or all three
  annotators disagree three ways.

### Routing policy

Deterministic, and the ratio is the entire point of the system:

1. Every suspected label error.
2. Every ambiguity flag.
3. One representative per duplicate cluster, the canonical member.

PII hits are **not** routed to a human by default. The regex battery is high
precision on synthetic data and scrubbing is mechanical, so spending human
attention there would be waste. PII items are scrubbed automatically and a
sample is surfaced in the report for spot checking. A reviewer may still
confirm PII from the queue when an item arrives there for another reason.

Target queue size is 60 to 100 items, roughly 10 to 17 percent of the corpus.
QA prints the realized ratio and warns when it lands outside that band. If a
system routes everything to humans it has no value, and if it routes nothing it
has no safety. Landing in a narrow band on purpose, and reporting when it does
not, is the claim being made.

QA never opens `data/ground_truth.jsonl`. `scripts/check_contract.py` enforces
that by static check.

---

## 7. Human review

A `rich` CLI. One item per screen: the ticket, all three labels with confidence
and the LLM's rationale, and the reason the item was routed. Taxonomy
definitions on demand. Single keystroke adjudication. Elapsed seconds are
recorded per item, because a claim about human review cost is worthless without
a measured time.

Appends to `data/adjudications.jsonl`. Resumable: relaunching skips ticket ids
already present, so the pass can be done in several sittings.

---

## 8. Improvement pass

Deterministic, from v1 plus adjudications plus automated fixes:

- Adjudicated corrections applied.
- Duplicate clusters collapsed to the canonical member. Non canonical members
  are marked dropped rather than deleted, so the diff stays auditable.
- PII spans replaced with typed placeholders, `[EMAIL_1]`, `[PHONE_1]`,
  `[CARD_1]`, numbered per ticket.
- Ambiguous items dual labeled or quarantined per their adjudication.

Every change appends to `data/v1_to_v2_diff.jsonl` with field, before, after,
and reason. A dataset improvement you cannot diff is a dataset you cannot
defend, and the diff is what makes the v1 to v2 delta reviewable rather than
asserted.

---

## 9. Eval harness

`eval/harness.py` is the only *pipeline adjacent* module permitted to open
`data/ground_truth.jsonl`. `scripts/check_contract.py` greps the repo for that
path and fails on any reference outside `eval/`, with two carve outs:
`dql/generate.py` writes the file and is allowed to name it, and
`scripts/verify_seeds.py` reads it because proving the seeded counts are real is
the honesty mechanism the repo rests on. Neither is imported by any labeling,
QA, review, improvement, report, or export code path. The generator carve out is
further constrained: the contract check fails if the generator ever *reads* the
path it is permitted to write. Both carve outs are restated in
`BUILD_CONTRACT.md`, because an unexplained exception in a lint rule is how
contracts rot.

Scores both dataset versions: label accuracy through the eval alignment, seeded
error recall out of 40 and how many v2 corrected, duplicate recall out of 18 and
precision, PII recall out of 25 and precision and post scrub leak count, LLM and
heuristic accuracy, and abstain calibration.

Abstain calibration is reported as accuracy on abstained items versus accuracy
on non abstained items. Lower accuracy on abstained items is the passing
result: it means the model abstained on genuinely hard items rather than at
random.

Release gates fail the build loudly: v2 accuracy at least v1 plus 5 points,
seeded error recall at least 85 percent, PII leaks exactly 0, duplicate recall
at least 80 percent, total cost at most 3.00 USD.

If a gate fails or an eval exposes a bug anywhere upstream, the rule is to fix
it, re-run, and record the incident in `docs/06-CHANGE-LOG.md` and in the README
Known Gaps. Evals that never fail are evals nobody is reading.

---

## 10. Data model

SQLite at `data/dql.db`.

| Table | Purpose |
|---|---|
| `tickets` | the corpus, one row per ticket |
| `runs` | one row per stage execution, with model and cost |
| `labels` | one row per (run_id, ticket_id, source) |
| `qa_flags` | one row per flag, with type, detail, rank, routed_to_human |
| `adjudications` | mirror of the jsonl, for joins |
| `costs` | one row per API call |
| `artifacts` | name, sha256, locked_at, for the taxonomy hash lock |
| `llm_cache` | cache_key, model, response json, tokens, created_at |

---

## 11. Failure modes and responses

| Failure | Detection | Response |
|---|---|---|
| Taxonomy edited after labeling | sha256 compare against `artifacts` | hard stop, both hashes printed |
| Induction proposes an unusable taxonomy | deterministic validators | non zero exit, written report, no lock |
| Unmappable rate above 5 percent | mapping step | induction fails, taxonomy not locked |
| API key absent or invalid | client construction | clear message naming `.env`, no partial writes |
| Cost cap reached mid run | pre call check against `costs` | refuse the call, print spend, exit non zero |
| Cache miss storm on re-run | cost printed per batch | visible immediately, `--fresh` is opt in |
| Vendor label unmappable to induced leaf | alignment lookup returns null | excluded from agreement, counted and reported |
| Human queue outside 60 to 100 | QA prints ratio | warn, do not silently proceed |
| PII leak in export | harness scans export | gate failure, build fails |
| Ground truth read outside eval | `scripts/check_contract.py` | non zero exit |
