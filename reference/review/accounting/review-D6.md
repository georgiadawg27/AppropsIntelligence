# D-6: items for owner review

## Totals whose accounts don't add up to the printed total

Owner, 2026-10-14: a total whose accounts held, as printed, don't add up to the printed total is not computable on
every basis but As printed, reason "its N accounts as printed sum to $Y, not the printed $Z: $X is in no account
held". A printed parallel total that measure_reconcile matched still gives its checked view there. Every cell is in
`total_gaps.csv` (`total_gaps.py`): 96 cells, 10 totals. One item per agency; no figure changed.

`lines_beside_headlines` names lines the data already holds in those accounts' cells beside their headlines
(supplemental, advance, a program line printed under the account, rescission), with document and page: the fewest
that add up to the gap exactly (`explains` yes). Blank: no held line explains it, and the table has to be read.

| total | cells | explained by held lines | what the held lines say |
|---|---|---|---|
| NASA (CJS) | 5 | 5 | supplemental lines (Exploration, Construction) and a Space Operations rescission, printed under the accounts and counted in the agency total |
| NSF (CJS) | 7 | 7 | supplemental lines (R&RA, MREFC); FY2019 and FY2024 requests: the R&RA 'Defense function' line, which the request's R&RA headline does not include (see review-D3b.md, NSF R&RA FY2019 and FY2024) |
| CMS (LHHS) | 41 | 39 | Medicaid's advance for the next year (the table's 'advance' line), counted in the agency total, not in Grants to States for Medicaid's headline. FY2021 House and Senate: $1,000 more than the advance (rounding of the table's thousands; not explained) |
| ACF (LHHS) | 14 | 1 | FY2019 request: SSBG 'Discretionary Funds' line. The other 13 not named; most are $1.3-1.4 billion (FY2021-FY2024). FY2023 Enacted ($4.075 billion): the held LIHEAP CR-funding lines are part of it but don't sum to it |
| OS (LHHS) | 10 | 0 | not named; several $500,000,000 and $1,000,000,000 differences (FY2017-FY2018, FY2022), FY2019 request $10.01 billion |
| NIH (LHHS) | 8 | 0 | not named; requests FY2018-FY2021 $256-606 million more; FY2022 Enacted and FY2023 House the accounts sum to more than the total (-$1.0 billion, -$2.74 billion) |
| CDC (LHHS) | 5 | 0 | not named; -$255 million to +$35 million |
| SAMHSA (LHHS) | 3 | 1 | FY2017 House: the Comprehensive Opioid Response Grants line ($500,000,000); FY2026 and FY2027 requests (+$4.0 and +$4.5 billion): not named |
| ASPR (LHHS) | 2 | 0 | not named (FY2024 request $400,000,000, FY2024 Senate $75,000,000) |
| HRSA (LHHS) | 1 | 1 | FY2017 Enacted: a $15,000,000 MCH line from another law (P.L. 114-254 p.18) |

Owner, D-6 rounds 2-3 (2026-10-14): a held line counts in the total's sum only where the table prints it inside
that total (its position under the total, or the table's printed arithmetic), never because it makes the sum match.
Evidence per line: `total_lines_inside.csv` (from `total_lines.py`'s text-layer report, and the page image where the
table is scanned). What shows each kind of line is inside:

| line | cells | evidence |
|---|---|---|
| Medicaid's 'New advance, 1st quarter, FY N+2' | CMS 35 | the row sits under 'Grants to States for Medicaid', inside 'Total, Grants to States for Medicaid, appropriated in this bill', above 'Total, Centers for Medicare and Medicaid Services' with only CMS's own account totals between (text layer, 25 cells); on the scanned pages the total prints its own parts below it -- 'New advance, FY 2018' / 'FY 2022 Advance' / 'Advance appropriations' (page image, 10 cells: H.Rept. 114-699, 116-450, 119-271, 119-696, CR H1642) |
| supplementals '(emergency)' | NASA 5, NSF 4 | the '(emergency)' row under the account, inside its 'Subtotal' and above 'Total, National Aeronautics and Space Administration' / 'Total, National Science Foundation' (S.Rept. 118-62, 118-198: text layer; H.Rept. 118-582, 119-272: page image) |
| NASA Space Operations rescission | NASA FY2025 request | '-1,000' printed under Space Operations in the NASA block (H.Rept. 118-582 p.247, page image) |
| NSF 'Defense function' | NSF FY2019, FY2024 requests | printed under 'Research and related activities' inside its 'Subtotal', above 'Total, National Science Foundation' (H.Rept. 115-704 p.129 image; S.Rept. 118-62 p.220 text) |
| ACF 'Discretionary Funds' (SSBG) | ACF FY2019 request | under Social Services Block Grant, above 'Total, ACF' with only ACF account totals between (H.Rept. 115-862 pp.375-377, text) |
| SAMHSA 'Comprehensive Opioid Response Grants' | SAMHSA FY2017 House | inside 'Total, Substance Abuse Prevention' 711,148, above 'Total, SAMHSA' (H.Rept. 114-699 p.244, image) |

Moved back to not computable (no position evidence): NSF FY2025 request (the line is S.Rept. 118-198's, the total
H.Rept. 118-582's, whose request column prints no NSF emergency line) and HRSA FY2017 Enacted (the line is P.L.
114-254's) -- both lines of another document that would have closed the gap exactly; CMS FY2022 Senate, FY2023
Senate, FY2024 House (the committee documents are not reachable here) and FY2025 House (H.Rept. 118-585's PDF is not
retrievable here) -- for a session that can read them.

Published: 48 cells (NASA 5, NSF 6, CMS 35, ACF 1, SAMHSA 1). Not computable: 48 (ACF 13, CMS 6, OS 10, NIH 8, CDC 5,
ASPR 2, SAMHSA 2, HRSA 1, NSF 1).

### CMS FY2021 House and Senate: $1,000 over the advance

The FY2021 House (H.Rept. 116-450) and Senate (Senate explanatory statement) CMS totals are $148,732,316,000 more
than the accounts held; the advance printed inside the House total is $148,732,315,000 (H.Rept. 116-450 pp.408-410,
PDF pp.412-414: the total's parts print 'FY 2022 Advance (148,732,315)'). $1,000 is in no account held -- likely the
rounding of the table's thousands somewhere in CMS's lines, but not shown. Not rounded: both cells stay not
computable. For review: which CMS line the table rounds (or which line is $1,000 off).

## Notes

- Titles: Title II's sum is not computable in most cells because the title's General Provisions rescission lines
  (Adoption Incentives, Nonrecurring Expenses Fund) are not budget-authority headlines and have no published view,
  and because of missing lines (Limitation for Title XVIII, CDC-Wide Activities). Its checked views are kept.
- Agencies held only as their total (AHRQ, ACL, AHA) are their own one line: their views are their own headline and
  components, as an account's. With no budget_class on file for a total, their Discretionary views are not
  computable (84 cells).
