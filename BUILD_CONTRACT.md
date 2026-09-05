# BUILD CONTRACT

Things that must not drift. If a change would violate a clause here, the change
is wrong until this file is amended deliberately and the amendment is logged in
`docs/06-CHANGE-LOG.md`.

---

## C1. One canonical dataset

`data/dql.db` is the single source of truth for tickets, labels, flags,
adjudications, costs, and artifact hashes. No script invents a value that
another script contradicts. Any number that appears in `REPORT.md` or
`README.md` is read from the database or from a committed results file, never
recomputed by hand and never typed in from memory.

## C2. The taxonomy is the single vocabulary

Once induced and locked into `taxonomy.yaml`, the induced taxonomy is the
operating vocabulary. Every label produced by any labeler, stored in `labels`,
routed through QA, or written into an export, is a leaf of it.

The sha256 of `taxonomy.yaml` is recorded in the `artifacts` table at lock time.
Every stage after `induce` recomputes the hash and refuses to run if it differs,
printing both the locked and current values. Relabeling after a taxonomy edit
requires an explicit re-lock, never a silent one.

Two other vocabularies exist and are bridged by recorded alignments, never by
ad hoc code:

- vendor vocabulary to induced leaf, via `data/vendor_alignment.yaml`
- induced leaf to ground-truth leaf, via `eval/taxonomy_alignment.yaml`

## C3. Sealed ground truth

`data/ground_truth.jsonl` is read **only** by code under `eval/`, plus the one
verification tool named below.

Labeling, QA, review, improvement, report, and export components must never
import it, open it, or otherwise consult it, directly or transitively.

Two carve outs, both narrow and both explicit:

1. `dql/generate.py` **writes** the file, and is therefore permitted to name the
   path. It never reads it back, and the contract check enforces that: a read
   call on that path inside the generator is a violation.
2. `scripts/verify_seeds.py` **reads** the file. It is a verification tool, not
   a pipeline component: nothing in the labeling, QA, review, improvement,
   report, or export path imports it, and its output is an assertion result,
   never a label. It exists to prove the seeded counts are real, which is the
   honesty mechanism the whole repo rests on.

`scripts/check_contract.py` enforces this by scanning the repo for references to
the path outside `eval/`, allowing only the generator carve out, and exiting non
zero on any other reference.

## C4. Determinism

Deterministic, with fixed seeds, byte identical across runs:

- corpus generation
- the heuristic labeler
- duplicate detection
- the PII regex battery
- routing policy
- the improvement pass
- the eval harness

Nondeterministic, and the only such component: LLM calls in `induce` and
`label`. Every raw response is cached to SQLite keyed by model plus content
hash. A re-run without `--fresh` performs zero API calls and reproduces the
prior run exactly.

## C5. Cost

Hard cap: **3.00 USD total** across all development and final runs.

- Every API call writes tokens in, tokens out, and USD into the `costs` table.
- Cumulative spend prints after every batch.
- A call that would push cumulative spend past `DQL_COST_CAP_USD` is refused
  before it is sent.
- The model is read from the `DQL_MODEL` environment variable. No model id is
  hardcoded in a call site.
- The total printed in `README.md` is the sum of the `costs` table, not an
  estimate.

## C6. Dependency allowlist

Python 3.11 or newer, the standard library, and:

`anthropic`, `pydantic`, `rapidfuzz`, `scikit-learn` (metrics only), `rich`,
`python-dotenv`. Optional: `resend`, for the summary email only.

Anything else requires a written justification in `docs/06-CHANGE-LOG.md` before
it is added. Notably, YAML is handled by `dql/yamlio.py`, a restricted subset
emitter and parser, rather than by adding PyYAML.

## C7. Honesty

- Every number in `README.md` comes from a real run and traces to a committed
  results file. No projected, estimated, or illustrative metrics anywhere.
- If an eval exposes a bug, the bug is fixed, the run is repeated, and the
  incident is recorded in `docs/06-CHANGE-LOG.md` and in the README Known Gaps.
  Incidents are not quietly dropped.
- If a run has not happened yet, the README says so rather than leaving a
  plausible looking placeholder.

## C8. Secrets

`.env` is gitignored from the first commit and is never committed. Screenshots
are checked for a visible API key before they are added. `.env.example` carries
empty values and is the only env file in git.

## C9. Prose

No em dash appears in any prose file in this repository. Commas, colons, and
periods only. Checked in step 10.
