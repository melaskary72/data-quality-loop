# 04 Acceptance criteria

State as of 2026-09-05, from the committed results files.

## Met

| Criterion | Evidence |
|---|---|
| 600 tickets, deterministic, no model calls in generation | two runs, identical sha256 of `data/ground_truth.jsonl` |
| Every seeded count matches what is planted | `scripts/verify_seeds.py`, 24 checks green |
| Taxonomy induced from unlabeled tickets and locked by hash | 14 leaves, 4 domains, `50934dec47d576fc` |
| Hash lock refuses drift | tampered with the file, stage refused, both hashes printed |
| Deterministic validators gate induction | rejected 5 domains, 17 leaves, duplicate names, a domain used as a leaf, a single-leaf domain |
| Unmappable rate under 5 percent | 3.3 percent, 4 of 120 |
| A cached re-run costs nothing | 38 of 60 batches replayed at 0.0000 USD; `induce --relock` replayed 6 calls to the identical hash |
| Human queue inside the 60 to 100 band | 94 items, 15.7 percent of the corpus |
| Human review actually performed | 100 adjudications, 44.1 minutes, median 16.0s per item |
| PII recall and precision | 25 of 25 tickets, 32 of 32 spans, precision 100 percent |
| Zero PII leaks in the export | export scans its own output and found 0 |
| Duplicate recall at or above 80 percent | 94.4 percent, 17 of 18 pairs |
| Total cost at or under 3.00 USD | 2.0343 USD |
| Ground truth read only by the harness and the seed verifier | `scripts/check_contract.py` green |
| No em dash anywhere in prose | `scripts/check_contract.py` green |
| `.env` never committed | gitignored from the first commit, `git ls-files` empty |

## Not met, and left failing

| Criterion | Required | Actual | Why it is not closed |
|---|---|---|---|
| Seeded label error recall | >= 85% | **80.0%** | Ceiling is 87.5 percent. 5 of the 40 planted errors were independently reproduced by the LLM labeler, and a detector built on annotator disagreement cannot catch an error both annotators share. Closing the last 5 points means tuning a threshold against the answer key. |
| v2 accuracy at least v1 plus 5 points | >= +5.0 | **+1.1** | v1 is already 89.7 percent, and 27 of its 62 errors are `data_export_request`, a ground-truth class the induced taxonomy has no leaf for. No amount of human review reaches them, and adding the leaf would mean designing the vocabulary from ground truth. |

Both are reported in the README rather than resolved by moving the gate. The
build exits non-zero.

## Explicitly out of scope

- Real customer data. Everything is synthetic and disposable.
- A production classifier. 600 tickets is a demonstration, not a training set.
- Languages beyond English and Arabic.
- Overlapping seeded phenomena. Each planted defect sits on its own ticket set
  so each detector can be scored without confounding.
