# D-8 review items: anchored totals

Built by D-8 (reference/specs/anchored-totals.md). Accounts' views are unchanged; every change below is a total's view (no headline or printed figure changed). Counts are entries of views on the four bases (All appropriations, Program level, Discretionary, Discretionary incl. designated), both timings, against main before D-8.

## 1. Total views whose amount changed (6 cells, both timings)

Before D-8 these were the sum of the accounts, which differed from the report's printed program-level line (measure_reconcile VAL-MRC). Now the total is the printed line, with the difference as one 'not itemised' line (spec rule 3).

| total | FY | stage | basis | old (sum) | new (printed line) | not itemised | anchor |
|---|---|---|---|---|---|---|---|
| ACC-HHS-CDC-TOTAL | 2018 | Enacted | program_level | $8,061,166,000 | $8,301,166,000 | $240,000,000 | 'Total, Centers for Disease Control Program Level' (SRC-CRPT-115HRPT862 p.363) |
| ACC-HHS-CDC-TOTAL | 2020 | Enacted | program_level | $7,749,554,000 | $7,974,554,000 | $225,000,000 | 'Total, Centers for Disease Control Program Level' (SRC-CRPT-116HRPT450 p.401) |
| ACC-HHS-CDC-TOTAL | 2021 | Senate Reported | program_level | $7,819,954,000 | $7,879,954,000 | $60,000,000 | 'Total, Centers for Disease Control Program Level' (SRC-EXPL-LHHS-FY2021-SENATE p.249) |
| ACC-HHS-OS-TOTAL | 2017 | Senate Reported | program_level | $2,944,659,000 | $2,959,659,000 | $15,000,000 | 'Total, Office of the Secretary Program Level' (SRC-CRPT-114SRPT274 p.249) |
| ACC-HHS-OS-TOTAL | 2021 | Senate Reported | program_level | $4,318,234,000 | $4,492,234,000 | $174,000,000 | 'Total, Office of the Secretary Program Level' (SRC-EXPL-LHHS-FY2021-SENATE p.262) |
| ACC-HHS-OS-TOTAL | 2023 | President's Budget | program_level | $5,552,583,000 | $5,656,197,000 | $103,614,000 | 'Total, Office of the Secretary Program Level' (SRC-CRPT-117HRPT403 p.836) |

## 2. Total views that had a figure and now don't (41 entries)

Each was a printed total kept as checked (D-6 `kept_check`) where the lines couldn't be summed: measure_reconcile matched the printed line from the total and its accounts' components, which assumes the money in no account held (or the line with no figure) carries no advance, designation or class. Decision E forbids that. Most are the 'available' timing: the printed line is on the tables' own convention (advances when enacted), and with a part unknown it can't be moved to 'available'. The 'enacted' entry keeps the printed figure (matches or computed). They stay blank: the owner kept the strict rule (2026-10-15). They come back as D-9 holds the unheld lines and fills the figures and classes.

