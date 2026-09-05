# 06 Change log

Dated entries. Bugs found during the build are recorded here with what was
wrong, how it was found, and what changed. Entries are not removed once the
problem is fixed: a build where nothing went wrong is a build where nobody was
looking.

Every entry below was found by a check in this repo, not by inspection after
the fact.

---

## 2026-09-04

### Scaffold, contract, specs

Spec-first per the repo standard: `specs/requirements.md`, `specs/design.md`,
and `specs/tasks.md` were written before any implementation module existed.

### Dependency decision: no PyYAML

The taxonomy is the one artifact a human is expected to read and hand edit, so
it should be YAML rather than JSON. PyYAML is not on the allowlist in
`BUILD_CONTRACT.md` C6.

Resolution: `dql/yamlio.py`, a restricted-subset emitter and parser in the
standard library, about 200 lines, covering nested mappings, sequences of
scalars, sequences of mappings, and scalar types. Every file it reads is either
written by its own emitter or hand written in the same subset. A round trip
self test (`python -m dql.yamlio`) guards it against a document containing
colons, hashes, dashes, escaped quotes, Arabic text, floats, booleans, nulls,
and empty collections.

No dependency was added.

### Incident: the seed verifier was leaking ground truth into the detector

`scripts/verify_seeds.py` originally asserted that every one of the 18 seeded
duplicate pairs scored at or above the QA duplicate threshold of 90. The first
run failed at 88.2 on DUP-13.

The tempting fix was to lower the QA threshold to 85 so all pairs cleared it.
That would have been seeded-truth leakage: tuning a detector using the answer
key, which forces duplicate recall to 100 percent by construction and makes the
headline metric meaningless.

The check was wrong, not the corpus. It now asserts that the pairs are genuine
paraphrases (ratio at or above 70) and that they separate from a random
baseline of unrelated tickets, and it reports how many clear the QA threshold
without gating on it. Whether the detector catches all of them is the eval's
question.

### Incident: incoherent tickets from independent slot resolution

Slot fillers were resolved once per placeholder rather than once per ticket, so
a ticket's subject asked about Salesforce while its body described SAP Concur.
Found by reading generator output rather than by a check, which is why a
coherence check now exists in the form of paired templates.

Fixed by giving each ticket one filler context, and by pairing subject and body
by template index so `subjects[i]` summarizes `bodies[i]`.

### Incident: the coherence fix destroyed corpus diversity

Pairing subject and body by index collapsed within-leaf lexical variety.
Unrelated tickets reached 93.5 token set ratio at the 99th percentile, above the
weakest planted pair at 89.0, which would have made duplicate precision
meaningless.

Caught by the random-baseline separation check added in the entry above, on its
first run after the change. Fixed by adding per-ticket detail sentences drawn
independently of the template. The 99th percentile fell to 77.5 against a
weakest pair of 90.9.

### Contract amendment: a second reader carve out

`scripts/verify_seeds.py` must read `data/ground_truth.jsonl` to prove the
seeded counts are real, which C3 forbade.

Amended deliberately rather than worked around. C3 now names two narrow carve
outs: `dql/generate.py` writes the file and may name the path, and
`scripts/verify_seeds.py` reads it as a verification tool that no pipeline
component imports. The generator carve out is further constrained: the contract
check fails if the generator ever reads the path it is permitted to write.
Recorded in `BUILD_CONTRACT.md` and `specs/design.md`.

---

## 2026-09-05

### Incident: the first induced taxonomy failed structural validation

The first proposal came back with 5 domains, 17 leaves, and a duplicated leaf
name, against bounds of 2 to 4 domains and 8 to 14 leaves.

Relaxing the bounds would have made them decorative. Instead the validator
output is now fed back to the agent as a bounded repair task with the
constraints restated and unchanged, up to 3 attempts. The attempt count is
recorded in the run note and in `taxonomy_rationale.md`.

### Incident: the mapping step assigned a domain name as a leaf

After a valid taxonomy was produced, the mapping step assigned a ticket to
`product_and_feature_issues`, which is a domain, not a leaf. Caught by the
coverage validator.

Listing the valid leaves in the prompt is a request. The `leaf` field is now an
enum in the structured output schema, which makes the invalid value
unrepresentable. The same was applied to the vendor alignment call.

### Incident: a single-leaf domain

A later proposal shipped `support_and_operations` holding one leaf. A domain
with one leaf is not a domain, it is a leaf with a heading, and it signals the
agent ran out of room against the leaf ceiling.

Added a deterministic validator: every domain holds at least 2 leaves. The rule
is also stated in the propose and repair prompts.

### Decision: a stronger model for taxonomy induction only

With `claude-haiku-4-5`, induction needed three repair attempts and still failed
the 5 percent unmappable ceiling at 7.5 percent, at one point emitting its
rationale as a domain name (schema-valid, semantically wrong).

Taxonomy induction is seven calls that set the vocabulary every later stage is
bound to, and it is genuinely hard reasoning. Labeling is 60 batches of routine
classification against a taxonomy handed to the model. Those are different jobs.

Added `DQL_INDUCE_MODEL`, defaulting to `claude-sonnet-5`, while `DQL_MODEL`
stays on `claude-haiku-4-5` for the bulk work. Induction then passed structural
validation with zero repairs at a 3.3 percent unmappable rate. The extra cost
is a few cents against a 3.00 USD cap.

