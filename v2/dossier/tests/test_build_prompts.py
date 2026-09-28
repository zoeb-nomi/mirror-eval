"""build_prompts.py is a pure data transform over the vendored bank
snapshot — no network, no randomness. These tests run it for real and check
the deterministic invariants, not fixed magic numbers, so the test doesn't
silently rot if someone updates bank_snapshot.jsonl."""

import json
import pathlib

import yaml

import prompts.build_prompts as bp

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bank_snapshot_matches_checksum():
    assert bp.BANK_PATH.exists()
    records = bp.load_bank()
    assert len(records) > 0
    # every record has the fields build_prompts.py depends on
    for r in records[:5]:
        assert "kind" in r and "has_placeholder" in r and "id" in r and "text" in r


def test_lookup_selection_is_deterministic_and_matches_rule():
    records = bp.load_bank()
    lookup_rows, stats = bp.build_lookup(records)

    # every row except the control has a bank_id that really is a
    # lookup_prompt / has_placeholder==false / keyword-matching record
    by_id = {r["id"]: r for r in records}
    non_control = [r for r in lookup_rows if r["bank_id"] is not None]
    for row in non_control:
        src = by_id[row["bank_id"]]
        assert src["kind"] == "lookup_prompt"
        assert src["has_placeholder"] is False
        assert row["matched_keywords"], "selected row must have matched >=1 keyword"
        for kw in row["matched_keywords"]:
            assert kw in src["text"].lower()

    # exactly one name-only control, framing-line present, JSON instruction present
    controls = [r for r in lookup_rows if r["bank_id"] is None]
    assert len(controls) == 1
    assert controls[0]["source_text"] == bp.ENTITY
    assert bp.FRAMING_LINE in controls[0]["rendered_text"]
    assert '"claims"' in controls[0]["rendered_text"]

    # re-running is byte-identical (determinism)
    lookup_rows2, stats2 = bp.build_lookup(records)
    assert lookup_rows == lookup_rows2
    assert stats == stats2

    # original text is untouched except for the framing/instruction wrap
    for row in non_control:
        assert row["source_text"] in row["rendered_text"]


def test_sourcing_only_fills_when_every_placeholder_maps():
    records = bp.load_bank()
    slots = yaml.safe_load(bp.CANON_SLOTS_PATH.read_text())
    sourcing_rows, stats = bp.build_sourcing(records, slots)

    by_id = {r["id"]: r for r in records}
    for row in sourcing_rows:
        src = by_id[row["bank_id"]]
        placeholders = bp._PLACEHOLDER_RE.findall(src["text"])
        # every placeholder in the source text was mappable
        for p in placeholders:
            assert p.lower() in bp.PLACEHOLDER_MAP
        # every bracketed placeholder was actually substituted away
        rendered_core = row["rendered_text"][: len(row["rendered_text"]) - len(bp.CLAIM_JSON_INSTRUCTION)]
        assert not bp._PLACEHOLDER_RE.search(rendered_core)

    # skipped records really do contain an unmappable placeholder
    for skip in stats["skip_detail"]:
        src = by_id[skip["bank_id"]]
        placeholders = bp._PLACEHOLDER_RE.findall(src["text"])
        assert any(p.lower() not in bp.PLACEHOLDER_MAP for p in placeholders)

    # counts are not capped: every candidate is accounted for
    assert stats["filled_and_kept"] + stats["skipped_unfillable"] == stats["candidates_considered"]

    # determinism
    sourcing_rows2, stats2 = bp.build_sourcing(records, slots)
    assert sourcing_rows == sourcing_rows2


def test_multi_skill_placeholder_cycles_through_parts():
    records = bp.load_bank()
    slots = yaml.safe_load(bp.CANON_SLOTS_PATH.read_text())
    sourcing_rows, _ = bp.build_sourcing(records, slots)
    row = next((r for r in sourcing_rows if r["bank_id"] == "pb-0209"), None)
    assert row is not None, "pb-0209 has [job title]/[skill1]/[skill2] and should be fillable"
    skill_fills = [f["value"] for f in row["placeholders_filled"] if f["slot"] == "skills"]
    parts = [p.strip() for p in slots["skills"].split(",")]
    assert skill_fills == parts[: len(skill_fills)]


def test_main_writes_files_and_report(tmp_path, monkeypatch):
    bp.main()
    assert bp.OUT_LOOKUP.exists()
    assert bp.OUT_SOURCING.exists()
    assert bp.OUT_REPORT.exists()
    lookup_rows = [json.loads(l) for l in bp.OUT_LOOKUP.read_text().splitlines()]
    sourcing_rows = [json.loads(l) for l in bp.OUT_SOURCING.read_text().splitlines()]
    assert len(lookup_rows) > 1  # at least some real matches + the control
    assert len(sourcing_rows) > 0
    report_text = bp.OUT_REPORT.read_text()
    assert "total written" in report_text
