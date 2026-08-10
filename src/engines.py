"""
Engine adapters for MIRROR-EVAL.

Each adapter takes a question string and a mode ("search" | "knowledge") and returns
a normalised Probe result. The four provider APIs disagree about almost everything —
citation shape, how to disable search, how search is billed — so normalisation happens
here and nowhere else.

Model selection principle: pick the tier that proxies the DEFAULT CONSUMER PRODUCT,
not the most capable model. We are measuring what a recruiter sees, not what the
frontier can do.

API specs verified against official docs 2026-08-03.
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Literal

import httpx

Mode = Literal["search", "knowledge"]

TIMEOUT = httpx.Timeout(180.0, connect=15.0)

def _load_dotenv() -> None:
    """Load .env from the repo root into os.environ. No dependency, deliberately."""
    import pathlib as _pl
    f = _pl.Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.split("#")[0].strip().strip('"').strip("'")
        if k and v and not v.endswith("...") and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


# Model per engine. Principle: pick the tier that proxies the DEFAULT CONSUMER
# PRODUCT, not the most capable model. Override via env if a name drifts.
MODELS = {
    "chatgpt":    os.environ.get("ME_MODEL_OPENAI", "gpt-5.6-terra"),
    "claude":     os.environ.get("ME_MODEL_ANTHROPIC", "claude-sonnet-5"),
    "perplexity": os.environ.get("ME_MODEL_PERPLEXITY", "sonar"),
    "gemini":     os.environ.get("ME_MODEL_GEMINI", "gemini-flash-latest"),
}


RETRY_STATUS = {429, 500, 502, 503, 529}


def _post(url: str, tries: int = 4, **kw) -> httpx.Response:
    """
    Drop-in for httpx.post with backoff on transient failures.

    Two very different things both surface as 429: per-minute RATE limiting,
    which clears if you wait, and QUOTA exhaustion or a missing entitlement,
    which does not. Retrying the second only burns wall-clock, so we confirm it
    once and then let the caller record a clean error instead of stalling a
    whole wave behind a limit that will never lift.
    """
    r = None
    delay = 2.0
    for i in range(tries):
        r = httpx.post(url, **kw)
        if r.status_code in RETRY_STATUS and i < tries - 1:
            if r.status_code == 429 and "quota" in r.text.lower() and i >= 1:
                break
            ra = r.headers.get("retry-after", "")
            time.sleep(min(float(ra) if ra.replace(".", "", 1).isdigit() else delay, 30.0))
            delay *= 2
            continue
        break
    if r.status_code >= 400:
        # Attach the provider's response body to the exception. Without this the
        # error reads "Client error '400 Bad Request'" and says nothing about WHY —
        # which is exactly the failure this harness exists to stop other people
        # making. The body is where the diagnosis lives.
        try:
            detail = r.text[:600]
        except Exception:                                     # noqa: BLE001
            detail = "<unreadable body>"
        raise httpx.HTTPStatusError(
            f"HTTP {r.status_code} from {r.request.url.host}: {detail}",
            request=r.request, response=r)
    return r


@dataclass
class Probe:
    engine: str
    model: str
    mode: Mode
    prompt_id: str
    question: str
    answer: str = ""
    model_resolved: str = ""   # what the provider ACTUALLY served. Aliases repoint
                               # silently; this field is how a repoint gets caught.
    citations: list[dict] = field(default_factory=list)   # [{"url": ..., "title": ...}]
    search_performed: bool = False       # did the engine ACTUALLY search, not just get offered the tool
    usage: dict = field(default_factory=dict)
    error: str | None = None
    latency_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _dedupe(cites: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cites:
        u = (c.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "title": (c.get("title") or "").strip()})
    return out


# --------------------------------------------------------------------------- OpenAI
def probe_openai(question: str, mode: Mode, model: str | None = None) -> Probe:
    """OpenAI Responses API. Search via the built-in `web_search` tool."""
    model = model or MODELS["chatgpt"]
    p = Probe(engine="chatgpt", model=model, mode=mode, prompt_id="", question=question)
    body: dict = {"model": model, "input": question}
    if mode == "search":
        body["tools"] = [{"type": "web_search"}]

    t0 = time.time()
    try:
        r = _post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                     "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        p.model_resolved = d.get("model", "")
        texts, cites = [], []
        for item in d.get("output", []):
            if item.get("type") == "web_search_call":
                p.search_performed = True
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("text"):
                        texts.append(block["text"])
                    for a in block.get("annotations", []) or []:
                        if a.get("type") == "url_citation":
                            cites.append({"url": a.get("url"), "title": a.get("title")})
        p.answer = "\n".join(texts).strip()
        p.citations = _dedupe(cites)
        p.usage = d.get("usage", {})
    except Exception as e:                                    # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
    p.latency_s = round(time.time() - t0, 2)
    return p


# ------------------------------------------------------------------------ Anthropic
def probe_anthropic(question: str, mode: Mode, model: str | None = None) -> Probe:
    """Anthropic Messages API. Search via the `web_search` server tool (GA, no beta header)."""
    model = model or MODELS["claude"]
    p = Probe(engine="claude", model=model, mode=mode, prompt_id="", question=question)
    body: dict = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": question}],
    }
    if mode == "search":
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]

    t0 = time.time()
    try:
        r = _post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        p.model_resolved = d.get("model", "")
        texts, cites = [], []
        for block in d.get("content", []):
            if block.get("type") == "web_search_tool_result":
                p.search_performed = True
                for res in block.get("content", []) or []:
                    if isinstance(res, dict) and res.get("type") == "web_search_result":
                        cites.append({"url": res.get("url"), "title": res.get("title")})
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
                for c in block.get("citations", []) or []:
                    if c.get("url"):
                        cites.append({"url": c.get("url"), "title": c.get("title")})
        p.answer = "\n".join(texts).strip()
        p.citations = _dedupe(cites)
        p.usage = d.get("usage", {})
    except Exception as e:                                    # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
    p.latency_s = round(time.time() - t0, 2)
    return p


# ----------------------------------------------------------------------- Perplexity
def probe_perplexity(question: str, mode: Mode, model: str | None = None) -> Probe:
    """Perplexity Sonar (OpenAI-compatible). Only engine with an explicit `disable_search`."""
    model = model or MODELS["perplexity"]
    p = Probe(engine="perplexity", model=model, mode=mode, prompt_id="", question=question)
    body: dict = {"model": model, "messages": [{"role": "user", "content": question}]}
    if mode == "knowledge":
        body["disable_search"] = True
    else:
        body["web_search_options"] = {"search_context_size": "medium"}

    t0 = time.time()
    try:
        r = _post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}",
                     "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        p.model_resolved = d.get("model", "")
        p.answer = (d.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        results = d.get("search_results") or []
        if results:
            p.search_performed = True
            p.citations = _dedupe([{"url": s.get("url"), "title": s.get("title")} for s in results])
        elif d.get("citations"):
            p.search_performed = True
            p.citations = _dedupe([{"url": u, "title": ""} for u in d["citations"]])
        p.usage = d.get("usage", {})
    except Exception as e:                                    # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
    p.latency_s = round(time.time() - t0, 2)
    return p


# --------------------------------------------------------------------------- Gemini
_REDIRECT_CACHE: dict[str, str] = {}


def _resolve_grounding_uri(uri: str, title: str) -> str:
    """
    Gemini's grounding chunks return Google redirect URLs, not real ones. Left
    unresolved they would destroy the citation trail — the most valuable output
    this harness produces, since it is what turns a score into a fix list. Follow
    each redirect once and cache it; fall back to the chunk `title`, which Gemini
    populates with the source domain.
    """
    if "grounding-api-redirect" not in uri:
        return uri
    if uri in _REDIRECT_CACHE:
        return _REDIRECT_CACHE[uri]
    resolved = ""
    try:
        resolved = str(httpx.head(uri, follow_redirects=True,
                                  timeout=httpx.Timeout(20.0)).url)
    except Exception:                                         # noqa: BLE001
        resolved = ""
    if not resolved or "grounding-api-redirect" in resolved:
        resolved = f"https://{title}" if title and "." in title else uri
    _REDIRECT_CACHE[uri] = resolved
    return resolved


def probe_gemini(question: str, mode: Mode, model: str | None = None) -> Probe:
    """
    Google Gemini via `generateContent` with Google Search grounding.

    Not the Interactions API — that endpoint 404s on this project. The 2.5 line is
    preferred anyway: it is the only line with free-tier Search grounding.
    """
    model = model or MODELS["gemini"]
    p = Probe(engine="gemini", model=model, mode=mode, prompt_id="", question=question)
    body: dict = {"contents": [{"parts": [{"text": question}]}]}
    if mode == "search":
        body["tools"] = [{"google_search": {}}]

    t0 = time.time()
    try:
        r = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"],
                     "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        d = r.json()
        p.model_resolved = d.get("modelVersion", "")
        cand = (d.get("candidates") or [{}])[0]
        p.answer = "\n".join(part.get("text", "")
                             for part in cand.get("content", {}).get("parts", [])
                             if part.get("text")).strip()
        gm = cand.get("groundingMetadata") or {}
        if gm.get("webSearchQueries") or gm.get("groundingChunks"):
            p.search_performed = True
        cites = []
        for c in gm.get("groundingChunks") or []:
            w = c.get("web") or {}
            uri, title = w.get("uri", ""), w.get("title", "")
            if uri:
                cites.append({"url": _resolve_grounding_uri(uri, title), "title": title})
        p.citations = _dedupe(cites)
        p.usage = d.get("usageMetadata", {})
    except Exception as e:                                    # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
    p.latency_s = round(time.time() - t0, 2)
    return p


ENGINES = {
    "chatgpt":    (probe_openai,     "OPENAI_API_KEY"),
    "claude":     (probe_anthropic,  "ANTHROPIC_API_KEY"),
    "perplexity": (probe_perplexity, "PERPLEXITY_API_KEY"),
    "gemini":     (probe_gemini,     "GEMINI_API_KEY"),
}


def available_engines() -> list[str]:
    return [name for name, (_, key) in ENGINES.items() if os.environ.get(key)]


# ------------------------------------------------------------------ judge back-ends
# The judge runs on two model families so self-preference bias is measured rather
# than assumed. Claude judging Claude — one of the four systems under test — is a
# real conflict of interest, not a footnote.

def judge_anthropic(system: str, user: str, model: str | None = None,
                    out_meta: dict | None = None) -> str:
    model = model or os.environ.get("ME_JUDGE_ANTHROPIC", "claude-haiku-4-5")
    r = _post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 3000,
              # The system prompt (rubric + canon) is byte-identical on every judge
              # call, so cache it: reads bill at 0.1x. That discount is what lets the
              # judge run on Sonnet for the same money as Haiku.
              "system": [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": user}]},
        timeout=TIMEOUT,
    )
    d = r.json()
    if out_meta is not None:
        out_meta["model_requested"] = model
        out_meta["model_resolved"] = d.get("model", "")
    out = "".join(b.get("text", "") for b in d.get("content", [])
                  if b.get("type") == "text")
    if not out.strip():
        # An empty judge response is not a parse failure — it means the model
        # produced nothing usable, and the reason is in stop_reason / block types.
        # Surfacing that beats "no JSON:" with nothing after the colon.
        if d.get("stop_reason") == "max_tokens" and \
                any(b.get("type") == "thinking" for b in d.get("content", [])):
            raise ValueError(
                f"judge spent its entire token budget on extended thinking and "
                f"produced no answer. Use a model without default thinking "
                f"(claude-haiku-4-5) or raise max_tokens. usage={d.get('usage')}")
        raise ValueError(
            f"empty judge response · stop_reason={d.get('stop_reason')} "
            f"· blocks={[b.get('type') for b in d.get('content', [])]} "
            f"· usage={d.get('usage')} · raw={str(d)[:300]}")
    return out


def judge_gemini(system: str, user: str, model: str | None = None,
                 out_meta: dict | None = None) -> str:
    """generateContent (no grounding) — the judge must not search, only reason over canon."""
    model = model or os.environ.get("ME_JUDGE_GEMINI", "gemini-flash-latest")
    r = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={"systemInstruction": {"parts": [{"text": system}]},
              "contents": [{"parts": [{"text": user}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    if out_meta is not None:
        out_meta["model_requested"] = model
        out_meta["model_resolved"] = d.get("modelVersion", "")
    return "".join(p.get("text", "") for p in
                   d.get("candidates", [{}])[0].get("content", {}).get("parts", []))


JUDGES = {"claude": judge_anthropic, "gemini": judge_gemini}
JUDGE_KEYS = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def available_judges() -> list[str]:
    return [n for n, k in JUDGE_KEYS.items() if os.environ.get(k)]


# ------------------------------------------------------------------------ preflight
def preflight() -> int:
    """
    Verify every configured engine and judge answers a trivial call. Run this FIRST
    on a new machine — model names drift, keys lack credit, networks block hosts.
    Failing here costs one call; failing mid-run costs a wave.
    """
    print("engines")
    ok = True
    for name, (fn, key) in ENGINES.items():
        if not os.environ.get(key):
            print(f"  skip  {name:<11} ({key} not set)")
            continue
        for mode in ("knowledge", "search"):
            p = fn("Reply with exactly: OK", mode)          # type: ignore[arg-type]
            status = "ok  " if not p.error else "FAIL"
            if p.error:
                ok = False
            res = p.model_resolved or "?"
            pin = os.environ.get(f"ME_PIN_{name.upper()}")
            drift = bool(pin and p.model_resolved and p.model_resolved != pin)
            if drift:
                ok = False
                status = "FAIL"
            print(f"  {status}  {name:<11} {mode:<9} model={str(p.model or '?'):<22}"
                  f" -> {res:<26} {p.latency_s:>5.1f}s"
                  + (f"  {p.error[:300]}" if p.error else ""))
            if drift:
                print(f"        PIN MISMATCH: provider now serves {res!r}, pinned "
                      f"{pin!r}. The alias repointed — wave comparability is broken. "
                      f"Do not run a wave until this is resolved.")
    print("judges")
    for name in JUDGES:
        if not os.environ.get(JUDGE_KEYS[name]):
            print(f"  skip  {name} ({JUDGE_KEYS[name]} not set)")
            continue
        try:
            meta: dict = {}
            out = JUDGES[name]("Reply with JSON only.", 'Return {"ok":true}',
                               out_meta=meta)
            res = meta.get("model_resolved") or "?"
            pin = os.environ.get(f"ME_PIN_JUDGE_{name.upper()}")
            if pin and meta.get("model_resolved") and meta["model_resolved"] != pin:
                ok = False
                print(f"  FAIL  {name:<11} PIN MISMATCH: provider now serves "
                      f"{res!r}, pinned {pin!r}")
            else:
                print(f"  ok    {name:<11} serves {res:<26} -> {out.strip()[:40]}")
        except Exception as e:                                # noqa: BLE001
            ok = False
            print(f"  FAIL  {name:<11} {type(e).__name__}: {str(e)[:110]}")
    return 0 if ok else 1
