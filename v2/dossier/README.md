# mirror-eval v2 "Prompt B" — the agentic dossier harness

## What this is

mirror-eval (v1) asks four consumer AI engines fixed questions about Zoeb
and grades the answers against a hand-written answer key (`canon.yaml`).
This is a different, complementary check: what happens when an
**agentic, web-search-capable** model is given the exact kind of prompt a
real recruiter would paste in — pulled from a corpus of real recruiter
prompts (`recruiter-prompt-bank`), not written for this harness — and asked
to research or source a candidate matching Zoeb's profile?

Two tasks:
- **lookup** — "who is this candidate, is he worth a screen, verify this
  claim" style prompts, framed around Zoeb by name.
- **sourcing** — "write me a Boolean search string for an AI Product
  Manager" style prompts, filled in with the exact role Zoeb is targeting.
  The question here isn't whether the model gets facts right — it's
  whether Zoeb's own site or GitHub ever surfaces at all when an engine
  sources for the role he wants.

Two stacks (Anthropic web search, OpenAI web search), three conditions per
prompt (`full`, `blocked_owned`, `blocked_linkedin` — leave-one-surface-out:
what happens when the model can't reach zoebnomi.com/GitHub, or can't reach
LinkedIn).

## Uploading via the GitHub web UI

This folder is meant to be dragged into `mirror-eval` at `v2/dossier/` through
the GitHub web UI. It ships **no dotfiles** (the web uploader skips them): the
env template is `env.example`, and there is no `.gitignore` here because the
repo root's `.gitignore` does that job.

I checked the root `.gitignore` of `zoeb-nomi/mirror-eval` (fresh shallow
clone of the default branch). The relevant lines today:

```
.env
.venv/
__pycache__/
*.pyc
results/*/
!results/.gitkeep
```

- `.env`, `.venv/`, `__pycache__/`, `*.pyc` have no slash in the middle, so
  git matches them at any depth. They already cover `v2/dossier/`.
- `results/*/` contains a slash, so git anchors it to the repo root. It
  covers `results/<x>/` at the top level only, **not**
  `v2/dossier/results/<run_id>/`.

**ACTION NEEDED (one time, before or right after the upload):** open the
repo root `.gitignore` in the web editor and add these two lines verbatim at
the bottom, so run output and keys under `v2/dossier/` can never be committed:

```
v2/dossier/results/*/
v2/dossier/.env
```

(`.env` is already covered by the existing root rule; the explicit line is
belt and braces.) `results/README.md` is a plain placeholder so the otherwise
empty `results/` folder survives the upload; the `results/*/` pattern ignores
run folders but not that file. Never upload a `.env` or a `results/<run_id>/`
folder; publish results deliberately with the lift report.

## Why no LLM judge

Every metric in `metrics.py` is a count, a URL-membership check, or a
min/max — never another model's opinion of whether an answer was good. A
judge model scoring "was this accurate" just reintroduces the
unverified-claim problem this harness measures. Claim extraction is
deterministic too (see `stacks/trace_schema.py`): the task prompt asks the
model to end with one fenced JSON block, and the parser reads only that
block. A malformed or missing block is a **parse failure**, counted and
reported — never repaired by asking another model to fix it.

## How to run it (on your Mac)

```
make setup            # venv + deps + .env copied from env.example
# open .env, paste ANTHROPIC_API_KEY and/or OPENAI_API_KEY
make prompts           # regenerate prompts/lookup.jsonl, prompts/sourcing.jsonl (no keys needed)
make test               # full test suite, --mock only, no network
make pilot               # REAL: Anthropic, lookup, 3 reps, full+blocked_owned — prints cost, asks to confirm
```

For a full run, call `run.py` directly with the conditions/reps/task/stack
you want, e.g.:

```
.venv/bin/python run.py --stack anthropic --task lookup \
  --condition full,blocked_owned,blocked_linkedin --reps 10 --yes
```

Then:

```
.venv/bin/python report.py --run-id <run_id>
```

`run_id` defaults to a deterministic label built from your arguments
(`<stack>_<task>_<conditions>_reps<N>_<pilot|full>`) — printed at the start
of the run, and reused automatically by resumability.

## Resumability

