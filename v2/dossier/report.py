#!/usr/bin/env python3
"""
Renders results/<run_id>/metrics.json (computing it first if missing) into
results/<run_id>/report.md (plain language) and results/<run_id>/summary.json
(the same numbers, machine-readable).

    python report.py --run-id <run_id>
"""

from __future__ import annotations

import argparse
import json
import pathlib

from metrics import compute_all, load_calls, load_canon

ROOT = pathlib.Path(__file__).resolve().parent


def _fmt_pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _esc(k: str) -> str:
    """Pipes in group keys would split markdown table cells."""
    return k.replace("|", "\\|")


def render_md(run_id: str, records: list[dict], m: dict) -> str:
    lines = [f"# mirror-eval v2 report — {run_id}", ""]
    tasks = sorted({r.get("task") or "?" for r in records})
    stacks = sorted({r.get("stack") or "?" for r in records})
    lines.append(f"Task: {', '.join(tasks)}  |  Stack: {', '.join(stacks)}")
    lines.append("")
    lines.append(f"Calls: {m['n_calls_total']} total, {m['n_ok']} ok, {m['n_errored']} errored.")
    lines.append("")

    lines.append("## Owned-surface retrieval hit rate")
    lines.append("Did zoebnomi.com or github.com/zoeb-nomi get fetched at all, per task x stack x condition.")
    lines.append("")
    lines.append("| task\\|stack\\|condition | hit rate | n |")
    lines.append("|---|---|---|")
    for k, v in sorted(m["owned_surface_hit_rate"].items()):
        lines.append(f"| {_esc(k)} | {_fmt_pct(v['hit_rate'])} | {v['n_calls']} |")
    lines.append("")

    lines.append("## Hops to truth")
    lines.append("Position of the first fetched URL that is an owned surface or a canon")
    lines.append("verifying_url. `no_hit_rate` = fraction of calls that never reached one.")
    lines.append("")
    lines.append("| task\\|stack\\|condition | no-hit rate | min hop | max hop | n |")
    lines.append("|---|---|---|---|---|")
    for k, v in sorted(m["hops_to_truth"].items()):
        lines.append(f"| {_esc(k)} | {_fmt_pct(v['no_hit_rate'])} | {v['min_hop']} | {v['max_hop']} | {v['n_calls']} |")
    lines.append("")

    lines.append("## Field accuracy vs canon (TRUTH fields only)")
    lines.append("Claim fields are never scored right/wrong here — only independently")
    lines.append("verifiable canon fields are. See canon/README.md.")
    lines.append("")
    for fid, fv in m["field_accuracy"].items():
        if not fv.get("scored"):
            lines.append(f"- `{fid}`: not scored ({fv.get('reason')})")
            continue
        rule = f"all of {fv['match_all']}" if fv.get("match_all") else ""
        if fv.get("match_any"):
            rule += (" AND " if rule else "") + f"any of {fv['match_any']}"
        lines.append(f"- `{fid}` (rule: {rule}):")
        for k, v in sorted(fv["by_group"].items()):
            lines.append(f"  - {k}: {_fmt_pct(v['accuracy'])} (n={v['n_calls']})")
    lines.append("")

    lines.append("## Provenance matrix (claim source domain x label)")
    lines.append(f"Task: {', '.join(tasks)} (pooled across the stacks/conditions above).")
    lines.append("")
    lines.append("| domain | verified | claimed | unverifiable | unlabeled |")
    lines.append("|---|---|---|---|---|")
    for d, labels in sorted(m["provenance_matrix"].items()):
        lines.append(f"| {d} | {labels.get('verified', 0)} | {labels.get('claimed', 0)} "
                      f"| {labels.get('unverifiable', 0)} | {labels.get('(unlabeled)', 0)} |")
    lines.append("")

    lines.append("## Label distribution per domain, spread ACROSS REPS (min-max, not a mean)")
    lines.append(f"Task: {', '.join(tasks)} (pooled across the stacks/conditions above).")
    lines.append("")
    lines.append("| domain | label | min | max | reps seen |")
    lines.append("|---|---|---|---|---|")
    for d, labels in sorted(m["label_distribution"].items()):
        for label, v in sorted(labels.items()):
            lines.append(f"| {d} | {label} | {v['min']} | {v['max']} | {v['n_reps_seen']} |")
    lines.append("")

    lines.append("## Parse-failure rate")
    lines.append("Fraction of calls whose fenced claims JSON block was missing or malformed.")
    lines.append("Never repaired — counted as-is. See stacks/trace_schema.py.")
    lines.append("")
    lines.append("| task\\|stack\\|condition | parse-failure rate | n |")
    lines.append("|---|---|---|")
    for k, v in sorted(m["parse_failure_rate"].items()):
        lines.append(f"| {_esc(k)} | {_fmt_pct(v['parse_failure_rate'])} | {v['n_calls']} |")
    lines.append("")

    lines.append("## Cost (estimated from actual usage)")
    lines.append("")
    for k, v in sorted(m["cost_usd_est"].items()):
        lines.append(f"- {k}: ${v:.4f}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()

    records = load_calls(a.run_id)
    canon = load_canon()
    m = compute_all(records, canon)

    outdir = ROOT / "results" / a.run_id
    (outdir / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
    (outdir / "summary.json").write_text(json.dumps(m, indent=2) + "\n")
    md = render_md(a.run_id, records, m)
    (outdir / "report.md").write_text(md)

    print(md)
    print(f"\nwrote {outdir / 'report.md'} and {outdir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
