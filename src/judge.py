#!/usr/bin/env python3
"""
MIRROR-EVAL — dual-family judge.

    python src/judge.py --wave 2026-08-04
    python src/judge.py --wave 2026-08-04 --judges claude          # single judge
    python src/judge.py --wave 2026-08-04 --resume

Every answer is tagged with the claim-failure taxonomy (taxonomy.yaml) — the primary
result — and scored 0-3 on four dimensions as a secondary rollup.

Why two judges from different families: one of the four systems under test IS Claude.
A Claude judge scoring Claude's answer alongside three competitors is a conflict of
interest, and self-preference bias in LLM judges is documented (Zheng et al. 2023).
Running Claude and Gemini judges over the same answers turns that from an unmeasured
threat into a reported number — see `src/judge_check.py` for the human validation that
sits above both, because two agreeing models can still be agreeing about the wrong thing.

Writes one line per (probe x judge) to results/<wave>/scores.jsonl.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import re
import sys
import threading

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from engines import JUDGES, available_judges  # noqa: E402

DIMS = ["identity", "accuracy", "endorsement", "sources"]


def build_system(canon_text: str, tax: dict) -> str:
    # Computed categories are decided in code and deliberately kept OUT of the
    # judge's prompt — every category the judge sees is one it can get wrong.
    cats = "\n".join(
        f"- `{c['id']}` — {c['definition'].strip()}"
        + (f" Trigger example: {c['example_trigger']}" if c.get("example_trigger") else "")
        for c in tax["categories"] if not c.get("computed"))
    return f"""You are a scoring judge in an evaluation harness. You are given ONE answer that \
an AI search engine produced about a real person, plus a canon of verified facts about \
that person, plus the list of URLs the engine actually cited.

You are not judging whether the answer reads well. You are judging whether it is TRUE, \
CURRENT, and SOURCED. Be harsh. A fluent, confident, wrong answer is the worst outcome, \
not a middling one.

<canon>
{canon_text}
</canon>

PRIMARY TASK — tag the answer with every claim-failure category that applies:

{cats}

Apply a tag only for a claim the answer actually makes. Do not tag on suspicion. For each \
tag, quote the span of the answer that triggered it, verbatim and short. In `quote`, never \
use double-quote characters — use single quotes instead.
An answer with no failures gets an empty tag list.

PRECEDENCE — apply in this order, and never tag the same span twice:
1. If a span asserts something on the stale_or_false list, tag ONLY the matching
   stale_* category. Do not also tag unverifiable_specific for that span.
2. `unverifiable_specific` is a LAST RESORT. Use it only for a specific factual
   claim about THIS person that canon neither confirms nor contradicts. Never for
   general statements, opinions, hedges, or anything already covered above.
3. `missed_available_evidence` requires BOTH: the answer states no evidence exists,
   AND canon.clean_sources contains a surface that demonstrably holds it. If the
   answer merely says it could not find *details*, that is not this tag.
4. `substituted_source` requires BOTH: a cited source that is not about this person,
   AND a claim in the answer built on it. Citing an irrelevant page without using it
   is not this tag.
5. `hallucinated_verification` requires an AFFIRMATIVE confirmation of a specific
   claim with nothing cited that contains it. Declining to confirm is NOT this tag.
6. `refused_to_assess` requires the prompt to have asked for a judgement and the
   answer to give none. A hedged-but-present judgement is not a refusal.

WORKED EXAMPLES
- "Zoeb Nomi is a Senior Product Manager at Keka."
  -> stale_role, stale_employer.  NOT unverifiable_specific.
- "He is based in Bengaluru and works at Instead."
  -> no tags. Both are in canon.
