# Handoff: accounting views, component backfill FY2017-FY2023, 2026-10-12

Start here instead of the long thread. The rules themselves are in `reference/ACCOUNTING_RULES.md`.

## Component backfill FY2017-FY2023 (this PR)

- The tagger now auto-tags every Labor-HHS and CJS cell FY2017-FY2023, every stage (`AUTO_CELLS` = the pilot
  cells plus `BACKFILL` in `scripts/tag_components.py`). 1,185 components (was 583): 602 new, 0 retired, 0 existing
  changed. New by method: 380 auto_text, 188 table_line (PPHF), 34 appendix_text (advances). Labor-HHS new by FY:
  2017 85, 2018 76, 2019 86, 2020 58, 2021 85, 2022 84, 2023 87 (plus 10 each FY2024-2026 and 6 FY2027 from the
  derived advance_available). CJS: 5 earmarks (FY2022 2, FY2023 3); nothing else in the CJS text matched a rule.
- Nesting: an amount larger than the paragraph's main amount is `separate`, unless it equals the headline (CDC B&F
  FY2020 House NEF $225M is beside, CMS PM FY2017 House trust fund limitation stays inside).
- PPHF: `build_pphf_table.py` reads the allocation tables FY2017-FY2023 (House, Senate, and Enacted from the
  explanatory statements/comparative columns); 332 rows, 0 mismatches against the printed CDC totals. FY2020 has no
  Senate bill; FY2021 House pages are image-only, so its rows are read from the text layer without the total check.
  FY2023 House/Senate $160M block grant sits on the CDC-wide line, which has no headline those stages
  (`member_no_headline`).
- Advances: `reference/review/accounting/advance_lines.py` reads the Budget Appendix language (request
  advance_enacted for Child Support and Foster Care; the FY2016 law's advance for FY2017 from the bracketed text).
  From FY2018 on, advance_available is derived from the FY N-1 law's advance (Child Support, Foster Care,
  Medicaid). in_headline is set by arithmetic; 11 advance_available defaults are listed in the tagger report. Both
  ACF items pass (FY2023 Enacted, FY2024 request).
- Class: a mixed account-year with no D/M mark takes the class of the account's last marked year when the Appendix
  account is the same and no mandatory_portion exists that year ("carried from FY____ mark"). Mixed: 483 -> 177
  (enacted LHHS 441 -> 135; request 23 and CJS 19 unchanged); 306 carried, all discretionary.
- measure_reconcile: base 427 -> 797 pass, 0 fail. Views 115 pass / 3 fail / 241 not checked -> 213 / 25 / 121.
  Agency totals outside the tagged cells 200 -> 70. Discretionary totals 26: 25 pass (Title II FY2027 request,
  resolved). Failures are grouped by cause (one VAL-MRC review item each): agency_total_only 3,
  emergency_lines_not_held 1, evaluation_tap_phrase_untagged 1, headline_superseded 4 (Medicaid FY2021, $1,000),
  member_no_headline 2, nef_transfer_untagged 4, onc_evaluation_tap_line 2, onc_no_headline 1,
  pandemic_flu_balances 4, prif_outside_printed_program_level 2, request_stage_class 1.
- Text check ambiguous 4 -> 4. Page images 0, $0. No headline figure changed.

## Phase 2 (before this PR)


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

1. The new VAL-MRC cause items above are pending owner review. Untagged phrase candidates (tagger --report): CDC
   Scientific Services FY2022 S $132M section 241; CDC B&F FY2018 E $240M BSL-4 lab; OS-GDM $6.8M section 241 teen
   pregnancy evaluations; FY2020 E sec. 237 NEF $225M; OS-GDM FY2023 S $350M general provision. Tagger defaults:
   OBS-LHHS-1582, -1664 (transfer_in), -3755 (trust_fund_limitation); PB advance 'neither' defaults OBS-LHHS-1933,
   -2362, -2763.
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
5. Still not checked: 70 agency totals outside the tagged cells (66 President's Budget, which the text tagger
   does not read; 4 FY2027 House program levels), 25 printed
   scopes with no computed view, 25 account_not_held, 1 with no component tagged.
6. Carried over: four ambiguous text-check figures; 55 untagged pilot phrases; auto-tagging beyond the pilot; the
   FY2024 explanatory statement is not held.

## UI-R: design reference and data-gated features (2026-10-12)

