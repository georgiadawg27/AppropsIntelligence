# Handoff: accounting views, phases 1 and 1b, and the bill-text parser fix (2026-10-11)

Start here instead of the long thread. The rules themselves are in `reference/ACCOUNTING_RULES.md`.

## Where things stand

- Phase 1 (#46, #47) and phase 1b (#50) added the component records, the account-attribute table (still empty)
  and the `measure_reconcile` check. The parser-fix PR keeps a bill-text paragraph whole past its enumerated lists.
- Labor-HHS pilot cells (FY2024 to FY2026) and CJS FY2026: 462 components. Base check 346 pass, 0 fail. Printed
  views 92 pass, 0 differ, 267 not checked. Text check 2,529 match, 0 mismatch, 4 ambiguous.
- No headline figure has changed in any of these PRs. Every headline keeps its As printed figure.

## Decisions (the owner's)

- Advances count when available (CBO) by default. Earmarks are included in every view by default.
- Draft committee stages are tagged like reported stages.
- FY2025 full-year CR (P.L. 119-4): components continue from FY2024. Where an anomaly states the new amount, a new
  FY2025 component is read from its text (`cr_anomaly`, citing the section). An item the anomaly ends is dropped.
  Earmarks never continue (sec. 1111).
- ONC (six cells): the headline is $0 budget authority as the bill text prints it, and the section 241 amount is a
  `transfer_in` beside it. Each is confirmed by its report's comparative table.
- Prevention and Public Health Fund: each report table row is a `transfer_in` beside the receiving account.
  FY2024 Enacted uses the FY2025 Senate report's 2024 column. ACL rows are reported, not recorded.
- A nested component (`within_component_id`) counts in a view only when the view splits its parent; no view counts
  a nested component and its parent together.
- Bill or law text controls over the report table for the same scope.
- No headline figure changes without the owner's approval.

## Working rules

- `data/staged.json` is the source of truth; write it with `json.dumps(data, indent=1, ensure_ascii=False)`.
- IDs are never reused or renumbered; a record no longer produced is retired.
- After a data change, in order: `scripts/tag_components.py --write`, `scripts/measure_reconcile.py --write`,
  `scripts/check_legislative_text.py --write`, `scripts/build_workbook.py`, `reference/build_accounts.py`; then the
  workbook fixture (`scripts/workbook_digest.py`, `data_sha256`, the note), `reference/review/coverage.py`, commit,
  `export_static.py`, `scripts/grid_counts.py > tests/fixtures/grid_counts.json`.
- Changing the parser section of `legislative_text.py` changes the parser version, so every committed text file
  must be reparsed (`scripts/fetch_legislative_text.py --reparse`, from the gitignored download cache).
- Don't commit the scoping doc or the NTU workbook, and don't copy their contents into the repo.

## Open items

1. Phase 2: fill the account-attribute table (budget_class, subfunction, fund type, account ID code) from the
   Budget Appendix, then compute the discretionary view and the 200 agency totals outside the pilot cells.
2. 267 printed views are not checked: 200 agency totals outside the pilot, 26 that need `budget_class`, 25 with no
   computed view for their scope, 9 in accounts we don't hold (ACL), 7 with no component tagged.
3. Four text-check figures remain ambiguous (several plausible amounts in one paragraph); they wait for review.
4. 55 phrases in the pilot paragraphs match no tagging rule; they are listed by `scripts/tag_components.py`.
5. Extend auto-tagging beyond the pilot cells (other fiscal years and subcommittees).
6. The FY2024 explanatory statement is not held; FY2024 Enacted PPHF rows come from the FY2025 Senate report.
