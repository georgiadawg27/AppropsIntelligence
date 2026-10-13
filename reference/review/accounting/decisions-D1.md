# Decisions for the owner: D-1 accounting follow-up (2026-10-13)

One section per open item. Each gives what the check found, the documents, pages and amounts side by side, what
each option would change, and a recommendation. Nothing here has been applied: every validation named below is
still flagged and pending. No option changes a headline figure unless it says so.

Amounts are dollars. "Report" figures are printed in thousands; the dollar form is the figure x 1,000.

Counts are from `scripts/measure_reconcile.py` on this branch: base 810 pass / 0 fail; printed views 217 pass,
24 fail, 118 not checked.

---

## 1. headline_superseded: Medicaid FY2021, $1,000 (`VAL-MRC-HEADLINE-SUPERSEDED`, 4 cells)

**Found.** Four printed Medicaid views differ from the computed ones by $1,000 each. Each printed view counts the
report's own headline; the bill text superseded that headline with a figure $1,000 lower.

| | House (H.Rept. 116-450 / H.R. 7614) | Senate (FY2021 Senate explanatory statement / chair's draft) |
|---|---|---|
| Report headline, 'Total, Grants to States for Medicaid' (superseded) | OBS-LHHS-2294: 313,904,099 thousand, H.Rept. 116-450 p.412 | OBS-LHHS-2489: 313,904,099 thousand, explanatory statement p.254 |
| Bill text headline (stands) | OBS-LHHS-4102: $313,904,098,000, H.R. 7614 p.78 | OBS-LHHS-4103: $313,904,098,000, Senate chair's draft p.77 |
| Printed 'appropriated in this bill' | OBS-LHHS-2297: 462,636,414 thousand (p.412) | OBS-LHHS-2492: 462,636,414 thousand (p.254) |
| Computed (bill headline + advance_enacted $148,732,315,000) | $462,636,413,000 | $462,636,413,000 |
| Printed 'program level, available this fiscal year' | OBS-LHHS-2298: 453,807,174 thousand (p.412) | OBS-LHHS-2493: 453,807,174 thousand (p.254) |
| Computed (bill headline + advance_available $139,903,075,000) | $453,807,173,000 | $453,807,173,000 |

The $1,000 is the report rounding the bill's $313,904,098,000 up to 313,904,099 thousand.

**Options.**
- A. Resolve the cause item as explained (report rounding to thousands). No data change; the 4 cells stay
  failing in the counts but are no longer pending. Headline effect: none.
- B. Compute these printed views from the report's own (superseded) headline when the printed total names it. The 4
  cells would pass (+4 pass, -4 fail), but the views would then rest on a figure the bill text replaced, which the
  "bill text beats the table" rule rules out. Headline effect: none.
- C. Allow a tolerance for thousands rounding. That loosens the check; not offered as a real option.

**Recommendation: A.**

---

## 2. agency_total_only: AHRQ (`VAL-MRC-AGENCY-TOTAL-ONLY`, 3 cells)

**Found.** AHRQ is held only as its agency total (no account lines), and a total's own paragraph is not read for
components, so the section 241 amount its program level counts is not tagged. Each difference equals the section 241
amount printed in AHRQ's own paragraph exactly.

| Cell | Printed BA (headline) | Printed program level | Difference | AHRQ paragraph's section 241 amount |
|---|---|---|---|---|
| FY2021 House | OBS-LHHS-2275: 143,091 thousand, H.Rept. 116-450 p.412 | OBS-LHHS-2276: 343,000 thousand, p.412 | $199,909,000 | H.R. 7614 (P0081): "in addition to amounts provided herein, $199,909,000 shall be available from amounts available under section 241" |
| FY2022 House | OBS-LHHS-1847: 250,792 thousand, H.Rept. 117-96 p.481 | OBS-LHHS-1848: 380,000 thousand, p.481 | $129,208,000 | H.R. 4502 (P0083): "$129,208,000 shall be available from amounts available under section 241" |
| FY2022 Senate | OBS-LHHS-2041: 353,000 thousand, FY2022 Senate explanatory statement p.351 | OBS-LHHS-2042: 380,000 thousand, p.351 | $27,000,000 | S. 3062 (P0083): "$27,000,000 shall be available to this appropriation ... from amounts prov[ided]" |

**Options.**
- A. Read an agency total's own paragraph for components when the agency has no account lines (AHRQ only, today).
  The text check would match AHRQ's total headlines to AHRQ's paragraph; the phrase rules then tag each section 241
  amount as a `transfer_in` beside the total. Expected: these 3 cells pass (+3 pass, -3 fail), plus a `transfer_in` on
  every other auto-tagged AHRQ total whose paragraph prints one (the exact count comes from the run). Headline
  effect: none.
- B. Add an AHRQ account line (Healthcare Research and Quality) beside the total. Larger: new account records and
  headlines in every year. Headline effect: new headline records, none changed.
- C. Leave as a known cause.

**Recommendation: A** (smallest change; the amounts are printed in the bill text we hold).

---

## 3. emergency_lines_not_held: ACF FY2023 Enacted (`VAL-MRC-EMERGENCY-LINES-NOT-HELD`, 1 cell)

**Found.** S.Rept. 118-84 prints, for ACF in its FY2023 appropriation column:

| Line | Observation | Amount | Page |
|---|---|---|---|
| Total, Administration for Children and Families | OBS-LHHS-0027 | 52,962,553 thousand | p.386 |
| Total, ACF (excluding emergencies) | OBS-LHHS-0028 | 47,687,553 thousand | p.387 |
| Total, Administration for Children and Families, discretionary | OBS-LHHS-0029 | 35,928,553 thousand | p.387 |

The difference, $5,275,000,000, is emergency-designated money the total counts from P.L. 117-328's other divisions.
We hold no ACF line for it (no ACF FY2023 Enacted observation is a supplemental or emergency line), so no
`designated/emergency` component takes it off. The lines themselves are on the report's table pages, which I could
not open in this session (the network policy denies govinfo.gov and congress.gov; `document_store/` is not in the
container).

**Options.**
- A. Record the report's ACF emergency lines for FY2023 Enacted (each a supplemental '(emergency)' line, which the
  existing retag rule turns into `designated/emergency`). If they add to $5,275,000,000 the cell passes (+1 pass,
  -1 fail). New observations; no existing headline changes. Needs the report pages.
