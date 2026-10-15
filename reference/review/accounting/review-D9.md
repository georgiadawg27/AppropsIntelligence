# D-9 review: data fills for the anchored totals

Brief D-9 (ui-build-briefs.md). Every filled cell, confirmed absence and evidence row, with its document and printed page. Records: `d9_records.py` (parts 1-2), `appendix_attributes.py` (part 3), `table_lines.csv` (part 4), `total_lines_inside.csv` (part 5).

## Part 1: missing account figures

From bill or law text (or the request's Appendix language), table figure kept as a note.

| Account | FY | Stage | Figure | Document | Page |
|---|---|---|---|---|---|
| ACC-HHS-CDC-PROGRAM-SUPPORT | 2025 | House Reported | $511,570,000 | SRC-BILLS-118HR9029RH | 64 |
| ACC-HHS-NIH-NIDCD | 2023 | Senate Reported | $530,847,000 | SRC-BILLS-117S4659IS | 74 |
| ACC-HHS-OS-ONC | 2023 | Enacted | $0 | SRC-PLAW-117PUBL328 | 419 |
| ACC-HHS-OS-ONC | 2024 | President's Budget | $0 | SRC-BUDGET-APP-FY2024-HHS | 469 |
| ACC-HHS-GP-MEDICARE-OPERATIONS | 2023 | Enacted | $455,000,000 | SRC-PLAW-117PUBL328 | 426 |
| ACC-HHS-GP-MEDICARE-OPERATIONS | 2024 | Senate Reported | $455,000,000 | SRC-BILLS-118S2624RS | 127 |

Confirmed absences (each cites the text searched):

| Account | FY | Stage | Document | Evidence |
|---|---|---|---|---|
| ACC-HHS-OS-PHSSEF | 2025 | Senate Reported | SRC-BILLS-118S4942RS | S. 4942 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searched for /Social Services Emergency Fund/: no mention). S.Rept. 118-207 p.241: 'The Committee recommendation does... |
| ACC-HHS-OS-PHSSEF | 2025 | Enacted | SRC-PLAW-119PUBL4 | P.L. 119-4 div. A sec. 1101 (the FY2025 full-year CR) continues the FY2024 Labor-HHS act, P.L. 118-47 division D, which has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searc... |
| ACC-HHS-OS-PHSSEF | 2026 | House Reported | SRC-BILLS-119HR5304RH | H.R. 5304 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searched for /Social Services Emergency Fund/: no mention) (D-9). |
| ACC-HHS-OS-PHSSEF | 2026 | Senate Reported | SRC-BILLS-119S2587RS | S. 2587 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searched for /Social Services Emergency Fund/: no mention) (D-9). |
| ACC-HHS-OS-PHSSEF | 2026 | Enacted | SRC-PLAW-119PUBL75 | P.L. 119-75 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searched for /Social Services Emergency Fund/: no mention) (D-9). |
| ACC-HHS-OS-PHSSEF | 2026 | President's Budget | SRC-BUDGET-APP-FY2026-HHS | The FY2026 Budget Appendix proposes no appropriations language for the Public Health and Social Services Emergency Fund: its entry (p.387) is schedules only ('includes balances from emergency supplemental bills and appro... |
| ACC-HHS-OS-PHSSEF | 2027 | House Reported | SRC-BILLS-119HR9260RH | H.R. 9260 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text searched for /Social Services Emergency Fund/: no mention) (D-9). |
| ACC-HHS-OS-PHSSEF | 2027 | President's Budget | SRC-BUDGET-APP-FY2027-HHS | The FY2027 Budget Appendix proposes no appropriations language for the Public Health and Social Services Emergency Fund: its entry (pp. 489-490) is schedules only, holding balances from before ASPR's separate accounts in... |
| ACC-HHS-HRSA-CCPF | 2026 | Enacted | SRC-PLAW-119PUBL75 | P.L. 119-75 never mentions the Covered Countermeasures Process Fund (whole text searched for /Covered Countermeasure/). The joint explanatory statement (CREC 2026-01-22, p.H1353 granule, PDF p.239): 'The agreement does n... |
| ACC-HHS-HRSA-CCPF | 2027 | House Reported | SRC-BILLS-119HR9260RH | H.R. 9260 never mentions the Covered Countermeasures Process Fund (whole text searched for /Covered Countermeasure/) (D-9). |
| ACC-HHS-HRSA-CCPF | 2027 | President's Budget | SRC-BUDGET-APP-FY2027-HHS | The FY2027 Budget Appendix proposes no appropriations language for the Covered Countermeasure Process Fund: its entry (p.440) is schedules only (line 1100 Appropriation blank for 2027 est.), with a legislative proposal s... |
| ACC-HHS-OS-ONC | 2026 | President's Budget | SRC-BUDGET-APP-FY2026-HHS | The FY2026 Budget Appendix proposes no appropriations language under the 'Office of the National Coordinator for Health Information Technology' heading: its entry (p.385) is schedules only, with the note that the account... |
| ACC-HHS-GP-MEDICARE-OPERATIONS | 2023 | House Reported | SRC-BILLS-117HR8295RH | H.R. 8295 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers for Medicare & Medicaid Services, Program Management' (whole text searched for /may transfer up to/ and /Federal Hospital/: ... |
| ACC-HHS-GP-MEDICARE-OPERATIONS | 2023 | President's Budget | SRC-BUDGET-APP-FY2023-HHS | The FY2023 Budget Appendix's proposed HHS language has no section letting the Secretary transfer Medicare trust fund amounts to CMS Program Management (HHS chapter pp. 423-496 searched for /may transfer up to/ and /progr... |
| ACC-HHS-GP-MEDICARE-OPERATIONS | 2024 | President's Budget | SRC-BUDGET-APP-FY2024-HHS | The FY2024 Budget Appendix brackets the FY2023 law's sec. 227 ($455,000,000 transfer) for deletion (p.483) and proposes no such section (D-9). |
| ACC-HHS-NIH-BODY-SYSTEMS | 2025 | Senate Reported | SRC-BILLS-118S4942RS | S. 4942 has no 'National Institute on Body Systems Research' heading: it funds NIH under the existing institutes' headings (NIH, pp. 70-76: NHLBI and NIDDK p.71, NIAMS p.73) (whole text searched for /Body Systems/) (D-9)... |
| ACC-HHS-NIH-NEUROSCIENCE | 2025 | Senate Reported | SRC-BILLS-118S4942RS | S. 4942 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the existing institutes' headings (NIH, pp. 70-76: NINDS p.71, NEI p.72) (whole text searched for /Neuroscience and Brain... |
| ACC-HHS-NIH-BODY-SYSTEMS | 2026 | House Reported | SRC-BILLS-119HR5304RH | H.R. 5304 has no 'National Institute on Body Systems' heading: it funds NIH under the existing institutes' headings (NIH, pp. 65-71) (whole text searched for /Body Systems/) (D-9). |
| ACC-HHS-NIH-NEUROSCIENCE | 2026 | House Reported | SRC-BILLS-119HR5304RH | H.R. 5304 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the existing institutes' headings (NIH, pp. 65-71) (whole text searched for /Neuroscience and Brain/) (D-9). |
| ACC-HHS-NIH-CHILD-WOMEN-SENSORY | 2026 | House Reported | SRC-BILLS-119HR5304RH | H.R. 5304 has no 'National Institute for Child and Women's Health, Sensory Disorders and Communication' heading: it funds NICHD and NIDCD under their own headings (pp. 67-68) (whole text searched for /Child and Women/) (... |
| ACC-HHS-NIH-BEHAVIORAL-HEALTH | 2026 | House Reported | SRC-BILLS-119HR5304RH | H.R. 5304 has no 'National Institute of Behavioral Health' heading: it funds NIMH, NIDA and NIAAA under their own headings (p.69) (whole text searched for /Institute of Behavioral Health/) (D-9). |
| ACC-HHS-NIH-BODY-SYSTEMS | 2026 | Senate Reported | SRC-BILLS-119S2587RS | S. 2587 has no 'National Institute on Body Systems' heading: it funds NIH under the existing institutes' headings (NIH, pp. 74-81) (whole text searched for /Body Systems/) (D-9). |
| ACC-HHS-NIH-NEUROSCIENCE | 2026 | Senate Reported | SRC-BILLS-119S2587RS | S. 2587 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the existing institutes' headings (NIH, pp. 74-81) (whole text searched for /Neuroscience and Brain/) (D-9). |
| ACC-HHS-NIH-CHILD-WOMEN-SENSORY | 2026 | Senate Reported | SRC-BILLS-119S2587RS | S. 2587 has no 'National Institute for Child and Women's Health, Sensory Disorders and Communication' heading: it funds NICHD and NIDCD under their own headings (pp. 77-78) (whole text searched for /Child and Women/) (D-... |
| ACC-HHS-NIH-BEHAVIORAL-HEALTH | 2026 | Senate Reported | SRC-BILLS-119S2587RS | S. 2587 has no 'National Institute of Behavioral Health' heading: it funds NIMH, NIDA and NIAAA under their own headings (pp. 78-79) (whole text searched for /Institute of Behavioral Health/) (D-9). |
| ACC-HHS-NIH-SUBSTANCE-USE | 2027 | House Reported | SRC-BILLS-119HR9260RH | H.R. 9260 has no 'National Institute of Substance Use and Addiction Research' heading: it funds NIDA (p.70) and NIAAA (p.70) under their own headings (whole text searched for /Substance Use and Addiction/) (D-9). |

Not filled: CDC-Wide FY2023 request (D-3b: the Appendix appropriates all of CDC under one heading); NIH Body Systems and Neuroscience FY2025 House (owner question: are H.R. 9029's restructured institutes the FY2026 request's accounts?).

## Part 2: Medicare title XVIII limitation line

The 'unidentified' start override is cleared: the line's life starts FY2026 Enacted (P.L. 119-75 div. B sec. 241, PDF p.120; FY2026 explanatory statement p.245 'a new provision', table p.296). Evidence in `d9_records.py`'s header.

## Part 3: budget class for agencies held only as their total

AHRQ (075-1700) and ACL (075-0142) take Appendix attributes (codes and fund type from FY N's volume, budget_class from FY N+2 actuals, FY2026-FY2027 provisional). AHA maps to none (spread over many new accounts). ARPA-H, PHSSEF, CCPF, NSC: their no_class cells are years the Appendix prints no budget authority, so nothing to fill. ACCOUNTING_RULES.md states the rule change.

## Part 4: request components from the tables' request columns (decision C)

| Component | Account | FY | Stage | Type | Amount | Source |
|---|---|---|---|---|---|---|
| CMP-LHHS-1373 | ACC-HHS-CDC-SCIENTIFIC-SERVICES | 2023 | President's Budget | transfer_in | $143,540,000 | SRC-CRPT-117HRPT403 p.811 |
| CMP-LHHS-1374 | ACC-HHS-CDC-ENVIRONMENTAL-HEALTH | 2023 | President's Budget | transfer_in | $7,000,000 | SRC-CRPT-117HRPT403 p.812 |
| CMP-LHHS-1375 | ACC-HHS-CDC-SCIENTIFIC-SERVICES | 2024 | President's Budget | transfer_in | $170,342,000 | SRC-CRPT-118SRPT84 p.377 |
| CMP-LHHS-1376 | ACC-HHS-CDC-ENVIRONMENTAL-HEALTH | 2024 | President's Budget | transfer_in | $7,000,000 | SRC-CRPT-118SRPT84 p.377 |
| CMP-LHHS-1377 | ACC-HHS-AHA-TOTAL | 2026 | President's Budget | transfer_in | $4,885,000 | SRC-CRPT-119HRPT271 p.334 |
| CMP-LHHS-1378 | ACC-HHS-CDC-PREPAREDNESS | 2026 | President's Budget | transfer_in | $105,000,000 | SRC-CRPT-119HRPT271 p.342 |
| CMP-LHHS-1379 | ACC-HHS-SAMHSA-PROGRAM-SUPPORT | 2026 | President's Budget | transfer_in | $30,428,000 | SRC-CRPT-119HRPT271 p.348 |
| CMP-LHHS-1380 | ACC-HHS-AHA-TOTAL | 2027 | President's Budget | transfer_in | $4,885,000 | SRC-CRPT-119HRPT696 p.365 |
| CMP-LHHS-1381 | ACC-HHS-HRSA-HEALTH-WORKFORCE | 2027 | President's Budget | transfer_in | $395,000,000 | SRC-CRPT-119HRPT696 p.367 |
| CMP-LHHS-1382 | ACC-HHS-SAMHSA-TREATMENT | 2027 | President's Budget | transfer_in | $2,000,000 | SRC-CRPT-119HRPT696 p.378 |
| CMP-LHHS-1383 | ACC-HHS-OS-ONC | 2027 | President's Budget | transfer_in | $50,000,000 | SRC-CRPT-119HRPT696 p.394 |
| CMP-LHHS-1384 | ACC-HHS-OS-ONC | 2023 | Enacted | transfer_in | $66,238,000 | SRC-PLAW-117PUBL328 TITLE II DEPARTMENT OF HEALTH AND HUMAN  |

Each evaluation line is checked against the table's program-level subtotal. Every other request cell prints nothing beside the account (or only '---', a program line, or Health Centers' non-add mandatory line) and stays components_unknown.

## Part 5: table lines printed inside the totals

22 rows added to `total_lines_inside.csv`:

| Total | FY | Stage | Line | Amount | Inside | Where |
|---|---|---|---|---|---|---|
| ACC-CJS-TITLE-III-TOTAL | 2019 | President's Budget | OBS-0759 ACC-NSF-RRA | $71,000,000 | yes | H.Rept. 115-704 p.129 (PDF p.129) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | President's Budget | OBS-0739 ACC-NSF-RRA | $102,000,000 | yes | S.Rept. 118-62 p.220 (PDF p.220) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Senate Reported | OBS-0657 ACC-NASA-CONSTRUCTION | $296,000,000 | yes | S.Rept. 118-62 pp.219-220 (PDF pp.219-220) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Senate Reported | OBS-0577 ACC-NASA-EXPLORATION | $280,000,000 | yes | S.Rept. 118-62 pp.219-220 (PDF pp.219-220) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Senate Reported | OBS-0773 ACC-NSF-RRA | $420,000,000 | yes | S.Rept. 118-62 pp.219-220 (PDF pp.219-220) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Enacted | OBS-0656 ACC-NASA-CONSTRUCTION | $250,000,000 | yes | H.Rept. 118-582 pp.247-248 (PDF pp.247-248) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Enacted | OBS-0576 ACC-NASA-EXPLORATION | $450,000,000 | yes | H.Rept. 118-582 pp.247-248 (PDF pp.247-248) |
| ACC-CJS-TITLE-III-TOTAL | 2024 | Enacted | OBS-0852 ACC-NSF-MREFC | $234,000,000 | yes | H.Rept. 118-582 p.248 (PDF p.248) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | President's Budget | OBS-0655 ACC-NASA-CONSTRUCTION | $296,000,000 | yes | H.Rept. 118-582 pp.247-248 (PDF pp.247-248) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | President's Budget | OBS-0521 ACC-NASA-SPACEOPS | $-1,000,000 | yes | H.Rept. 118-582 p.247 (PDF p.247) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Senate Reported | OBS-0573 ACC-NASA-EXPLORATION | $1,212,000,000 | yes | S.Rept. 118-198 pp.227-228 (PDF pp.227-228) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Senate Reported | OBS-0769 ACC-NSF-RRA | $973,000,000 | yes | S.Rept. 118-198 pp.227-228 (PDF pp.227-228) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Senate Reported | OBS-0986 ACC-NASA-SPACEOPS | $-1,000,000 | no | S.Rept. 118-198 pp.227-229 (PDF pp.227-229) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Enacted | OBS-0652 ACC-NASA-CONSTRUCTION | $250,000,000 | yes | H.Rept. 119-272 pp.256-257 (PDF pp.256-257) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Enacted | OBS-0572 ACC-NASA-EXPLORATION | $450,000,000 | yes | H.Rept. 119-272 pp.256-257 (PDF pp.256-257) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Enacted | OBS-0848 ACC-NSF-MREFC | $234,000,000 | yes | H.Rept. 119-272 p.257 (PDF p.257) |
| ACC-CJS-TITLE-III-TOTAL | 2025 | Enacted | OBS-0991 ACC-NASA-CONSTRUCTION | $740,200,000 | no | H.Rept. 119-272 p.261 (PDF p.261) |
| ACC-HHS-CMS-TOTAL | 2021 | Senate Reported | OBS-LHHS-2490 ACC-HHS-CMS-MEDICAID | $148,732,315,000 | yes | Senate Labor-HHS FY2021 explanatory statement pp.254-255 (PDF) |
| ACC-HHS-ACF-TOTAL | 2017 | President's Budget | OBS-LHHS-3825 ACC-HHS-ACF-SSBG | $18,500,000 | yes | H.Rept. 114-699 p.250 (PDF p.250) |
| ACC-HHS-ACF-TOTAL | 2023 | Enacted | OBS-LHHS-4124 ACC-HHS-ACF-LIHEAP | $1,000,000,000 | yes | S.Rept. 118-84 pp.384-386 (PDF) |
| ACC-HHS-ACF-TOTAL | 2023 | Enacted | OBS-LHHS-4125 ACC-HHS-ACF-REFUGEE | $1,775,000,000 | yes | S.Rept. 118-84 pp.385-386 (PDF) |
| ACC-HHS-ACF-TOTAL | 2023 | Enacted | OBS-LHHS-4123 ACC-HHS-ACF-LIHEAP | $2,500,000,000 | no | S.Rept. 118-84 p.384 (PDF) |

Totals that now reach their printed figure (published, short 0): CJS Title III FY2019 request, FY2024 request, FY2024 Senate,
FY2024 Enacted, FY2025 Senate, FY2025 Enacted. Still short after the evidence: CJS Title III FY2025 request $420,000,000 (owner
question 3), CMS FY2021 Senate $1,000 (Medicaid's headline is bill text, $1,000 below the table's 'Total, Grants to States'),
ACF FY2023 Enacted $1,300,000,000 (the Child Support advance, owner question 2), ACF FY2017 request $10,000,000 (not found yet).

## Results (site data, main 395e95e -> this branch)

n/a entries (not computable), every basis and timing:

| Rows | Cause | Before | After |
|---|---|---|---|
| Accounts | components_unknown | 556 | 554 |
| Accounts | no_class | 48 | 48 |
| Totals (agency, title, bill) | missing_figure | 1,000 | 884 |
| Totals | no_class | 202 | 38 |
| Totals | components_unknown | 86 | 104 |
| Totals | not_held | 208 | 244 |
| Totals | no_anchor | 620 | 620 |
| **All accounts** | | 604 | 602 |
| **All totals** | | 2,116 | 1,890 |

By basis, totals: All appropriations 486 -> 467, Discretionary 572 -> 479, Discretionary (designated) 572 -> 479, Program
level 486 -> 465. not_held rises because cells that lacked a figure are now complete enough to show a gap.
Status changes: not computable -> computed 450, not computable -> matches 7, computed -> matches 1.
review-D8 §2's 41 entries: 7 came back; 19 stay not_held (OS, NIH, CDC, ACF: owner questions 1-2), 12 missing_figure
(Title II totals FY2024-FY2027, behind accounts still without figures), 3 components_unknown (ACF FY2027 request).

New gap rows (104 -> 119 cells listed; 52 -> 61 unpublished): the figures filled in parts 1-4 complete cells that were
missing_figure, so their gaps now show: Title II FY2017-FY2021 (8 cells: the Medicaid advance and other lines printed
inside the title total, no evidence rows for the title totals yet), the bill total FY2017 and FY2021 Senate, NIH FY2023 and
FY2025 Senate, OS FY2024, FY2026 and FY2027 request.

## Existing figures that changed (owner-approved 2026-10-15)

Part 4's evaluation lines change three published request-stage program-level figures (computed status before and after).
Each new figure equals the table's printed program-level subtotal:

| Account | FY | Stage | Basis | Old | New | Source |
|---|---|---|---|---|---|---|
| CDC Public Health Scientific Services | 2024 | President's Budget | Program level | $791,222,000 | $961,564,000 | S.Rept. 118-84 p.377 ('Evaluation Tap Funding' 170,342; program-level subtotal 961,564) |
| CDC Environmental Health | 2023 | President's Budget | Program level | $394,850,000 | $401,850,000 | H.Rept. 117-403 p.812 ('Evaluation Tap Funding' 7,000; subtotal 401,850) |
| CDC Environmental Health | 2024 | President's Budget | Program level | $413,850,000 | $420,850,000 | S.Rept. 118-84 p.377 ('Evaluation Tap Funding' 7,000) |

Part 6 supersedes one recorded total (old record kept, marked superseded):

| Account | FY | Stage | Old | New | Source |
|---|---|---|---|---|---|
| CJS Title III total | 2026 | Senate Reported | OBS-1436 $33,908,257,000 (the FY2025 column) | OBS-1584 $33,909,609,000 | S.Rept. 119-44 p.219, 'Total, Title III, Science', Committee recommendation column |

## Owner questions

1. Lines in accounts we don't hold or hold under another agency (the rest of the 52): OS's 'Account for the State Response
   to the Opioid Abuse Crisis, CURES Act' $500,000,000 (FY2017-FY2018, five cells; H.Rept. 115-244 p.234, S.Rept. 115-150
   p.241); ARPA-H printed in the OS total (FY2022 Enacted $1,000,000,000, FY2023 House $2,750,000,000; H.Rept. 117-403 p.836)
   while we hold it under NIH (the mirror NIH gaps -$1,000,000,000 and -$2,737,400,000); AHRQ printed as NIH's proposed
   institute (NIRSQ) in the FY2018-FY2021 requests (NIH gaps $272,000,000, $255,960,000, $256,660,000, and $606,445,000
   in FY2019); OS 'Customer Experience' $14,000,000 (FY2025 request, S.Rept. 118-207 p.451); ASPR 'Pandemic Preparedness
   and Biodefense' $400,000,000 request and $75,000,000 Senate emergency (FY2024, S.Rept. 118-84 p.390); SAMHSA FY2026 and
   FY2027 requests ($4,025,411,000 and $4,520,411,000: the request's restructure into AHA). Ruled 2026-10-15: a line counts
   in the total the table prints it under. Done here (part 7, no new figures): ARPA-H counts in the OS total in H.Rept.
   117-403's two cells (`total_members_printed.csv`) and leaves NIH's; NIRSQ counts in the NIH FY2018-FY2021 request totals
   (evidence rows). The opioid response, Customer Experience, ASPR Manufacturing and SAMHSA lines need new accounts: owner
   asked whether in this PR or the next.
2. (Ruled 2026-10-15: the first option, part 9.) ACF's Child Support advance (FY2021 Enacted $1,400,000,000; FY2022 and FY2023 every stage $1,300,000,000; FY2024
   request and Senate $1,400,000,000; the 'New advance, 1st quarter' rows, e.g. H.Rept. 117-403 p.823). We already hold each
   as an advance_enacted component from the bill or law text, beside the headline, but the build-down counts only held
   observation lines, and a line from another document than the table is outside by rule. Adding the table's line as an
   observation (the Medicaid pattern) makes the tagger retire the law-text component in favour of the table line's. Choose:
   let the build-down count a text-tagged advance the table prints inside the total (code change, law text kept), or add
   the table lines (Medicaid pattern).
