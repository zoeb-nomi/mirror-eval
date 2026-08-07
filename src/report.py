#!/usr/bin/env python3
"""
MIRROR-EVAL — report generator.

    python src/report.py --wave 2026-08-04
    python src/report.py --wave 2026-08-31 --baseline 2026-08-04     # lift

The failure taxonomy leads; the composite score follows. Counts name fixes, scores
do not — a table saying `stale_employer: 7` tells you what to do on Monday, and a
table saying `1.4 / 3` does not.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIMS = ["identity", "accuracy", "endorsement", "sources"]
LABEL = {"chatgpt": "ChatGPT", "claude": "Claude", "perplexity": "Perplexity", "gemini": "Gemini"}


def load(wave: str):
    wdir = ROOT / "results" / wave
    rows = [json.loads(l) for l in (wdir / "scores.jsonl").read_text().splitlines() if l.strip()]
    raws = [json.loads(l) for l in (wdir / "raw_results.jsonl").read_text().splitlines() if l.strip()]
    return rows, raws


def composite(r):
    v = [r.get(d) for d in DIMS if isinstance(r.get(d), (int, float))]
    return statistics.mean(v) if v else None


def mean(vals):
    v = [x for x in vals if isinstance(x, (int, float))]
    return statistics.mean(v) if v else None


def f(v, nd=2):
    return "—" if v is None else f"{float(v):.{nd}f}"


def tag_table(rows, tax, engines) -> list[str]:
    """Rate = share of judged answers carrying the tag, per engine. Rates, not raw
    counts, so engines with different probe counts stay comparable."""
    fixmap = {c["id"]: c["fix_class"] for c in tax["categories"]}
    coverage = {c["id"] for c in tax["categories"] if c.get("reporting") == "coverage"}
    tot = collections.Counter()
    per = collections.defaultdict(collections.Counter)
    n_per = collections.Counter()
    for r in rows:
        n_per[r["engine"]] += 1
        for t in r["tag_ids"]:
            tot[t] += 1
            per[r["engine"]][t] += 1

    out = ["## Failure taxonomy", "",
           "Share of judged answers carrying each tag. This table is the fix roadmap: "
           "`fix` names what kind of intervention the category responds to — "
           "`surface` means a live web page is teaching it, `model` means the engine "
           "invented it and no amount of site work will help.", "",
           "| Failure | fix | " + " | ".join(LABEL.get(e, e) for e in engines) + " | all |",
           "|---|---|" + "---|" * (len(engines) + 1)]
    for c in tax["categories"]:
        t = c["id"]
        if not tot[t] or t in coverage:
            continue
        cells = [f"{100*per[e][t]/n_per[e]:.0f}%" if n_per[e] else "—" for e in engines]
        allr = 100 * tot[t] / max(sum(n_per.values()), 1)
        out.append(f"| `{t}` | {fixmap[t]} | " + " | ".join(cells) + f" | **{allr:.0f}%** |")
    if not tot:
        out.append("| *(no failures tagged)* | | " + " | ".join("—" for _ in engines) + " | — |")
    out.append("")

    for t in sorted(coverage):
        if tot[t]:
            out.append(f"*Reported separately — `{t}` fired on {100*tot[t]/max(len(rows),1):.0f}% "
                       f"of answers. This is a CANON COVERAGE measure, not an engine failure: "
                       f"it counts specifics our ground truth simply does not cover. Treat a high "
                       f"rate as a prompt to widen `canon.yaml`, not as evidence the engines erred.*\n")
    clean = sum(1 for r in rows if not (set(r["tag_ids"]) - coverage))
    out.append(f"**{clean}/{len(rows)} judged answers ({100*clean/max(len(rows),1):.0f}%) "
               f"carried no failure tag at all.**\n")
    return out


def quotes(rows, tax, limit=12) -> list[str]:
    seen, out = set(), ["### What that looks like in the answers", ""]
    prio = {"fabricated_metric": 0, "hallucinated_verification": 1, "conflated_identity": 2}
    flat = [(prio.get(t["category"], 9), r["engine"], t["category"], t.get("quote", ""))
            for r in rows for t in r.get("tags", []) if t.get("quote")]
    for _, eng, cat, q in sorted(flat):
        k = (cat, q[:60])
        if k in seen:
            continue
        seen.add(k)
        out.append(f"- **`{cat}`** — {LABEL.get(eng, eng)}: “{q.strip()[:220]}”")
        if len(seen) >= limit:
            break
    out.append("")
    return out if len(out) > 3 else []


def score_table(rows, engines) -> list[str]:
    out = ["## Composite scores (secondary)", "",
           "Mean of four rubric dimensions, 0–3. Reported for the lift comparison; the "
           "taxonomy above is what drives decisions.", "",
           "| Engine | " + " | ".join(d.upper() for d in DIMS) + " | composite |",
           "|---|" + "---|" * (len(DIMS) + 1)]
    for e in engines:
        er = [r for r in rows if r["engine"] == e]
        cells = [f(mean([r.get(d) for r in er])) for d in DIMS]
        out.append(f"| {LABEL.get(e, e)} | " + " | ".join(cells) +
                   f" | **{f(mean([composite(r) for r in er]))}** |")
    out.append("")
    return out


def sources_section(raws) -> list[str]:
    cited = collections.Counter()
    for r in raws:
        for c in r.get("citations", []):
            u = c.get("url", "")
            dom = u.split("/")[2] if "://" in u and len(u.split("/")) > 2 else u
            if dom:
                cited[dom.lower().removeprefix("www.")] += 1
    if not cited:
        return []
    out = ["## Which surfaces the engines actually read", "",
           "The citation trail is the diagnostic. Every domain here is either an asset "
           "or a fix target — there is no third category.", "",
           "| Domain | times cited |", "|---|---|"]
    for d, n in cited.most_common(25):
        out.append(f"| `{d}` | {n} |")
    out.append("")
    return out


def nosearch_section(raws) -> list[str]:
    bad = [r for r in raws if r["mode"] == "search" and not r["search_performed"] and not r.get("error")]
    if not bad:
        return []
    per = collections.Counter((r["engine"], r["prompt_id"]) for r in bad)
    out = ["### Probes where the engine declined to search", "",
           "The search tool was offered and never fired — the answer came from parametric "
           "memory. For these, fixing the live web cannot help; only time and the next "
           "training run can.", "",
           "| Engine | Prompt | n |", "|---|---|---|"]
    for (e, p), n in per.most_common():
        out.append(f"| {LABEL.get(e, e)} | {p} | {n} |")
    out.append("")
    return out


def judge_section(rows, wave) -> list[str]:
    judges = sorted({r["judge"] for r in rows})
    out = ["## Judge validation", ""]
    if len(judges) < 2:
        out.append(f"Single judge (`{judges[0] if judges else 'none'}`). Cross-family "
                   "self-preference bias was **not measured** — disclose as a limitation.\n")
    else:
        g = collections.defaultdict(dict)
        for r in rows:
            g[(r["engine"], r["prompt_id"], r["mode"], r["rep"])][r["judge"]] = set(r["tag_ids"])
        both = [v for v in g.values() if len(v) > 1]
        agree = sum(1 for v in both if len(set(map(frozenset, v.values()))) == 1)
        out.append(f"Two judges from different model families (`{'`, `'.join(judges)}`) tagged "
                   f"every answer independently. Exact tag-set agreement: "
                   f"**{agree}/{len(both)} ({100*agree/max(len(both),1):.0f}%)**.\n")
        out.append("One of the systems under test is Claude, so a Claude-only judge would "
                   "have been grading a contestant it shares a family with. Per-engine "
                   "agreement breakdown and the blind human check are in "
                   f"`results/{wave}/human_check.yaml` — run `src/judge_check.py --score`.\n")
    hc = ROOT / "results" / wave / "human_check.yaml"
    if not hc.exists():
        out.append("> **Not yet human-validated.** Two models agreeing is evidence they share "
                   "a prior, not evidence they are right. Run `src/judge_check.py --export`, "
                   "label the sample blind, then `--score`. Until then every table above is "
                   "provisional.\n")
    return out


def lift_section(cur, base, cw, bw, tax, engines) -> list[str]:
    def rate(rows, t):
        return 100 * sum(1 for r in rows if t in r["tag_ids"]) / max(len(rows), 1)
    out = ["## Lift", "", f"`{bw}` → `{cw}`", "",
           "### Failure rates", "",
           "| Failure | before | after | Δ |", "|---|---|---|---|"]
    for c in tax["categories"]:
        t = c["id"]
        b, a_ = rate(base, t), rate(cur, t)
        if b == 0 and a_ == 0:
            continue
        out.append(f"| `{t}` | {b:.0f}% | {a_:.0f}% | **{a_-b:+.0f} pts** |")
    out += ["", "### Composite", "", "| Engine | before | after | Δ |", "|---|---|---|---|"]
    for e in engines:
        bv = mean([composite(r) for r in base if r["engine"] == e])
        av = mean([composite(r) for r in cur if r["engine"] == e])
        d = f"{av-bv:+.2f}" if (av is not None and bv is not None) else "—"
        out.append(f"| {LABEL.get(e, e)} | {f(bv)} | {f(av)} | **{d}** |")
    out += ["", "> Compare these deltas against the sampling noise reported by "
                "`src/analyze.py`. A delta smaller than the within-prompt standard "
                "deviation is not a result.", ""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--baseline", default="")
    a = ap.parse_args()

    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    rows, raws = load(a.wave)
    engines = sorted({r["engine"] for r in rows})

    title = "Lift report" if a.baseline else "Baseline report"
    doc = [f"# MIRROR-EVAL — {title} (`{a.wave}`)", "",
           f"{len(raws)} probes · {len(rows)} judgements · {len(engines)} engines · "
           f"{len({r['prompt_id'] for r in rows})} prompts · "
           f"{max((r['rep'] for r in raws), default=1)} reps per cell", ""]

    if a.baseline:
        brows, _ = load(a.baseline)
        doc += lift_section(rows, brows, a.wave, a.baseline, tax, engines)

    doc += tag_table(rows, tax, engines)
    doc += quotes(rows, tax)
    doc += sources_section(raws)
    doc += nosearch_section(raws)
    doc += score_table(rows, engines)
    doc += judge_section(rows, a.wave)

    outdir = ROOT / "reports"
    outdir.mkdir(exist_ok=True)
    name = f"lift-{a.wave}.md" if a.baseline else f"baseline-{a.wave}.md"
    (outdir / name).write_text("\n".join(doc))
    print("\n".join(doc))
    print(f"\n--> reports/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
