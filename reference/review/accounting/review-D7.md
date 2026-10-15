# D-7: items for owner review

The printed summary lines of every held Labor-HHS and CJS report (FY2017-FY2027, every stage), recorded on the bill
and title total rows (`d7_records.py`; per-line outcome in `summary_lines.csv`, the reads in `summary_rows.csv` and
`summary_rows_images.csv`). No account figure changed; no existing observation changed.

## Differences (measure_reconcile, one item per cause)

| cause | cells | what the table prints |
|---|---|---|
| emergency_line_untagged (VAL-MRC-EMERGENCY-LINE-UNTAGGED) | 2 | Title II 'Emergency appropriations' counts an '(emergency)' line no component tags: FY2023 Senate $2.5 billion, 'Unaccompanied Children, BA (emergency)' under Refugee and Entrant Assistance (Senate FY2023 explanatory statement p.412); FY2024 Senate $75 million, 'Pandemic Preparedness and Biodefense (emergency)' under ASPR (S.Rept. 118-84 p.390; which held account it belongs to is inferred, not read) |
| advance_line_untagged (VAL-MRC-ADVANCE-LINE-UNTAGGED) | 1 | Title II 'Advance appropriations, FY 2026' (FY2025 Senate) counts $1.7 billion printed as 'New advance, 1st quarter, FY 2026' under Child Support Enforcement (S.Rept. 118-207 p.443), which has no advance_enacted component |

Each fix is a component tag on the account's own observation (tag_components), not a change to the printed line.

## Not checked

- A line of a title whose accounts aren't all held is "not checked: accounts not held". The count of accounts not
  held is not known from the store (the tables' account rows aren't read), so the reason names how many of the
  title's accounts are held instead. Labor-HHS Title II is the only title counted as fully held (measure_reconcile
  TITLES_HELD; title_ii_totals.py).
- CJS Title III's held accounts add up to its printed total in 30 of its 38 cells (the other 8 are in
  `total_gaps.csv`; 7 have held supplemental or defense lines beside the headlines). Counted as fully held, its emergency lines would give 4 pass
  and 1 difference (FY2026 Senate, $934 million printed, none computed). It is left not checked until you confirm
  every Title III account is held.
- CJS tables print no mandatory or discretionary line under a title or the grand total (as read), so CJS has none.

## Bill total not recorded (11 Labor-HHS cells)

H.Rept. 114-699, 115-244, 115-862 and 116-62 print no plain 'Grand total': the bill block is a RECAP ending in
'Grand Total, current year', a current-year scope that is not the sum of the printed title totals (H.Rept. 115-244,
Bill column: 961,056,391 against 986,328,844). No bill total is recorded for those cells (FY2017 Enacted, House, PB;
FY2018 Enacted, House, PB; FY2019 Enacted, House, PB; FY2020 House, PB), and blank is not zero.

## Two grand totals (S.Rept. 118-207, 119-55)

The table prints 'Grand total' twice: the first (p.467 / p.450) with (Mandatory) and (Discretionary), ahead of the
discretionary rescissions, CHIMPs and Other Appropriations; the second (p.470 / p.451) ends the table with
Appropriations, Emergency appropriations, Trust funds, Advance appropriations and Rescissions, which add up to it.
The second is recorded as the bill total; the first and its two lines as views (before_rescissions_and_other and
its mandatory and discretionary forms).

## Kept as printed

- 118HRPT582 (CJS FY2025 request): Title V's total and the bill's Rescissions line differ by $1,000 (-10,729,000
  and -10,730,000 thousand). Both recorded as printed.
- A second discretionary line under one total (H.Rept. 119-271, 119-696 Title I 'excluding BLS Request comparability
  adjustment'; H.Rept. 116-450 'Total: Discretionary, current year') is not recorded: no component for it.

## D-6 carry-over (four CMS cells)

All four read inside, with page and evidence in `total_lines_inside.csv`: the Medicaid advance prints inside 'Grants
to States for Medicaid, appropriated in this bill', above the CMS total (FY2022 Senate pp.352-353, FY2023 Senate
pp.409-411, FY2024 House pp.267-269, FY2025 House H.Rept. 118-585 pp.320-321). The CMS gap cells published with the
held lines counted go from 35 to 39 of 41 (the other two are FY2021 House and Senate, $1,000 over).