- `reference/ui/explore-v1/` committed as given (SPEC.md is the build target for every Explore UI brief).
- `web/ui_contract.json` (contract_version 1): one entry per SPEC section 9 row, each with the
  `<file glob>:<dotted path>` fields it reads. `export_static.features()` writes `features` + `contract_version`
  into `docs/data/index.json`; a flag is true only if every listed field holds a non-null, non-empty value.
- Today: figure_source, grid, account_panel, overview_panel, search_filter, interaction_state, abbreviations,
  export_csv, phone, watchlist true; accounting_views, budget_class, defense_split false.
- Briefs (owner): grid, search_filter, overview_panel, interaction_state, abbreviations UI-1; figure_source,
  account_panel UI-2; phone UI-P; accounting_views, budget_class, defense_split UI-0 / UI-3; export_csv UI-4;
  watchlist UI-6.
- UI-0 paths (owner, 2026-10-12): contract_version 1 keeps the proposed paths (`rows[].cells.*.views.*`,
  `rows[].budget_class.*`, `rows[].cells.*.components.defense_portion`) for now. UI-0 will move budget_class to
  `rows[].attributes.*.budget_class` and defense to `rows[].cells.*.defense_portion` in web/ui_contract.json and
  bump contract_version.
  The live /api has no index/features equivalent yet; the page reads no flags yet (no page changes in UI-R).

## UI-0: accounting layer in the site data (2026-10-12)

- `site_accounting.py` adds the layer to `approps_store.subcommittee_grid()` (so /api, the static export and the
  store answer the same), from the store's own records (`approps_store.accounting_data`, data/staged.json's shape)
  with accounting.py and `measure_reconcile.run()`. Per printed-figure cell (value or printed zero): `views.<basis>`
  (list: an `available` entry, plus `enacted` only where it differs; printed has timing null), `components[]`,
  `earmark` / `defense_portion` (only where tagged). Per row: `attributes.<FY>` (enacted basis, `request` beside it,
  each attribute's citation). Totals: only views a passing measure_reconcile printed total gives; else
  not_computable "not checked" with `accounts_behind`.
- Basis definitions (`accounting.BASES`, `basis_amount`) follow `reference/ACCOUNTING_RULES.md` (owner, 2026-10-12),
  cited in the code: all_appropriations = advances by timing; discretionary_designated = ACCOUNTING_RULES'
  Discretionary (mandatory excluded, mixed less its mandatory_portion; a mixed account with none tagged counts its
  headline, status computed, reason "mixed, no mandatory portion tagged: counted as discretionary (ACCOUNTING_RULES)");
  discretionary = that less every in-headline `designated` component; program_level = headline + transfer_in +
  trust_fund_limitation, advances by timing. Timing applies to every basis but printed. fees_offsets and
  transfer_out are components only, counted by no basis.
- Printed totals: 'discretionary' confirms only discretionary_designated where designated amounts exist; an
  'excluding emergencies' total confirms discretionary only where every designated amount is emergency and nothing in
  scope is mandatory (owner). Today none qualifies. Unused, all ACF Total: OBS-LHHS-0031 (FY2024 Enacted),
  -0037 (FY2024 Senate), -1743 (FY2024 House), -0040 (FY2025 House), -0046 (FY2025 Senate): measure_reconcile pass,
  but ACF holds mandatory accounts (Child Support, ...); OBS-LHHS-0028 (FY2023 Enacted): measure_reconcile fail
  (emergency_lines_not_held); OBS-LHHS-0034, -0043 (FY2024, FY2025 President's Budget): not checked.
- Whole LHHS.json stays next to the per-year files; removing it is a separate PR with the owner's OK. /api flags:
  UI-1.
- Outside the auto-tagged cells (President's Budget, FY2027), a view is not_computable unless the headline carries a
  component the view uses (measure_reconcile's own rule).
- LHHS.json is 13.8 MB (> `export_static.SPLIT_BYTES`, 12 MB), so it is also written as
  `docs/data/subcommittees/LHHS/<FY>.json` + `index.json`; index.json `split_subcommittees` lists it, and the page's
  static loader reads the per-year files. The whole file is still written; drop it once nothing reads it.
- `web/ui_contract.json` contract_version 2 (paths moved as the owner set; a `shapes` block documents them);
  accounting_views, budget_class, defense_split now true. `scripts/grid_counts.py` adds `view_status_counts`.
- Open: page controls (UI-3); the ACF program-level totals (6 cells) pass but mix advance timings, so they confirm
  neither timing; accounts with no budget_class on file (48 entries) have no discretionary view.

## UI-1: Explore workspace (2026-10-12)

- Explore is the default view (`/`); One account stays reachable (header tab, `?view=account`, `?q=`, `?account=`) until UI-5.
- Feature flags in both serving modes: `features.py` holds `field_values`, `held`, `flags`; `export_static.py` writes
  them into `docs/data/index.json` from the files it exported, and `approps_web.py` serves `GET /api/features` from the
  payloads it serves (`payloads(db)`, cached per database file). `tests/test_static.py` checks the two agree. The page
  shows a feature only when it is built (`BUILT` in web/index.html: grid, search_filter, overview_panel,
  interaction_state, abbreviations) and its flag is true.
- Page assets: `web/abbreviations.js` (display-only map keyed by canonical_account_id), `web/fonts/` (IBM Plex Sans and
  Sans Condensed woff2 from @fontsource 5.2.5, SIL OFL in `web/fonts/OFL.txt`). Both modes serve them; the export copies them.
- Default fiscal year: the current one from today's date (October 1 starts it), never hard-coded; FY2027 now.
- A year with no Enacted figure: the lead stage is the latest stage the printed title total holds (no title total:
  the latest any row holds); it gets the wash, the change reads "<Stage> vs FY<n-1> enacted", the units label adds
  "FY<n> not yet enacted · latest: <Stage>", and the overview shows a Status block instead of "Enacted in".
- URL state: `sc, grid, fy | a, b, stages, basis, units, density, cols, rev, agency, status, search, show, open`
  (`view=compare` still read). Every change is a history entry (search typing replaces); Back/Forward re-render.
- Kept from the old grid, now in the View menu: Units, Expand/Collapse all, Reviewer mode, the two-years stage
  checkboxes and basis, the "Accounts shown" lifecycle checkboxes. Kept below the legend: state counts, stage notes,
  provenance.

Owner follow-ups (2026-10-12, same PR):

- SPEC.md replaced with the owner's second version (FY2027 section, test 15, the zero rule).
- A printed zero is a figure: `figure()` returns it (0) and it counts in dollar changes and movers; a % from a zero
  base shows "new" ("—" when both are zero, `GridMath.pctText`). A confirmed absence (no observation) never counts.
- Lead stage: none when the title total holds nothing that year (change column empty, overview "No printed totals
  yet"). CJS prints no title total at all, so it takes the latest stage any row holds. View › Columns adds
  "Request vs FY<n-1> enacted" in a year without an Enacted stage.
- Filter › Advanced shows its ungated items: change threshold (`minpct`, `mink`: the first change column), Include
  committee drafts (on by default; off drops draft columns and leaves drafts out of every difference, `drafts=0`),
  the three lifecycle checkboxes (moved from View), Only figures pending review or flagged (`review=1`). Amount
  basis, timing, Exclude earmarks and Account type are not built (UI-3) and stay hidden although UI-0 (#57) turned
  their flags on.
- Footer: the default view shows the one-line legend and the provenance line; the cell counts and the corner-mark
  key show in Reviewer mode.
- The CJS section label and the temporary nav (Explore, One account) are approved.

Open items:

1. Include committee drafts defaults to on (today's behaviour); the design shows the box unticked. Owner to confirm.
2. UI-3 builds the basis, timing, earmark and Account type controls; their flags are already on since UI-0.

## UI-2: Click-to-source and the account panel (2026-10-12)

- SPEC.md: the two owner decisions from #58 added word for word (§4 CJS lead-stage bullet; §5.6 drafts on by default).
- Figures: every cell holding an observation (a value, or a printed zero) is a `<button class="fig">` with aria-label
  "Source for <account>, <stage>" (stage as `stageText`: "FY2026 enacted", "FY2025 enacted (full-year CR)", "FY2026 House").
  It keeps `data-testid` amount / token-not-funded and `data-source`; the page link moved from `href` to `data-href`.
  Confirmed absences and outside-life cells stay plain spans. Gated by `figure_source` (now in `BUILT`).
- Panel modes (`renderPanel`): figure source > account > overview. `SEL` holds the selection; URL params
  `fig=<id>|<fy>|<stage>` and `acct=<id>`. A selection not on file in the grid is dropped (`validSel`); changing
  subcommittee clears it. Selection is marked with `data-sel=fig|row` and drawn with `!important` over hover and wash.
- `web/documents.js` (served by both modes via `approps_web.ASSETS`): display-only short names keyed by
  source_document_id, 72 entries. Built by hand from the ID patterns plus 12 explanatory-statement and draft entries
  named from their URL and publication_date. A document with no entry shows its ID.
- Account names open the account mode when `account_panel` is on and the row has its own page (not a printed
  total); "Open account page" opens the One account view (until UI-5). Watch stays hidden (`watchlist` not built, UI-6).
- Esc: closes an open menu first, then clears the selection, and focus goes back to the figure or name. A click outside
  the grid, panel and menus clears; a press that closes a menu only closes it; controls never clear.

Owner follow-ups on #59 (2026-10-12):
- Draft documents are named as drafts ("Senate chair's draft explanatory statement, FY20xx (released <date>)",
  "House subcommittee draft explanatory materials, FY2024 (released <date>)", "S. 3062, as introduced (Senate
  chair's draft)"); every dated name says "released".
- Names that open the account panel always carry the link style (navy #16325f, #9fb4d6 underline, 3px offset),
  line names included (SPEC §4's #3a4150 for line names no longer applies to them).
- SPEC.md §8: human-verified → "Verified by a person" (green, as auto-validated); §5.2: history bars start at zero.

Open items:
1. "Explanatory statement, Cong. Rec. Jan. 22, 2026" keeps the design's wording (an issue date, not "released").
2. The account panel's stage lines show document · page · bill. The design adds a column hint ("FY2025 column",
   "request column") that the data has no field for, so it is left out.

## UI-3: Amount basis and class features (2026-10-12)

- SPEC.md: §4 line-name rule replaced with the owner's #59 wording; §5.6 basis definitions now cite
  `reference/ACCOUNTING_RULES.md` (was `accounting-views-spec.md`, not in the repo). Nothing else in SPEC changed.
- `BUILT` gains accounting_views, budget_class, defense_split; each control renders only when its flag is true.
- Filter › Advanced: Amount basis (five options, `amt=`; definitions paraphrase ACCOUNTING_RULES' Arithmetic table and
  Discretionary rule as accounting.BASES applies them), "Advance appropriations count" (`timing=enacted`). Account type
  (`type=`) sits between Agency and Status. Each non-default choice is a chip and counts in the badge; Reset/Clear all
  restore them. The summary reads "<basis> · advances when <timing> · earmarks included".
- `figure()` is basis-aware (the published view when status checked/computed, else no figure); `printedFig()` keeps the
  printed figure for the lead stage, enacted-history years and the account panel. Cells under a basis: the view amount;
  "excl." (excluded); "n/a" (account not computable) or "not checked" (total not computable); reason on hover; each a
  button to the figure's source. Overview totals: the view only where checked, else "not checked (n accounts)".
- Source panel under a basis: view value + status + reason, the printed headline (UI-2 fields), every component (type,
  amount, in/beside the figure, document, page, ID) and the arithmetic: the printed figure, the components the view
  used (`component_ids`, less if in the headline, plus if beside), "= " the published amount. The page adds nothing up.
- budget_class: the enacted-basis record, else the request basis (proposed accounts). Mixed → "incl. mandatory" tag;
  mandatory lines go under "Appropriated mandatory" in their agency (tag "provisional" where provisional); an agency gets
  "incl. mandatory" when a line in view is mandatory or mixed. Provisional = dashed outline (legend says so).
- View › Columns: "Defense / nondefense (defense portion)" (`cols=defense`): the lead stage's `defense_portion` amount
  where tagged, empty otherwise (untagged is not zero). Only CJS NSF RRA is tagged today.
- Tests: tests/test_compare.py AmountBasis (basis x timing cells and changes on Medicaid, CURES, SAMHSA HSPS, CMS Program
  Management, the title total, FY2025 CR; source panel arithmetic; overview totals; Account type and tags; defense
  column; URL round-trip of every option). Test 12 forces the three flags false and checks the controls appear when on.

Open items (owner):
1. Exclude earmarks is not built: no published view leaves earmarks out, and the page never computes one. Needs e.g.
   `rows[].cells.*.views_excluding_earmarks.<basis>` (same shape as views) from site_accounting.py.
2. Nondefense is not shown: it is a view (ACCOUNTING_RULES "nondefense"); needs a published nondefense amount per cell.
3. Design's Program level definition mentions fees and transfers out; ACCOUNTING_RULES/accounting.py count neither, so
   the page's definition follows ACCOUNTING_RULES.
4. "incl. mandatory" shows on HRSA, CDC, NIH and OS too (each holds a mixed line in FY2026); the design shows it on
   CMS and ACF only.
