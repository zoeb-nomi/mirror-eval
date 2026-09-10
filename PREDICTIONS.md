# Pre-registration — predictions before the first probe

**Written 2026-08-03, before any live API call was made.** Committed ahead of the baseline
wave so it cannot be revised to match what the data shows.

An eval you design after seeing the results is not an eval, it is a rationalisation. The
cheapest defence against that is to write down what you expect, in numbers, and then
publish the scorecard against it — **including the misses**. A timestamped wrong prediction
is worth more to a reader than a clean success story, because it proves the measurement
wasn't reverse-engineered from the outcome.

Every prediction below is falsifiable from `results/<wave>/scores.jsonl` with no
interpretation required. Fill in the outcome column after the wave. Do not edit the
predictions.

## Context the predictions are made against

- The canonical site `www.zoebnomi.com` went live **2026-08-03** — one to two days before
  the baseline wave. Sitemap submitted to GSC the same day.
- The stale portfolio at `portfolio-a5z.pages.dev` now 301s to the canonical site
  (verified on the wire, same day).
- `weekday.works` profile returns 410; `rocketreach.co` self-corrected to the current role.
- LinkedIn was rewritten to canon in July; GitHub profile and `crosssource` are current.
- The July 23 manual, product-mode baseline read: ChatGPT mid-level (sourced from
  LinkedIn) · Claude declined to endorse · Perplexity believed the Keka-era role ·
  Gemini confident but working from the stale site.

---

## Predictions

| # | Prediction | Threshold | Outcome |
|---|---|---|---|
| **P1** | The canonical site has barely been indexed. `zoebnomi.com` appears in fewer than **25%** of search-mode probes' citations in the baseline wave. | <25% | |
| **P2** | **The dominant failure is absence, not error.** Combined rate of `no_sources` + `refused_to_assess` exceeds combined `stale_role` + `stale_employer` + `stale_positioning`. | absence > staleness | |
| **P3** | Claude has the highest `refused_to_assess` rate of the four, and the lowest mean ENDORSEMENT. | rank 1 of 4 on both | |
| **P4** | ChatGPT has the *lowest* staleness rate of the four, because LinkedIn — its July source — was already rewritten to canon. | rank 4 of 4 on staleness | |
| **P5** | At least one engine confirms the 0.981 → 0.994 claim on `F3_verify` while citing nothing that contains those figures. `hallucinated_verification` fires on **≥25%** of F3 probes. | ≥25% | |
| **P6** | `conflated_identity` is rare — the name is distinctive enough that engines don't blend in a different person. | <5% | |
| **P7** | **Single-shot probing is unreliable.** Tag instability across repeated identical asks exceeds **30%** of prompt-cells. | >30% | |
| **P8** | The weights know essentially nothing. In `knowledge` mode, **≥75%** of answers either decline or contain no canon-consistent specific. | ≥75% | |
| **P9** | Cross-family judge agreement (exact tag-set) lands between **60% and 75%**. Below 60% means the taxonomy is underspecified, not that the engines are erratic. | 60–75% | |
| **P10** | Self-preference bias is real and visible: the Claude judge agrees with the Gemini judge **≥10 points less** on Claude's own answers than its average across the other three engines. | ≥10 pt gap | |
| **P11** | By wave 2 (~3 weeks post-launch), `zoebnomi.com` citation rate rises above **60%**, and Perplexity adopts it fastest, Gemini slowest. | >60%, ordering as stated | |

## Why P2 is the one worth watching

The project was founded on a premise: *stale surfaces are poisoning the answer, remove them
and the answer improves.* That premise was largely true in July. It may already be obsolete.
The old portfolio now redirects, Weekday is gone, RocketReach corrected itself — so if P2
holds, the remaining problem is **thin presence rather than wrong presence**, and the fix
roadmap inverts: stop removing bad sources, start creating citable ones.

If P2 is confirmed, the headline finding of this repo is not "I cleaned up my footprint."
It is: *removing bad sources is the easy half and it does not, by itself, produce a good
answer.* That is a more useful result for anyone else running this harness, and it is the
opposite of what the project set out to show.

## Kill criterion

Set in advance: **if the baseline shows all four engines already substantially correct —
staleness under 5% and mean composite above 2.5 — the study stops and is not published.**
No finding, no project. Recording this now removes the temptation to publish a null result
dressed as a success because the work was already done.

## Scoring protocol

After the baseline wave, add HIT / MISS / PARTIAL to the outcome column with the observed
number beside it. P11 is scored after wave 2. The README's results section must state the
overall hit rate, and the report must discuss at least one miss in detail — a
pre-registration nobody scores is decoration.

## Scored against Wave 2 (2026-09-04)

| # | Prediction | Verdict | Actual |
|---|---|---|---|
| P1 | Site <25% of citations at baseline | MISS | 136/252 search probes (54%) already cited it |
| P2 | Absence > staleness | NOT TESTABLE | no_sources not broken out; staleness bands 6–39% |
| P3 | Claude: top refused_to_assess + bottom ENDORSEMENT | PARTIAL | ENDORSEMENT lowest (1.14); refused_to_assess not highest (0–7% vs Gemini 0–19%) |
| P4 | ChatGPT least stale of the four | HIT (tied) | ~0–4% combined, tied with Perplexity |
| P5 | hallucinated_verification ≥25% on F3 | MISS | overall band 0–10%, one judge only |
| P6 | conflated_identity <5% | HIT | 0–2% |
| P7 | Tag instability >30% | HIT | 86% of prompt-cells |
| P8 | Knowledge mode ≥75% blank | NOT TESTABLE | not broken out this wave |
| P9 | Cross-family agreement 60–75% | MISS | 35% (117/332) |
| P10 | Claude self-preference ≥10pt | INCONCLUSIVE | Claude-answer agreement 22% is below the others' mean (~40%) but above Perplexity (13%) — tracks junk load, not model family; this design can't separate the two |
| P11 | Site >60% by W2; Perplexity fastest, Gemini slowest | HIT | 180/252 (71%); Perplexity +50, Gemini +2 |

**Scorecard: 4 HIT (1 tied) · 1 PARTIAL · 1 INCONCLUSIVE · 3 MISS · 2 NOT TESTABLE.** P9 is load-bearing: 35% sits well under the pre-declared 60% floor, so by the protocol's own rule the taxonomy is underspecified — hence bands for nine of eleven categories. P1 and P5 miss in the same direction: both assumed the world was worse than it was, so creating more surface mattered less than fixing the judge. P11 holds in aggregate only because Perplexity and Gemini carried it — Claude cited the site on zero probes in both waves, the finding this scorecard had no line for.
