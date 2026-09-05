# Tasks: data-quality-loop

Implementation plan as a checkbox list, mirroring Section 9 of the build brief.

**Stamping rule.** A task is checked only after it is verified. Under every
checked task sits an italic verification stamp naming what was verified, the
command run, what was observed, and the date. Unverified work stays unchecked.
Nothing is stamped that was not actually run.

Dates are ISO, in the builder's local timezone.

---

## Step 1: Scaffold, contract, specs, store, cost tracking

- [x] 1.1 Create repo layout, `.gitignore` with `.env` in it from the first commit, `.env.example`, `.env`
  _Verified: repo layout created, `git check-ignore -v .env` reports `.gitignore:2:.env`, .env untracked and present only as .env.example in git, 2026-09-04_
- [x] 1.2 Pin the virtualenv to Python 3.13 with uv, install the allowlisted dependencies only
  _Verified: `uv venv --python 3.13` created .venv on CPython 3.13.14, `uv pip install` brought in only the six allowlisted packages, all six import cleanly, 2026-09-04_
- [x] 1.3 Write `specs/requirements.md` before any implementation
  _Verified: specs/requirements.md written before any dql module existed, 10 capability groups, every requirement stated as a checkable condition, 2026-09-04_
- [x] 1.4 Write `specs/design.md` before any implementation
  _Verified: specs/design.md written before any dql module existed, covers the three-vocabulary split, disjoint seeding rationale, routing policy, and a failure-mode table, 2026-09-04_
- [x] 1.5 Write `specs/tasks.md` before any implementation
  _Verified: specs/tasks.md written before any dql module existed, mirrors Section 9 of the brief as 11 steps, 2026-09-04_
- [x] 1.6 Write `BUILD_CONTRACT.md`
  _Verified: BUILD_CONTRACT.md written with clauses C1 through C9, each one machine checkable or explicitly human checked, 2026-09-04_
- [x] 1.7 Implement `dql/store.py`: schema, migrations, run and cost recording
  _Verified: dql/store.py schema applied against an empty file, `store.session()` created all 9 tables, ticket_count 0 and total_cost 0.0 returned without error, 2026-09-04_
- [x] 1.8 Implement `dql/yamlio.py`: restricted subset YAML emitter and parser
  _Verified: dql/yamlio.py round trips a document containing colons, hashes, dashes, escaped quotes, Arabic text, floats, bools, nulls, empty lists and nested mappings, `python -m dql.yamlio` prints 'yamlio selftest: ok', 2026-09-04_
- [x] 1.9 Implement `dql/llm.py`: client, cache, cost tracking, cost cap enforcement
  _Verified: dql/llm.py resolves DQL_MODEL to claude-haiku-4-5 and the cap to 3.0 from .env, usd_for(3000 in, 600 out) returns 0.006 USD against the published haiku rate, 2026-09-04_
- [x] 1.10 Implement `dql/__main__.py`: CLI surface and `status`
  _Verified: dql/__main__.py exposes all 11 subcommands, `python -m dql status` renders state, taxonomy lock status, and spend, 2026-09-04_
- [x] 1.11 Implement `scripts/check_contract.py`
  _Verified: scripts/check_contract.py implemented for clauses C3, C5, C6, C8, C9, 2026-09-04_
- [x] 1.12 Verify `python -m dql status` runs clean against an empty database
  _Verified: `python -m dql status` on an empty database printed 0 tickets, taxonomy not locked, 0.0000 USD of 3.00 cap, all 9 stages 'not run', exit code 0, 2026-09-04_

## Step 2: Corpus generator and seed verification

- [x] 2.1 Implement the hidden ground-truth taxonomy, 4 domains and 12 leaves
  _Verified: dql/templates.py defines 4 domains and 12 leaves, CLASS_WEIGHTS sums to 1.0, deliberately uneven so frequent-class bias stays visible, 2026-09-04_
- [x] 2.2 Implement templated and combinatorial ticket synthesis, fixed seed, no LLM calls
  _Verified: `python -m dql generate` produced 600 tickets with 0 LLM calls and 0.0000 USD, subject and body paired by template index and sharing one filler context per ticket, 2026-09-04_
