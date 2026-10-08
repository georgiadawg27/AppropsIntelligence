# Federal Appropriations Intelligence

Ingests appropriations documents (govinfo), extracts their comparative tables,
validates them, and serves each account's multi-year, multi-stage funding
history with a source citation for every figure.

| Piece | What it does |
| --- | --- |
| `govinfo_ingest.py` | Finds and stores bills, committee reports and public laws |
| `extract_approps.py` | Extracts comparative tables (text layer, OCR, or vision) into observations |
| `validate_approps.py` | Arithmetic, structural, unit and account-identity checks |
| `data/staged.json` + `scripts/build_workbook.py` | The canonical data, and the script that builds the workbook from it (see **Data**) |
| `approps_store.py` | SQLite store loaded from the workbook built from `data/staged.json`; account matching and history queries (CLI) |
| `approps_web.py` + `web/` | Read-only local web UI over the store |
| `export_static.py` → `docs/` | Static copy of the UI for GitHub Pages |

## Data

**Where it lives.** The source of truth is `data/staged.json`: every account,
observation, source document, Bill Report Reference row, validation record and
the rest, one JSON document. `scripts/build_workbook.py` builds the workbook
(`Approps_Pilot_Schema_Loaded.xlsx`) from it. No workbook is committed: the site,
the store and the tests all read a build of `data/staged.json`.

**How to rebuild.**

```
mkdir -p build && python scripts/build_workbook.py      # -> build/Approps_Pilot_Schema_Loaded.xlsx
python export_static.py                                 # the site (docs/) and the Download workbook file
python reference/build_accounts.py                      # reference/accounts.json
```

The build runs its own checks first and stops on any failure: a value outside its
allowed list, a broken reference (unknown account, document, component, headline or
parent), a duplicate ID, a leftover effective date, a govinfo landing-page link, or
enactment fields on a row that is not Enacted. `approps_store.reference_workbook()`
runs the same build when `data/` or the script is newer than `build/`, then stores
each lookup formula's result in the file (`calculate_lookups`), as a spreadsheet
does when it recalculates; that copy (`build/site/`) is what the store loads and
what the page's **Download workbook** link serves (`docs/Approps_Pilot_Schema_Loaded.xlsx`,
its timestamps fixed so an unchanged build is the same file).

CI (`.github/workflows/tests.yml`, data job) builds the workbook, recalculates it in
LibreOffice headless and fails on any formula error (`scripts/recalc_check.py`;
data text that begins with "=" stays text), checks the stored lookup results against
LibreOffice's, and uploads the workbook as an artifact. `tests/test_build_workbook.py`
holds the build to the reference build value for value, sheet by sheet
(`tests/fixtures/workbook_values.json`; when the data changes on purpose, regenerate
it with `python scripts/workbook_digest.py build/Approps_Pilot_Schema_Loaded.xlsx`).
On a push that changes the data, `.github/workflows/export-static.yml` regenerates
`docs/` and `reference/accounts.json` and commits them.

IDs are never reused or renumbered (REL-LHHS-0003/0004 stay retired).

**Draft stages.** Bill Report Reference `draft` (TRUE/FALSE) marks a stage whose figures come from a
committee draft -- the full committee never reported the bill (CJS FY2021-23 Senate, CJS FY2024 House,
Labor-HHS FY2023 Senate and FY2024 House). The grid labels those columns "House (draft)" / "Senate (draft)".
The FY2024 House Labor-HHS rows were added by `reference/review/lhhs/fy2024_house/build_rows.py`
(appends to `data/staged.json`; its bill-text and cross-document reports sit beside it).

**Review statuses.** Two fields, never inferred from wording on the page:

- `verification_status` (each observation): `auto-validated` only with confidence
  >= 0.90, every check passing and at least one check that confirms the figure (a sum
  or a second document); `flagged` when a check fails or a person flagged it for review;
  otherwise `unverified`. `human-verified`, `provisional` and `superseded` are set by
  their own steps. Reviewer mode marks a flagged cell with a solid dot and an
  unverified one with an open dot.
- `human_review_status` (each validation record): `pending` -- a person still has to
  look at it -- or `resolved` (with `reviewer` and `resolution`); blank on a check that
  passed. Reviewer mode lists a cell's pending records in its mark's tooltip, counts
  the cells in view that have any, and marks a verified cell that still has one with
  an outlined dot.

## Local UI (live)

```
python approps_store.py load     # builds the workbook from data/staged.json, then loads it
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
before an account's first record (accounts carry no effective dates since
v33: what their figures cover is computed, `reference/review/coverage.py`); a supplemental
act line shows only at Enacted and a budget amendment only at President's
Budget, where they can exist. Rows are grouped by
bill title (Account `title`) in bill order and ordered by `display_order`;
accounts not yet placed in a title come last. An account whose figure is inside another's
(Account `parent_account_id`, v33: Health Centers under Primary Health Care, Head Start under
Children and Families Services Programs) sits indented under it and is never added again --
`approps_store.agency_members()` / `agency_sum()` leave it out of an agency total's sum. A stage's
Bill Report Reference note (e.g. FY2023 Labor-HHS Senate Reported: a committee draft, never reported)
is marked (†) on its column and listed under the grid; a document's own note shows with its citation. A rollup the workbook notes as
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

**Freshness: as of the last committed `data/staged.json` — updated
automatically, not in real time.** The page shows which data (path, version,
commit date, sha256) it was built from, and links the workbook built from it.

How it stays current: `.github/workflows/export-static.yml` runs on every push
that changes `data/`, the build script or the code the export depends on, runs
`python export_static.py` and `python reference/build_accounts.py`, and commits
`docs/` and `reference/accounts.json` back to the same branch if anything changed.
Re-running on unchanged data produces identical files, so nothing is committed.
It can also be run by hand from the Actions tab (workflow_dispatch).

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

Faster, the way CI runs it (`pip install pytest pytest-xdist`): two groups,
each spread across the CPUs.

```
# page: tests/test_web.py, test_compare.py, test_static.py
python -m pytest tests/test_web.py tests/test_compare.py tests/test_static.py -n auto --dist loadscope
# everything else: store, ingest, extraction, build
python -m pytest tests -n auto --dist loadscope --ignore=tests/test_web.py --ignore=tests/test_compare.py --ignore=tests/test_static.py
```

While working, run the modules for the files you changed; CI runs both groups
on every pull request (a page-only change skips the second, which still reports).
On a pull request the matcher-parity test checks a fixed, seeded sample of about
1,500 queries spread across query kinds (`PARITY_SAMPLE=1500`); pushes to main
check all 14,574.

## Source links

Each stored document gets a public link (`public_links.py`), separate from the
API link it was fetched from: Congress.gov for an Appropriations committee report
found through its bill, else govinfo's content PDF (with the granule the download
used), else a manual ingest's `--source-url` (required unless `--not-public`).
A link is kept only when the file there has the stored sha256; otherwise the
document is listed for review. `python govinfo_ingest.py links` (re)derives them
all and writes `document_store/links_needing_review.json`. No stored or shown URL
ever carries an `api_key`.