| total | FY | stage | basis | timing | old | old status | cause now |
|---|---|---|---|---|---|---|---|
| ACC-HHS-CDC-TOTAL | 2022 | Enacted | program_level | available | $8,457,204,000 | checked | not_held |
| ACC-HHS-CDC-TOTAL | 2024 | House Reported | program_level | available | $7,591,566,000 | checked | not_held |
| ACC-HHS-CDC-TOTAL | 2025 | House Reported | program_level | available | $7,446,058,000 | checked | missing_figure |
| ACC-HHS-NIH-TOTAL | 2020 | President's Budget | program_level | available | $34,151,068,000 | checked | not_held |
| ACC-HHS-NIH-TOTAL | 2021 | President's Budget | program_level | available | $38,371,491,000 | checked | not_held |
| ACC-HHS-NIH-TOTAL | 2022 | Enacted | program_level | available | $44,959,000,000 | checked | not_held |
| ACC-HHS-NIH-TOTAL | 2024 | Senate Reported | program_level | available | $49,224,000,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2023 | Enacted | discretionary_designated | available | $35,928,553,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2024 | President's Budget | discretionary | available | $36,615,420,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2024 | President's Budget | discretionary | enacted | $36,615,420,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2024 | President's Budget | discretionary_designated | available | $36,615,420,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2024 | Senate Reported | discretionary_designated | available | $34,097,216,000 | checked | not_held |
| ACC-HHS-ACF-TOTAL | 2027 | President's Budget | discretionary | available | $26,225,579,000 | checked | components_unknown |
| ACC-HHS-ACF-TOTAL | 2027 | President's Budget | discretionary | enacted | $26,225,579,000 | checked | components_unknown |
| ACC-HHS-ACF-TOTAL | 2027 | President's Budget | discretionary_designated | available | $26,225,579,000 | checked | components_unknown |
| ACC-HHS-OS-TOTAL | 2018 | President's Budget | program_level | available | $3,399,444,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2018 | House Reported | program_level | available | $3,477,853,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2018 | Senate Reported | program_level | available | $3,493,650,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2018 | Enacted | program_level | available | $3,969,150,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2019 | President's Budget | program_level | available | $13,547,742,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2020 | House Reported | program_level | available | $4,548,592,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2022 | Enacted | program_level | available | $5,826,514,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2023 | House Reported | program_level | available | $8,226,117,000 | checked | not_held |
| ACC-HHS-OS-TOTAL | 2025 | Senate Reported | program_level | available | $2,014,795,000 | checked | missing_figure |
| ACC-HHS-OS-TOTAL | 2025 | Enacted | program_level | available | $1,997,786,000 | checked | missing_figure |
| ACC-HHS-OS-TOTAL | 2026 | House Reported | program_level | available | $1,920,407,000 | checked | missing_figure |
| ACC-HHS-OS-TOTAL | 2026 | Senate Reported | program_level | available | $2,017,182,000 | checked | missing_figure |
| ACC-HHS-OS-TOTAL | 2026 | Enacted | program_level | available | $2,019,337,000 | checked | missing_figure |
| ACC-HHS-OS-TOTAL | 2027 | House Reported | program_level | available | $1,811,658,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2024 | Enacted | discretionary_designated | available | $117,442,958,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2025 | President's Budget | discretionary_designated | available | $122,119,671,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2025 | House Reported | discretionary_designated | available | $107,640,959,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2025 | Senate Reported | discretionary_designated | available | $122,557,069,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2025 | Enacted | discretionary_designated | available | $114,965,801,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2026 | President's Budget | discretionary_designated | available | $83,067,660,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2026 | House Reported | discretionary_designated | available | $108,066,863,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2026 | Senate Reported | discretionary_designated | available | $116,857,390,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2026 | Enacted | discretionary_designated | available | $114,797,239,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2027 | House Reported | discretionary | available | $110,767,281,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2027 | House Reported | discretionary | enacted | $110,767,281,000 | checked | missing_figure |
| ACC-HHS-TITLE-II-TOTAL | 2027 | House Reported | discretionary_designated | available | $110,767,281,000 | checked | missing_figure |

## 3. 302(b) comparison lines

Read twice (page images: allocation_302b_rows.csv; text layer: allocation_302b.py, allocation_302b_text.csv; they agree on every row both read). 200 observations recorded on ACC-CJS-BILL-TOTAL and ACC-LHHS-BILL-TOTAL (d8_records.py; per-cell outcome in allocation_302b_lines.csv). Each report's table pages gain its 302(b) page. Notes:

- **Recorded as budget authority (owner, 2026-10-15):** H.Rept. 118-585 (FY2025 House Labor-HHS) p.288 prints 185,797 under '302(b) Allocation, Outlays' with the budget-authority cell blank. The report's own text calls $185,797,000,000 the 302(b) allocation of current year discretionary funding, so it is recorded as allocation_302b_discretionary, budget authority (OBS-LHHS-5081), with its printed position in source_table_or_section.
- **Not recorded, no bill total to attach to:** H.Rept. 114-699, 115-244, 115-862, 116-62 (Labor-HHS House FY2017-FY2020): D-7 holds no bill total from those reports (they print only the RECAP's current-year grand total).
- **No table:** H.Rept. 116-450 (FY2021 House Labor-HHS) prints no 302(b) comparison.
- **No allocation printed:** S.Rept. 119-44 and 119-55 (FY2026) print 'Committee allocation' as leaders (footnote: no 302(a) allocation); only the bill's figures are recorded. S.Rept. 115-139 and 115-150 (FY2018) head the column 'Committee guidance' (no 302(a) allocation for FY2018); recorded as the allocation with the printed header in source_table_or_section.
- **Not recorded by design:** the Senate Security / Nonsecurity and Defense / Non-defense splits of the discretionary row (the spec records discretionary and mandatory).
- The scored figures anchor no basis (ACCOUNTING_RULES 'Totals' 7).
