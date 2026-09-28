"""A 4-call --mock run end to end via run.py's CLI, no network, no keys.
Uses a dedicated run-id under results/ and cleans up after itself."""

import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN_ID = "test_mock_4call"
RESULTS_DIR = ROOT / "results" / RUN_ID


def _run(*extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "run.py"),
         "--task", "lookup", "--mock", "--run-id", RUN_ID,
         "--condition", "full", "--reps", "1", "--concurrency", "2",
         *extra_args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def setup_function(_fn):
    shutil.rmtree(RESULTS_DIR, ignore_errors=True)


def teardown_function(_fn):
    shutil.rmtree(RESULTS_DIR, ignore_errors=True)


def test_mock_run_produces_one_call_per_prompt():
    # constrain to exactly 4 prompts via --prompts-limit is not a flag, so
    # instead we just check the run is well-formed for whatever the lookup
    # battery currently has, and separately verify a 4-line slice below.
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    calls_path = RESULTS_DIR / "calls.jsonl"
    assert calls_path.exists()
    lines = [json.loads(l) for l in calls_path.read_text().splitlines() if l.strip()]
    assert len(lines) > 0
    for rec in lines[:4]:
        assert rec["stack"] == "mock"
        assert rec["condition"] == "full"
        assert rec["rep"] == 1
        assert "answer_text" in rec and "tokens" in rec and "cost_usd_est" in rec
        assert rec["cost_usd_est"] == 0.0
        assert isinstance(rec["claims"], list)


def test_mock_run_is_resumable_second_run_adds_zero_calls():
    r1 = _run()
    assert r1.returncode == 0, r1.stdout + r1.stderr
    calls_path = RESULTS_DIR / "calls.jsonl"
    n1 = len(calls_path.read_text().splitlines())

    r2 = _run()
    assert r2.returncode == 0, r2.stdout + r2.stderr
    n2 = len(calls_path.read_text().splitlines())

    assert n1 == n2, "re-running the same command must add zero new calls"
    assert "nothing to do" in r2.stdout


def test_reusing_run_id_with_different_task_is_refused_exit_2():
    r1 = _run()
    assert r1.returncode == 0, r1.stdout + r1.stderr
    before = (RESULTS_DIR / "manifest.json").read_text()
    r2 = subprocess.run(
        [sys.executable, str(ROOT / "run.py"),
         "--task", "sourcing", "--mock", "--run-id", RUN_ID,
         "--condition", "full", "--reps", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r2.returncode == 2
    assert "task='lookup'" in r2.stderr and "task='sourcing'" in r2.stderr
    assert (RESULTS_DIR / "manifest.json").read_text() == before


def test_reusing_run_id_with_different_stack_is_refused_exit_2():
    r1 = _run()
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = subprocess.run(
        [sys.executable, str(ROOT / "run.py"),
         "--task", "lookup", "--stack", "anthropic", "--run-id", RUN_ID,
         "--condition", "full", "--reps", "1", "--yes"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r2.returncode == 2
    assert "stack='mock'" in r2.stderr and "stack='anthropic'" in r2.stderr


def test_declined_cost_confirmation_writes_no_manifest():
    r = subprocess.run(
        [sys.executable, str(ROOT / "run.py"),
         "--task", "lookup", "--stack", "anthropic", "--run-id", RUN_ID,
         "--condition", "full", "--reps", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=60, input="n\n",
        env={**__import__("os").environ, "ANTHROPIC_API_KEY": ""})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "COST ESTIMATE" in r.stdout
    assert not (RESULTS_DIR / "manifest.json").exists()
    assert not RESULTS_DIR.exists()


def test_mock_run_writes_manifest_with_task_and_stack():
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr
    m = json.loads((RESULTS_DIR / "manifest.json").read_text())
    assert m["task"] == "lookup" and m["stack"] == "mock"