- [x] 2.3 Plant 40 vendor label errors from a plausible confusion map
  _Verified: 40 seeded errors present, all drawn from CONFUSION_MAP, 0 seeded errors carry the correct vendor label, and 0 unseeded tickets disagree with truth, 2026-09-04_
- [x] 2.4 Plant 18 near-duplicate pairs by paraphrase transform
  _Verified: 18 pairs, 36 members, 18 canonical, token_set_ratio 90.9 to 100.0, and unrelated tickets sit at 77.5 at the 99th percentile so the pairs separate cleanly, 2026-09-04_
- [x] 2.5 Plant 30 ambiguous tickets with `true_label` plus `acceptable_alt`
  _Verified: 30 ambiguous tickets, every one carries an acceptable_alt distinct from its primary, and 0 unambiguous tickets carry a stray alternate, 2026-09-04_
- [x] 2.6 Plant 25 tickets with synthetic PII, spans recorded
  _Verified: 25 PII tickets, every recorded span re-reads as the exact planted substring in the final body, 0 spans on clean tickets, 2026-09-04_
- [x] 2.7 Plant 12 mixed English and Arabic tickets
  _Verified: 12 multilingual tickets all contain Arabic script, and 0 unmarked tickets contain any Arabic characters, 2026-09-04_
- [x] 2.8 Write `data/ground_truth.jsonl` and `data/seed_manifest.json`
  _Verified: data/ground_truth.jsonl and data/seed_manifest.json written by the generator, which never reads either back, enforced by scripts/check_contract.py, 2026-09-04_
- [x] 2.9 Implement `scripts/verify_seeds.py`
  _Verified: scripts/verify_seeds.py implemented with 24 checks covering counts, well-formedness, detectability, and disjointness, 2026-09-04_
- [x] 2.10 Verify all seeded counts match the manifest
  _Verified: `python scripts/verify_seeds.py` exits 0 with all 24 checks green, every manifest count matching what is planted, 2026-09-04_
- [x] 2.11 Verify generation is deterministic across two runs
  _Verified: `python -m dql generate` run twice, sha256 of data/ground_truth.jsonl identical at 12aba13a6cee, ticket rows identical, 2026-09-04_

## Step 3: Taxonomy induction, validators, hash lock

- [x] 3.1 Implement the 120 ticket sampler that reads neither ground truth nor vendor labels
  _Verified: sample_tickets selects 120 tickets under the generator seed, projecting only ticket_id, subject and truncated body, so neither vendor_label nor ground truth reaches the prompt, 2026-09-05_
- [x] 3.2 Implement the propose call with structured output
  _Verified: propose call returned a taxonomy under TAXONOMY_SCHEMA structured output, 0.0303 USD for the call, 2026-09-05_
- [x] 3.3 Implement the mapping call and the unmappable rate measurement
  _Verified: mapping ran 4 batches of 30 over the full 120 sample, unmappable rate 1.7 percent against a 5 percent ceiling, 2026-09-05_
- [x] 3.4 Implement deterministic validators
  _Verified: validators rejected the first proposal for 5 domains, 17 leaves and a duplicate leaf name, then rejected the first mapping for using a domain name as a leaf, both without relaxing any bound, 2026-09-05_
- [x] 3.5 Emit `taxonomy.yaml` and `taxonomy_rationale.md`
  _Verified: taxonomy.yaml written with 12 leaves across 4 domains and re-read by yamlio, taxonomy_rationale.md carries the agent's own reasoning and the repair-attempt count, 2026-09-05_
- [x] 3.6 Emit `data/vendor_alignment.yaml`
  _Verified: data/vendor_alignment.yaml written, all 12 vendor labels mapped, invoice_dispute and refund_request both collapsing onto the merged billing_dispute_or_refund leaf, 2026-09-05_
- [x] 3.7 Record the taxonomy sha256 in `artifacts` and enforce the lock downstream
  _Verified: sha256 c0181023b42f recorded in the artifacts table at lock time, python -m dql status reports 'locked c0181023b42f intact', 2026-09-05_
- [x] 3.8 Verify the hash lock refuses to run on a drifted taxonomy
  _Verified: appended a comment to taxonomy.yaml, status flipped to DRIFTED and assert_taxonomy_locked raised TaxonomyDrift printing both hashes, file restored and the lock reads intact again, 2026-09-05_

## Step 4: Labelers

- [ ] 4.1 Implement the heuristic labeler from taxonomy text only
- [ ] 4.2 Implement the LLM labeler with structured output and batching
- [ ] 4.3 Implement response caching keyed by model and content hash
- [ ] 4.4 Verify a second labeling run hits cache and costs 0.00 USD

## Step 5: Quality framework

- [ ] 5.1 Implement vendor label mapping through `data/vendor_alignment.yaml`
- [ ] 5.2 Implement Cohen's kappa and raw agreement for all three pairs
- [ ] 5.3 Implement per-class confusion and per-class precision and recall
- [ ] 5.4 Implement suspected label error detection and ranking
- [ ] 5.5 Implement duplicate detection and clustering
- [ ] 5.6 Implement the PII regex battery with spans
- [ ] 5.7 Implement ambiguity flags
- [ ] 5.8 Implement the deterministic routing policy
- [ ] 5.9 Verify flag counts and that the human queue lands in the 60 to 100 band

## Step 6: Human review

- [ ] 6.1 Implement the `rich` review CLI with all adjudication actions
- [ ] 6.2 Implement resumable queue state and `data/adjudications.jsonl`
- [ ] 6.3 Human review pass performed for real by Mohamed
- [ ] 6.4 Verify adjudications are recorded with elapsed seconds

## Step 7: Improvement pass

- [ ] 7.1 Apply adjudicated corrections
- [ ] 7.2 Collapse duplicate clusters to canonical members
- [ ] 7.3 Scrub PII spans with typed placeholders
- [ ] 7.4 Handle ambiguous items per adjudication
- [ ] 7.5 Write `data/v1_to_v2_diff.jsonl`
- [ ] 7.6 Verify the diff accounts for every v1 to v2 change

## Step 8: Eval harness, alignment table, release gates

- [ ] 8.1 Hand write `eval/taxonomy_alignment.yaml` after reading the induced taxonomy
- [ ] 8.2 Implement label accuracy scoring with the acceptable-alt policy
- [ ] 8.3 Implement seeded error recall and v2 correction rate
- [ ] 8.4 Implement duplicate recall and precision
- [ ] 8.5 Implement PII recall, precision, and export leak count
- [ ] 8.6 Implement labeler accuracy and abstain calibration
- [ ] 8.7 Implement release gates that fail the build loudly
- [ ] 8.8 Write `eval/results_v1.json` and `eval/results_v2.json`
- [ ] 8.9 Run the full eval, fix anything it exposes, re-run, log the incident

## Step 9: Report, export, datasheet

- [ ] 9.1 Implement `REPORT.md` generation
- [ ] 9.2 Implement the optional Resend summary email
- [ ] 9.3 Implement the stratified 85/15 export split
- [ ] 9.4 Write `export/DATASHEET.md`
- [ ] 9.5 Verify no exported record contains unscrubbed PII

## Step 10: Presentation and final checks

- [ ] 10.1 Capture screenshots of real runs into `assets/screenshots/`
- [ ] 10.2 Export `assets/architecture.svg`
- [ ] 10.3 Write the docs package, `00-INDEX.md` through `07-AI-USAGE.md`
- [ ] 10.4 Write `README.md` last, every number traced to a committed results file
- [ ] 10.5 Verify `scripts/check_contract.py` and `scripts/verify_seeds.py` both exit 0
- [ ] 10.6 Verify no em dash appears in any prose file

## Step 11: Publish

- [ ] 11.1 Human verification pass by Mohamed on screenshots and README numbers
- [ ] 11.2 Push to GitHub
- [ ] 11.3 Verify screenshots render on main from a logged out browser
- [ ] 11.4 Set repo description and topics
