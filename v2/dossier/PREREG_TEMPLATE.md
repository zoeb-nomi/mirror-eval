# PREREGISTRATION — mirror-eval v2 "Prompt B"

Fill this in and date/sign it **before** running the real (non-pilot,
non-`--mock`) wave. The point of pre-registering is that the hypotheses are
locked before the data that could confirm or deny them exists.

Wave label (run_id prefix): ______________________
Date filled in: ______________________
Date the real run actually starts: ______________________
Signed off by: Zoeb Nomi

---

## H1 — Owned-surface visibility

**Hypothesis:**
______________________________________________________________________
(e.g. "In the `full` condition, zoebnomi.com or github.com/zoeb-nomi is
fetched in at least X% of lookup-task calls on both stacks.")

**Metric that will decide it:** `owned_surface_hit_rate`
**Threshold / direction stated in advance:** ______________________

---

## H2 — Structured claims vs prose

**Hypothesis:**
______________________________________________________________________
(e.g. "Answers that end with the structured claims block cite a source URL
for at least X% of their claims on both stacks, versus the free-prose answer
text, where a claim's source can only be inferred." — i.e. does asking for
structured, labeled claims change how well-sourced the claims are, and how
often the block parses at all.)

**Metric that will decide it:** `parse_failure_rate`, `provenance_matrix`
(claims with a source domain vs `(no source)`)
**Threshold / direction stated in advance:** ______________________

---

## H3 — Leave-one-surface-out degrades findability

**Hypothesis:**
______________________________________________________________________
(e.g. "owned_surface_hit_rate in `blocked_owned` drops by at least X
percentage points vs. `full`, on the Anthropic stack." — note the OpenAI
stack's blocked_domains is a weaker guarantee; see README.md limitations.)

**Metric that will decide it:** `owned_surface_hit_rate`,
`blocked_domain_leak` rate on the OpenAI stack
**Threshold / direction stated in advance:** ______________________

---

## H4 — Sourcing-task discoverability

**Hypothesis:**
______________________________________________________________________
(e.g. "When an engine is asked to source candidates for the exact role
Zoeb targets, his own surfaces appear in fetched_urls in at least X% of
sourcing-task calls in the `full` condition.")

**Metric that will decide it:** `owned_surface_hit_rate` on `task=sourcing`
**Threshold / direction stated in advance:** ______________________

---

## H5 — Claim provenance quality

**Hypothesis:**
______________________________________________________________________
(e.g. "Fewer than X% of claims about Zoeb are labeled `verified` with a
source domain that is NOT an owned surface — i.e. most 'verified' claims
are actually self-sourced, not independently confirmed.")

**Metric that will decide it:** `provenance_matrix`, `field_accuracy`
(truth fields only)
**Threshold / direction stated in advance:** ______________________

---

## Fixed before the run (do not change after seeing data)

- Stacks: ______________________
- Task(s): ______________________
- Conditions: ______________________
- Reps: ______________________ (sized from the pilot's per-call cost and
  variance, not picked arbitrarily)
- Battery: `prompts/lookup.jsonl` / `prompts/sourcing.jsonl` sha256 at run
  time: ______________________ (copy from the run's `manifest.json` once
  run.py writes one, or from `prompts/build_report.md`'s bank snapshot hash)

## What would change the conclusion

One sentence per hypothesis, written now, about what result would make you
say "this hypothesis was wrong," not just "inconclusive":

- H1: ______________________________________________________________
- H2: ______________________________________________________________
- H3: ______________________________________________________________
- H4: ______________________________________________________________
- H5: ______________________________________________________________
