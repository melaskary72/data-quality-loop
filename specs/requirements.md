# Requirements: data-quality-loop

Numbered, testable requirements grouped by capability. Every requirement is
checkable by a command, a committed artifact, or an assertion in the eval
harness. Requirement ids are stable and referenced from `specs/tasks.md`,
`docs/04-ACCEPTANCE-CRITERIA.md`, and the eval harness.

Terminology used throughout:

- **Ground-truth taxonomy**: the hidden 12 leaf vocabulary the corpus generator
  labels against. Sealed. Read only by the eval harness.
- **Induced taxonomy**: the vocabulary a Claude agent proposes from unlabeled
  tickets, locked into `taxonomy.yaml`. This is the operating vocabulary.
- **Vendor label**: the intentionally imperfect incoming label shipped with each
  ticket, simulating a labeling vendor. Expressed in the vendor vocabulary,
  which is public.
- **Seeded phenomenon**: a defect planted on purpose by the generator and
  recorded in the sealed ground truth, so detection can be scored as recall.

---

## Req 1: Corpus generation

- **1.1** The generator produces exactly 600 tickets with fields `ticket_id`,
  `subject`, `body`, `channel`, `customer_tier`, `created_at`, `vendor_label`.
- **1.2** `channel` is one of `email`, `chat`, `portal`. `customer_tier` is one
  of `free`, `growth`, `enterprise`. Both are drawn from a fixed seed.
- **1.3** Generation performs zero LLM calls and completes offline.
- **1.4** Generation is deterministic: two runs at the same seed produce
  byte-identical `tickets` rows and a byte-identical `data/ground_truth.jsonl`.
- **1.5** The hidden ground-truth taxonomy has 4 domains and 12 leaves.
- **1.6** Exactly 40 tickets carry a seeded vendor label error, where
  `vendor_label` is a plausible confusion of the true leaf, not a random leaf.
- **1.7** Exactly 18 near-duplicate pairs exist, each a paraphrase of the same
  underlying incident. The 36 member tickets are recorded with a shared pair id
  and a designated canonical member.
- **1.8** Exactly 30 tickets are genuinely ambiguous, and ground truth records
  both a `true_label` and an `acceptable_alt` for each.
- **1.9** Exactly 25 tickets contain synthetic PII. Every planted PII string is
  recorded with its type and character span. All PII is obviously synthetic:
  555 phone numbers, `example.com` and `example.org` addresses, and test card
  prefixes reserved for testing.
- **1.10** Exactly 12 tickets mix English and Arabic in the body.
- **1.11** The five seeded phenomena in 1.6 through 1.10 are planted on mutually
  disjoint ticket sets, so each detector can be scored without confounding.
- **1.12** `data/seed_manifest.json` records the intended count of every seeded
  phenomenon plus the generator seed and the ground-truth taxonomy.
- **1.13** `scripts/verify_seeds.py` asserts that every manifest count matches
  what is actually present in `data/ground_truth.jsonl`, and exits non-zero on
  any mismatch.

## Req 2: Taxonomy induction

- **2.1** Induction samples 120 tickets using the fixed seed and never reads
  `data/ground_truth.jsonl` or the `vendor_label` column.
- **2.2** A Claude agent proposes a two level taxonomy. Every leaf carries
  `name`, `definition`, `inclusion_criteria`, `exclusion_criteria`, and at least
  two `boundary_examples`.
- **2.3** Deterministic validators reject a proposal that has fewer than 2 or
  more than 4 domains, fewer than 8 or more than 14 leaves, or any duplicate
  leaf name. A rejected proposal fails the stage with a written report and
  exits non-zero.
- **2.4** The agent maps all 120 sampled tickets to proposed leaves. If more
  than 5 percent are unmappable, induction fails with a report.
- **2.5** Induction writes `taxonomy.yaml` and `taxonomy_rationale.md`.
- **2.6** The sha256 of `taxonomy.yaml` is recorded in the `artifacts` table at
  lock time. Any later stage refuses to run if the file hash has changed, with
  a message naming the locked and current hashes.
- **2.7** Induction writes `data/vendor_alignment.yaml`, mapping each observed
  vendor label to an induced leaf or to `null`. This uses only public vendor
  label strings and the induced taxonomy, never ground truth.

## Req 3: Labeling

- **3.1** The LLM labeler emits, per ticket, `label`, `confidence` in [0,1],
  `rationale` of at most 25 words, and an `abstain` flag.
- **3.2** Every non abstaining LLM label is a leaf of the locked taxonomy.
- **3.3** Every LLM response is cached keyed by model plus a content hash. A
  second run without `--fresh` performs zero API calls and adds zero cost.
- **3.4** The heuristic labeler is deterministic, derives its rules only from
  the locked taxonomy text, never from ground truth, and always emits a label
  and a score for every ticket.
- **3.5** Labels are versioned by `run_id`. A new labeling run appends and never
  overwrites or deletes prior rows.
- **3.6** Every API call records tokens in, tokens out, and USD cost in the
  `costs` table. Cumulative spend prints after every batch.
- **3.7** The runner refuses to start a call that would push cumulative spend
  past `DQL_COST_CAP_USD`.

## Req 4: Quality framework

- **4.1** QA computes Cohen's kappa and raw agreement for all three annotator
  pairs: vendor vs LLM, vendor vs heuristic, LLM vs heuristic.
