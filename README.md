# Federal Appropriations Intelligence — CJS Title III pilot

Ingests appropriations documents (govinfo), extracts their comparative tables,
validates them, and serves each account's multi-year, multi-stage funding
history with a source citation for every figure.

| Piece | What it does |
| --- | --- |
| `govinfo_ingest.py` | Finds and stores bills, committee reports and public laws |
| `extract_approps.py` | Extracts comparative tables (text layer, OCR, or vision) into observations |
| `validate_approps.py` | Arithmetic, structural, unit and account-identity checks |
| `approps_store.py` | SQLite store built from the reference workbook (`reference/Approps_Pilot_Schema_Loaded_vNN.xlsx`; through v29 `CJS_Title_III_Science_Pilot_Schema_Loaded_vNN.xlsx` -- both accepted, highest vNN wins); account matching and history queries (CLI) |
| `approps_web.py` + `web/` | Read-only local web UI over the store |
| `export_static.py` → `docs/` | Static copy of the UI for GitHub Pages |

## Local UI (live)

```
python approps_store.py load reference/Approps_Pilot_Schema_Loaded_vNN.xlsx   # or the CJS_Title_III_... name through v29
python approps_web.py            # http://127.0.0.1:8765/
```

Two views: **One account** (search by name, current or former) and
**Subcommittee grid** (every account of a subcommittee side by side, for the
fiscal years and stages you pick). Each grid row is that account's own
history -- the same cells, the same value / not applicable / missing rules --
from `approps_store.subcommittee_grid()`; a cell's headline is budget
authority, and its other lines (supplemental, rescission, transfer, component
lines) open under "+ more", never summed in. A line inside the headline or
another scope of it (component kind `contained` / `view`, e.g. NIH's CURES
Act line, "program level (excluding ARPA-H)") shows right under the headline
it names (`headline_observation_id`), marked "not added", and only where a
document prints it; `approps_store.additive_lines()` / `cell_total()` leave
it out of every sum, while `part` lines (defense, CHIMP, a supplemental act)
add. A dashed outline marks a year
before an account's first record or after its `effective_end`; a supplemental
act line shows only at Enacted and a budget amendment only at President's
Budget, where they can exist. Rows are grouped by
bill title (Account `title`) in bill order and ordered by `display_order`;
accounts not yet placed in a title come last. A rollup the workbook notes as
"Derived rollup" heads the accounts it totals (its title's accounts of its
agency), which fold away under it. Title and bill totals are shown only from
a sourced row -- a rollup noted "title total" / "bill total" -- and never by
summing the accounts loaded. Citations open on a click ("source"). The
grid's selection is in the URL, e.g.
`?view=compare&sc=CJS&fy=2017-2026&stage=Enacted`.

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
that changes the reference workbook (either file name) or the code the export depends on, runs
`python export_static.py`, and commits `docs/` back to the same branch if
anything changed. Re-running on an unchanged workbook produces identical
files, so nothing is committed. It can also be run by hand from the Actions
tab (workflow_dispatch).

One-time setup: Settings → Pages → Build and deployment → Source: *Deploy
from a branch*, branch `main`, folder `/docs`. Pages serves the default
branch, so the public site reflects a workbook once it is on `main`.

Scope: no live backend, no write path, and no query beyond what is
pre-exported (every account's full history; each subcommittee's grid; name
search over current and former names).

## Tests

```
python -m unittest discover -s tests
```

Browser tests need `playwright` (uses the preinstalled Chromium); the
matcher-parity test needs `node`.
