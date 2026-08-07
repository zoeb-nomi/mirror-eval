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
