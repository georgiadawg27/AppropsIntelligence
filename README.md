# Federal Appropriations Intelligence — CJS Title III pilot

Ingests appropriations documents (govinfo), extracts their comparative tables,
validates them, and serves each account's multi-year, multi-stage funding
history with a source citation for every figure.

| Piece | What it does |
| --- | --- |
| `govinfo_ingest.py` | Finds and stores bills, committee reports and public laws |
| `extract_approps.py` | Extracts comparative tables (text layer, OCR, or vision) into observations |
| `validate_approps.py` | Arithmetic, structural, unit and account-identity checks |
| `approps_store.py` | SQLite store built from the reference workbook (`reference/*.xlsx`); account matching and history queries (CLI) |
| `approps_web.py` + `web/` | Read-only local web UI over the store |
| `export_static.py` → `docs/` | Static copy of the UI for GitHub Pages |

## Local UI (live)

```
python approps_store.py load reference/CJS_Title_III_Science_Pilot_Schema_Loaded_v20.xlsx
python approps_web.py            # http://127.0.0.1:8765/
```

## Static site (GitHub Pages)

`docs/` is a static, read-only snapshot of the same UI: every account's
history is pre-exported to JSON by `export_static.py`, which calls the same
`approps_store` / `approps_web` functions the live UI uses, and the page
matches account names in the browser with `web/match.js` — a port of the
Python matching rule, held to identical answers by `tests/test_static.py`.
Values, "not applicable" (confirmed absences) and "missing" render exactly as
in the live UI, as does the note when a name matched through a former name.

**Freshness: as of the last committed reference workbook — updated
automatically, not in real time.** The page shows which workbook (file name,
commit date, sha256) it was built from.

How it stays current: `.github/workflows/export-static.yml` runs on every push
that changes `reference/*.xlsx` (or the code the export depends on), runs
`python export_static.py`, and commits `docs/` back to the same branch if
anything changed. Re-running on an unchanged workbook produces identical
files, so nothing is committed. It can also be run by hand from the Actions
tab (workflow_dispatch).

One-time setup: Settings → Pages → Build and deployment → Source: *Deploy
from a branch*, branch `main`, folder `/docs`. Pages serves the default
branch, so the public site reflects a workbook once it is on `main`.

Scope: no live backend, no write path, and no query beyond what is
pre-exported (every account's full history; name search over current and
former names).

## Tests

```
python -m unittest discover -s tests
```

Browser tests need `playwright` (uses the preinstalled Chromium); the
matcher-parity test needs `node`.