### Incident: empty response from the induction model

The first call on the stronger model returned no text block at all and raised a
bare `ValueError` from the JSON parser. The model runs adaptive thinking by
default and the 8000 token ceiling was consumed before an answer was written.

Two fixes. Induction calls now stream with a 32000 token ceiling. And an empty
response now raises a diagnostic naming the model, the stop reason, the block
types returned, and the token ceiling, instead of a parse error about an empty
string.

### Bug: a failed call was not billed

Cost was recorded after the response was parsed, so a call that came back
unparseable spent money that never reached the `costs` table. That would
understate the total printed in the README, which is exactly the number this
repo promises is real.

Cost is now recorded before parsing. Found while fixing the entry above.

### Measured: prompt caching does not engage at this prompt size

The taxonomy block is identical across all 60 labeling batches, so it is marked
with `cache_control`. The API accepted it and reported zero cache tokens written
and zero read across a full 60 batch run.

Measured rather than assumed: the block is 2844 tokens, and a padded probe at
4398 tokens cached correctly (write on the first call, read on the second). The
minimum cacheable prefix for this model is 4096 tokens, so the taxonomy block
sits below it and `cache_control` is silently ignored.

The code is kept, because it is correct and costs nothing, and the labeler now
says plainly that the cache did not engage and why, rather than printing a
zero that reads like a bug. The prompt was not padded to cross the threshold:
inflating a prompt with filler to trigger caching trades prompt quality for a
saving this build does not need.

### Bug: a hung request stalled a labeling run for 35 minutes

A labeling run stopped producing cost rows for 35 minutes with no output. The
SDK default request timeout is 10 minutes, and with retries a single hung
request is indistinguishable from slow progress.

The client now sets an explicit 90 second timeout with 4 retries, and the
labeler prints per-batch timing so a stall is visible immediately. On the next
run two batches took 62 and 102 seconds, which is the timeout firing and the
retry succeeding, exactly the intended behaviour. A batch normally takes 9 to
18 seconds.

The killed run cost nothing to resume: 38 of 60 batches replayed from the
response cache at 0.0000 USD.

### Bug: the vendor column counted the wrong vocabulary

The label distribution table counted raw vendor labels against induced leaf
names, so every row read 0 except `feature_request`, the one string the two
vocabularies happen to share. Vendor labels are now mapped through
`data/vendor_alignment.yaml` before counting, and tickets whose vendor label no
induced leaf covers are reported on their own row.

### Incident: QA routed 209 items against a 60 to 100 target

The first QA run routed 209 of 600 tickets, 34.8 percent, far outside the design
band. Three causes, all fixed at the cause rather than by moving a threshold:

1. **Taxonomy granularity counted as vendor error.** 50 of 102 suspected label
   errors proposed a leaf that no vendor label maps onto, concentrated in three
   leaves the induced taxonomy split out. Those are not vendor mistakes, they
   are the induced taxonomy being finer than the vendor's spec, and routing them
   would ask a reviewer to make the same taxonomy decision fifty times. They are
   now recorded as a separate taxonomy-level finding and not routed.
2. **Duplicate detection driven by boilerplate.** Token set ratio treats a
   shared greeting as worth as much as a shared error code, producing 94
   clusters against 18 planted pairs, some with 11 members. A second stage was
   added: TF-IDF cosine over corpus-level IDF, which prices a term by how rare
   it is. Both thresholds were fixed a priori.
3. **Uncontested duplicates routed pointlessly.** A cluster whose members all
   carry the same label needs no human, the merge is mechanical. Only clusters
   whose members disagree are routed now.

Final queue: 65 items, 10.8 percent of the corpus, inside the band.

### Incident: the corpus contained more near duplicates than were planted

While fixing the above, the generator was found to produce accidental near
duplicates: with 12 leaves and 5 body variants, roughly ten tickets shared each
template shape and differed only in filler values. The detector was right to
flag them.

Fixed at the source by adding a leaf-specific context sentence drawn
independently of the body variant, which multiplies distinct shapes per leaf by
six.

A first attempt at the measurement side was wrong and was reverted: ground truth
briefly recorded every shape-sharing group as a `near_duplicate_group`, which
covered 461 of 600 tickets while the detector flagged 47 clusters. Sharing a
template shape does not make two tickets near duplicates. The field is now named
`shape_group` and is documented as a diagnostic for explaining where false
positives come from, not as a duplicate label. The only duplicates this corpus
asserts remain the 18 planted paraphrase pairs.

### Incident: the agent wrote em dashes into a repo artifact

`scripts/check_contract.py` failed C9 on `taxonomy.yaml`,
`taxonomy_rationale.md`, and `data/induction_report.md`: the agent's own
rationale prose contained an em dash.

Model-written prose is now normalized before it is stored, replacing em and en
dashes with commas. This is punctuation only: no word is added, removed, or
reordered, and `taxonomy_rationale.md` states the normalization where it claims
to keep the agent's reasoning. A stale failure report from an earlier attempt is
also deleted on a successful lock, so it cannot sit in the repo contradicting a
successful run.

The same check flagged `dql/qa.py` for naming the sealed ground truth path in a
docstring that says the module never opens it. The lint is deliberately blunt,
so the docstring was reworded rather than the rule weakened.
