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

Owner, D-6 round 2 (2026-10-14): where held lines explain the gap exactly, they count in the total's sum (each as
its basis counts it; ACCOUNTING_RULES 'Totals') and the total is published: 56 cells (`published` yes: NASA 5, NSF
7, CMS 39, ACF 1, HRSA 1, SAMHSA 1 -- 2 CJS totals and 42 LHHS cells in all). Still not computable, for review (40
cells, `published` no): ACF 13, OS 10, NIH 8, CDC 5, ASPR 2, CMS 2 (FY2021 House and Senate: $1,000 over the
advance), SAMHSA 2 (FY2026 and FY2027 requests). Each needs the table read for the line we don't hold.

## Notes

- Titles: Title II's sum is not computable in most cells because the title's General Provisions rescission lines
  (Adoption Incentives, Nonrecurring Expenses Fund) are not budget-authority headlines and have no published view,
  and because of missing lines (Limitation for Title XVIII, CDC-Wide Activities). Its checked views are kept.
- Agencies held only as their total (AHRQ, ACL, AHA) are their own one line: their views are their own headline and
  components, as an account's. With no budget_class on file for a total, their Discretionary views are not
  computable (84 cells).
