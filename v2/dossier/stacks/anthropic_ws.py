"""
Anthropic stack — Messages API with the `web_search_20250305` server tool
(GA, no beta header; same tool shape mirror-eval's src/engines.py already
verified against official docs and uses in production — see
../INFRA_NOTES.md for what was reused from there vs. changed here).

Supports blocked_domains / allowed_domains natively — the web_search tool
accepts both directly in the tool spec, so a leave-one-surface-out condition
(e.g. "blocked_owned" = zoebnomi.com + github.com/zoeb-nomi) is a single
parameter, not a prompt-level workaround. This is NOT symmetric with the
OpenAI stack; see stacks/openai_agents.py's module docstring.

The anthropic package is imported lazily inside probe() so this module can
be imported (e.g. by tests) without the package installed, as long as the
real stack is never actually invoked.
"""

from __future__ import annotations

import os
import time

from .trace_schema import Trace, now_iso, parse_claims_block, redact

MODEL = os.environ.get("ME2_MODEL_ANTHROPIC", "claude-sonnet-5")

# USD per 1M tokens (in, out), plus per-search-call fee. Same shape and same
# claude-sonnet numbers as mirror-eval/src/budget.py's PRICES table — kept
# in sync deliberately since both harnesses probe the same model family.
PRICE_IN_PER_M = 2.00
PRICE_OUT_PER_M = 10.00
PRICE_PER_SEARCH = 0.010


def _cost(usage: dict, search_calls: int) -> float:
    tin = usage.get("input_tokens", 0) or 0
    tout = usage.get("output_tokens", 0) or 0
    return (tin / 1e6) * PRICE_IN_PER_M + (tout / 1e6) * PRICE_OUT_PER_M + search_calls * PRICE_PER_SEARCH


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
    max_uses: int = 6,
    max_tokens: int = 2048,
) -> Trace:
    import anthropic  # lazy — see module docstring

    model = model or MODEL
    tr = Trace(run_id=run_id, stack="anthropic", task=task, prompt_id=prompt_id,
               bank_id=bank_id, condition=condition, rep=rep, model=model,
               started_at=now_iso())

    tool: dict = {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}
    if blocked_domains:
        tool["blocked_domains"] = list(blocked_domains)
    if allowed_domains:
        tool["allowed_domains"] = list(allowed_domains)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=[tool],
            messages=[{"role": "user", "content": prompt_text}],
        )
        tr.model_resolved = getattr(resp, "model", "") or ""
        texts: list[str] = []
        queries: list[str] = []
        fetched: list[str] = []
        tool_calls: list[dict] = []
        search_calls = 0

        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "server_tool_use" and getattr(block, "name", "") == "web_search":
                search_calls += 1
                q = (getattr(block, "input", None) or {}).get("query", "")
                queries.append(q)
                tool_calls.append({"kind": "search", "query": q, "result_urls": []})
            elif btype == "web_search_tool_result":
                content = getattr(block, "content", None) or []
                urls = []
                if isinstance(content, list):
                    for res in content:
                        url = getattr(res, "url", None) if not isinstance(res, dict) else res.get("url")
                        if url:
                            urls.append(url)
                            fetched.append(url)
                if tool_calls:
                    tool_calls[-1]["result_urls"] = urls
            elif btype == "text":
                texts.append(getattr(block, "text", "") or "")

        tr.answer_text = "\n".join(texts).strip()
        tr.queries = queries
        tr.fetched_urls = Trace.dedupe_urls(fetched)
        tr.tool_calls = tool_calls

        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        tr.tokens = {"in": tin, "out": tout, "search_calls": search_calls}
        tr.cost_usd_est = round(_cost({"input_tokens": tin, "output_tokens": tout}, search_calls), 6)

        parsed = parse_claims_block(tr.answer_text)
        tr.claims = parsed["claims"]
        tr.verdict = parsed["verdict"]
        tr.score = parsed["score"]
        tr.parse_ok = parsed["parse_ok"]

    except Exception as e:  # noqa: BLE001 — one clean error field, never a crash mid-wave
        tr.error = redact(f"{type(e).__name__}: {e}")

    tr.ended_at = now_iso()
    return tr
