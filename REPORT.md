# REPORT: data-quality-loop

Generated from `eval/results_v1.json` and `eval/results_v2.json`. Every number below is read from those files or from the pipeline's
own store. None of it is typed in by hand.

## Headline

| Metric | v1 (pipeline output) | v2 (after the loop) |
|---|---|---|
| Label accuracy vs ground truth | 89.7% | 90.7% (+1.1 pts) |
| Seeded label errors caught (of 40) | 32 (80.0%) | 31 corrected |
| Duplicate pairs found (of 18) | 17 (94.4%) | 17 merged |
| PII items caught (of 25) / leaks in export | 25 (100.0%) | 0 leaks |
| Items routed to human / total | 94 / 600 (15.7%) | |
| Human review time | 44.1 minutes over 100 items | |
| Total API cost | 2.0343 USD | |

## Accuracy detail

| Measure | Value |
|---|---|
| Vendor labels as delivered | 93.3% |
| LLM labeler | 89.7% |
| Heuristic labeler | 75.3% |
| Dataset v2 | 90.7% |

The induced taxonomy has no counterpart for these ground-truth leaves, so every ticket in them is a guaranteed error. They are 27 of 600 tickets, and they are counted as errors rather than excluded.

Acceptable-alternate policy: A prediction matching the documented acceptable_alt on a planted ambiguous ticket counts as correct.

Predictions are in the induced vocabulary and truth is in the generator's
hidden vocabulary, so every comparison passes through the hand written
alignment in `eval/taxonomy_alignment.yaml`. That file is committed and
short enough to read in full, which is the point.

## Quality flags

| Flag | Tickets |
|---|---|
| duplicate | 97 |
| suspected_label_error | 64 |
| ambiguity | 31 |
| pii | 25 |

## Human review

- items routed: 94 of 600 (15.7% of the corpus)
- items adjudicated: 100
- total review time: 44.1 minutes
- median seconds per item: 16.0

| Decision | Count |
|---|---|
| accept_llm | 72 |
| accept_vendor | 16 |
| pii_confirmed | 4 |
| accept_heuristic | 3 |
| other_leaf | 3 |
| ambiguous_both | 2 |

## Abstention calibration

- abstentions: 15
- share of abstentions that are planted-ambiguous tickets: 6.7%, against a base rate of 5.0%
- heuristic accuracy on abstained items: 20.0%
- heuristic accuracy on answered items: 76.8%

Abstention is calibrated when the deterministic labeler scores materially lower on abstained items than on answered ones, which means the model abstained on genuinely hard tickets rather than at random.

## Cost accounting

| Stage | Calls | Tokens in | Tokens out | USD |
|---|---|---|---|---|
| label | 180 | 757,092 | 106,492 | 1.2896 |
| induce | 33 | 193,154 | 72,933 | 0.7447 |
| **total** | | | | **2.0343** |

Generation, the heuristic labeler, QA, the improvement pass, the eval
harness, and export make no model calls at all. Only induction and LLM
labeling spend anything.

## Duplicate precision, stated honestly

Precision is scored against the 18 planted pairs only. 30 of 30 unmatched clusters consist entirely of tickets built from one template shape, so they are real textual near duplicates that were never planted. The denominator is stated rather than adjusted.
