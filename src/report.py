#!/usr/bin/env python3
"""
MIRROR-EVAL — report generator.

    python src/report.py --wave 2026-08-04
    python src/report.py --wave 2026-08-31 --baseline 2026-08-04     # lift

The failure taxonomy leads; the composite score follows. Counts name fixes, scores
do not — a table saying `stale_employer: 7` tells you what to do on Monday, and a
table saying `1.4 / 3` does not.

BANDS, NOT POINT ESTIMATES. Two judges from different model families tag every
answer, and on the interpretive categories they agree on the exact tag set only
17% of the time. Averaging two judges who disagree that often produces a number
with no referent. So every interpretive category is reported as an interval:

    FLOOR   = share of answers where BOTH judges independently fired the tag
    CEILING = share of answers where EITHER judge fired it

The truth is somewhere in there and this harness cannot say where. A category
whose band is 5%–70% is not a measurement and is labelled as such rather than
quietly averaged into one. Only the `computed: true` categories — decided in code
by string-matching the citation list, not by a model — are reported as point
estimates, and only after this file re-verifies at runtime that the two judges'
recorded tags for them are in fact identical.
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

# Band-width thresholds, in percentage points, fixed here rather than chosen after
# looking at the table. A band wider than UNINFORMATIVE_PTS carries no information
# about the engine — only about the judges — and is barred from the headline.
DIRECTIONAL_PTS = 10.0
UNINFORMATIVE_PTS = 20.0


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


# --------------------------------------------------------------------- band maths
def probes_by_key(rows) -> dict:
    """(engine, prompt_id, mode, rep) -> {judge: set(tag_ids)}. One entry per ANSWER,
    not per judgement — the unit a rate should be expressed over."""
    g: dict = collections.defaultdict(dict)
    for r in rows:
        g[(r["engine"], r["prompt_id"], r["mode"], r["rep"])][r["judge"]] = set(r["tag_ids"])
    return dict(g)


def band(probes: list[dict], cat: str) -> tuple:
    """(floor%, ceiling%, n_floor, n_ceiling, n) for one category over a probe list.

    A probe judged by only one judge has floor == ceiling by construction, which
    understates the true band. `fully_judged` below counts those so the report can
    say so instead of hiding it."""
    n = len(probes)
    if not n:
        return (None, None, 0, 0, 0)
    fl = sum(1 for v in probes if v and all(cat in s for s in v.values()))
    ce = sum(1 for v in probes if any(cat in s for s in v.values()))
    return (100 * fl / n, 100 * ce / n, fl, ce, n)


def concordance(probes: list[dict], cat: str) -> float | None:
    """both / either — positive-class agreement. Not raw agreement, which is inflated
    to >90% by the answers where neither judge fired a rare tag."""
    fl = sum(1 for v in probes if v and all(cat in s for s in v.values()))
    ce = sum(1 for v in probes if any(cat in s for s in v.values()))
    return (fl / ce) if ce else None


def one_sided(probes: list[dict], cat: str, judges: list[str], floor_n: int = 5):
    """Return the judge firing a tag alone if the other judge never fires it at all.
    hallucinated_verification in wave 2026-08-06 is this: Claude 47, Gemini 0. That
    is not a disagreement about a rate, it is one judge using a category the other
    does not have."""
    fired = {j: sum(1 for v in probes if cat in v.get(j, set())) for j in judges}
    live = [j for j, n in fired.items() if n]
    if len(live) == 1 and fired[live[0]] >= floor_n:
        return live[0], fired
    return None, fired


def verdict(fl, ce, conc, sole) -> str:
    if sole:
        return f"**one judge only** (`{sole}`)"
    if fl is None:
        return "—"
    w = ce - fl
    if w <= DIRECTIONAL_PTS and (conc or 0) >= 0.5:
        return "narrow"
    if w <= UNINFORMATIVE_PTS:
        return "directional"
    return "**uninformative**"


def bandstr(fl, ce) -> str:
    """Always an interval, even when floor == ceiling. A bare number in this column
    would read as a point estimate, and none of these are."""
    if fl is None:
        return "—"
    return f"{fl:.0f}–{ce:.0f}%"


# ------------------------------------------------------------------- taxonomy table
def band_table(rows, tax, engines) -> list[str]:
    fixmap = {c["id"]: c["fix_class"] for c in tax["categories"]}
    coverage = {c["id"] for c in tax["categories"] if c.get("reporting") == "coverage"}
    computed = {c["id"] for c in tax["categories"] if c.get("computed")}
    interpretive = [c["id"] for c in tax["categories"]
                    if c["id"] not in computed and c["id"] not in coverage]

    g = probes_by_key(rows)
    judges = sorted({r["judge"] for r in rows})
    allp = list(g.values())
    per_engine = {e: [v for k, v in g.items() if k[0] == e] for e in engines}
    partial = sum(1 for v in allp if len(v) < len(judges))

    out: list[str] = []

    if len(judges) < 2:
        out += ["## Failure taxonomy (SINGLE JUDGE — point estimates)", "",
                f"Only one judge (`{judges[0] if judges else 'none'}`) tagged this wave, so no "
                "band can be formed. Every number below is one model's opinion reported as "
                "though it were a rate. Do not compare it against a two-judge wave.", ""]
        tot = collections.Counter()
        n_per = collections.Counter()
        for r in rows:
            n_per[r["engine"]] += 1
            for t in r["tag_ids"]:
                tot[t] += 1
        out += ["| Failure | fix | " + " | ".join(LABEL.get(e, e) for e in engines) + " | all |",
                "|---|---|" + "---|" * (len(engines) + 1)]
        for c in tax["categories"]:
            t = c["id"]
            if not tot[t]:
                continue
            cells = [f"{100*sum(1 for r in rows if r['engine']==e and t in r['tag_ids'])/n_per[e]:.0f}%"
                     if n_per[e] else "—" for e in engines]
            out.append(f"| `{t}` | {fixmap[t]} | " + " | ".join(cells) +
                       f" | **{100*tot[t]/max(len(rows),1):.0f}%** |")
        out.append("")
        return out

    # ---------------------------------------------------------------- computed first
    out += ["## Failure taxonomy", "",
            "### Mechanically checked (point estimates)", "",
            "These categories are decided in `judge.py` by matching the citation list "
            "against `canon.yaml`, not by asking a model to do string comparison. They "
            "are the only rates in this report quoted as single numbers, and the "
            "generator re-verifies below that both judges' stored tags for them are "
            "byte-identical before printing them that way.", "",
            "| Failure | fix | " + " | ".join(LABEL.get(e, e) for e in engines) + " | all |",
            "|---|---|" + "---|" * (len(engines) + 1)]
    demoted = []
    for c in tax["categories"]:
        t = c["id"]
        if t not in computed:
            continue
        disc = sum(1 for v in allp if len({t in s for s in v.values()}) > 1)
        fl, ce, _, ce_n, n = band(allp, t)
        if disc:
            demoted.append((t, disc))
            continue
        if not ce_n:
            continue
        cells = []
        for e in engines:
            _, ec, _, _, en = band(per_engine[e], t)
            cells.append(f"{ec:.0f}%" if en else "—")
        out.append(f"| `{t}` | {fixmap[t]} | " + " | ".join(cells) + f" | **{ce:.0f}%** |")
    out.append("")
    out.append(f"*Judge-vs-judge discrepancies on the computed categories: "
               f"{'none — exact' if not demoted else 'SEE BELOW'}.*")
    out.append("")
    for t, disc in demoted:
        out += [f"> **`{t}` was NOT reported as a point estimate.** It is marked "
                f"`computed: true` in `taxonomy.yaml`, but the two judges' stored tags "
                f"disagree on {disc} answers — so the code path that is supposed to make "
                f"it exact did not run for every row. It appears in the band table below "
                f"instead. Fix `judge.py` or unset `computed`; do not quote a single "
                f"number for it.", ""]

    # ------------------------------------------------------------------- band table
    banded = interpretive + [t for t, _ in demoted]
    out += ["### Judge-interpreted (bands, never point estimates)", "",
            f"**floor** = both judges independently fired the tag · **ceiling** = either "
            f"judge did. The two judges agree on the exact tag set on "
            f"{100*sum(1 for v in allp if len({frozenset(s) for s in v.values()})==1)/max(len(allp),1):.0f}% "
            f"of answers, so the honest statement about any category here is an interval, "
            f"not a number. `verdict` is fixed by band width: ≤{DIRECTIONAL_PTS:.0f} pts and "
            f"positive-class concordance ≥0.5 is *narrow*, ≤{UNINFORMATIVE_PTS:.0f} pts is "
            f"*directional*, wider is **uninformative** and must not lead a headline.", "",
            "| Failure | fix | " + " | ".join(LABEL.get(e, e) for e in engines) +
            " | all | width | both/either | verdict |",
            "|---|---|" + "---|" * (len(engines) + 4)]
    diag = []
    for t in banded:
        fl, ce, _, ce_n, n = band(allp, t)
        if not ce_n:
            continue
        conc = concordance(allp, t)
        sole, fired = one_sided(allp, t, judges)
        cells = []
        for e in engines:
            efl, ece, _, _, en = band(per_engine[e], t)
            cells.append(bandstr(efl, ece) if en else "—")
        out.append(f"| `{t}` | {fixmap.get(t,'—')} | " + " | ".join(cells) +
                   f" | **{bandstr(fl, ce)}** | {ce-fl:.0f} pts | "
                   f"{'—' if conc is None else f'{100*conc:.0f}%'} | {verdict(fl, ce, conc, sole)} |")
        diag.append((t, fl, ce, conc, sole, fired))
    if not diag:
        out.append("| *(no failures tagged)* | | " + " | ".join("—" for _ in engines) +
                   " | — | — | — | — |")
    out.append("")

    unusable = [d for d in diag if d[4] or (d[2] - d[1]) > UNINFORMATIVE_PTS]
    if unusable:
        out += ["**Categories barred from the headline.** Each of these is a statement "
                "about the judges, not about the engines:", ""]
        for t, fl, ce, conc, sole, fired in unusable:
            if sole:
                other = [j for j in judges if j != sole]
                out.append(f"- `{t}` — floor {fl:.0f}%, ceiling {ce:.0f}%. The `{sole}` judge "
                           f"fired it {fired[sole]} times; "
                           f"{' and '.join(f'`{j}` fired it {fired[j]} times' for j in other)}. "
                           f"Zero overlap, so the floor is structurally 0 and the ceiling is "
                           f"one model's disposition. Report it as an open question or drop it.")
            else:
                out.append(f"- `{t}` — {bandstr(fl, ce)} ({ce-fl:.0f} pts wide, "
                           f"{100*(conc or 0):.0f}% both/either). The interval spans too much "
                           f"to constrain anything. Usable as a fix-list pointer, not as a rate.")
        out.append("")

    # ------------------------------------------------------------ coverage + clean
    for t in sorted(coverage):
        fl, ce, _, ce_n, n = band(allp, t)
        if not ce_n:
            continue
        out.append(f"*Reported separately — `{t}` band **{bandstr(fl, ce)}** of answers. This "
                   f"is a CANON COVERAGE measure, not an engine failure: it counts specifics "
                   f"our ground truth simply does not cover. Treat a high rate as a prompt to "
                   f"widen `canon.yaml`, not as evidence the engines erred. It is judge-"
                   f"interpreted, so it gets a band like everything else.*\n")

    cf = sum(1 for v in allp if all(not (s - coverage) for s in v.values()))
    cc = sum(1 for v in allp if any(not (s - coverage) for s in v.values()))
    out.append(f"**Clean answers (no failure tag): floor {100*cf/max(len(allp),1):.0f}% "
               f"({cf}/{len(allp)}) — ceiling {100*cc/max(len(allp),1):.0f}% "
               f"({cc}/{len(allp)}).** Floor = both judges called it clean; ceiling = at "
               f"least one did.\n")

    if partial:
        out.append(f"> **{partial} of {len(allp)} answers were judged by fewer than "
                   f"{len(judges)} judges.** For those, floor and ceiling collapse to the "
                   f"same value, which makes every band above narrower than it should be. "
                   f"Re-run `judge.py --resume` before publishing.\n")
    return out


def quotes(rows, tax, limit=12) -> list[str]:
    seen, out = set(), ["### What that looks like in the answers", "",
                        "*Illustrative spans, one judge each. A quote is evidence the tag was "
                        "triggered by something real; it is not evidence of the rate.*", ""]
    prio = {"fabricated_metric": 0, "hallucinated_verification": 1, "conflated_identity": 2}
    flat = [(prio.get(t["category"], 9), r["engine"], t["category"], t.get("quote", ""), r["judge"])
            for r in rows for t in r.get("tags", []) if t.get("quote")]
    for _, eng, cat, q, j in sorted(flat):
        k = (cat, q[:60])
        if k in seen:
            continue
        seen.add(k)
        out.append(f"- **`{cat}`** — {LABEL.get(eng, eng)} (tagged by `{j}`): “{q.strip()[:220]}”")
        if len(seen) >= limit:
            break
    out.append("")
    return out if len(out) > 5 else []


def score_table(rows, engines) -> list[str]:
    judges = sorted({r["judge"] for r in rows})
    out = ["## Composite scores (secondary)", "",
           "Mean of four rubric dimensions, 0–3. Reported for the lift comparison; the "
           "taxonomy above is what drives decisions. Each judge's mean is shown "
           "separately — the judges disagree far less here than on tags, but a single "
           "averaged column would hide that they also score different numbers of "
           "dimensions (each judge decides independently when a dimension is `null`).", "",
           "| Engine | " + " | ".join(d.upper() for d in DIMS) + " | " +
           " | ".join(f"composite ({j})" for j in judges) + " | composite (mean) |",
           "|---|" + "---|" * (len(DIMS) + len(judges) + 1)]
    for e in engines:
        er = [r for r in rows if r["engine"] == e]
        cells = [f(mean([r.get(d) for r in er])) for d in DIMS]
        percol = [f(mean([composite(r) for r in er if r["judge"] == j])) for j in judges]
        out.append(f"| {LABEL.get(e, e)} | " + " | ".join(cells) + " | " + " | ".join(percol) +
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
           "or a fix target — there is no third category. Counted from the probe records, "
           "not from a judge, so this table is exact.", "",
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


def judge_section(rows, tax, wave) -> list[str]:
    judges = sorted({r["judge"] for r in rows})
    out = ["## Judge validation", ""]
    if len(judges) < 2:
        out.append(f"Single judge (`{judges[0] if judges else 'none'}`). Cross-family "
                   "self-preference bias was **not measured** — disclose as a limitation.\n")
        return out

    computed = {c["id"] for c in tax["categories"] if c.get("computed")}
    g = probes_by_key(rows)
    allp = list(g.values())
    both = [v for v in allp if len(v) > 1]
    agree = sum(1 for v in both if len({frozenset(s) for s in v.values()}) == 1)
    interp = sum(1 for v in both
                 if len({frozenset(s - computed) for s in v.values()}) == 1)
    load = {j: mean([len(r["tag_ids"]) for r in rows if r["judge"] == j]) for j in judges}
    out.append(f"Two judges from different model families (`{'`, `'.join(judges)}`) tagged "
               f"every answer independently. Exact tag-set agreement: "
               f"**{agree}/{len(both)} ({100*agree/max(len(both),1):.0f}%)**; on the "
               f"interpretive categories alone, "
               f"**{100*interp/max(len(both),1):.0f}%**. Pre-registered expectation was "
               f"60–75%, with below-60% pre-declared to mean the taxonomy is "
               f"underspecified. It is. That is why this report bands.\n")
    out.append("Tags per answer: " + " · ".join(f"`{j}` {load[j]:.2f}" for j in judges) +
               ". A stable ratio here across rubric revisions is a model-level "
               "disposition, not a prompt problem, and no further rubric work will "
               "move it.\n")

    out += ["| Engine | exact tag-set agreement | mean Jaccard |", "|---|---|---|"]
    for e in sorted({k[0] for k in g}):
        ps = [v for k, v in g.items() if k[0] == e and len(v) > 1]
        if not ps:
            continue
        ag = sum(1 for v in ps if len({frozenset(s) for s in v.values()}) == 1)
        jac = []
        for v in ps:
            a, b = list(v.values())[:2]
            jac.append(1.0 if not a and not b else len(a & b) / len(a | b))
        out.append(f"| {LABEL.get(e, e)} | {ag}/{len(ps)} ({100*ag/len(ps):.0f}%) | "
                   f"{statistics.mean(jac):.2f} |")
    out += ["", "One of the systems under test is Claude, so a Claude-only judge would "
            "have been grading a contestant it shares a family with. If agreement on "
            "Claude's own answers is markedly lower than on the other three, that is "
            "self-preference bias showing up as a number rather than a caveat.\n"]

    hc = ROOT / "results" / wave / "human_check.yaml"
    if not hc.exists():
        out.append("> **Not yet human-validated.** Two models agreeing is evidence they share "
                   "a prior, not evidence they are right — and at this agreement level they "
                   "are mostly not agreeing at all. Run `src/judge_check.py --export`, "
                   "label the sample blind, then `--score`. Until then the bands above are "
                   "the widest honest claim, and even the floor is unverified.\n")
    return out


def lift_section(cur, base, cw, bw, tax, engines) -> list[str]:
    """Lift is reported floor-to-floor and ceiling-to-ceiling. Subtracting one
    midpoint from another midpoint would manufacture a precision neither wave has."""
    coverage = {c["id"] for c in tax["categories"] if c.get("reporting") == "coverage"}
    computed = {c["id"] for c in tax["categories"] if c.get("computed")}
    gc, gb = list(probes_by_key(cur).values()), list(probes_by_key(base).values())
    out = ["## Lift", "", f"`{bw}` → `{cw}`", "",
           "### Failure rates", "",
           "Computed categories move as point estimates. Everything else moves as a "
           "band, and a change only counts if the intervals separate — if the after-"
           "ceiling is still above the before-floor, the wave did not resolve it.", "",
           "| Failure | before | after | floor Δ | ceiling Δ | separated? |",
           "|---|---|---|---|---|---|"]
    for c in tax["categories"]:
        t = c["id"]
        bfl, bce, _, bn, _ = band(gb, t)
        afl, ace, _, an, _ = band(gc, t)
        if not bn and not an:
            continue
        if t in computed:
            out.append(f"| `{t}` (computed) | {bce:.0f}% | {ace:.0f}% | "
                       f"**{ace-bce:+.0f} pts** | — | point estimate |")
            continue
        sep = "yes" if (ace < bfl or afl > bce) else "**no — overlapping**"
        note = " *(coverage)*" if t in coverage else ""
        out.append(f"| `{t}`{note} | {bandstr(bfl, bce)} | {bandstr(afl, ace)} | "
                   f"{afl-bfl:+.0f} pts | {ace-bce:+.0f} pts | {sep} |")
    out += ["", "### Composite", "", "| Engine | before | after | Δ |", "|---|---|---|---|"]
    for e in engines:
        bv = mean([composite(r) for r in base if r["engine"] == e])
        av = mean([composite(r) for r in cur if r["engine"] == e])
        d = f"{av-bv:+.2f}" if (av is not None and bv is not None) else "—"
        out.append(f"| {LABEL.get(e, e)} | {f(bv)} | {f(av)} | **{d}** |")
    out += ["", "> Compare these deltas against the sampling noise reported by "
                "`src/analyze.py`. A delta smaller than the within-prompt standard "
                "deviation is not a result. A taxonomy band that still overlaps its "
                "baseline band is not a result either, however large the midpoint "
                "shift looks.", ""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--baseline", default="")
    ap.add_argument("--out", default="", help="output dir (default: reports/)")
    a = ap.parse_args()

    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    rows, raws = load(a.wave)
    engines = sorted({r["engine"] for r in rows})
    judges = sorted({r["judge"] for r in rows})

    title = "Lift report" if a.baseline else "Baseline report"
    doc = [f"# MIRROR-EVAL — {title} (`{a.wave}`)", "",
           f"{len(raws)} probes · {len(rows)} judgements · {len(engines)} engines · "
           f"{len({r['prompt_id'] for r in rows})} prompts · "
           f"{max((r['rep'] for r in raws), default=1)} reps per cell · "
           f"{len(judges)} judge(s)", "",
           ("> Interpretive taxonomy rates are **intervals**, not point estimates. Floor = "
            "both judges fired the tag; ceiling = either did. Quoting the midpoint of one "
            "of these bands as a rate misrepresents the measurement."
            if len(judges) > 1 else
            "> **Single judge — no bands available.** Every taxonomy rate below is one "
            "model's opinion. It is not comparable with a two-judge wave and must not be "
            "quoted alongside one."), ""]

    if a.baseline:
        brows, _ = load(a.baseline)
        doc += lift_section(rows, brows, a.wave, a.baseline, tax, engines)

    doc += band_table(rows, tax, engines)
    doc += quotes(rows, tax)
    doc += sources_section(raws)
    doc += nosearch_section(raws)
    doc += score_table(rows, engines)
    doc += judge_section(rows, tax, a.wave)

    outdir = pathlib.Path(a.out) if a.out else ROOT / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    name = f"lift-{a.wave}.md" if a.baseline else f"baseline-{a.wave}.md"
    (outdir / name).write_text("\n".join(doc))
    print("\n".join(doc))
    print(f"\n--> {outdir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
