#!/usr/bin/env python3
"""
Builds prompts/lookup.jsonl and prompts/sourcing.jsonl from the vendored
recruiter-prompt-bank snapshot (prompts/bank_snapshot.jsonl), deterministically.

    python prompts/build_prompts.py

Writes lookup.jsonl, sourcing.jsonl, build_report.md. Re-running with the
same bank_snapshot.jsonl and canon_slots.yaml produces byte-identical output
(no randomness, no model calls) — this is a data-transform script, not a
probe.

--- LOOKUP task -------------------------------------------------------------
Select bank records where kind == "lookup_prompt" and has_placeholder ==
false, and whose text is about researching a named/online candidate: the
lowercased text contains at least one of LOOKUP_KEYWORDS. Selected records
get the fixed FRAMING_LINE prepended and CLAIM_JSON_INSTRUCTION appended —
no other edit. This is deliberately conservative: a keyword match on
"candidate"/"LinkedIn"/etc. over-selects a little (some hits are about
resume parsing, not online lookup) rather than a hand-curated list that
can't be regenerated the same way twice.

One additional record is always appended: a paraphrase-free "name-only"
control — literally the entity's name with the JSON instruction and nothing
else, framing-line included per the FRAMING policy below. This is the F0-
style baseline mirror-eval's battery.yaml uses (see ../INFRA_NOTES.md):
the purest read on what a search-augmented model already associates with
the bare name, with no bank-derived wording in the way.

--- SOURCING task -----------------------------------------------------------
Select bank records where kind == "sourcing_query" and has_placeholder ==
true. Each `[bracketed placeholder]` is looked up (case-insensitively) in
PLACEHOLDER_MAP below. If EVERY placeholder in a record maps to a canon
slot, the record is filled and kept; if ANY placeholder has no mapping, the
whole record is skipped and the reason logged — no partial fills, since a
half-filled Boolean string is not a query anyone would actually run.

Multiple placeholders mapping to the same "skills" slot within one record
(e.g. [skill1] and [skill2]) are filled by splitting canon_slots.yaml's
comma-separated `skills` value and assigning parts in order, cycling if a
record has more skills-placeholders than parts. This is the one place a
single slot value fans out to several substitutions; every other slot
(title, location, seniority) substitutes its whole value at every
occurrence in a record.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stacks.trace_schema import CLAIM_JSON_INSTRUCTION  # noqa: E402

import yaml  # noqa: E402

BANK_PATH = pathlib.Path(__file__).resolve().parent / "bank_snapshot.jsonl"
CANON_SLOTS_PATH = ROOT / "canon" / "canon_slots.yaml"
OUT_LOOKUP = pathlib.Path(__file__).resolve().parent / "lookup.jsonl"
OUT_SOURCING = pathlib.Path(__file__).resolve().parent / "sourcing.jsonl"
OUT_REPORT = pathlib.Path(__file__).resolve().parent / "build_report.md"

ENTITY = "Zoeb Nomi"
# Deliberately names NO employer and NO URL: canon field `current_company`
# is scored by whether the model finds "Instead" itself, so the prompt must
# never hand it that answer (nor a starting URL that shortcuts retrieval).
FRAMING_LINE = f"Candidate: {ENTITY}, product manager, Bengaluru."

LOOKUP_KEYWORDS = (
    "candidate", "this person", "background", "profile", "verify",
    "red flag", "linkedin", "github", "portfolio", "online",
)

# placeholder inner-text (lowercased, as it appears inside [ ]) -> canon slot
PLACEHOLDER_MAP = {
    "job title": "title",
    "title": "title",
    "key skills/technologies": "skills",
    "keywords": "skills",
    "skill1": "skills",
    "skill2": "skills",
    "specific skill": "skills",
    "skill/tool": "skills",
    "location": "location",
    "location or 'remote'": "location",
}

_PLACEHOLDER_RE = re.compile(r"\[([^\]]+)\]")


def load_bank() -> list[dict]:
    records = []
    for line in BANK_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def build_lookup(records: list[dict]) -> tuple[list[dict], dict]:
    out = []
    matched, skipped_no_keyword = 0, 0
    for r in records:
        if r.get("kind") != "lookup_prompt" or r.get("has_placeholder"):
            continue
        text_l = (r.get("text") or "").lower()
        hits = [kw for kw in LOOKUP_KEYWORDS if kw in text_l]
        if not hits:
            skipped_no_keyword += 1
            continue
        matched += 1
        rendered = f"{FRAMING_LINE}\n\n{r['text']}{CLAIM_JSON_INSTRUCTION}"
        out.append({
            "prompt_id": f"lookup_{r['id']}",
            "bank_id": r["id"],
            "task": "lookup",
            "source_text": r["text"],
            "framing_line": FRAMING_LINE,
            "matched_keywords": hits,
            "rendered_text": rendered,
        })

    # paraphrase-free name-only control — bare name, no bank wording at all.
    control_rendered = f"{FRAMING_LINE}\n\n{ENTITY}{CLAIM_JSON_INSTRUCTION}"
    out.append({
        "prompt_id": "lookup_control_name_only",
        "bank_id": None,
        "task": "lookup",
        "source_text": ENTITY,
        "framing_line": FRAMING_LINE,
        "matched_keywords": [],
        "rendered_text": control_rendered,
    })

    total_candidates = sum(
        1 for r in records if r.get("kind") == "lookup_prompt" and not r.get("has_placeholder")
    )
    stats = {
        "candidates_considered": total_candidates,
        "selected_by_keyword": matched,
        "skipped_no_keyword_match": skipped_no_keyword,
        "control_added": 1,
        "total_written": len(out),
    }
    return out, stats


def build_sourcing(records: list[dict], slots: dict) -> tuple[list[dict], dict]:
    skills_parts = [p.strip() for p in slots["skills"].split(",") if p.strip()]
    out = []
    kept, skipped = 0, []

    for r in records:
        if r.get("kind") != "sourcing_query" or not r.get("has_placeholder"):
            continue
        text = r["text"]
        placeholders = _PLACEHOLDER_RE.findall(text)
        unmapped = [p for p in placeholders if p.lower() not in PLACEHOLDER_MAP]
        if unmapped:
            skipped.append({"bank_id": r["id"], "text": text, "unfillable": unmapped})
            continue

        skills_idx = 0
        filled = []

        def _sub(m: re.Match) -> str:
            nonlocal skills_idx
            slot = PLACEHOLDER_MAP[m.group(1).lower()]
            if slot == "skills" and len(skills_parts) > 1:
                val = skills_parts[skills_idx % len(skills_parts)]
                skills_idx += 1
            else:
                val = slots[slot]
            filled.append({"placeholder": m.group(1), "slot": slot, "value": val})
            return val

        rendered_core = _PLACEHOLDER_RE.sub(_sub, text)
        rendered = f"{rendered_core}{CLAIM_JSON_INSTRUCTION}"
        kept += 1
        out.append({
            "prompt_id": f"sourcing_{r['id']}",
            "bank_id": r["id"],
            "task": "sourcing",
            "source_text": text,
            "placeholders_filled": filled,
            "rendered_text": rendered,
        })

    total_candidates = sum(
        1 for r in records if r.get("kind") == "sourcing_query" and r.get("has_placeholder")
    )
    stats = {
        "candidates_considered": total_candidates,
        "filled_and_kept": kept,
        "skipped_unfillable": len(skipped),
        "skip_detail": skipped,
        "total_written": len(out),
    }
    return out, stats


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    records = load_bank()
    slots = yaml.safe_load(CANON_SLOTS_PATH.read_text())
    bank_sha = hashlib.sha256(BANK_PATH.read_bytes()).hexdigest()

    lookup_rows, lookup_stats = build_lookup(records)
    sourcing_rows, sourcing_stats = build_sourcing(records, slots)

    write_jsonl(OUT_LOOKUP, lookup_rows)
    write_jsonl(OUT_SOURCING, sourcing_rows)

    report = [
        "# build_prompts.py report",
        "",
        f"Bank snapshot: `bank_snapshot.jsonl` sha256 `{bank_sha}`",
        f"Bank records total: {len(records)}",
        "",
        "## lookup.jsonl",
        f"- lookup_prompt candidates (kind==lookup_prompt, has_placeholder==false): {lookup_stats['candidates_considered']}",
        f"- selected by keyword match: {lookup_stats['selected_by_keyword']}",
        f"- skipped, no keyword match: {lookup_stats['skipped_no_keyword_match']}",
        f"- name-only control added: {lookup_stats['control_added']}",
        f"- **total written: {lookup_stats['total_written']}**",
        f"- keywords used: {', '.join(LOOKUP_KEYWORDS)}",
        "",
        "## sourcing.jsonl",
        f"- sourcing_query candidates (kind==sourcing_query, has_placeholder==true): {sourcing_stats['candidates_considered']}",
        f"- filled and kept (every placeholder mapped to a canon slot): {sourcing_stats['filled_and_kept']}",
        f"- skipped, unfillable placeholder(s): {sourcing_stats['skipped_unfillable']}",
        f"- **total written: {sourcing_stats['total_written']}**",
        "",
        "### Skipped sourcing records and why",
    ]
    if sourcing_stats["skip_detail"]:
        for s in sourcing_stats["skip_detail"]:
            report.append(f"- `{s['bank_id']}` — unfillable: {s['unfillable']} — text: {s['text']!r}")
    else:
        report.append("- (none)")
    report += [
        "",
        "### Placeholder -> slot map used",
    ]
    for k, v in PLACEHOLDER_MAP.items():
        report.append(f"- `[{k}]` -> `{v}`")
    report += [
        "",
        "## Design notes",
        "- CLAIM_JSON_INSTRUCTION (stacks/trace_schema.py) is appended to every",
        "  generated prompt, lookup AND sourcing, so both tasks fit the one trace",
        "  schema run.py/metrics.py read. For sourcing prompts this means the",
        "  model is also asked for claims/verdict/score about a Boolean-string-",
        "  generation answer, which is a weaker signal than for lookup — the",
        "  sourcing task's real signal is queries[] and fetched_urls[] (does",
        "  Zoeb's own site/GitHub ever surface when an engine sources for the",
        "  exact role he's targeting), not the claims block. metrics.py treats",
        "  sourcing claims/verdict/score as informational only.",
        "- Counts above are NOT capped — every candidate that matches the",
        "  deterministic rule is included.",
    ]
    OUT_REPORT.write_text("\n".join(report) + "\n")

    print(f"lookup.jsonl: {lookup_stats['total_written']} prompts "
          f"({lookup_stats['selected_by_keyword']} matched + 1 control)")
    print(f"sourcing.jsonl: {sourcing_stats['total_written']} prompts "
          f"({sourcing_stats['skipped_unfillable']} skipped, unfillable)")
    print(f"report: {OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