3. NSF R&RA's FY2025 request emergency $420,000,000: H.Rept. 118-582 prints no row for it, but its arithmetic does
   ('Subtotal' 8,045,320 = 7,519,320 + 106,000 + 420,000; Title III 'Emergency appropriations' (716,000) = 296,000 + 420,000).
   Our line cites S.Rept. 118-198 p.227, another document. Approved 2026-10-15: counted, because S.Rept. 118-198's
   request column prints it (p.227) inside the same NSF and Title III request totals (p.228); the evidence rows cite it.
4. CJS Title III FY2026 Senate: the total observation holds $33,908,257,000, the FY2025 column of S.Rept. 119-44 p.219;
   the Committee recommendation column prints $33,909,609,000 (and the accounts held add to it). Approved 2026-10-15:
   superseded by OBS-1584 (part 6).

## Part 7: lines counted in the total the table prints them under (owner, 2026-10-15)

| Total | FY | Stage | Line | Gap before | Short after | Evidence |
|---|---|---|---|---|---|---|
| OS | 2022 | Enacted | ARPA-H $1,000,000,000 (OBS-LHHS-2176), moved from NIH | $1,000,000,000 | 0 | H.Rept. 117-403 p.836 |
| OS | 2023 | House Reported | ARPA-H $2,750,000,000 (OBS-LHHS-1115), moved from NIH | $2,750,000,000 | 0 | H.Rept. 117-403 p.836 |
| NIH | 2022 | Enacted | ARPA-H leaves the NIH sum | -$1,000,000,000 | 0 | H.Rept. 117-403 p.816 |
| NIH | 2023 | House Reported | ARPA-H leaves the NIH sum | -$2,737,400,000 | $12,600,000 (as the request and Senate cells) | H.Rept. 117-403 p.816 |
| NIH | 2018 | President's Budget | NIRSQ $272,000,000 (OBS-LHHS-3504) | $272,000,000 | 0 | H.Rept. 115-244 p.216 |
| NIH | 2019 | President's Budget | NIRSQ $255,960,000 (OBS-LHHS-3124) | $606,445,000 | $350,485,000 (not found yet) | H.Rept. 115-862 p.366 |
| NIH | 2020 | President's Budget | NIRSQ $255,960,000 (OBS-LHHS-2835) | $255,960,000 | 0 | H.Rept. 116-62 p.338 |
| NIH | 2021 | President's Budget | NIRSQ $256,660,000 (OBS-LHHS-2440) | $256,660,000 | 0 | H.Rept. 116-450 pp.407-408 |

