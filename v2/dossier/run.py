#!/usr/bin/env python3
"""
mirror-eval v2 "Prompt B" — agentic dossier harness. CLI runner.

    python run.py --stack anthropic --task lookup --condition full --reps 3 --pilot
    python run.py --stack openai --task sourcing --condition full,blocked_owned --reps 5
    python run.py --stack anthropic --task lookup --condition full --reps 10 --mock

Writes every call to results/<run_id>/calls.jsonl AS IT HAPPENS (append,
flush per line), so an interrupted run loses at most the in-flight call.
Re-running the SAME command later reuses the same deterministic run_id (see
_default_run_id) and skips any (prompt_id, condition, rep) already present
in that file — this is the whole resumability story, no separate --resume
flag needed.

Conditions interleave within one run window: jobs are built as the full
cross product of prompts x conditions x reps, shuffled with a FIXED seed (so
two people running the same command get the same call order, which matters
for eyeballing a partial log), then executed with a thread pool.

Before any paid call (i.e. whenever --mock is not set) this prints a cost
estimate and refuses to proceed without --yes or an interactive "yes" at the
prompt. See COST ESTIMATE FORMULA below and README.md.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import pathlib
import random
import sys
import threading
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TASK_FILES = {
    "lookup": ROOT / "prompts" / "lookup.jsonl",
    "sourcing": ROOT / "prompts" / "sourcing.jsonl",
}

CANON_PATH = ROOT / "canon" / "canon.yaml"

# Transient-only error fingerprints. Anything else (bad key, quota, 4xx that
# isn't rate limiting) is NOT retried — retrying a permanent failure just
# burns wall-clock for a result that will never change.
RETRYABLE_SIGNATURES = ("429", "500", "502", "503", "529", "timeout", "connection")

# --- COST ESTIMATE FORMULA ---------------------------------------------------
# estimated_cost = n_calls * (
#     (ASSUMED_INPUT_TOKENS  / 1e6) * price_in[stack]  +
#     (ASSUMED_OUTPUT_TOKENS / 1e6) * price_out[stack] +
#      ASSUMED_SEARCH_CALLS         * price_search[stack]
# )
# ASSUMED_* are deliberately conservative round numbers (a lookup/sourcing
# probe with the JSON-instruction tail and a couple of search round-trips),
# not a measurement — the real per-call cost is tracked live from actual
# usage as the run proceeds and printed in the final SPEND summary, which is
# always the number to trust over this estimate.
ASSUMED_INPUT_TOKENS = 800
ASSUMED_OUTPUT_TOKENS = 500
ASSUMED_SEARCH_CALLS = 3

PRICES = {
    # stack -> (usd/1M in, usd/1M out, usd/search-call). Kept in sync with
    # the per-stack constants in stacks/anthropic_ws.py and openai_agents.py.
    "anthropic": (2.00, 10.00, 0.010),
    "openai": (1.00, 6.00, 0.010),
    "mock": (0.0, 0.0, 0.0),
}


def _load_dotenv() -> None:
    """.env -> os.environ. No dependency, deliberately (same approach as
    mirror-eval/src/engines.py and screener-eval). Keys are never printed."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.split("#")[0].strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def _domain_only(entry: str) -> str:
    return entry.split("/", 1)[0]


def load_conditions() -> dict[str, dict]:
    """Builds the three leave-one-surface-out conditions from canon.yaml, so
    the domain lists live in exactly one place."""
    canon = yaml.safe_load(CANON_PATH.read_text())
    owned_domains = sorted({_domain_only(s) for s in canon.get("owned_sources", [])})
    linkedin_domains = sorted({_domain_only(s) for s in canon.get("self_published_sources", [])})
    return {
        "full": {"blocked_domains": None, "allowed_domains": None},
        # NOTE: blocks the whole domain (e.g. all of github.com), not just
        # zoeb-nomi's paths — neither provider's API can block by path. See
        # README.md limitations.
        "blocked_owned": {"blocked_domains": owned_domains, "allowed_domains": None},
        "blocked_linkedin": {"blocked_domains": linkedin_domains, "allowed_domains": None},
    }


