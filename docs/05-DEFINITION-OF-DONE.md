# 05 Definition of done

- [x] All release gates evaluated on committed eval results
  _3 of 5 pass. 2 fail and are left failing with a structural explanation, by explicit decision._
- [x] Every number in the README traceable to a committed results file
  _`eval/results_v1.json`, `eval/results_v2.json`, `REPORT.md`, or the pipeline store._
- [x] Human review pass actually performed and `adjudications.jsonl` committed
  _100 adjudications, 44.1 minutes, median 16.0 seconds per item._
- [x] `specs/tasks.md` fully stamped, change log honest, Known Gaps dated
  _Every completed task carries a dated verification stamp naming the command run and what was observed._
- [x] `check_contract.py` and `verify_seeds.py` both green
- [x] Total spend printed in the README and at or under 3.00 USD
  _2.0343 USD._
- [x] Screenshots verified rendering on GitHub main from a logged-out browser
  _All 9 screenshots and architecture.svg return HTTP 200 with correct content types over unauthenticated raw.githubusercontent.com, and all 18 internal README links resolve to files present on main._
- [x] Repo description and topics set
  _training-data, data-quality, llm-agents, evals, human-in-the-loop, taxonomy._

## The standard applied here

Done means a reviewer can clone the repo, read the contract, run the pipeline,
and get the same numbers. It does not mean every gate is green. Two gates fail,
the failures are explained in one sentence each, and the build exits non-zero.
A green board produced by moving a threshold would be worth less than this.