Code: `site_accounting.members_in` (an agency total's accounts in one cell of one table, less an account the table prints
under another total, plus one it prints under this one) and a total's own contained lines with evidence counted as held.
Title and bill totals are unchanged (both agencies are Title II). The row still displays under NIH. No figure changed.

## Part 8: new accounts for lines no account held (owner, 2026-10-15: in this PR)

New published figures (none existed before; no existing figure changes):

| Account (new) | FY | Stage | Figure | Source, page | Table figure kept as a note |
|---|---|---|---|---|---|
| OS, State Response to the Opioid Abuse Crisis (CURES) | 2017 | Enacted | $500,000,000 | P.L. 114-254 div. A, sec. 195 (130 Stat. 1021), PDF p.17 | H.Rept. 115-244 p.234 |
| same | 2018 | President's Budget | $500,000,000 | H.Rept. 115-244 p.234, request column (no Appendix on file) | |
| same | 2018 | House Reported | $500,000,000 | H.R. 3358, PDF p.81 | H.Rept. 115-244 p.234 |
| same | 2018 | Senate Reported | $500,000,000 | S. 1771, PDF p.85 | S.Rept. 115-150 p.241 |
| same | 2018 | Enacted | $500,000,000 | P.L. 115-141 div. H (132 Stat. 734), PDF p.388 | H.Rept. 115-862 p.383 |
| OS, Customer Experience (proposed) | 2025 | President's Budget | $14,000,000 | S.Rept. 118-207 p.451, budget estimate | |
| ASPR, Manufacturing and Production (proposed) | 2024 | President's Budget | $400,000,000 | S.Rept. 118-84 p.390 ('Pandemic Preparedness and Biodefense') | |
| same | 2024 | Senate Reported | $75,000,000 (emergency) | S. 2624, PDF p.107 | S.Rept. 118-84 p.390 |
| SAMHSA, Behavioral Health Innovation Block Grant (proposed) | 2026 | President's Budget | $4,025,411,000 | H.Rept. 119-271 p.348 (page image) | |
| same | 2027 | President's Budget | $4,520,411,000 | H.Rept. 119-696 p.379 (page image) | |