- B. Treat the printed "excluding emergencies" total as not checkable where its scope includes other divisions' money
  we don't hold (like `account_not_held`): -1 fail, +1 not checked. No data change.
- C. Leave flagged.

**Recommendation: A**, in a session that can open S.Rept. 118-84 pp.386-387; B if you'd rather not add lines.

---

## 4. member_no_headline: CDC FY2023 House and Senate (`VAL-MRC-MEMBER-NO-HEADLINE`, 2 cells)

**Found.** CDC's program-level totals count the $160,000,000 Prevention and Public Health Fund block grant on the
CDC-Wide Activities line, but CDC-Wide Activities (ACC-HHS-CDC-PROGRAM-SUPPORT) has no headline in either cell, so
the PPHF row has nothing to sit on.

| | House | Senate |
|---|---|---|
| Printed CDC total (BA) | OBS-LHHS-0502: 9,596,054 thousand, H.Rept. 117-403 p.813 | OBS-LHHS-0550: 9,597,529 thousand, FY2023 Senate explanatory statement p.405 |
| Printed CDC program level | OBS-LHHS-0503: 10,499,354 thousand, p.813 | OBS-LHHS-0551: 10,500,829 thousand, p.405 |
| Computed | $10,339,354,000 | $10,340,829,000 |
| Difference | $160,000,000 | $160,000,000 |
| PPHF row with no headline | H.Rept. 117-403 p.248: 'Preventive Health and Health Services Block Grant' 160,000,000 (H.R. 8295 sec. 221) | explanatory statement p.234: 'Preventive Health and Health Services Block Grants' 160,000 thousand (S. 4659 sec. 221) |
| CDC-Wide paragraph in the bill text | H.R. 8295 P0050: $958,570,000 | S. 4659 P0050: $817,070,000 |