Every call is written to `results/<run_id>/calls.jsonl` the instant it
completes (append, flush). Re-running the **exact same command** reuses the
same `run_id` and skips any `(prompt_id, condition, rep)` already recorded
without error — so an interrupted run (closed laptop, dead network) picks up
exactly where it left off. Nothing special to remember: just run the same
command again.

A `--run-id` belongs to one task and one stack. Reusing it for a different
task or stack exits with code 2 and a clear error before anything is written.
`manifest.json` is written only after the cost confirmation succeeds (or in
`--mock`), so a declined or refused run leaves no `results/<run_id>/` behind.
Metrics and the report group by (task, stack, condition).

## Cost estimate formula

Before any real (non-`--mock`) call, `run.py` prints:

```
estimated_cost = n_calls * (
    (800 / 1e6) * price_in[stack] +
    (500 / 1e6) * price_out[stack] +
     3           * price_search[stack]
)
```

800 input / 500 output tokens and 3 search calls are conservative round
numbers for a lookup/sourcing prompt with the JSON-instruction tail and a
couple of search round-trips — **a planning number, not a measurement**.
The real, usage-based total is tracked live from each response and printed
as SPEND at the end of the run; trust that number over the estimate. You
must pass `--yes` or answer "y" at the prompt before any paid call is made.

## What each metric means

- **owned_surface_hit_rate** — did zoebnomi.com or a `github.com/zoeb-nomi`
  repo get fetched at all, per stack×condition. The headline "does he show
  up on his own terms" number.
- **hops_to_truth** — position in the fetched-URL list of the first URL
  that's an owned surface or a canon `verifying_url`. Lower is better; a
  `no_hit_rate` of 1.0 means the run never reached one.
- **field_accuracy** — match rule (`match_all` AND `match_any`; capitalised
  tokens case-sensitive whole-word, others case-insensitive; see
  `canon/README.md`, "Match rules") against canon fields with
  `truth: true` **only**. Claim fields (`truth: false`) are never scored
  right/wrong — there's nothing independent to check them against yet. See
  `canon/README.md`.
- **provenance_matrix** — every claim's source domain × label
  (verified/claimed/unverifiable), summed across the whole run.
- **label_distribution** — the same, but per domain×label reported as
  **(min, max) across reps**, deliberately never averaged into one number:
  a mean of 1.5 verified claims/rep hides whether that's "always 1 or 2" or
  "0 half the time, 3 the other half," and that difference is the finding.
- **parse_failure_rate** — fraction of calls whose fenced claims JSON block
  was missing or malformed.
- **cost_usd_est** — summed from each call's actual usage.

## Known limitations

- **blocked_domains is asymmetric between stacks.** Anthropic's
  `web_search_20250305` tool accepts `blocked_domains` natively. OpenAI's
  `web_search` tool only exposes an allow-list (`filters.allowed_domains`)
  — there's no block-list parameter. `stacks/openai_agents.py` compensates
  with a hard prompt instruction plus post-hoc flagging of any fetched URL
  that matches a blocked domain (`tool_calls[].blocked_domain_leak`), but
  that's a weaker guarantee than the Anthropic stack's. Don't present the
  two stacks' `blocked_owned`/`blocked_linkedin` results as equivalent —
  check the leak flags first.
- **Domain blocking is domain-level, not path-level.** `blocked_owned`
  blocks all of `github.com`, not just `github.com/zoeb-nomi` — neither
  provider's API can block by path. For this battery (everything is about
  one named person) a GitHub hit during that condition is almost certainly
  about him, but it's worth knowing the block is blunter than the intent.
- **Most canon fields are `claim`, not `truth`, right now.** Restructuring
  mirror-eval's already-vetted facts into individual fields didn't include
  re-verifying each one against a fresh third-party URL — see
  `canon/README.md` for why, and which fields are flagged `# TODO(zoeb)` as
  likely-real.
- **Sourcing-task claims/verdict/score are informational, not scored.**
  The JSON instruction is appended to sourcing prompts for trace-schema
  uniformity, but "claims" about a Boolean search string aren't a
  meaningful accuracy signal — `queries[]` and `fetched_urls[]` are the
  real sourcing-task output. See `prompts/build_report.md`.
- **No live UI.** screener-eval has a full local control-surface
  (`ui/server.py`) for a non-technical user; this build is CLI-only per
  spec. If Zoeb wants one later, the pattern to copy is documented in
  `../INFRA_NOTES.md`.
