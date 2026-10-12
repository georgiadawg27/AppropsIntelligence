# Handoff: accounting views, phase 2 (account attributes from the Budget Appendix), 2026-10-12

Start here instead of the long thread. The rules themselves are in `reference/ACCOUNTING_RULES.md`.

## Where things stand

- Phases 1 and 1b (#46, #47, #50) added the component records and the `measure_reconcile` check; the parser fix
  (#51) keeps a bill-text paragraph whole. Phase 2 (this PR) fills the account-attribute table and computes the
  discretionary view.
- 4,303 account attributes (Labor-HHS and CJS, FY2017 to FY2027), each citing its Appendix chapter and page (or,
  for a class set from a report mark, the report table page): `account_id_code`, `budget_subfunction`,
  `fund_type`, `budget_class`, with `stage_basis` `enacted` or `request`, and `provisional` on the FY2026 and
  FY2027 enacted-basis classes. Read from the text layer only: 0 page images, $0.
- Mixed budget_class records: 624 before the follow-ups (601 enacted, 23 request), 483 after (460 enacted, 23
  request). The 272 marked lines on mixed accounts all now take their mark's class; what stays mixed has no
  mark (FY2021 on, or a line the image-only reports print) or an Appendix account that is genuinely mixed by
  activity (PSSF, CFSP, VICTF, HCFAC, CMS Program Management).
- 583 components (was 462): 121 new `mandatory_portion` records (PSSF section 436, VICTF claims, EEOICP), and
  their FY2025 CR continuations.
- measure_reconcile: base 427 pass, 0 fail. Printed views 115 pass, 3 differ, 241 not checked. The 26 printed
  discretionary totals (ACF and Title II): 23 pass, 3 differ; the VICTF FY2027 request difference is resolved by
  the owner (a request-stage classification difference), the two ACF Child Support differences stay pending.
- No headline figure has changed. Every headline keeps its As printed figure.

## Decisions

The owner's (from the brief):

- Enacted, House and Senate stages take FY N from the FY N+2 Appendix (actuals); the request stage takes FY N
  from FY N's own Appendix. A request value is recorded (`stage_basis` `request`) only where it differs from the
  enacted one.
- `mixed` where an account has discretionary and mandatory budget authority in the same year; the split is carried
  by components (`mandatory_portion`).
- Proposed-only accounts take the request year's Appendix only. Totals and part lines carry no attributes. No
  schedule found: empty with a note. Defense stays on the `defense_portion` lines.
- Cross-check failures go to review; nothing is adjusted.

The owner's follow-ups (2026-10-12, same PR):

- Activity-level class from the report tables: a mixed account-year whose held lines carry one D or M mark takes
  the mark's class, cited to the table page (`class_marks.csv`). `mixed` stays only where no mark exists and the
  Appendix can't split it.
- `account_id_code`, `fund_type` and `budget_subfunction` from FY N's own Appendix volume; `budget_class` stays
  from the FY N+2 actuals.
- fund_type: the Appendix wins. The eight Account-tab entries were corrected, with a note on each account. A
  general fund with trust_fund_limitation components (CMS Program Management, Medicare Hearings and Appeals) is
  consistent.
- FY2026 and FY2027 enacted-basis classes are `provisional`: re-check them when the FY2028 and FY2029 Appendix
  volumes are published. The codes are FY N's own volume's, so they are not provisional.
- VICTF FY2027 request (+$15.2M) resolved as a request-stage classification difference
  (VAL-MRC-REQUEST-STAGE-CLASS).

Made in this phase (flag any you disagree with):

- FY2026 has no FY2028 volume. Its enacted basis is the FY2027 Appendix's 2026 est. column (the enacted level), not
  FY2026's own volume, whose 2026 column is the request (it would read HRSA as mandatory, and SAMHSA, LIHEAP, ONC,
  OMHA with no budget authority, because the FY2026 request moves them).
- FY2027 enacted basis: code, fund and subfunction from the FY2027 schedule; class from its 2026 est. column, since
  the 2027 column is the request (which moves whole agencies into the Administration for a Healthy America).
- A mixed Appendix account that labels its mandatory activities (HRSA 075-0350 '(Mandatory)', the FY2017 'ACA'
  lines; OS 075-9912) gives each of our accounts the class of its own activity lines. One that does not (CDC 0943,
  NIH 9915, SAMHSA 1362, ACF 1536) leaves all its activities `mixed`, and the evidence says so.
- The discretionary view counts a mixed account with no `mandatory_portion` as wholly discretionary, and an
  account with no class as printed (named in the check). Both passed against every printed total where they apply.
- EEOICP's mandatory portion is its whole line, from the report's own sentence ('$55,358,000 in mandatory funds
  under the terms of the EEOICPA'), tag_method `report_text`.
- The enacted class reflects every kind of budget authority the account had that year, including reconciliation
  acts (American Rescue Plan FY2021, the 2025 reconciliation act for NASA), not only the appropriations bill.

## Working rules

- `data/staged.json` is the source of truth; write it with `json.dumps(data, indent=1, ensure_ascii=False)`.
- IDs are never reused or renumbered; a record no longer produced is retired.
- After a data change, in order: `scripts/appendix_attributes.py --write` (when the map or extract changes),
  `scripts/tag_components.py --write`, `scripts/measure_reconcile.py --write`, `scripts/check_legislative_text.py
  --write`, `scripts/build_workbook.py`, `reference/build_accounts.py`; then the workbook fixture
  (`scripts/workbook_digest.py`, `data_sha256`, the note), `reference/review/coverage.py`, commit,
  `export_static.py`, `scripts/grid_counts.py > tests/fixtures/grid_counts.json`.
- Appendix sources: `scripts/appendix_attributes.py --extract` reads the 66 cached chapter PDFs
  (`document_store/appendix/`, gitignored; fetched from govinfo when missing) into
  `reference/review/accounting/appendix_schedules.json`, which is committed so the build is offline. The account
  map is `appendix_map.csv`; per-row results and notes are in `appendix_coverage.csv`.
- Table and text readers that need PDFs (run locally, outputs committed): `table_lines.py` (VICTF admin and claims),
  `mandatory_lines.py` (PSSF, EEOICP), `class_crosscheck.py` (the cross-checks), `build_pphf_table.py`.
- Don't commit `docs/Approps_Pilot_Schema_Loaded.xlsx`: only CI's export job on main writes it
  (`export_static.py --publish-workbook`). `export_static.py` without the flag leaves it alone, and the Tests
  workflow fails a pull request that changes it. Its content is held to `tests/fixtures/workbook_values.json`.
- Install with `pip install -r requirements.lock` (Python 3.11.17). The build forces openpyxl's ElementTree
  writer, so a machine with lxml builds the same bytes.
- Changing the parser section of `legislative_text.py` changes the parser version, so every committed text file
  must be reparsed (`scripts/fetch_legislative_text.py --reparse`, from the gitignored download cache).
- Don't commit the scoping doc or the NTU workbook, and don't copy their contents into the repo.

## Open items

1. The two ACF Child Support differences (VAL-MRC-MANDATORY-ADVANCE-UNTAGGED: ACF FY2023 Enacted -$1.3B, FY2024
   President's Budget -$1.4B; the advance is not tagged outside the pilot) stay pending until the FY2017-FY2023
   component backfill.
2. Subfunction 999 for review (`budget_subfunction` 999, all read as printed): NSF 049-0100 Research and Related
   Activities (FY2017-FY2026 enacted); CDC 075-0943 (13 accounts, FY2017-FY2027 enacted, 143 records); NIH
   075-9915 FY2020 enacted (26 institutes: the FY2020 volume's own code, as its request moved AHRQ into NIH); OS
   075-0160 FY2027 request (OCR, OMHA in the request's proposed account).
3. Cross-checks (`class_crosscheck.csv`): no disagreement left. Marks: 277 lines set from the mark, 57 agree; HRSA
   7 agree; trust 112 agree, 2 consistent (general fund with trust fund limitation).
4. Empty attributes with notes: Medicare limitation (a general provision with no schedule), OJP rescission (no
   account named), ASPR and HRSA/SAMHSA program support in the FY2026/FY2027 requests (split across accounts), NIH
   restructured institutes in the FY2025 request, NSC FY2017/FY2018 requests, and class-only gaps where a year shows
   no budget authority (NEF, CCPF, PHSSEF, ARPA-H FY2022).
5. Still not checked: 200 agency totals outside the pilot cells, 25 printed scopes with no computed view, 9 in ACL
   (not held), 7 with no component tagged.
6. Carried over: four ambiguous text-check figures; 55 untagged pilot phrases; auto-tagging beyond the pilot; the
   FY2024 explanatory statement is not held.