(FY2023 Enacted has the headline, OBS-LHHS-0934, 563,570 thousand, S.Rept. 118-84 p.377, and passes.)

**Options.**
- A. Record CDC-Wide Activities headlines for FY2023 House and Senate from the bill text ($958,570,000 and
  $817,070,000), cited to the paragraphs above, with the report-table figure as a note where it prints one. The PPHF
  rows then sit on them and both cells should pass (+2 pass, -2 fail). **Headline effect: two new headline figures**
  (none changed); needs your approval.
- B. Let a PPHF row whose account has no headline sit on the agency total instead. Rule change; no new figures;
  +2 pass expected.
- C. Leave flagged.

**Recommendation: A** (the bill text prints the figure; "bill text beats the table").

---

## 5. onc_no_headline: Office of the Secretary FY2023 Senate (`VAL-MRC-ONC-NO-HEADLINE`, 1 cell)

**Found.** OS's printed program level counts ONC's section 241 money, but ONC has no FY2023 Senate headline.

| | Amount | Source |
|---|---|---|
| Printed OS total (BA) | 5,608,850 thousand | OBS-LHHS-0563, FY2023 Senate explanatory statement p.419 |
| Printed OS program level | 5,747,916 thousand | OBS-LHHS-0564, p.419 |
| Computed | $5,673,678,000 | |
| Difference | $74,238,000 | |
| ONC paragraph, S. 4659 (P0099) | "... interoperable health information technology, $74,238,000 shall be from amounts made available under section 241 of the PHS Act." | bill text |
| ONC FY2023 House, for comparison | $0 headline, OBS-LHHS-1647 (H.Rept. 117-403 p.834); passes | |

**Options.**
- A. Record ONC's FY2023 Senate headline as $0 budget authority, as the bill text prints it (the phase 1b ONC rule:
  no new budget authority; the paragraph's whole amount is its section 241 `transfer_in`, beside). The cell should
  pass (+1 pass, -1 fail). **Headline effect: one new headline figure ($0)**; needs your approval.
- B. Leave flagged.

**Recommendation: A** (same rule as FY2024-FY2026 ONC).

---

## 6. prif_outside_printed_program_level: NIH FY2023 House and Senate (`VAL-MRC-PRIF-OUTSIDE-PRINTED-PROGRAM-LEVEL`, 2 cells)

**Found.** In FY2023 House and Senate the bills make the Pediatric Research Initiative Fund money "in addition to"
the Office of the Director's amount, so it is tagged beside the OD headline; the report's program level does not count
it. In every other year the PRIF $12,600,000 (or $3,000,000 in FY2022) is inside the OD headline and NIH's program
level passes.

| | House | Senate |
|---|---|---|
| OD paragraph | H.R. 8295 P0075: $2,537,213,000; "In addition to other funds appropriated for the Common Fund ..., $12,600,000 is appropriated to the Common Fund from the 10-year Pediatric Research Initiative Fund" | S. 4659: $2,560,065,000; same PRIF wording, $12,600,000 |
| OD headline | OBS-LHHS-1545 $2,537,213,000 (PRIF beside: CMP-LHHS-0537, by arithmetic) | OBS-LHHS-1547 $2,560,065,000 (CMP-LHHS-0539, by arithmetic) |
| Printed NIH program level | OBS-LHHS-0523: 47,459,000 thousand, H.Rept. 117-403 p.816 | OBS-LHHS-0561: 47,959,000 thousand, explanatory statement p.407 |
| Computed | $47,471,600,000 | $47,971,600,000 |
| Difference | -$12,600,000 | -$12,600,000 |

**Options.**
- A. Resolve the cause item as explained: the report's program level leaves out the PRIF money that the bill makes in
  addition. No data change; the 2 cells stay failing but not pending. Headline effect: none.
- B. Leave PRIF transfers out of the program-level view. In the 18 other cells PRIF is inside the OD headline, which
  the program level does not touch, so only these 2 move (+2 pass, -2 fail). It changes the view's definition
  (ACCOUNTING_RULES) to follow one report's convention. Headline effect: none.

