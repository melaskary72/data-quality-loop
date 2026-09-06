# 07 AI usage

An honest account. The audience for this repo evaluates data and AI systems for
a living, so a vague note about "AI assistance" would be worth less than nothing.

## How this was built

Written with Claude Code (Claude Opus 5), driven by a human author who set the
brief, made the design calls, ran the human review pass, and decided what
shipped. Effectively all the code and prose was drafted by the model. That is
the interesting part, not something to bury.

## What the model wrote

Every Python module, the specs, the SDLC package, this file, and the README.
Also the corpus templates, including the Arabic fragments, which the author
reads natively and checked.

## What the human decided

The decisions that shaped the result were not the model's:

- The brief, the domain, and the architecture.
- **That failing release gates stay failing.** The model surfaced the option to
  re-specify them and was told no. Both remaining failures are reported with
  their structural cause instead.
- **That seeded-truth leakage is unacceptable**, including when it would have
  been convenient. The model proposed asserting that every planted duplicate
  cleared the QA threshold; that was rejected as answer-key tuning.
- The human review pass itself, 100 adjudications over 44.1 minutes.
- That the review tool needed a guard after the author key-mashed 22 decisions
  in five seconds, and that the guard should confirm rather than discard.
- That the vendor-beats-induced-taxonomy result goes in the README.

## How it was verified

Not by reading the code and nodding.

- **Deterministic checks.** `scripts/verify_seeds.py` runs 24 assertions over
  what the generator actually planted. `scripts/check_contract.py` enforces five
  contract clauses statically. Both must exit 0.
- **Sealed ground truth.** Every quality claim is scored by a harness that is
  the only component allowed to see the answer key, enforced by a lint rule.
- **Dated verification stamps.** Every completed task in `specs/tasks.md` names
  the command run and what was observed. Nothing is stamped that was not run.
- **Adversarial checks on the model's own output.** The taxonomy the model
  proposed was rejected five separate times by deterministic validators before
  one passed.

## Where the model got it wrong

Recorded in full in `06-CHANGE-LOG.md`. The pattern worth naming: the model's
first attempt was usually plausible and subtly wrong in a way that only a check
caught.

- Proposed a seed verifier that would have made duplicate recall 100 percent by
  construction.
- Resolved slot fillers per placeholder, producing tickets whose subject and
  body described different incidents.
- Fixed that in a way that collapsed corpus diversity, which the random-baseline
  check caught immediately.
- Built a one-to-one vendor alignment that silently excluded 106 of 600 tickets
  from every agreement statistic.
- While fixing that, compared a label string against a set of labels, marking
  all 47 duplicate clusters contested. The queue-size band caught it.
- Recorded API cost after parsing the response, so a call that returned garbage
  spent money that never reached the ledger.
- Claimed prompt caching was active when the API was silently ignoring it,
  because the prefix sat below the model's minimum. Only measurement found it.

Each was caught by a check that existed because the risk was written down first.
That is the actual argument this repo makes about building with AI: the model
writes quickly and confidently, and the value is in the rails around it.

## Reproducing

`make demo` runs everything except the human review. Cached responses make a
re-run free and byte-identical. An API key is only needed to regenerate the
cache from scratch.