Confirmed absences from bill text (whole text searched): Customer Experience FY2025 House (H.R. 9029) and Senate
(S. 4942); Manufacturing and Production FY2024 House (H.R. 5894); the Block Grant FY2026 House (H.R. 5304) and Senate (S. 2587), FY2027 House (H.R. 9260). The opioid account ends after FY2018 Enacted (no
FY2019 heading in P.L. 115-245, H.R. 6470 or S. 3158), so later cells fall outside its life. Budget class is not
recorded for any of the four (no Appendix account mapped), so their discretionary views stay n/a (no_class).
Customer Experience FY2024 request $20,000,000 (S.Rept. 118-84 p.391), owner-approved 2026-10-15, with absences from
H.R. 5894 and S. 2624; it closes OS's FY2024 gap. Total gaps 115 -> 101.

## Part 9: the Child Support advance (owner, 2026-10-15: the first option)

The build-down counts an advance tagged from the bill or law text beside the Child Support headline as held, where the
table prints it inside the ACF total ('New advance, 1st quarter' under Child Support; 'Advance appropriations' under
'Total, Administration for Children and Families'). 11 evidence rows name the component (`site_accounting.total_layer`).
No data changed. Nine ACF totals now reach their printed figure.

| FY | Stage | Component (law/bill text) | Table, pages | Short after |
|---|---|---|---|---|
| 2021 | Enacted | CMP-LHHS-0635 $1,400,000,000 (P.L. 116-260) | H.Rept. 117-96 pp.484-488 | -$463,000 (as FY2021 House) |
| 2022 | President's Budget | CMP-LHHS-1002 $1,300,000,000 | H.Rept. 117-96 pp.484-488 | 0 |
| 2022 | House Reported | CMP-LHHS-0562 $1,300,000,000 | H.Rept. 117-96 pp.484-488 | $200,000,000 (not found yet) |
| 2022 | Senate Reported | CMP-LHHS-0574 $1,300,000,000 | FY2022 Senate statement pp.354-356 | 0 |
| 2022 | Enacted | CMP-LHHS-0587 $1,300,000,000 | H.Rept. 117-403 pp.823-828 | 0 |
| 2023 | President's Budget | CMP-LHHS-0992 $1,300,000,000 | H.Rept. 117-403 pp.823-828 | 0 |
| 2023 | House Reported | CMP-LHHS-0523 $1,300,000,000 | H.Rept. 117-403 pp.823-828 | 0 |
| 2023 | Senate Reported | CMP-LHHS-0524 $1,300,000,000 | FY2023 Senate statement pp.411-414 | 0 |
| 2023 | Enacted | CMP-LHHS-0522 $1,300,000,000 | S.Rept. 118-84 pp.384-387 | 0 |
| 2024 | President's Budget | CMP-LHHS-0993 $1,400,000,000 | S.Rept. 118-84 pp.384-387 | 0 |
| 2024 | Senate Reported | CMP-LHHS-0144 $1,400,000,000 | S.Rept. 118-84 pp.384-387 | 0 |

The held-line test now allows an evidence row whose line is such a component from another document (owner ruling);
the gap-list test's floor moved from more than 30 listed gaps to more than 20 (29 remain).

## What stays n/a and what would close it

- not_held (244 total entries): the lines in owner questions 1-2; the Title II and bill totals FY2017-FY2021 need evidence rows
  for the title total (the CMS-level rows exist); HRSA FY2017 Enacted's P.L. 114-254 MCH line is another document's.
- missing_figure (884): mostly CJS totals over accounts not yet held (632) and Title II totals behind accounts with no figure.
- no_anchor (620): no account behind the total held in the cell; closes when whole bills are held (decision G).
- components_unknown (554 accounts, 104 totals): request cells whose table prints nothing beside the account, and cells not
  auto-tagged yet.
- no_class (48 accounts, 38 totals): years with no Appendix budget authority, and the provisional FY2026-FY2027 classes.