**Recommendation: A.**

---

## 7. Tagger defaults (6): in_headline not confirmed by arithmetic

These are component notes, not validations; each keeps its default and is listed by `tag_components.py`.

### 7a. OBS-LHHS-1582, OS General Departmental Management FY2023 Senate: `transfer_in` $64,828,000 (CMP-LHHS-0542, beside)

| | Amount | Source |
|---|---|---|
| Headline, 'Total, General Departmental Management' | 915,394 thousand | FY2023 Senate explanatory statement p.417 |
| Paragraph's main amount | $565,394,000 | S. 4659 P0097 |
| Section 241 amount ("together with ... from the amounts available under section 241") | $64,828,000 | S. 4659 P0097 |
| Sec. 243: "In addition to amounts otherwise appropriated ... under the heading Office of the Secretary—General Departmental Management there are hereby appropriated an additional $350,000,000" | $350,000,000 | S. 4659 sec. 243 (P0104) |

$565,394,000 + $350,000,000 = $915,394,000, the headline. With sec. 243 counted in the paragraph's amount, the
arithmetic confirms the default (the $64,828,000 beside). This PR now reads sec. 243's amount as the headline's own
appropriation (not a component) but does not feed it into the arithmetic, so the default stays flagged.

- A. Count a general provision's additional amount that is the headline's own appropriation in the arithmetic's main
  amount. The note changes from "by default" to "by arithmetic"; in_headline stays FALSE. No view changes.
- B. Keep the default.

**Recommendation: A.**

### 7b. OBS-LHHS-1664, SAMHSA Mental Health FY2023 Enacted: `transfer_in` $21,039,000 (CMP-LHHS-0547, beside)

| | Amount | Source |
|---|---|---|
| Headline, 'Subtotal, Mental Health' | 2,755,507 thousand | S.Rept. 118-84 p.380 (2023 appropriation column) |
| Paragraph's main amount | $2,693,507,000 | P.L. 117-328 (136 Stat. 4865-4866) |
| Section 241 ("in addition to amounts provided herein, $21,039,000 shall be available under section 241") | $21,039,000 | same paragraph |
| PPHF row (beside) | $12,000,000 | CMP-LHHS-0991, S.Rept. 118-84 p.379 |

Headline less main amount: $62,000,000. Inside or beside, the $21,039,000 and $12,000,000 leave $28,961,000 (or more)
unexplained by anything in the paragraph we hold, so no assignment works.

- A. Keep the default (beside: the text says "in addition to amounts provided herein"). The $62,000,000 gap goes to
  review against S.Rept. 118-84 p.380's lines.
- B. Mark it inside. Not supported by the text or the arithmetic.

**Recommendation: A**, and read p.380's lines in a session that can open the report.

### 7c. OBS-LHHS-3755, CMS Program Management FY2017 House: `trust_fund_limitation` $3,093,690,000 (CMP-LHHS-0764, inside)

| | Amount | Source |
|---|---|---|
| Headline, 'Total, Program management' | 3,093,690 thousand | H.Rept. 114-699 p.247 |
| "not to exceed $3,093,690,000, to be transferred from the Federal Hospital Insurance Trust Fund and the Federal Supplementary Medical Insurance Trust Fund" | $3,093,690,000 | H.R. 5926 P0082 |
| Main amount the text check picks | $710,533,000 ("of the total amount made available under this heading ... $710,533,000 shall be for Federal Administration") | same paragraph; the text check reads it as ambiguous |

The headline equals the limitation exactly, so inside (base $0) is the only reading that fits; the arithmetic fails
only because the text check takes a proviso's sub-allocation as the paragraph's main amount.

- A. Keep the default (inside). No change.
- B. Teach the text check's main-amount reader to skip "of the total amount made available under this heading"
  provisos; it would confirm inside and may clear one of the 4 ambiguous text-check figures. A text-check change,
  better in its own PR.

**Recommendation: A now, B as a follow-up.**

