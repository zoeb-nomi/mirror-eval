# mirror-eval

**An evaluation harness for what AI search engines say about a person — and whether you can change it.**

Ask ChatGPT, Claude, Perplexity, and Gemini the same questions about someone, repeatedly.
Tag every answer against a canon of verified facts using a claim-failure taxonomy. Trace
each failure to the web surface that taught the engine to say it. Fix that surface.
Re-run. Measure whether the change exceeds the noise floor.

It is a normal eval loop pointed at an unusual target, and it is built to the same method
as [zoeb-nomi/crosssource](https://github.com/zoeb-nomi/crosssource): explicit ground
truth, a failure taxonomy rather than a bare score, an LLM judge whose agreement with a
human is measured rather than assumed, and a limitations section that says what the
numbers cannot support.

---

## Status — 2026-08-06

**Wave 1 is complete: 332 probes across ChatGPT, Claude, Perplexity and Gemini.**
Results are not published yet, and the reason is the honest one.

The harness runs two judges from different model families specifically so their
agreement can be measured rather than assumed. It came back at **8%** on the first
pass. Tightening the taxonomy — moving two mechanically-checkable categories into
code, adding precedence rules and worked examples — lifted it to **17%**. That is
still far below anything that would justify publishing failure *rates*.

So the numbers stay unpublished until a blind human-labelled sample can adjudicate
them, and the interpretive categories will be reported as **bands** (floor = both
judges agree, ceiling = either judge fires) rather than as point estimates.

Two things are already solid and judge-independent, because they are counted rather
than scored:

- the **citation trail** — which surfaces each engine actually read
- **`poisoned_citation`**, which moved from a 77%-agreement judgement to a 100%-exact
  computation the moment it stopped being asked of a language model

[`PREDICTIONS.md`](PREDICTIONS.md) was committed **before** wave 1 ran. It will be
scored publicly, misses first — two of nine predictions held, and the misses changed
the roadmap more than the hits did.

---

## Why

Buyers, recruiters, and counterparties increasingly ask an AI engine about you before they
ask you. That answer is assembled from whatever the crawlers found — often a dead
portfolio, a scraped recruiting aggregator, or a job you left two years ago. You cannot see
that answer from inside your own account, you cannot A/B it, and nobody sends you a report.

Existing GEO/AEO tooling mostly measures *whether you are mentioned*. That is the easy
half. The hard half is whether the mention is **true**, **current**, and **sourced to
something you control** — which is a scoring problem, which makes it an eval problem.

## What it measures, and what it doesn't

Three different mechanisms get conflated in this space. Only one is testable here, and
saying so is part of the method:

| Channel | Mechanism | In scope? |
|---|---|---|
| A person asks a consumer AI engine about you | live retrieval + model | **Yes — this is the instrument.** |
| Training-data capture ("what gets scraped into the weights") | pretraining corpus | **Partially.** `knowledge` mode reads what the last scrape captured. Fixes made today land in a future model and cannot be measured now. |
| AI sourcing tools (SeekOut, hireEZ, recruiter-database enrichment) | aggregator scrapes | **No.** Requires paid seats. A real blind spot; fixed at the source, verified only by proxy. |
| Classic ATS (Greenhouse, Lever, Workday) | parses the résumé you upload | **Not applicable.** No web lookup occurs. |

**Provider-native APIs only.** Aggregators like OpenRouter or Bedrock are cheaper but
would measure the wrong thing: bolting a third-party search layer onto a model produces a
system nobody actually uses. The provider's own retrieval stack *is* the object under test.

## The design decisions worth arguing with

**The taxonomy leads, the score follows.** `stale_employer: 7` tells you what to do on
Monday. `1.4 / 3` does not. Categories carry a `fix_class` — `surface` means a live page is
teaching it, `model` means the engine invented it and no amount of site work will help.

**Two judges from different model families.** One of the four systems under test is Claude.
A Claude-only judge would be grading a contestant it shares a family with, and LLM judge
self-preference is documented (Zheng et al., 2023). Claude and Gemini judge every answer
independently and cross-family agreement is reported per engine.

**Two agreeing models are not evidence.** They are evidence of a shared prior. `judge_check.py`
exports a blind, stratified sample — every judge disagreement, every high-impact tag, plus
a control group of answers both judges called clean — for a human to label without seeing
the verdicts. Percent agreement against that sample is the number that decides whether any
other number here can be cited.

**Repetition over breadth.** These engines are stochastic. A single probe per cell cannot
distinguish "the fix worked" from "we resampled," which makes a single-shot before/after
chart worthless no matter how good it looks. Every cell is run *k* times and
`analyze.py` reports the minimum difference the design can actually resolve.

**Battery size is measured, not asserted.** `analyze.py` decomposes between-prompt against
within-prompt variance. If they are comparable, more prompts buy nothing and reps buy
everything. If between greatly exceeds within, answers are phrasing-sensitive and the
battery is undersampled — which is itself a finding worth publishing.

**Prompts are derived from facets, not topics.** Scaffolding (bare name → name + specific
claim), prior (neutral vs. skeptical), decision (identify / screen / verify), output shape.
A prompt that does not own a facet no other prompt owns does not belong in the battery.

**No silent truncation.** Where the harness caps anything — sample size, retries — it says
what was dropped. A quietly truncated sample reads as full coverage.

## Run it on yourself

```bash
git clone https://github.com/zoeb-nomi/mirror-eval && cd mirror-eval
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add whichever keys you have; it runs on any subset

$EDITOR canon.yaml          # describe your subject — the only file with facts in it

.venv/bin/python run_eval.py --preflight                  # verify keys, models, network
.venv/bin/python run_eval.py --wave pilot --engines claude --reps 5
.venv/bin/python src/judge.py --wave pilot
.venv/bin/python src/analyze.py --wave pilot              # how big should the battery be?

# then the real wave
.venv/bin/python run_eval.py --wave $(date +%F)
.venv/bin/python src/judge.py --wave $(date +%F)
.venv/bin/python src/judge_check.py --wave $(date +%F) --export   # label it by hand
.venv/bin/python src/judge_check.py --wave $(date +%F) --score
.venv/bin/python src/report.py --wave $(date +%F)

# after the fixes land and the crawlers come back
.venv/bin/python src/report.py --wave <later> --baseline <earlier>
```

Every stage writes JSONL incrementally and takes `--resume`, so an interrupted run loses
nothing. A full wave — 6 prompts × 4 engines × 5 reps, judged twice — is roughly **$4**.

`canon.yaml` is the only file you need to edit. Everything else is entity-agnostic: point
it at a company, a product, or a competitor without touching code.

## Layout

```
canon.yaml            ground truth — true claims, stale claims, clean and poisoned sources
taxonomy.yaml         the claim-failure categories and what kind of fix each responds to
prompts/battery.yaml  facet-derived prompt battery, versioned
rubric.md             scoring anchors and the judge-validation protocol
run_eval.py           probe runner — parallel, resumable, --preflight
src/engines.py        four provider adapters, normalised (they agree about nothing)
src/judge.py          dual-family judge: taxonomy tagging + secondary 0–3 scoring
src/judge_check.py    blind human validation of the judges
src/analyze.py        variance decomposition — sizes the battery, sets the noise floor
src/report.py         taxonomy table, citation trail, lift
results/<wave>/       raw_results.jsonl · scores.jsonl · human_check.yaml
reports/              baseline-<wave>.md · lift-<wave>.md
```

## Limitations

- **The API is a proxy for the product.** ChatGPT-the-app has memory, its own system
  prompt, and its own retrieval; the API does not. A small manual product-mode spot-check
  runs alongside each wave and the gap is reported as a number rather than a caveat.
- **The judges and two of the systems under test share model families.** Cross-family
  agreement and blind human labelling are the mitigations, not a cure.
- **Small n.** Treat deltas below the resolution reported by `analyze.py` as directional,
  not significant.
- **One channel of three.** See the scope table above. Recruiter-database enrichment is
  unmeasured.
- **Gemini grounding requires a billed Google project.** The Gemini 2.5 line — the only
  line with free-tier Search grounding — is closed to projects created after mid-2026
  (`404: no longer available to new users`). The 3.x line is reachable but has zero free
  grounding quota. Anyone reproducing this harness needs billing enabled on their Google
  Cloud project for the Gemini column; every other engine runs on a $5 minimum top-up.

- **Canon is authored by the subject.** Ground truth about a person mostly has to be. It is
  auditable — every claim in `canon.yaml` is checkable against a public résumé or repo —
  but it is not independent.

## References

- Zheng, L., et al. (2023). *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.*
- Es, S., et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation.*
- Method and taxonomy structure adapted from [zoeb-nomi/crosssource](https://github.com/zoeb-nomi/crosssource).

## License

MIT. Point it at yourself and tell me what it found.
