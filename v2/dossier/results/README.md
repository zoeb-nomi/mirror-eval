# results/

Placeholder so this folder exists after a GitHub web-UI upload (git does not
track empty folders).

`run.py` writes each run to `results/<run_id>/` (`manifest.json`,
`calls.jsonl`, then `metrics.json`, `summary.json`, `report.md` from
`report.py`). Those run folders are local output and are ignored by the repo
root `.gitignore` (see "Uploading via the GitHub web UI" in `../README.md`).
Publish results deliberately, not by accident.