def load_prompts(task: str) -> list[dict]:
    path = TASK_FILES[task]
    if not path.exists():
        print(f"!! {path} does not exist yet — run `make prompts` first.", file=sys.stderr)
        sys.exit(1)
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def done_keys(calls_path: pathlib.Path) -> set[tuple]:
    if not calls_path.exists():
        return set()
    out = set()
    for line in calls_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("error"):
            out.add((r["prompt_id"], r["condition"], r["rep"]))
    return out


def get_stack_fn(stack: str):
    if stack == "mock":
        from stacks.mock import probe
        return probe
    if stack == "anthropic":
        from stacks.anthropic_ws import probe
        return probe
    if stack == "openai":
        from stacks.openai_agents import probe
        return probe
    raise ValueError(f"unknown stack: {stack}")


def _default_run_id(stack: str, args, conditions: list[str]) -> str:
    tag = "pilot" if args.pilot else "full"
    return f"{stack}_{args.task}_{'-'.join(conditions)}_reps{args.reps}_{tag}"


def _run_id_conflict(manifest_path: pathlib.Path, calls_path: pathlib.Path,
                     task: str, stack: str) -> str | None:
    """Returns an error fragment if an existing run under this run_id was made
    with a different task or stack, else None. Reads manifest.json; if there is
    no manifest (e.g. a run interrupted before R4 wrote one), falls back to the
    first record of calls.jsonl."""
    prev_task = prev_stack = None
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            prev_task, prev_stack = m.get("task"), m.get("stack")
        except json.JSONDecodeError:
            return "has an unreadable manifest.json; refusing to guess."
    elif calls_path.exists():
        for line in calls_path.read_text().splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prev_task, prev_stack = r.get("task"), r.get("stack")
                break
    if prev_task is None and prev_stack is None:
        return None
    if prev_task != task or prev_stack != stack:
        return (f"already holds a run with task={prev_task!r}, stack={prev_stack!r}, "
                f"but this command is task={task!r}, stack={stack!r}.")
    return None


def estimate_cost(stack: str, n_calls: int) -> float:
    pin, pout, psearch = PRICES[stack]
    per_call = (ASSUMED_INPUT_TOKENS / 1e6) * pin + (ASSUMED_OUTPUT_TOKENS / 1e6) * pout + ASSUMED_SEARCH_CALLS * psearch
    return n_calls * per_call


def call_with_retry(fn, prompt_text, *, tries=4, **kw):
    delay = 2.0
    tr = None
    for i in range(tries):
        tr = fn(prompt_text, **kw)
        if not tr.error:
            return tr
        if any(sig in tr.error.lower() for sig in RETRYABLE_SIGNATURES) and i < tries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        return tr
    return tr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", choices=["anthropic", "openai"], help="required unless --mock")
    ap.add_argument("--task", choices=["lookup", "sourcing"], required=True)
    ap.add_argument("--condition", default="full,blocked_owned,blocked_linkedin",
                     help="comma list of full|blocked_owned|blocked_linkedin, interleaved in one run")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--pilot", action="store_true", help="label the run 'pilot'; use a small --reps")
    ap.add_argument("--mock", action="store_true", help="deterministic fake stack, no keys, no network")
    ap.add_argument("--yes", action="store_true", help="skip the cost-estimate confirmation prompt")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    _load_dotenv()

    stack = "mock" if a.mock else a.stack
    if not stack:
        ap.error("--stack is required unless --mock is set")

    conditions_all = load_conditions()
    conditions = [c.strip() for c in a.condition.split(",") if c.strip()]
    for c in conditions:
        if c not in conditions_all:
            ap.error(f"unknown condition {c!r}; choose from {list(conditions_all)}")

    prompts = load_prompts(a.task)
    run_id = a.run_id or _default_run_id(stack, a, conditions)
    outdir = ROOT / "results" / run_id
    calls_path = outdir / "calls.jsonl"
    manifest_path = outdir / "manifest.json"

    # A run_id is a (task, stack) namespace. Reusing one for a different task
    # or stack would silently pool incomparable calls, so refuse (exit 2)
    # before anything is written, spent, or created.
    err = _run_id_conflict(manifest_path, calls_path, a.task, stack)
    if err:
        print(f"error: --run-id {run_id!r} {err} Pick a different --run-id "
              f"(or omit it to get the deterministic default).", file=sys.stderr)
        return 2

    seen = done_keys(calls_path)

    jobs = [(p, c, rep) for p in prompts for c in conditions for rep in range(1, a.reps + 1)
            if (p["prompt_id"], c, rep) not in seen]

    if not jobs:
        print(f"nothing to do — {run_id} already complete ({len(seen)} calls)")
        return 0

    print(f"run_id={run_id}  task={a.task}  stack={stack}  "
          f"{len(jobs)} calls to make ({len(prompts)} prompts x {len(conditions)} conditions x {a.reps} reps"
          + (f", {len(seen)} already done" if seen else "") + ")")

    if stack != "mock":
        est = estimate_cost(stack, len(jobs))
        print(f"\nCOST ESTIMATE: ${est:.2f} for {len(jobs)} calls "
              f"(assumes ~{ASSUMED_INPUT_TOKENS} input / ~{ASSUMED_OUTPUT_TOKENS} output tokens "
              f"and ~{ASSUMED_SEARCH_CALLS} search calls per call — a planning number, not a "
              f"measurement; the real total is tracked live and printed at the end).")
        if not a.yes:
            try:
                reply = input(f"Proceed with {len(jobs)} real {stack} calls? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("aborted — no calls made, nothing written. Re-run with --yes to skip this prompt.")
                return 1

    # Cost confirmed (or --mock): only now touch the disk. An aborted or
    # refused run leaves no results/<run_id>/ folder and no manifest behind.
    outdir.mkdir(parents=True, exist_ok=True)
    task_file = TASK_FILES[a.task]
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if manifest_path.exists():
        try:
            created_at = json.loads(manifest_path.read_text()).get("created_at", created_at)
        except json.JSONDecodeError:
            pass
    manifest = {
        "run_id": run_id, "stack": stack, "task": a.task, "conditions": conditions,
        "reps": a.reps, "pilot": a.pilot, "concurrency": a.concurrency,
        "n_prompts": len(prompts),
        "prompts_file": str(task_file.relative_to(ROOT)),
        "prompts_file_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "created_at": created_at,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    probe_fn = get_stack_fn(stack)

    rng = random.Random(1234567)  # fixed seed: same command -> same call order
    rng.shuffle(jobs)

    lock = threading.Lock()
    fh = calls_path.open("a")
    spent = {"total": 0.0}
    counts = {"ok": 0, "error": 0}

    def one(job):
        p, cond, rep = job
        cond_cfg = conditions_all[cond]
        tr = call_with_retry(
            probe_fn, p["rendered_text"],
            run_id=run_id, task=a.task, prompt_id=p["prompt_id"], bank_id=p.get("bank_id"),
            condition=cond, rep=rep,
            blocked_domains=cond_cfg["blocked_domains"], allowed_domains=cond_cfg["allowed_domains"],
        )
        with lock:
            fh.write(json.dumps(tr.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()
            spent["total"] += tr.cost_usd_est
            counts["error" if tr.error else "ok"] += 1
            flag = "ERR " if tr.error else "    "
            print(f"  {flag}{cond:<15} {p['prompt_id']:<28} r{rep} "
                  f"{len(tr.answer_text):>5}ch {len(tr.fetched_urls):>2}url "
                  f"parse_ok={tr.parse_ok}"
                  + (f"  {tr.error[:90]}" if tr.error else ""))
        return tr

    with cf.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        list(ex.map(one, jobs))
    fh.close()

    print(f"\n{counts['ok']} ok, {counts['error']} errored -> {calls_path}")
    if stack != "mock":
        print(f"SPEND (actual, estimated from usage): ${spent['total']:.4f}")
    if counts["error"]:
        print("re-run the SAME command to retry only the failed/unrecorded calls")
    print(f"\nnext:  python metrics.py --run-id {run_id}  (or: python report.py --run-id {run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
