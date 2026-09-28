"""
The ONE trace schema every stack (anthropic_ws.py, openai_agents.py) and the
--mock stack must return. metrics.py and report.py read only this shape —
they never look at a provider's native response format.

Claim extraction is DETERMINISTIC and lives here, not in a stack module, so
both stacks parse identically:

    The task prompt (see prompts/build_prompts.py CLAIM_JSON_INSTRUCTION)
    asks the model to end its answer with one fenced JSON block:

        ```json
        {"claims": [{"text": "...", "source_url": "https://..." | null,
                      "label": "verified" | "claimed" | "unverifiable"}],
         "verdict": "...", "score": 0.0}
        ```

    parse_claims_block() reads ONLY that block — the last fenced block in the
    answer whose parsed JSON has a "claims" key. It never asks another model
    to "fix" a malformed block. A block that is missing, malformed, or has no
    "claims" key is a parse FAILURE: claims=[], verdict=None, score=None,
    parse_ok=False. metrics.py counts parse failures as their own rate; it
    never repairs or drops them silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Label = Literal["verified", "claimed", "unverifiable"]

# Appended verbatim to every generated task prompt (lookup and sourcing).
# Keeping this in one place means a wording change applies to the whole
# battery at once and every stack asks for the exact same shape.
CLAIM_JSON_INSTRUCTION = """

When you are done, end your reply with exactly one fenced JSON code block \
(```json ... ```) and nothing after it, shaped like this:

```json
{
  "claims": [
    {"text": "<one factual claim you made above>", "source_url": "<url you got it from, or null>", "label": "verified" | "claimed" | "unverifiable"}
  ],
  "verdict": "<one short sentence: your overall conclusion>",
  "score": <a number from 0.0 to 1.0, your own confidence in that verdict>
}
```

List every distinct factual claim you made in the answer above, one per \
array entry. label "verified" only if a fetched source directly states it, \
"claimed" if you are repeating what someone (e.g. the subject) says about \
themselves without independent confirmation, "unverifiable" if you could \
not find any source either way."""


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_claims_block(answer_text: str) -> dict[str, Any]:
    """
    Deterministic parse. Tries every fenced block in the answer, LAST first
    (a model that self-corrects usually puts the good one last), and returns
    the first one that is valid JSON with a "claims" key. No retry, no
    second model call, no partial recovery of a broken block.
    """
    blocks = _FENCE_RE.findall(answer_text or "")
    for raw in reversed(blocks):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and "claims" in d and isinstance(d["claims"], list):
            claims = []
            for c in d["claims"]:
                if not isinstance(c, dict):
                    continue
                label = c.get("label")
                if label not in ("verified", "claimed", "unverifiable"):
                    label = None
                claims.append({
                    "text": str(c.get("text", "")).strip(),
                    "source_url": c.get("source_url") or None,
                    "label": label,
                })
            score = d.get("score")
            if not isinstance(score, (int, float)):
                score = None
            return {
                "claims": claims,
                "verdict": (str(d["verdict"]).strip() if d.get("verdict") is not None else None),
                "score": float(score) if score is not None else None,
                "parse_ok": True,
            }
    return {"claims": [], "verdict": None, "score": None, "parse_ok": False}


# Secret scrubbing for anything that could carry a credential into
# calls.jsonl — in practice provider exception strings, which sometimes echo
# request headers or a key prefix. Order matters: the specific sk-ant- form
# first, then the generic sk- form, then bearer tokens, then the header name
# (with its value when one follows a ":" or "=").
_REDACTIONS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{16,}"),
    re.compile(r"x-api-key(?:[\"']?\s*[:=]\s*[\"']?)?\S*", re.IGNORECASE),
]


def redact(s: str | None) -> str | None:
    """Replaces credential-shaped substrings with "[REDACTED]". Applied to
    every exception string before it is stored on a Trace (and again in
    Trace.to_dict, as a backstop)."""
    if not s:
        return s
    for rx in _REDACTIONS:
        s = rx.sub("[REDACTED]", s)
    return s


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCall:
    kind: str                       # "search" | other server-tool kind
    query: str | None = None
    result_urls: list[str] = field(default_factory=list)


@dataclass
class Trace:
    run_id: str
    stack: str                      # "anthropic" | "openai" | "mock"
    task: str                       # "lookup" | "sourcing"
    prompt_id: str
    bank_id: str | None
    condition: str                  # "full" | "blocked_owned" | "blocked_linkedin"
    rep: int
    queries: list[str] = field(default_factory=list)
    fetched_urls: list[str] = field(default_factory=list)
    answer_text: str = ""
    claims: list[dict] = field(default_factory=list)
    verdict: str | None = None
    score: float | None = None
    parse_ok: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    tokens: dict = field(default_factory=lambda: {"in": 0, "out": 0, "search_calls": 0})
    cost_usd_est: float = 0.0
    started_at: str = ""
    ended_at: str = ""
    error: str | None = None
    model: str = ""
    model_resolved: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["error"] = redact(d["error"])  # backstop: nothing secret reaches calls.jsonl
        return d

    @staticmethod
    def dedupe_urls(urls: list[str]) -> list[str]:
        seen, out = set(), []
        for u in urls:
            u = (u or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out
