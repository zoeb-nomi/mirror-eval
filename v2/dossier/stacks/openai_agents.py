"""
OpenAI stack — module is named openai_agents.py per spec, but this
implementation calls the Responses API's built-in `web_search` tool
directly, NOT the openai-agents SDK's Runner. Documented reasoning below and
repeated in ../INFRA_NOTES.md.

WHY Responses API over the Agents SDK:
  - The Agents SDK's Runner wraps the call in its own trace/span objects and
    a Runner.run() coroutine; getting raw per-call queries, fetched-URL lists
    and token usage back out means walking RunResult.new_items / the SDK's
    tracing processors, whose shape has changed across openai-agents minor
    versions. mirror-eval's src/engines.py already validated the Responses
    API's web_search tool shape against OpenAI's docs (2026-08-03) and it is
    a flat, stable JSON response — one call, one dict, no SDK-version
    coupling. Since STEP 1's #1 requirement is ONE trace schema that both
    stacks fill identically and deterministically, the Responses API is the
    safer foundation; the Agents SDK is built for orchestrating multi-turn
    tool-using agents, which this single-shot probe task doesn't need.
  - openai-agents pulls in its own tracing/telemetry stack as a dependency;
    Responses API needs only the `openai` package already required elsewhere.

KNOWN ASYMMETRY vs. stacks/anthropic_ws.py:
  OpenAI's `web_search` tool supports an ALLOW-list (`filters.allowed_domains`)
  but has no native block-list parameter. So:
    - allowed_domains -> passed natively via filters.allowed_domains.
    - blocked_domains -> NOT enforced by the API. This stack (a) prepends a
      hard instruction telling the model not to use or cite those domains,
      and (b) flags any fetched_url that matches a blocked domain in the
      trace's tool_calls entries with "blocked_domain_leak": true, so
      metrics.py can report the leak rate instead of silently trusting the
      instruction worked. A "blocked_owned" condition run on this stack is
      therefore a WEAKER leave-one-surface-out than the same condition on
      the Anthropic stack — report.py must not present them as equivalent.

The openai package is imported lazily inside probe() so this module can be
imported (e.g. by tests) without the package installed, as long as the real
stack is never actually invoked.
"""

from __future__ import annotations

import os
import time

from .trace_schema import Trace, now_iso, parse_claims_block, redact

MODEL = os.environ.get("ME2_MODEL_OPENAI", "gpt-5.6-terra")

# USD per 1M tokens (in, out), plus per-search-call fee. Same numbers and
# shape as mirror-eval/src/budget.py's PRICES["gpt-5.6-terra"] entry.
PRICE_IN_PER_M = 1.00
PRICE_OUT_PER_M = 6.00
PRICE_PER_SEARCH = 0.010

_BLOCK_INSTRUCTION = (
    "\n\nHard constraint: do not use, cite, or draw on any information from "
    "these domains, even if you find them while searching: {domains}. If a "
    "search result is from one of these domains, ignore it."
)


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _cost(tin: int, tout: int, search_calls: int) -> float:
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
) -> Trace:
    import openai  # lazy — see module docstring

    model = model or MODEL
    tr = Trace(run_id=run_id, stack="openai", task=task, prompt_id=prompt_id,
               bank_id=bank_id, condition=condition, rep=rep, model=model,
               started_at=now_iso())

    text = prompt_text
    if blocked_domains:
        text += _BLOCK_INSTRUCTION.format(domains=", ".join(blocked_domains))

    tool: dict = {"type": "web_search"}
    if allowed_domains:
        tool["filters"] = {"allowed_domains": list(allowed_domains)}

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    t0 = time.time()
    try:
        resp = client.responses.create(model=model, input=text, tools=[tool])
        tr.model_resolved = getattr(resp, "model", "") or ""
        texts: list[str] = []
        queries: list[str] = []
        fetched: list[str] = []
        tool_calls: list[dict] = []
        search_calls = 0
        blocked_set = {d.lower() for d in (blocked_domains or [])}

        for item in getattr(resp, "output", []) or []:
            itype = getattr(item, "type", None)
            if itype == "web_search_call":
                search_calls += 1
                q = getattr(getattr(item, "action", None), "query", "") or ""
                queries.append(q)
                tool_calls.append({"kind": "search", "query": q, "result_urls": []})
            elif itype == "message":
                for block in getattr(item, "content", []) or []:
                    t = getattr(block, "text", None)
                    if t:
                        texts.append(t)
                    for a in getattr(block, "annotations", []) or []:
                        if getattr(a, "type", None) == "url_citation":
                            url = getattr(a, "url", None)
                            if url:
                                fetched.append(url)
                                if tool_calls:
                                    tool_calls[-1]["result_urls"].append(url)
                                if _domain_of(url) in blocked_set:
                                    tool_calls[-1]["blocked_domain_leak"] = True

        tr.answer_text = "\n".join(texts).strip()
        tr.queries = queries
        tr.fetched_urls = Trace.dedupe_urls(fetched)
        tr.tool_calls = tool_calls

        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        tr.tokens = {"in": tin, "out": tout, "search_calls": search_calls}
        tr.cost_usd_est = round(_cost(tin, tout, search_calls), 6)

        parsed = parse_claims_block(tr.answer_text)
        tr.claims = parsed["claims"]
        tr.verdict = parsed["verdict"]
        tr.score = parsed["score"]
        tr.parse_ok = parsed["parse_ok"]

    except Exception as e:  # noqa: BLE001
        tr.error = redact(f"{type(e).__name__}: {e}")

    tr.ended_at = now_iso()
    return tr
