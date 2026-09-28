VENV := .venv
PY := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: setup prompts test pilot report clean

# Creates a venv, installs the pinned deps, and creates .env from the
# template if it doesn't exist yet.
setup:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q -r requirements.txt
	test -f .env || cp env.example .env
	@echo "Wrote .env from env.example (skipped if it already existed) — open it and paste your keys."

# Regenerates prompts/lookup.jsonl and prompts/sourcing.jsonl from the
# vendored bank snapshot. No network, no keys needed.
prompts:
	$(PY) prompts/build_prompts.py

# Full test suite. --mock only — no network, no keys.
test:
	$(PY) -m pytest tests/ -v

# REAL pilot: Anthropic stack, lookup task, 3 reps, full + blocked_owned
# conditions. Prints a cost estimate and asks to confirm before spending
# anything (skip the prompt with `make pilot YES=--yes`).
pilot:
	$(PY) run.py --stack anthropic --task lookup --condition full,blocked_owned --reps 3 --pilot $(YES)

# Renders results/$(RUN_ID)/report.md + summary.json. Usage: make report RUN_ID=...
report:
	$(PY) report.py --run-id $(RUN_ID)

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache
