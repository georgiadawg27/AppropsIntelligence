# Handoff: accounting views, phase 2 (account attributes from the Budget Appendix), 2026-10-12

Start here instead of the long thread. The rules themselves are in `reference/ACCOUNTING_RULES.md`.

## Where things stand

- Phases 1 and 1b (#46, #47, #50) added the component records and the `measure_reconcile` check; the parser fix
  (#51) keeps a bill-text paragraph whole. Phase 2 (this PR) fills the account-attribute table and computes the
  discretionary view.
- 4,404 account attributes (Labor-HHS and CJS, FY2017 to FY2027), each citing its Appendix chapter and page:
  `account_id_code`, `budget_subfunction`, `fund_type`, `budget_class`, with `stage_basis` `enacted` or `request`.
  Read from the text layer only: 0 page images, $0.
- 583 components (was 462): 121 new `mandatory_portion` records (PSSF section 436, VICTF claims, EEOICP), and
  their FY2025 CR continuations.
- measure_reconcile: base 427 pass, 0 fail. Printed views 115 pass, 3 differ, 241 not checked. The 26 printed
  discretionary totals (ACF and Title II): 23 pass, 3 differ.
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
- Don't commit the scoping doc or the NTU workbook, and don't copy their contents into the repo.

## Open items

1. Three discretionary totals differ, each for a named cause (VAL-MRC-*): ACF FY2023 Enacted and FY2024 President's
   Budget (-$1.3B, -$1.4B: Child Support's advance is not tagged outside the pilot, and its headline prints
   'available in this bill'); Title II FY2027 President's Budget (+$15.2M: the FY2027 request's Appendix shows VICTF
   with no discretionary budget authority, while the report counts its $15.2M administrative line as
   discretionary).
2. NIH subfunction: the FY2020 Appendix codes 075-9915 as subfunction 999 (its request moved AHRQ into NIH) and
   prints that code over its 2018 actual column, so FY2018 enacted carries 999 while the FY2018 request says 552.
   The rule (FY N+2 volume) was followed; consider taking the code from the FY N+1 volume where they disagree.
3. Cross-check disagreements for review (`class_crosscheck.csv`): CMS Program Management and OMHA carry
   trust_fund_limitation components but are general-fund accounts (funded by transfers from the trust funds); the
   Account tab's own fund_type differs from the Appendix fund digit for AFF, CVF, USTSF, DOJ WCF, CMS Program
   Management, Medicare Operations, CURES and NOAA PDF. The D/M/TF marks (340 lines compared) and the 7 HRSA
   discretionary splits all agree.
4. Empty attributes with notes: Medicare limitation (a general provision with no schedule), OJP rescission (no
   account named), ASPR and HRSA/SAMHSA program support in the FY2026/FY2027 requests (split across accounts), NIH
   restructured institutes in the FY2025 request, NSC FY2017/FY2018 requests, and class-only gaps where a year shows
   no budget authority (NEF, CCPF, PHSSEF, ARPA-H FY2022).
5. Still not checked: 200 agency totals outside the pilot cells, 25 printed scopes with no computed view, 9 in ACL
   (not held), 7 with no component tagged.
6. Carried over: four ambiguous text-check figures; 55 untagged pilot phrases; auto-tagging beyond the pilot; the
   FY2024 explanatory statement is not held.
