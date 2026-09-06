# 03 Architecture

Full reasoning in `specs/design.md`. This is the shape and the load-bearing
decisions.

## Stages

```
generate -> induce -> label -> qa -> review -> improve -> evaluate -> report -> export
```

Each is a CLI subcommand, each is idempotent, and all state lives in one SQLite
database. Stages communicate through the store and a small number of committed
artifacts, never through memory or a temporary file.

## The deterministic / judgement split

The central tension: demonstrate LLM judgement where judgement is needed, while
staying reproducible enough that a reviewer can re-run and get the same numbers.

**Deterministic, fixed seed, byte identical across runs:** corpus generation,
the heuristic labeler, duplicate detection, the PII battery, routing policy, the
improvement pass, the eval harness.

**LLM judgement, cached:** taxonomy induction, LLM labeling, vendor alignment.
Three places where a rule would be a poor substitute for reading the text.

Every response is cached by content hash, so a re-run without `--fresh` costs
nothing and reproduces exactly. Demonstrated twice: a killed labeling run
resumed across 38 cached batches at 0.0000 USD, and `induce --relock` replayed 6
cached calls to the byte-identical taxonomy hash.

## Three vocabularies

The part most likely to be misread as a bug.

1. **Ground-truth taxonomy**, 12 leaves. What the generator labeled against.
   Sealed. Read by `eval/` only.
2. **Vendor vocabulary.** The incoming labels. Public, and deliberately
   imperfect.
3. **Induced taxonomy**, 14 leaves across 4 domains. What the agent proposed
   from unlabeled tickets, locked in `taxonomy.yaml`. The operating vocabulary.

They do not match, and that is the realistic case. Two recorded alignments
bridge them, deliberately kept apart:

| Table | Direction | Written by | Read by | Sees truth |
|---|---|---|---|---|
| `data/vendor_alignment.yaml` | vendor to induced | the `induce` stage | `qa`, `improve` | no |
| `eval/taxonomy_alignment.yaml` | induced to ground truth | a human, by hand | `eval/` only | yes |

The vendor alignment is one-to-many: a coarse vendor label maps to every induced
leaf that covers it. It was one-to-one at first, which forced
`login_auth_failure` to null and excluded 106 of 600 tickets from every
agreement statistic.

## Two annotators, on purpose

Agreement between two independent annotators is what makes a kappa mean
anything, and a deterministic second annotator keeps the system working when the
API does not. The heuristic labeler is built from the taxonomy text alone and
scores 75.3 percent against the LLM's 89.7. That gap is the intended result: it
establishes the floor the LLM must beat, and its disagreement is diagnostic.

## Routing is the product

QA routes 94 of 600 tickets, 15.7 percent. Everything else is handled without
human attention. Three deterministic rules: every suspected label error, every
ambiguity flag, and one representative per *contested* duplicate cluster.
Uncontested clusters merge automatically, and PII is scrubbed automatically,
because neither needs judgement.

## Where the model calls are

`induce` uses a stronger model than `label`, set by `DQL_INDUCE_MODEL`. Seven
calls that set the vocabulary every later stage is bound to are a different job
from 60 batches of routine classification. The cheap model needed three repair
attempts at induction and still failed the coverage ceiling.

## Failure modes

| Failure | Detection | Response |
|---|---|---|
| Taxonomy edited after labeling | sha256 compare | hard stop, both hashes printed |
| Unusable taxonomy proposed | deterministic validators | bounded repair loop, then non-zero exit |
| Cost cap reached | pre-call check | refuse before sending |
| Hung API request | 90s timeout, 4 retries | fail fast, retry, per-batch timing printed |
| Ground truth read outside eval | `scripts/check_contract.py` | non-zero exit |
| Human queue outside 60 to 100 | QA prints the ratio | warn, never silently proceed |
| PII leak in export | export scans its own output | export refuses to write |
