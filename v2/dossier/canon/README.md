# canon/ — the answer key

`canon.yaml` holds one record per checkable fact about Zoeb (`fields:`), plus
three lookup lists (`stale_or_false`, `poisoned_sources`, `owned_sources` /
`self_published_sources`, `irrelevant_source_patterns`) that `metrics.py`
uses for provenance labeling. `canon_slots.yaml` holds the four values used
to fill SOURCING-task placeholders (see `prompts/build_prompts.py`).

## The truth/claim rule

Every field in `fields:` carries `truth: true` or `truth: false`.

- **`truth: true`** requires a `verifying_url` that is a real, currently
  working page, that is **not** `zoebnomi.com` and **not** a
  `github.com/zoeb-nomi/...` path, and that independently states the fact —
  i.e. a third party wrote it, not Zoeb.
- **`truth: false`** ("claim") is everything else: no URL at all, or a URL
  that is `zoebnomi.com`, a `zoeb-nomi` GitHub repo, or his own LinkedIn copy.
  Self-published is still self-published even when the platform is a
  household name — LinkedIn content is authored and edited by the profile
  owner, so it doesn't clear the bar.

This is a deliberately strict bar. A recruiter-facing engine that repeats
something Zoeb said about himself is not "verified" — it is quoting a
source with a motive. The harness only counts a fact as independently
checkable when someone with no stake in the outcome said it first.

## Why most fields are `claim` right now

This build reused mirror-eval's already-vetted `verified_claims` list
(`../study/mirror-eval/canon.yaml` at build time — see `../INFRA_NOTES.md`)
and restructured it into individual fields, but did not go relink every fact
to a fresh, confirmed third-party URL — that's a research task, not an infra
one, and a wrong or dead "verifying" URL shipped as `truth: true` is worse
than an honest `claim`. One field (`current_company`) reuses a URL
mirror-eval's own canon already vetted as non-owned
(`rocketreach.co/zoeb-nomi-email_380343494`).

Fields marked `# TODO(zoeb)` are ones where a real third-party URL plausibly
exists — the Keka Product Medium profile piece, a Keka BGV/Exit
announcement or public video — but the exact link isn't confirmed in this
build. Paste it in and flip `truth: true` yourself; the harness will not
guess a URL for you, and `metrics.py` treats an unlinked field as `claim`
whatever the prose says.

## Match rules (how `truth: true` fields are scored)

`metrics.field_accuracy()` scores an answer 1 or 0 per truth field. A field
carries a rule made of two token lists:

- `match_all` — **every** token must match, AND
- `match_any` — **at least one** token must match (skipped if the list is
  empty).

Per-token matching:

- A token that **starts with a capital letter** is a proper noun. It is
  matched **case-sensitively, as a whole word** (regex `\b` on both sides).
- Any other token is a **case-insensitive substring** match.

`current_company` uses `match_all: ["Instead"]` and
`match_any: ["instead.com", "tax", "Instead Inc", "Instead, a"]`. The company
is called "Instead", an ordinary English word, so a bare keyword hit would
score "I did X instead of Y" as correct. The two-token rule requires the
capitalised proper noun *and* a second, company-specific signal (the domain,
"tax", or the company-name phrasing). "Product Manager at Instead
(instead.com)" scores 1; "I did X instead of Y" scores 0 (lowercase, so the
case-sensitive token misses). Unit tests in `tests/test_metrics.py` pin both.

Known residual limit: a sentence that *starts* with "Instead" (capitalised
by position) and also mentions "tax" would score 1. The rule cuts the common
false positive (lowercase "instead of") to zero, not every conceivable one.

A legacy `match_terms` list, if present, is treated as `match_any`. A truth
field with no rule at all is reported as not scored, never as 0.

## Adding a field

One fact per field. Keep `text` short enough to be a single accuracy check
(a model either says this or it doesn't) — a field that bundles two claims
can't be scored cleanly. If a fact only matters conditionally on another
field's value, that's two fields, not one with a footnote.