### 7d. President's Budget advances "neither" (3)

The request's advance for the next year is read from FY N's Appendix language. Each headline is neither the
language's current-year amount nor that plus the advance, so the advance keeps "beside" by default.

| Observation | Headline (report, request column) | Appendix language (FY N volume) | Reading |
|---|---|---|---|
| OBS-LHHS-1933 Child Support FY2022 | 'Total, Family Support Payments, available in this bill' 2,794,432 thousand, H.Rept. 117-96 p.484 | current year $2,795,000,000; advance for Q1 FY2023 $1,300,000,000 (CMP-LHHS-1002), FY2022 Appendix p.476 | headline is $568,000 below the current-year amount; nothing near current + advance ($4,095,000,000) |
| OBS-LHHS-2362 Foster Care FY2021 | 'Total, Payments to States, available in this bill' 10,011,537 thousand, H.Rept. 116-450 p.418 | current year $7,012,000,000; advance for Q1 FY2022 $3,000,000,000 (CMP-LHHS-1005), FY2021 Appendix p.485 | headline is $463,000 below current + advance ($10,012,000,000) |
| OBS-LHHS-2763 Foster Care FY2020 | derived: 'Total, payments, current year' 5,744,000 + 'New Advance, 1st quarter, FY 2021' 2,800,000 = 8,544,000 thousand, H.Rept. 116-62 p.349 | current year $5,745,000,000; advance for Q1 FY2021 $3,000,000,000 (replacing the bracketed $2,800,000,000) (CMP-LHHS-1007), FY2020 Appendix p.470 | the report's request column prints the advance at $2,800,000,000 (the prior law's level), the Appendix proposes $3,000,000,000 |

- A. Keep all three "beside" by default (today). No change.
- B. 2362: inside (the label "available in this bill" and the amounts say current + new advance; the $463,000 is the
  report's current-year figure against the Appendix's). 2763: record the report's own 'New Advance' line
  ($2,800,000,000) as the headline's `advance_enacted`, inside, superseding the Appendix's $3,000,000,000 component
  with it kept as a note (the report's stage as its own document prints it). 1933: keep beside. Effects: 2
  components change in_headline (2362; 2763's new record), base for each falls by the advance; no headline changes.
  President's Budget cells are outside the auto-tagged cells, but an account's own printed view is checked where its
  headline carries a component the view uses, so printed Foster Care views for FY2020 and FY2021 may move; the run
  gives the counts.
- C. Defer all three until the President's Budget stage is read in full (step 2 of this brief, blocked: see the
  handoff).

**Recommendation: B for 2362 and 2763, A for 1933**, applied after step 2 so the request stage is settled once.

---

## New cause items from this PR

### pphf_general_provision_table, FY2027 House (`VAL-MRC-PPHF-GENERAL-PROVISION-TABLE`, 2 cells)

FY2027 House is now an auto-tagged cell, so its agency totals are checked. CDC and SAMHSA program levels differ by
Prevention and Public Health Fund money that the FY2027 House report's allocation table assigns, and
`build_pphf_table.py` has no FY2027 rows (it needs H.Rept. 119-696, which I could not fetch).

| Cell | Printed program level | Computed | Difference | Corroboration |
|---|---|---|---|---|
| CDC FY2027 House | OBS-LHHS-0147: 8,164,411 thousand (H.Rept. 119-696) | $6,766,036,000 | $1,398,375,000 | equals the FY2026 House report's CDC PPHF allocation exactly (699,933 + 45,000 + 417,442 + 185,000 + 51,000 thousand, H.Rept. 119-271 p.229) |
| SAMHSA FY2027 House | OBS-LHHS-0429: 7,294,275 thousand | $7,282,275,000 | $12,000,000 | SAMHSA's PPHF line is $12,000,000 every year |

- A. Add FY2027 House rows to `build_pphf_table.py` from H.Rept. 119-696's allocation table (run where the report PDF
  can be fetched). Expected +2 pass.
- B. Leave flagged.

**Recommendation: A**, in a session with network access to the report.
