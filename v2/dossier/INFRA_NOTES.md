# INFRA_NOTES — what this build reused, and what it changed

Studied read-only (STEP 0), cloned to `../study/`:
`mirror-eval`, `crosssource`, `screener-eval`, `recruiter-prompt-bank`.

**Reused, unchanged in shape:**
- `results/<run_id>/calls.jsonl` append-and-flush, `(key) not in done_keys(path)`
  resumability — from `mirror-eval/run_eval.py` (`raw_results.jsonl`) and
  `screener-eval/scripts/run_screen.py` (`calls.jsonl`, whose name this build
  copies directly).
- `.env` loaded by a hand-rolled parser (no `python-dotenv`) —
  `mirror-eval/src/engines.py::_load_dotenv`.
- Manifest-per-run with a sha256 of the frozen prompt/battery file —
  `mirror-eval/run_eval.py`'s `manifest.json`.
- The Anthropic `web_search_20250305` tool call/parse shape (`server_tool_use`,
  `web_search_tool_result`, citation blocks) — `mirror-eval/src/engines.py::probe_anthropic`,
  verified there against Anthropic's docs 2026-08-03; `stacks/anthropic_ws.py` copies it.
- Retry-with-backoff on transient HTTP codes, budget/spend tracking shape —
  `mirror-eval/src/budget.py`.
- `Makefile` targets (`setup`, `prompts`≈`variants`, `test`, `pilot`) —
  `screener-eval/Makefile`.
- Canon-as-answer-key pattern, `# TODO(zoeb)`-style unverified-field flags —
  `mirror-eval/canon.yaml`'s own `# VERIFY` convention.

**Changed deliberately:**
- `canon.yaml` restructured from one `verified_claims` list into per-field
  records (`truth`/`verifying_url`), since v2 needs to score fields
  individually, not the whole dossier as one blob.
- No LLM judge anywhere (`mirror-eval/src/judge.py` uses one); v2's claim
  extraction is a deterministic fenced-JSON-block parser instead
  (`stacks/trace_schema.py`).
- No local UI (`screener-eval/ui/server.py`, 1000+ lines) — out of scope per
  spec; CLI only.
- One shared trace schema across two provider stacks, vs. mirror-eval's four
  engine-specific `Probe` shapes normalized post-hoc.

**Note on canon's non-owned verifying source:** mirror-eval's `canon.yaml`
`clean_sources` lists `rocketreach.co/zoeb-nomi-email_380343494` as the one
entry not on zoebnomi.com/GitHub/LinkedIn — flagged there as
"self-corrected; now accurate." Reused as `canon/canon.yaml`'s one
`truth: true` field's `verifying_url`.
