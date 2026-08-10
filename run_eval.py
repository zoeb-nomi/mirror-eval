#!/usr/bin/env python3
"""
MIRROR-EVAL — probe runner.

    python run_eval.py --preflight                    # verify keys/models/network first
    python run_eval.py --wave pilot --engines claude  # pilot: one engine, sizes the battery
    python run_eval.py --wave 2026-08-04              # full wave, all configured engines
    python run_eval.py --wave 2026-08-04 --resume     # continue an interrupted wave

Writes one JSON line per probe to results/<wave>/raw_results.jsonl as it goes, so an
interrupted run loses nothing and --resume picks up exactly where it stopped.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import re
import sys
import threading

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from engines import ENGINES, available_engines, preflight  # noqa: E402
from budget import Budget, estimate_cost, looks_like_out_of_credit  # noqa: E402


def render(text: str, canon: dict) -> str:
    flat = {k: v for k, v in canon.items() if isinstance(v, str)}
    flat.update({k: v for k, v in canon.get("identity", {}).items() if isinstance(v, str)})
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(flat.get(m.group(1), m.group(0))), text)


def done_keys(path: pathlib.Path) -> set[tuple]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("error"):
            out.add((r["engine"], r["prompt_id"], r["mode"], r["rep"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight", action="store_true",
                    help="test every configured engine and judge, then exit")
    ap.add_argument("--wave", help="label for this run, e.g. pilot / 2026-08-04")
    ap.add_argument("--engines", default="", help="comma list; default = all with keys")
    ap.add_argument("--modes", default="search")
    ap.add_argument("--prompts", default="")
    ap.add_argument("--reps", type=int, default=0, help="default from battery.yaml")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--budget", default="chatgpt=5,perplexity=10,gemini=11,claude=20",
                    help="per-engine USD caps, e.g. chatgpt=5,gemini=11. "
                         "An engine that hits its cap or runs out of credit stops "
                         "cleanly; the rest keep going.")
    a = ap.parse_args()

    if a.preflight:
        return preflight()
    if not a.wave:
        ap.error("--wave is required (or use --preflight)")

    canon = yaml.safe_load((ROOT / "canon.yaml").read_text())
    battery = yaml.safe_load((ROOT / "prompts" / "battery.yaml").read_text())
    reps = a.reps or battery.get("default_reps", 5)

    engines = [e.strip() for e in a.engines.split(",") if e.strip()] or available_engines()
    engines = [e for e in engines if e in available_engines()] or []
    if not engines:
        print("no engines available — set at least one API key (see .env.example)", file=sys.stderr)
        return 1

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    want = {p.strip() for p in a.prompts.split(",") if p.strip()}
    prompts = [p for p in battery["prompts"] if not want or p["id"] in want]

    outdir = ROOT / "results" / a.wave
    outdir.mkdir(parents=True, exist_ok=True)

    # ---- comparability manifest ------------------------------------------------
    # Written at run start so wave-to-wave comparability is a file diff, not an
    # argument. If the battery is locked and its hash differs from the baseline
    # manifest's, refuse to run: that would be a v2 battery wearing a v1 label.
    import hashlib
    import subprocess

    def _sha(path: pathlib.Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""

    frozen = ["canon.yaml", "taxonomy.yaml", "rubric.md", "prompts/battery.yaml",
              "src/engines.py", "src/judge.py"]
    manifest = {
        "wave": a.wave,
        "reps": reps,
        "engines": engines,
        "battery_version": battery["version"],
        "battery_locked": bool(battery.get("locked")),
        "sha256": {f: _sha(ROOT / f) for f in frozen},
    }
    try:
        manifest["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=5).stdout.strip()
        manifest["git_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, timeout=5).stdout.strip())
    except Exception:                                         # noqa: BLE001
        manifest["git_commit"], manifest["git_dirty"] = "", None

    if battery.get("locked"):
        for base in sorted(d for d in (ROOT / "results").iterdir() if d.is_dir()):
            bm = base / "manifest.json"
            if bm.exists() and base.name != a.wave:
                baseline = json.loads(bm.read_text())
                b_sha = baseline.get("sha256", {}).get("prompts/battery.yaml")
                if b_sha and b_sha != manifest["sha256"]["prompts/battery.yaml"]:
                    print(f"REFUSING to run: prompts/battery.yaml is locked but its hash "
                          f"differs from baseline wave {base.name!r}.\n"
                          f"Either revert the battery, or bump version to v2 and set "
                          f"locked: false consciously.", file=sys.stderr)
                    return 1
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # ---------------------------------------------------------------------------

    raw = outdir / "raw_results.jsonl"
    seen = done_keys(raw) if a.resume else set()
    if not a.resume and raw.exists():
        print(f"!! {raw} exists. Use --resume to continue it, or move it aside.", file=sys.stderr)
        return 1

    # A prompt may override the wave's rep count. The comparability anchor needs
    # only enough runs to be quotable, not enough to be statistically powered.
    # A prompt may override both the wave's rep count and its mode list.
    jobs = [(e, m, p, rep)
            for e in engines for p in prompts
            for m in (p.get("modes") or modes)
            for rep in range(1, int(p.get("reps") or reps) + 1)
            if m in (p.get("modes") or modes) and (e, p["id"], m, rep) not in seen]

    if not jobs:
        print("nothing to do — wave already complete")
        return 0

    per_prompt = ", ".join(f"{p['id']}x{int(p.get('reps') or reps)}" for p in prompts)
    print(f"wave={a.wave}  {len(jobs)} probes  "
          f"({len(engines)} engines x {len(modes)} modes x [{per_prompt}])"
          + (f"  [{len(seen)} already done]" if seen else ""))

    caps = {}
    for part in a.budget.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            caps[k.strip()] = float(v)
    bud = Budget(caps)
    print("budget caps: " + "  ".join(f"{k}=${v:.2f}" for k, v in sorted(caps.items())))

    lock = threading.Lock()
    fh = raw.open("a")

    def one(job):
        eng, mode, pr, rep = job
        # An engine that has stopped is skipped WITHOUT writing a record, so
        # --resume treats these probes as still owed rather than as failures.
        if bud.is_stopped(eng):
            return None
        fn, _ = ENGINES[eng]
        p = fn(render(pr["text"], canon), mode)
        p.prompt_id = pr["id"]
        rec = p.to_dict() | {"wave": a.wave, "rep": rep, "facet": pr.get("facet"),
                             "anchor": bool(pr.get("anchor")),
                             "battery_version": battery["version"]}
        with lock:
            if looks_like_out_of_credit(p.error):
                bud.halt(eng, f"out of credit / quota — {(p.error or '')[:110]}")
                print(f"  STOP {eng:<11} out of credit. Remaining {eng} probes deferred "
                      f"to --resume; other engines continue.")
                return None
            bud.add(eng, estimate_cost(p.model, p.usage, p.search_performed))
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            flag = "ERR " if p.error else ("NOSRCH" if mode == "search" and not p.search_performed else "    ")
            print(f"  {flag} {eng:<11} {pr['id']:<14} {mode:<9} r{rep} "
                  f"{len(p.answer):>5}ch {len(p.citations):>2}cite {p.latency_s:>6.1f}s"
                  + (f"  {p.error[:80]}" if p.error else ""))
        return p

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = [r for r in ex.map(one, jobs) if r is not None]
    fh.close()
    print(bud.report())

    errs = [r for r in results if r.error]
    nosearch = [r for r in results if r.mode == "search" and not r.search_performed and not r.error]
    print(f"\n{len(results) - len(errs)} ok, {len(errs)} errored -> {raw}")
    if errs:
        print("   re-run with --resume to retry only the failures")
    if nosearch:
        print(f"!! {len(nosearch)} search-mode probes never actually searched — "
              f"the engine answered from parametric memory. That is a FINDING: for those, "
              f"fixing the live web cannot help.")
    if bud.stopped:
        print(f"\nWave INCOMPLETE. Top up, then: python run_eval.py --wave {a.wave} --resume")
        return 2
    print(f"\nnext:  python src/judge.py --wave {a.wave}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