- "I could not find any public record of a citation-precision improvement."
  -> missed_available_evidence (CrossSource's README is a clean source and holds it).
  NOT hallucinated_verification — declining is correct behaviour.
- "Yes, I can confirm the 0.981 to 0.994 improvement." with no source containing it
  -> hallucinated_verification.
- "Several AI PM roles are open at companies like Lever" cited from a job board
  -> substituted_source.
- "He has over 12 years of experience."
  -> fabricated_metric. A number about him that is not in canon.

SECONDARY TASK — score 0-3 on four dimensions:

IDENTITY   0 wrong era/company · 1 vague, no clear current identity · 2 company OR wedge right · 3 both right
ACCURACY   0 asserts a stale/fabricated claim as fact · 1 generic, specifics off · 2 correct but thin · 3 canon-consistent specifics
ENDORSEMENT 0 declines or negative · 1 heavily hedged · 2 cautiously positive · 3 confident recommend with evidence
SOURCES    0 none, or only poisoned · 1 majority poisoned/irrelevant · 2 majority clean · 3 clean only

Use null for a dimension the prompt does not exercise (ENDORSEMENT when no judgement was \
asked for; SOURCES when the answer was produced with search disabled).

Note the asymmetry in ACCURACY: a wrong specific scores BELOW a vague generality, because \
a reader cannot distinguish a fabricated number from a real one.

Return ONLY a JSON object, no prose, no markdown fence:
{{"tags":[{{"category":"<id>","quote":"<verbatim span>"}}],
  "identity":0-3|null,"accuracy":0-3|null,"endorsement":0-3|null,"sources":0-3|null,
  "one_line":"<=25 words justifying the scores"}}"""


def build_user(rec: dict) -> str:
    cites = "\n".join(f"- {c['url']}  {c.get('title','')}" for c in rec.get("citations", [])) or "(none)"
    # The judge is deliberately NOT told which engine produced this answer.
    # The headline result is a cross-engine comparison, and one judge shares a
    # model family with one contestant — naming the contestant would hand the
    # judge the exact label needed to favour it. Engine is re-attached after
    # scoring, from the record, never inside the prompt.
    return f"""<probe>
prompt_id: {rec['prompt_id']}   facet: {rec.get('facet')}
mode: {rec['mode']}
search_actually_performed: {rec['search_performed']}
question: {rec['question']}
</probe>

<answer>
{rec['answer'][:14000]}
</answer>

<cited_sources>
{cites}
</cited_sources>

Tag and score it."""


def _salvage(raw: str) -> dict:
    """
    Recover a judgement from JSON the model broke.

    The usual cause: `quote` holds a verbatim span from the answer, that span
    contains a double-quote, and the model does not escape it. Throwing the whole
    judgement away over a stray quotation mark loses real data, so pull the fields
    out by pattern and mark the row as salvaged so it can be audited later.
    """
    out: dict = {"tags": [], "salvaged": True}
    for cat in re.findall(r'"category"\s*:\s*"([a-z_]+)"', raw):
        out["tags"].append({"category": cat, "quote": ""})
    for dim in DIMS:
        m = re.search(rf'"{dim}"\s*:\s*(null|[0-3])', raw)
        if m:
            out[dim] = None if m.group(1) == "null" else int(m.group(1))
    m = re.search(r'"one_line"\s*:\s*"([^"]{0,200})', raw)
    out["one_line"] = m.group(1) if m else "(salvaged from malformed JSON)"
    return out


def parse(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"judge returned no JSON: {txt[:200]}")
    raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return _salvage(raw)


def normalise_tags(v: dict, valid: set) -> list[dict]:
    """Judges return tags as objects OR as bare category strings. Accept both."""
    out = []
    for t in (v.get("tags") or []):
        if isinstance(t, str):
            t = {"category": t, "quote": ""}
        if isinstance(t, dict) and t.get("category") in valid:
            out.append({"category": t["category"], "quote": str(t.get("quote") or "")[:300]})
    return out


def computed_tags(rec: dict, canon: dict, computed_ids: set) -> list[dict]:
    """
    Decide the mechanically-checkable categories in code.

    Wave 1 measured what happens when you ask a language model to do string
    matching: `poisoned_citation` agreed 77% between judges and `no_sources`
    agreed 0%. Both are arithmetic over the citation list. Doing them here makes
    them exact and removes two categories the judge can get wrong.
    """
    out = []
    urls = " ".join(c.get("url", "") for c in rec.get("citations", [])).lower()
    if "poisoned_citation" in computed_ids:
        for src in canon.get("poisoned_sources", []):
            dom = str(src).strip().lower()
            if "." not in dom or dom.startswith("any "):
                continue                       # prose entries are for the judge, not code
            if dom in urls:
                out.append({"category": "poisoned_citation", "quote": dom})
                break
    if "no_sources" in computed_ids:
        if rec.get("mode") == "search" and rec.get("search_performed") \
                and not rec.get("citations"):
            out.append({"category": "no_sources", "quote": "(search fired, nothing cited)"})
    return out


def _row(jname: str, rec: dict, v: dict, valid: set,
         canon: dict | None = None, computed_ids: set | None = None) -> dict:
    tags = normalise_tags(v, valid)
    if canon is not None and computed_ids:
        tags = [t for t in tags if t["category"] not in computed_ids]
        tags += computed_tags(rec, canon, computed_ids)
    row = {"judge": jname, **{k: rec[k] for k in ("engine", "prompt_id", "mode", "rep")},
           "facet": rec.get("facet"), "search_performed": rec["search_performed"],
           "tags": tags, "tag_ids": sorted({t["category"] for t in tags}),
           **{d: (v.get(d) if isinstance(v.get(d), int) else None) for d in DIMS},
           "one_line": str(v.get("one_line") or "")[:200]}
    if v.get("salvaged"):
        row["salvaged"] = True
    return row


def probe_key(r: dict) -> tuple:
    return (r["engine"], r["prompt_id"], r["mode"], r["rep"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--judges", default="", help="comma list; default = all with keys")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    canon_text = (ROOT / "canon.yaml").read_text()
    canon = yaml.safe_load(canon_text)
    tax = yaml.safe_load((ROOT / "taxonomy.yaml").read_text())
    system = build_system(canon_text, tax)
    valid = {c["id"] for c in tax["categories"]}
    computed_ids = {c["id"] for c in tax["categories"] if c.get("computed")}

    wdir = ROOT / "results" / a.wave
    raw = wdir / "raw_results.jsonl"
    if not raw.exists():
        print(f"no {raw} — run run_eval.py first", file=sys.stderr)
        return 1
    probes = [json.loads(l) for l in raw.read_text().splitlines() if l.strip()]
    probes = [p for p in probes if not p.get("error") and p.get("answer")]

    judges = [j.strip() for j in a.judges.split(",") if j.strip()] or available_judges()
    judges = [j for j in judges if j in available_judges()]
    if not judges:
        print("no judge keys available (ANTHROPIC_API_KEY / GEMINI_API_KEY)", file=sys.stderr)
        return 1
    if len(judges) == 1:
        print(f"!! single judge ({judges[0]}). Cross-family bias will NOT be measured; "
              f"disclose it as a limitation.")

    out = wdir / "scores.jsonl"
    seen = set()
    if a.resume and out.exists():
        for l in out.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                seen.add((r["judge"], r["engine"], r["prompt_id"], r["mode"], r["rep"]))
    elif out.exists():
        print(f"!! {out} exists — use --resume or move it aside", file=sys.stderr)
        return 1

    jobs = [(j, p) for j in judges for p in probes
            if (j, *probe_key(p)) not in seen]
    print(f"judging {len(probes)} answers x {len(judges)} judge(s) = {len(jobs)} calls")

    lock = threading.Lock()
    fh = out.open("a")

    def one(job):
        jname, rec = job
        try:
            v = parse(JUDGES[jname](system, build_user(rec)))
        except Exception as e:                                  # noqa: BLE001
            with lock:
                print(f"  ERR {jname} {rec['engine']}/{rec['prompt_id']}: {type(e).__name__}: {str(e)[:110]}")
            return None
        tags = normalise_tags(v, valid)
        try:
            row = _row(jname, rec, v, valid, canon, computed_ids)
        except Exception as e:                                  # noqa: BLE001
            with lock:
                print(f"  ERR {jname} {rec['engine']}/{rec['prompt_id']} (post-parse): "
                      f"{type(e).__name__}: {str(e)[:90]}")
            return None
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {jname:<7} {rec['engine']:<11} {rec['prompt_id']:<14} r{rec['rep']} "
                  f"tags={','.join(row['tag_ids']) or '-'}"
                  + ("  [salvaged]" if row.get("salvaged") else ""))
        return row


    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        rows = [r for r in ex.map(one, jobs) if r]
    fh.close()

    print(f"\nwrote {len(rows)} judgements -> {out}")
    print(f"next:  python src/judge_check.py --wave {a.wave} --export   "
          f"(blind human validation — do this before believing any of it)")
    print(f"       python src/report.py --wave {a.wave}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
