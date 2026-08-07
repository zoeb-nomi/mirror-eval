#!/usr/bin/env python3
"""
MIRROR-EVAL — blind human validation of the judges.

    python src/judge_check.py --wave 2026-08-04 --export    # writes a blind worksheet
    # ... you label it by hand, in a text editor ...
    python src/judge_check.py --wave 2026-08-04 --score     # agreement report

Two models agreeing is not evidence they are right; it is evidence they share a prior.
The only thing that grounds this harness is a human reading a sample of answers blind —
without seeing the judges' verdicts — and applying the taxonomy themselves. Percent
agreement against that sample is the number that decides whether any other number here
can be cited. Same method as `src/judge_check.py` in zoeb-nomi/crosssource, which is
where this pattern came from — and where blind labelling caught a real harness bug.

Sampling is stratified deliberately:
  * every answer where the two judges DISAGREED on tags   (highest information)
  * every answer carrying a high-impact tag                (fabricated_metric,
    hallucinated_verification, conflated_identity)
  * a random sample of answers both judges called clean    (catches false negatives —
    the failure mode you cannot see by reading flagged items only)
  * balanced across engines, so per-engine judge bias is visible rather than averaged away
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HIGH_IMPACT = {"fabricated_metric", "hallucinated_verification", "conflated_identity"}
SEED = 20260803


def load(wave: str):
    wdir = ROOT / "results" / wave
    raw = {(r["engine"], r["prompt_id"], r["mode"], r["rep"]): r
           for r in (json.loads(l) for l in (wdir / "raw_results.jsonl").read_text().splitlines() if l.strip())}
    scores = [json.loads(l) for l in (wdir / "scores.jsonl").read_text().splitlines() if l.strip()]
    return wdir, raw, scores


def by_probe(scores):
    d = collections.defaultdict(dict)
    for s in scores:
        d[(s["engine"], s["prompt_id"], s["mode"], s["rep"])][s["judge"]] = s
    return d


def do_export(wave: str, n_clean: int, max_items: int) -> int:
    wdir, raw, scores = load(wave)
    grouped = by_probe(scores)
    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())

    disagree, high, clean = [], [], []
    for k, js in grouped.items():
        sets = [frozenset(v["tag_ids"]) for v in js.values()]
        union = set().union(*sets) if sets else set()
        if len(set(sets)) > 1:
            disagree.append(k)
        elif union & HIGH_IMPACT:
            high.append(k)
        elif not union:
            clean.append(k)

    rng = random.Random(SEED)
    # balance the clean sample across engines so per-engine bias stays visible
    per_engine = collections.defaultdict(list)
    for k in clean:
        per_engine[k[0]].append(k)
    picked_clean = []
    if per_engine:
        quota = max(1, n_clean // len(per_engine))
        for eng in sorted(per_engine):
            pool = sorted(per_engine[eng])
            rng.shuffle(pool)
            picked_clean += pool[:quota]

    rng.shuffle(disagree)
    rng.shuffle(high)
    budget = max_items - len(picked_clean)
    take_high = high[:max(0, budget // 3)]
    take_dis = disagree[:max(0, budget - len(take_high))]
    dropped = (len(disagree) - len(take_dis)) + (len(high) - len(take_high))
    sample = sorted(set(take_dis + take_high + picked_clean))
    items = []
    for k in sample:
        r = raw[k]
        items.append({
            "id": f"{k[0]}|{k[1]}|{k[2]}|r{k[3]}",
            "question": r["question"],
            "answer": r["answer"],
            "cited_urls": [c["url"] for c in r.get("citations", [])],
            "search_performed": r["search_performed"],
            "human_tags": [],           # <-- YOU FILL THIS IN
            "human_note": "",
        })

    out = wdir / "human_check.yaml"
    header = (
        "# BLIND JUDGE VALIDATION WORKSHEET\n"
        f"# wave: {wave}   items: {len(items)}\n"
        "#\n"
        "# Fill in `human_tags` for each item using ONLY the categories below. Leave the\n"
        "# list empty if the answer has no failures. Do not look at scores.jsonl until\n"
        "# after you finish — the whole point is that these labels are independent.\n"
        "#\n"
        "# Categories:\n"
        + "".join(f"#   {c['id']:<28} {c['definition'].strip().splitlines()[0]}\n"
                 for c in tax["categories"])
        + "#\n# Then: python src/judge_check.py --wave " + wave + " --score\n\n"
    )
    out.write_text(header + yaml.safe_dump({"items": items}, sort_keys=False,
                                           allow_unicode=True, width=100))
    print(f"wrote {out}")
    print(f"  {len(take_dis)}/{len(disagree)} judge-disagreement · "
          f"{len(take_high)}/{len(high)} high-impact · {len(picked_clean)} clean control"
          f"  = {len(items)} to label")
    if dropped:
        print(f"  !! {dropped} eligible items were NOT sampled (cap --max {max_items}). "
              f"Report the cap in limitations; a silently truncated sample reads as full coverage.")
    if not disagree and len(grouped) and len({s['judge'] for s in scores}) > 1:
        print("  (judges agreed on tags everywhere — that is itself worth reporting)")
    return 0


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def do_score(wave: str) -> int:
    wdir, raw, scores = load(wave)
    wsp = wdir / "human_check.yaml"
    if not wsp.exists():
        print(f"no {wsp} — run --export first", file=sys.stderr)
        return 1
    ws = yaml.safe_load(wsp.read_text())
    labelled = {i["id"]: set(i.get("human_tags") or []) for i in ws["items"]
                if i.get("human_tags") is not None}
    filled = [i for i in ws["items"] if i.get("human_tags") or i.get("human_note")]
    if not filled:
        print("worksheet is unlabelled — nothing to score", file=sys.stderr)
        return 1

    grouped = by_probe(scores)
    judges = sorted({s["judge"] for s in scores})

    def key_of(sid: str):
        e, p, m, r = sid.split("|")
        return (e, p, m, int(r[1:]))

    print(f"human-labelled items: {len(filled)} of {len(ws['items'])}\n")

    # judge vs human
    rows = []
    for j in judges:
        exact = tot = 0
        jac = 0.0
        per_engine = collections.defaultdict(lambda: [0, 0])
        for i in filled:
            k = key_of(i["id"])
            v = grouped.get(k, {}).get(j)
            if not v:
                continue
            h = set(i.get("human_tags") or [])
            m = set(v["tag_ids"])
            tot += 1
            jac += jaccard(h, m)
            if h == m:
                exact += 1
                per_engine[k[0]][0] += 1
            per_engine[k[0]][1] += 1
        if tot:
            rows.append((j, exact, tot, jac / tot, dict(per_engine)))

    print("JUDGE vs HUMAN")
    print(f"{'judge':<9} {'exact-set agreement':<22} {'mean Jaccard':<14} per-engine exact")
    for j, exact, tot, jac, pe in rows:
        pes = "  ".join(f"{e}:{c}/{n}" for e, (c, n) in sorted(pe.items()))
        print(f"{j:<9} {exact}/{tot} ({100*exact/tot:.0f}%){'':<8} {jac:.2f}{'':<10} {pes}")

    # judge vs judge, over ALL probes not just the labelled sample
    if len(judges) > 1:
        a, b = judges[0], judges[1]
        agree = tot = 0
        jac = 0.0
        per_engine = collections.defaultdict(lambda: [0, 0])
        for k, js in grouped.items():
            if a in js and b in js:
                sa, sb = set(js[a]["tag_ids"]), set(js[b]["tag_ids"])
                tot += 1
                jac += jaccard(sa, sb)
                if sa == sb:
                    agree += 1
                    per_engine[k[0]][0] += 1
                per_engine[k[0]][1] += 1
        if tot:
            print(f"\nJUDGE vs JUDGE ({a} vs {b}), all {tot} probes")
            print(f"  exact-set agreement {agree}/{tot} ({100*agree/tot:.0f}%) · "
                  f"mean Jaccard {jac/tot:.2f}")
            print("  per engine: " + "  ".join(
                f"{e}:{100*c/n:.0f}%" for e, (c, n) in sorted(per_engine.items())))
            print("\n  Read this per-engine. If the Claude judge agrees with the Gemini judge\n"
                  "  markedly less on Claude's own answers than on the other three, that is\n"
                  "  self-preference bias showing up as a number rather than a caveat.")

    print("\nReport these figures in the limitations section verbatim. An unvalidated\n"
          "judge makes every other table in this repo uncitable.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--max", type=int, default=40, dest="max_items",
                    help="cap on items to hand-label; excess is reported, never silent")
    ap.add_argument("--clean-controls", type=int, default=12,
                    help="how many judged-clean answers to include as false-negative controls")
    a = ap.parse_args()
    if a.export:
        return do_export(a.wave, a.clean_controls, a.max_items)
    if a.score:
        return do_score(a.wave)
    ap.error("pass --export or --score")


if __name__ == "__main__":
    raise SystemExit(main())
