# 01 Requirements summary

Full, numbered, testable form in `specs/requirements.md`. This is the summary.

## What the system does

Takes a corpus of support tickets carrying imperfect incoming labels, induces a
taxonomy from the unlabeled text, labels everything with two independent
annotators, measures quality, routes only the items that need a human, applies
the resulting corrections, and proves whether the dataset actually improved.

## Capabilities

| Group | Requirement in one line |
|---|---|
| Req 1 | Generate 600 tickets deterministically with five seeded phenomena, sealed in a ground-truth file |
| Req 2 | Induce a taxonomy from unlabeled tickets, validate it deterministically, lock it by hash |
| Req 3 | Label with an LLM and a heuristic, cache every response, track and cap cost |
| Req 4 | Measure agreement, confusion, label errors, duplicates, PII, ambiguity, then route |
| Req 5 | Present exactly the routed items to a human, timed and resumable |
| Req 6 | Produce dataset v2 deterministically, with every change diffed |
| Req 7 | Score both versions against sealed truth and enforce release gates |
| Req 8 | Write the report, the splits, and the datasheet |
| Req 9 | Expose every stage as an idempotent CLI subcommand |
| Req 10 | Keep the repo honest: no secrets, no unlisted dependencies, no untraceable numbers |

## The requirements that shaped the design most

**Req 4.9 and Req 7.1, the sealed truth.** Ground truth is read by the eval
harness alone. Everything else is measured, and cannot see the answer key. This
single constraint is what makes every recall number in this repo mean something.

**Req 4.8, the routing ratio.** The human queue must land between 60 and 100
items, roughly 15 percent of the corpus. A system that routes everything has no
value and one that routes nothing has no safety, so the band is a first class
requirement rather than an outcome.

**Req 3.3, the response cache.** A re-run without `--fresh` performs zero API
calls and reproduces the prior run exactly. Verified by a killed run resuming
across 38 cached batches at 0.0000 USD, and by a taxonomy re-lock replaying 6
cached calls to the identical hash.

**Req 2.6, the hash lock.** A taxonomy edited after labeling begins silently
invalidates every downstream number, so drift is a hard stop rather than a
warning.
