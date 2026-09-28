"""
Deterministic fake stack. No network, no keys, no randomness (rep/prompt_id
seed the "answer" so the same job always produces the same fake trace) — used
by `run.py --mock` and every test in tests/.

Produces a well-formed claims JSON block every time so metrics.py and
report.py have real data to compute over, plus a rare deterministic
parse-failure case (when prompt_id ends in a digit that is 0 mod 7) so the
parse-failure-rate metric has something non-zero to count in tests.
"""

from __future__ import annotations

import hashlib

from .trace_schema import Trace, now_iso, parse_claims_block

MODEL = "mock-stack-v1"


def _seeded_bool(*parts: str) -> bool:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:2], 16) % 7 == 0


def probe(
    prompt_text: str,
    *,
    run_id: str,
    task: str,
    prompt_id: str,
    bank_id: str | None,
    condition: str,
    rep: int,
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    model: str | None = None,
) -> Trace:
    tr = Trace(run_id=run_id, stack="mock", task=task, prompt_id=prompt_id,
               bank_id=bank_id, condition=condition, rep=rep, model=MODEL,
               model_resolved=MODEL, started_at=now_iso())

    owned = "https://zoebnomi.com/"
    third_party = "https://example-recruiter-blog.test/zoeb-nomi-review"
    blocked = set(blocked_domains or [])

    urls = [owned, third_party]
    if "zoebnomi.com" in blocked:
        urls = [u for u in urls if "zoebnomi.com" not in u]

    tr.queries = [f"{prompt_id} query 1", f"{prompt_id} query 2"]
    tr.fetched_urls = urls
    tr.tool_calls = [{"kind": "search", "query": q, "result_urls": urls} for q in tr.queries]
    tr.tokens = {"in": 750, "out": 420, "search_calls": len(tr.queries)}
    tr.cost_usd_est = 0.0

    if _seeded_bool(run_id, prompt_id, condition, str(rep)):
        # Deterministic parse-failure case: no fenced block at all.
        tr.answer_text = f"[mock:{condition}] Answer for {prompt_id} rep {rep}, no JSON block."
    else:
        tr.answer_text = (
            f"[mock:{condition}] Answer for {prompt_id} rep {rep}. Zoeb Nomi is a "
            f"Product Manager at Instead (instead.com).\n\n```json\n"
            f'{{"claims": [{{"text": "Zoeb Nomi is a Product Manager at Instead", '
            f'"source_url": "{third_party}", "label": "verified"}}], '
            f'"verdict": "worth a screen", "score": 0.7}}\n```'
        )

    parsed = parse_claims_block(tr.answer_text)
    tr.claims = parsed["claims"]
    tr.verdict = parsed["verdict"]
    tr.score = parsed["score"]
    tr.parse_ok = parsed["parse_ok"]
    tr.ended_at = now_iso()
    return tr
