# 02 Risk register

Real risks to this design, what was done, and where each one stands. Risks that
materialized are marked, because a register where nothing ever fired is a
register nobody was using.

---

## R1. Seeded-truth leakage

**Risk.** Detectors are tuned until they catch the planted defects, which turns
recall into a statement about this corpus rather than about the detector.

**Severity.** Fatal to the whole premise. Every headline number depends on it.

**Mitigation.** Thresholds are fixed a priori from the design document, never
swept. `scripts/check_contract.py` fails on any reference to the sealed file
outside `eval/` and the two documented carve outs.

**Fired: yes, twice.**

- `scripts/verify_seeds.py` originally asserted that every planted duplicate
  cleared the QA threshold, which would have forced duplicate recall to 100
  percent by construction. Replaced with a well-formedness check plus separation
  from a random baseline.
- The seeded-error recall gate could have been closed from 80 to 85 percent by
  nudging a confidence threshold. It was left failing instead.

**Residual.** The PII regex battery and the duplicate thresholds were written by
someone who knew what was planted. They were derived from format definitions and
standard practice, and the negative cases are tested, but this cannot be fully
eliminated in a repo where one person writes both sides.

---

## R2. LLM labeler bias toward frequent classes

**Risk.** The labeler over-assigns common leaves, inflating agreement on the
head of the distribution and hiding failure in the tail.

**Mitigation.** The corpus is deliberately imbalanced rather than uniform. QA
prints a per-class confusion matrix and per-class precision and recall, so tail
classes are visible rather than averaged away.

**Fired: partially.** The label distribution table shows the LLM assigning
`integration_api_failure` and `billing_discrepancy` well above their vendor
frequency. It is visible in the output rather than hidden.

---

## R3. Kappa inflation on easy items

**Risk.** Agreement looks strong because most items are trivial, and the number
gets quoted as if the annotators agree on the hard cases too.

**Mitigation.** Kappa is reported per annotator pair with its n, alongside
abstention calibration that specifically measures behaviour on hard items. The
heuristic labeler scores 20.0 percent on abstained items against 76.8 percent on
answered ones, which is direct evidence that the hard items are genuinely hard.

**Residual.** Kappa is computed on the subset where the vendor label maps to
exactly one induced leaf, n=391 of 600. That subset is not random: it excludes
the coarse vendor classes. Stated wherever the number appears.

---

## R4. Cost overrun

**Risk.** An agent loop or a re-run burns the budget.

**Mitigation.** Every call is cost-tracked before it is parsed. A call that
would cross `DQL_COST_CAP_USD` is refused before it is sent. Cumulative spend
prints after every batch, and responses are cached so re-runs are free.

**Fired: no.** Final spend 2.0343 USD against a 3.00 cap, across three full
labeling passes and several induction attempts.

**Note.** Cost was originally recorded after parsing, so a call that returned an
unparseable response spent money that never reached the ledger. Fixed.

---

## R5. Taxonomy drift after labeling

**Risk.** Someone edits `taxonomy.yaml`, and every label silently becomes
invalid against a vocabulary that no longer exists.

**Mitigation.** The sha256 is locked in the `artifacts` table and every later
stage refuses to run on a mismatch, printing both hashes.

**Fired: no**, but verified deliberately by tampering with the file and
confirming the refusal.

---

## R6. The alignment table is where accuracy can be quietly inflated

**Risk.** Two alignments bridge three vocabularies. A generous entry in either
inflates accuracy, and nobody would notice.

**Mitigation.** `eval/taxonomy_alignment.yaml` is hand written, committed, short
enough to read in full, and every multi-target entry carries its justification
in the file. `data_export_request` is declared unreachable rather than credited
to an adjacent leaf, which costs 27 tickets of accuracy.

**Residual, and this is the one to push on.** `access_configuration_request`
maps to two ground-truth leaves. If that entry is too generous, accuracy is
overstated. It is flagged in the alignment file itself as the entry a sceptical
reviewer should attack first.

---

## R7. Human review as theatre

**Risk.** The review pass happens but is not real, so the headline improvement
rests on nothing.

**Mitigation.** Every adjudication records elapsed seconds. 100 adjudications
over 44.1 minutes, median 16.0 seconds per item.

**Fired: yes.** During the first pass the reviewer held the accept key down and
recorded 22 identical decisions in five seconds, at 0.16 to 0.60 seconds each.
Nothing complained. Those entries were removed and the items re-reviewed, and a
fast-adjudication guard now interrupts on a sustained run of fast identical
decisions and asks for confirmation. It confirms rather than discards, because
silently deleting a reviewer's input has its own failure mode.

---

## R8. The synthetic corpus is too clean to be a real test

**Risk.** Template-generated tickets are easier than real ones, so every quality
number is optimistic.

**Mitigation.** Deliberate imbalance, mixed-language tickets, plausible rather
than random label confusions, and genuinely ambiguous items with two defensible
labels.

**Fired: yes, in the opposite direction.** The corpus was initially too
repetitive: tickets sharing a template shape read as near duplicates, and the
duplicate detector flagged 94 clusters against 18 planted pairs. Fixed by adding
compositional variety. Accidental near duplicates still exist and are reported
rather than hidden.

**Residual.** Acknowledged in the README and the datasheet. 600 synthetic
tickets cannot stand in for a real corpus.
