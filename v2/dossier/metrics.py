#!/usr/bin/env python3
"""
Counted metrics over results/<run_id>/calls.jsonl. NO model judge anywhere
in this file — every number below is a count, a set-membership check, or a
min/max, never an LLM's opinion of another LLM's answer. That is a design
constraint of mirror-eval v2, not an oversight: a judge model scoring
"was this accurate" reintroduces exactly the unverified-claim problem this
harness exists to measure.

    python metrics.py --run-id <run_id>

Computes, per task x stack x condition (and overall):
  - owned_surface_hit_rate   any fetched_url on an owned surface (zoebnomi.com,
                              github.com/zoeb-nomi/...)
  - provenance_matrix        claim label counts x source domain
  - field_accuracy           exact/keyword match vs canon TRUTH fields only
  - hops_to_truth            index of first fetched_url that is a verifying_url
                              or an owned surface (min/max/no-hit rate)
  - label_distribution       verified/claimed/unverifiable per source domain,
                              reported as (min, max) ACROSS REPS — never a
                              single point estimate, because a mean across
                              reps hides exactly the run-to-run variance this
                              metric exists to surface
  - parse_failure_rate       fraction of calls where the fenced claims JSON
                              block was missing/malformed
  - cost                     sum of cost_usd_est
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import defaultdict
from urllib.parse import urlparse

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CANON_PATH = ROOT / "canon" / "canon.yaml"


def load_calls(run_id: str) -> list[dict]:
    path = ROOT / "results" / run_id / "calls.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no calls.jsonl at {path}")
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_canon() -> dict:
    return yaml.safe_load(CANON_PATH.read_text())


def _domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _is_owned(url: str, owned_sources: list[str]) -> bool:
    """owned_sources entries are 'domain' or 'domain/path-prefix'."""
    d = _domain(url)
    path = urlparse(url).path.lstrip("/") if url else ""
    for entry in owned_sources:
        parts = entry.split("/", 1)
        edom = parts[0]
        eprefix = parts[1] if len(parts) > 1 else ""
        if d == edom and (not eprefix or path.startswith(eprefix)):
            return True
    return False


def _verifying_urls(canon: dict) -> set[str]:
    return {f["verifying_url"] for f in canon.get("fields", []) if f.get("truth") and f.get("verifying_url")}


def _group(records: list[dict]) -> dict[tuple, list[dict]]:
    """Group by (task, stack, condition). Task is part of the key so a
    lookup run and a sourcing run can never be silently pooled."""
    g = defaultdict(list)
    for r in records:
        g[(r.get("task") or "?", r["stack"], r["condition"])].append(r)
    return g


def _gkey(task: str, stack: str, cond: str) -> str:
    return f"{task}|{stack}|{cond}"


def owned_surface_hit_rate(records: list[dict], canon: dict) -> dict:
    owned = canon.get("owned_sources", [])
    out = {}
    for (task, stack, cond), rs in _group(records).items():
        hits = sum(1 for r in rs if any(_is_owned(u, owned) for u in r.get("fetched_urls", [])))
        out[_gkey(task, stack, cond)] = {"hit_rate": round(hits / len(rs), 4) if rs else None, "n_calls": len(rs)}
    return out


def provenance_matrix(records: list[dict]) -> dict:
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        for c in r.get("claims", []):
            d = _domain(c.get("source_url")) or "(no source)"
            label = c.get("label") or "(unlabeled)"
            matrix[d][label] += 1
    return {d: dict(labels) for d, labels in matrix.items()}


def _token_matches(token: str, text: str) -> bool:
    """One match token against the answer text.

    A token starting with a capital letter is a proper noun: case-SENSITIVE,
    whole-word (regex \\b on both sides), so "Instead" matches "at Instead
    (" but never the English "instead of". Every other token is a
    case-insensitive substring ("instead.com", "tax")."""
    if token[:1].isupper():
        return re.search(r"\b" + re.escape(token) + r"\b", text) is not None
    return token.lower() in text.lower()


def field_terms(f: dict) -> tuple[list[str], list[str]]:
    """(match_all, match_any) for a canon field. Legacy `match_terms` is
    treated as match_any."""
    all_t = list(f.get("match_all") or [])
    any_t = list(f.get("match_any") or f.get("match_terms") or [])
    return all_t, any_t


def score_field(f: dict, text: str) -> bool:
    """1 iff EVERY match_all token matches AND (match_any is empty OR at
    least one match_any token matches). Caller guarantees at least one of the
    two lists is non-empty."""
    all_t, any_t = field_terms(f)
    text = text or ""
    if not all(_token_matches(t, text) for t in all_t):
        return False
    if any_t and not any(_token_matches(t, text) for t in any_t):
        return False
    return True


def field_accuracy(records: list[dict], canon: dict) -> dict:
    """Only fields with truth: true are scored. A field with no match rule
    is skipped and reported as such — never silently scored 0. Rules:
    match_all AND match_any, capitalised tokens case-sensitive whole-word,
    others case-insensitive (see canon/README.md, "Match rules")."""
    truth_fields = [f for f in canon.get("fields", []) if f.get("truth")]
    out = {}
    for f in truth_fields:
        all_t, any_t = field_terms(f)
        if not all_t and not any_t:
            out[f["id"]] = {"scored": False, "reason": "no match rule defined"}
            continue
        per_group = {}
        for (task, stack, cond), rs in _group(records).items():
            hits = sum(1 for r in rs if score_field(f, r.get("answer_text") or ""))
            per_group[_gkey(task, stack, cond)] = {
                "accuracy": round(hits / len(rs), 4) if rs else None, "n_calls": len(rs)}
        out[f["id"]] = {"scored": True, "match_all": all_t, "match_any": any_t, "by_group": per_group}
    return out


def hops_to_truth(records: list[dict], canon: dict) -> dict:
    owned = canon.get("owned_sources", [])
    verifying = _verifying_urls(canon)

    def _hop(r: dict) -> int | None:
        for i, u in enumerate(r.get("fetched_urls", []), start=1):
            if _is_owned(u, owned) or u in verifying:
                return i
        return None

    out = {}
    for (task, stack, cond), rs in _group(records).items():
        hops = [_hop(r) for r in rs]
        found = [h for h in hops if h is not None]
        out[_gkey(task, stack, cond)] = {
            "n_calls": len(rs),
            "n_found": len(found),
            "no_hit_rate": round(1 - len(found) / len(rs), 4) if rs else None,
            "min_hop": min(found) if found else None,
            "max_hop": max(found) if found else None,
        }
    return out


def label_distribution(records: list[dict]) -> dict:
    """(domain, label) -> counts PER REP, reduced to (min, max) across reps.
    No mean, no single point estimate — see module docstring."""
    per_rep: dict[tuple[str, str], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        rep = r.get("rep")
        for c in r.get("claims", []):
            d = _domain(c.get("source_url")) or "(no source)"
            label = c.get("label") or "(unlabeled)"
            per_rep[(d, label)][rep] += 1

    out: dict[str, dict] = {}
    for (d, label), by_rep in per_rep.items():
        counts = list(by_rep.values())
        out.setdefault(d, {})[label] = {"min": min(counts), "max": max(counts), "n_reps_seen": len(counts)}
    return out


def parse_failure_rate(records: list[dict]) -> dict:
    out = {}
    for (task, stack, cond), rs in _group(records).items():
        fails = sum(1 for r in rs if not r.get("parse_ok") and not r.get("error"))
        out[_gkey(task, stack, cond)] = {"parse_failure_rate": round(fails / len(rs), 4) if rs else None, "n_calls": len(rs)}
    return out


def cost(records: list[dict]) -> dict:
    out = {}
    total = 0.0
    for (task, stack, cond), rs in _group(records).items():
        c = sum(r.get("cost_usd_est", 0.0) for r in rs)
        total += c
        out[_gkey(task, stack, cond)] = round(c, 6)
    out["TOTAL"] = round(total, 6)
    return out


def compute_all(records: list[dict], canon: dict) -> dict:
    errored = [r for r in records if r.get("error")]
    ok = [r for r in records if not r.get("error")]
    return {
        "n_calls_total": len(records),
        "n_errored": len(errored),
        "n_ok": len(ok),
        "owned_surface_hit_rate": owned_surface_hit_rate(ok, canon),
        "provenance_matrix": provenance_matrix(ok),
        "field_accuracy": field_accuracy(ok, canon),
        "hops_to_truth": hops_to_truth(ok, canon),
        "label_distribution": label_distribution(ok),
        "parse_failure_rate": parse_failure_rate(ok),
        "cost_usd_est": cost(records),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()

    records = load_calls(a.run_id)
    canon = load_canon()
    result = compute_all(records, canon)

    out_path = ROOT / "results" / a.run_id / "metrics.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