- **4.2** QA computes a per-class confusion matrix and per-class precision and
  recall for vendor labels, treating LLM labels as the reference. Every report
  states in text that this is a proxy, not truth.
- **4.3** Suspected label errors are items where the LLM and heuristic agree
  with each other, disagree with the mapped vendor label, and LLM confidence is
  at or above 0.70. Output is ranked by confidence.
- **4.4** Duplicate detection uses rapidfuzz token set ratio over subject plus
  body at a threshold of 90, and reports clusters with a canonical member.
- **4.5** The PII audit runs a regex battery for email addresses, phone numbers,
  and card like digit sequences, and logs every hit with its character span.
- **4.6** An item is flagged ambiguous if the LLM abstains, or LLM confidence is
  below 0.55, or all three annotators disagree three ways.
- **4.7** Routing to the human queue is deterministic and documented: suspected
  label errors, ambiguity flags, and one representative per duplicate cluster.
- **4.8** The human queue holds between 60 and 100 items, that is roughly 10 to
  17 percent of the corpus. QA prints the ratio and warns if out of band.
- **4.9** QA never reads `data/ground_truth.jsonl`.

## Req 5: Human review

- **5.1** The review CLI displays the ticket, all three labels with confidences
  and rationales, and taxonomy definitions on demand.
- **5.2** Available adjudications: accept LLM, accept vendor, pick another leaf,
  mark ambiguous-both, mark duplicate, mark PII confirmed, skip.
- **5.3** Every adjudication appends to `data/adjudications.jsonl` with the
  ticket id, decision, final label, reviewer, ISO timestamp, and elapsed
  seconds.
- **5.4** The queue is resumable: relaunching skips already adjudicated items.

## Req 6: Improvement pass

- **6.1** Dataset v2 is produced deterministically from v1 plus adjudications
  plus automated fixes.
- **6.2** Adjudicated label corrections are applied to v2.
- **6.3** Duplicate clusters are collapsed to their canonical member in v2.
- **6.4** Every PII span is replaced with a typed placeholder, for example
  `[EMAIL_1]`, `[PHONE_1]`, `[CARD_1]`.
- **6.5** Ambiguous items are dual labeled or quarantined according to the
  adjudication recorded for them.
- **6.6** Every v1 to v2 change is logged to `data/v1_to_v2_diff.jsonl` with the
  field changed, the before value, the after value, and the reason.

## Req 7: Eval harness

- **7.1** `eval/` is the only code permitted to read `data/ground_truth.jsonl`.
  `scripts/check_contract.py` enforces this and exits non-zero on violation.
- **7.2** The harness scores label accuracy for v1 and v2 against ground truth,
  mapping induced leaves to ground-truth leaves through the recorded alignment
  in `eval/taxonomy_alignment.yaml`.
- **7.3** A prediction matching `acceptable_alt` on an ambiguous item counts as
  correct. The policy is stated in the results file and in `REPORT.md`.
- **7.4** The harness reports seeded label error recall out of 40, and how many
  flagged errors v2 actually corrected.
- **7.5** The harness reports duplicate recall out of 18 pairs, and precision.
- **7.6** The harness reports PII recall out of 25, precision, and the post
  scrub leak count in the v2 export.
- **7.7** The harness reports LLM labeler accuracy, heuristic labeler accuracy,
  and abstain calibration, where accuracy on abstained items is expected to be
  materially lower than on non abstained items.
- **7.8** Release gates, each failing the build loudly when unmet: v2 label
  accuracy at least v1 plus 5 points, seeded error recall at least 85 percent,
  PII leaks in export exactly 0, duplicate recall at least 80 percent, total
  cost at most 3.00 USD.
- **7.9** `eval/results_v1.json` and `eval/results_v2.json` are written and
  committed.

## Req 8: Report and export

- **8.1** `REPORT.md` contains the headline table, v1 vs v2 deltas, confusion
  matrices rendered as markdown tables, cost accounting, and human review stats.
- **8.2** `python -m dql report --email` sends a severity gated summary through
  Resend, and is a no op with a clear message when credentials are absent.
- **8.3** Export writes `export/train.jsonl` and `export/eval.jsonl` in a
  stratified 85/15 split by label.
- **8.4** Export writes `export/DATASHEET.md` covering motivation, composition,
  collection, labeling process, quality metrics, limitations, and recommended
  uses.
- **8.5** No exported record contains an unscrubbed PII span.

## Req 9: CLI and operations

- **9.1** `python -m dql <cmd>` supports `generate`, `induce`, `label`, `qa`,
  `review`, `improve`, `evaluate`, `report`, `export`, `run-all`, `status`.
- **9.2** Every stage is idempotent: re-running without `--fresh` neither
  duplicates rows nor changes committed artifacts.
- **9.3** `status` prints pipeline state, per stage run history, and cumulative
  spend, and runs cleanly against an empty database.
- **9.4** `run-all` executes every stage except `review`, and stops with
  instructions when human review is required.
- **9.5** `make demo` wraps `run-all`.

## Req 10: Repo integrity

- **10.1** `.env` is gitignored in the first commit and never committed.
- **10.2** No em dash appears in any prose file in the repo.
- **10.3** Dependencies are limited to the allowlist in `BUILD_CONTRACT.md`.
  Any addition requires a written justification in `docs/06-CHANGE-LOG.md`.
- **10.4** Every number in `README.md` traces to a committed results file.
- **10.5** `scripts/check_contract.py` and `scripts/verify_seeds.py` both exit 0
  in the final commit.
