#!/usr/bin/env python3
"""
MIRROR-EVAL — battery sizing.

    python src/analyze.py --wave pilot

Answers the only honest version of "are five prompts enough?": measure it.

Decompose the variance in the composite score two ways —
  * WITHIN-prompt  : spread across repeated asks of the SAME prompt to the SAME engine.
                     This is pure sampling noise. It is the floor on what any lift
                     measurement can resolve.
  * BETWEEN-prompt : spread across DIFFERENT prompts to the same engine.
                     This is signal about how phrasing-sensitive the engine's answer is.

If between ≈ within, extra prompts buy nothing and reps buy everything: the prompts are
not distinguishing anything the noise doesn't. If between >> within, the answer swings on
phrasing, the battery is undersampled — and that is itself a publishable finding, not
just a reason to add prompts.

Also reports TAG INSTABILITY: how often repeated asks of the identical prompt produce
different failure-tag sets. High instability means single-shot probing of AI engines —
which is what every "I asked ChatGPT about myself" blog post does — is measuring noise.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIMS = ["identity", "accuracy", "endorsement", "sources"]


def composite(row: dict):
    vals = [row.get(d) for d in DIMS if isinstance(row.get(d), (int, float))]
    return statistics.mean(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--judge", default="", help="restrict to one judge (default: all, averaged)")
    a = ap.parse_args()

    fp = ROOT / "results" / a.wave / "scores.jsonl"
    if not fp.exists():
        print(f"no {fp} — run judge.py first")
        return 1
    rows = [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]
    if a.judge:
        rows = [r for r in rows if r["judge"] == a.judge]

    # average judges per probe so judge disagreement doesn't masquerade as sampling noise
    cells = collections.defaultdict(list)          # (engine, prompt, mode, rep) -> composites
    tagsets = collections.defaultdict(list)        # (engine, prompt, mode) -> [frozenset]
    for r in rows:
        c = composite(r)
        if c is not None:
            cells[(r["engine"], r["prompt_id"], r["mode"], r["rep"])].append(c)
        tagsets[(r["engine"], r["prompt_id"], r["mode"])].append(frozenset(r["tag_ids"]))

    probe = {k: statistics.mean(v) for k, v in cells.items()}
    engines = sorted({k[0] for k in probe})

    print(f"wave={a.wave}  {len(probe)} probes  {len(engines)} engine(s)\n")
    print("VARIANCE DECOMPOSITION (composite score, 0-3)")
    print(f"{'engine':<12} {'within-prompt sd':<18} {'between-prompt sd':<19} ratio   verdict")

    overall = []
    for e in engines:
        prompts = sorted({k[1] for k in probe if k[0] == e})
        withins, means = [], []
        for p in prompts:
            vs = [v for k, v in probe.items() if k[0] == e and k[1] == p]
            if len(vs) > 1:
                withins.append(statistics.stdev(vs))
            if vs:
                means.append(statistics.mean(vs))
        w = statistics.mean(withins) if withins else 0.0
        b = statistics.stdev(means) if len(means) > 1 else 0.0
        ratio = (b / w) if w else float("inf")
        verdict = ("more reps — prompts aren't separating from noise" if ratio < 1.2
                   else "balanced" if ratio < 2.0
                   else "more prompts — answers are phrasing-sensitive")
        overall.append(ratio)
        print(f"{e:<12} {w:<18.3f} {b:<19.3f} {ratio:<7.2f} {verdict}")

    all_w = [statistics.stdev([v for k, v in probe.items() if k[0] == e and k[1] == p])
             for e in engines
             for p in sorted({k[1] for k in probe if k[0] == e})
             if len([v for k, v in probe.items() if k[0] == e and k[1] == p]) > 1]
    if all_w:
        w_all = statistics.mean(all_w)
        reps = max(len({k[3] for k in probe}), 1)
        # minimum detectable DIFFERENCE between two engine means at ~2 standard errors
        mdd = 2 * w_all * (2 / reps) ** 0.5
        print(f"\nSampling noise (mean within-prompt sd) = {w_all:.3f} composite points.")
        print(f"With {reps} rep(s) per cell, the smallest difference between two means you\n"
              f"can resolve at ~2 standard errors is {mdd:.2f} composite points.")
        print("Any before/after delta smaller than that is not a result. Say so in the\n"
              "report rather than letting a reader assume otherwise.")
        for target in (0.25, 0.5):
            need = max(1, round(2 * (2 * w_all / target) ** 2))
            print(f"  to resolve {target:.2f} points you would need ~{need} reps per cell")

    print("\nTAG INSTABILITY (identical prompt, repeated asks, different failure tags)")
    unstable = tot = 0
    per_engine = collections.defaultdict(lambda: [0, 0])
    for (e, p, m), sets in tagsets.items():
        if len(sets) < 2:
            continue
        tot += 1
        per_engine[e][1] += 1
        if len(set(sets)) > 1:
            unstable += 1
            per_engine[e][0] += 1
    if tot:
        print(f"  {unstable}/{tot} prompt-cells ({100*unstable/tot:.0f}%) changed tag set across reps")
        print("  per engine: " + "  ".join(f"{e}:{100*c/n:.0f}%" for e, (c, n) in sorted(per_engine.items())))
        if unstable / tot > 0.3:
            print("\n  >30% instability. Single-shot probing of these engines measures noise.\n"
                  "  That finding alone justifies the harness: it is what every\n"
                  "  'I asked ChatGPT about myself' post gets wrong.")
    else:
        print("  need >1 rep per cell to measure this — re-run with --reps 5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
