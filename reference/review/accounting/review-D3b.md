# D-3b: items for owner review

## Part 1: confirmed absences that rest on a table's silence

Rule (CLAUDE.md, Data): a confirmed absence cites bill or law text, never a table's silence; for the request
stage, the Budget Appendix's proposed language.

### Audit

`absence_audit.py` lists every live confirmed absence whose citation (`source_document_id`) is a committee report
or explanatory statement: `absence_audit.csv`. Before D-3b: 309 absences, 90 cite bill/law text or Appendix
language, 219 cite a report or explanatory statement (CA-LHHS-0037 among them). After CA-LHHS-0037's retirement:
308 and 218.

| document cited | stage | count |
|---|---|---|
| committee report | Enacted | 48 |
| committee report | House Reported | 59 |
| committee report | President's Budget | 56 (55 after CA-LHHS-0037) |
| committee report | Senate Reported | 38 |
| explanatory statement | Enacted | 8 |
| explanatory statement | House Reported | 1 |
| explanatory statement | President's Budget | 6 |
| explanatory statement | Senate Reported | 3 |

By subcommittee: CJS 197, Labor-HHS 22 (21 after CA-LHHS-0037). 33 rows' evidence names a bill or law
(`evidence_names_bill_or_law`, a hint only: several say the bill or law appropriates nothing, but the citation is
still the report). More than 10, so per the brief none of the 218 was changed: each needs one of the three
outcomes (record from text, re-cite to text, or retire), for review.

### CA-LHHS-0037 (CDC-Wide Activities and Program Support, FY2023 President's Budget): retired

- Was: H.Rept. 117-403 p.813, no budget-authority row for the account in the request column (the table's silence).
- FY2023 Budget Appendix, HHS chapter pp.437-438: under the heading 'CDC-WIDE ACTIVITIES AND PROGRAM SUPPORT'
  (075-0943), the proposed language appropriates $9,620,961,000 "To carry out titles II, III, IV, VII, XI, XV,
  XVII, XIX, XXI, XXIII, XXVI, and XXVIII of the [PHS Act], sections 101 ... of the Federal Mine Safety and Health
  Act ..." -- one appropriation for all of CDC. The report's CDC request total, 9,676,319 thousand, less EEOICP's
  55,358 thousand (mandatory) is the same 9,620,961 thousand. The two other paragraphs under the heading are
  bracketed (enacted FY2022 Afghanistan supplementals the request deletes).
- Outcome: neither a request amount for the account's scope nor a confirmed absence. Recording $9,620,961,000 as
  the account's headline would count CDC's other 12 accounts twice. Retired to the retired list
  (`reference/review/lhhs/retired_ids.json`); the cell is now missing (`coverage.csv`).
- Review item: the request column's Fund line under the account, $160,000,000 (H.Rept. 117-403 p.813,
  `pphf_table.csv`), stays reported, not recorded (tag_components: "PPHF rows with no headline in their cell").
  FY2024's Appendix also prints one CDC-wide appropriation under this heading ($10,217,311,000); that cell keeps the
  report table's headline (not in this brief).

## Part 2: CDC FY2025 request, the section 241 amount

- FY2025 Budget Appendix p.405, Injury Prevention and Control: "Provided, That in addition to amounts provided under
  this heading, $100,000,000 shall be available for implementation of the Community Violence Intervention initiative
  from amounts made available under section 241 of the PHS Act" (S.Rept. 118-207 p.435 prints it as 'Evaluation
  Funding (PHS Act Sec 241)').
- Component type: the one the repo already uses for section 241 money (ONC's evaluation tap, the SAMHSA and CDC
  section 241 lists): `transfer_in`, transfer_from "section 241 of the PHS Act (evaluation funds)". Never PPHF.
- Rule: `tag_components.PAST_WINDOW_241`, read only where no other rule classifies the amount, and only this wording
  ("shall be available for ... from amounts made available under section 241 of the PHS Act") is read on into the
  paragraph's text past the 100-character window. Across every committed bill/law text and Appendix paragraph the
  wording appears twice: this proviso, and CDC-Wide FY2024 request ("for health statistics"), which the existing
  rule already tags inside the window. One new component: CMP-LHHS-1372 on OBS-LHHS-0892, beside the headline (in
  addition: $843,379,000 headline unchanged).
- PPHF: the 10th President's Budget cell closes. CDC FY2025 request program level computed $9,638,690,000 ->
  $9,738,690,000 = printed (S.Rept. 118-207 p.436). The cause request_evaluation_tap_past_window is gone (its
  VAL-MRC record is no longer produced).

## Part 3: NSF Research and Related Activities, President's Budget headlines (owner-approved; D-3 part 1's rule)

Headline = the amount the FY N Budget Appendix's proposed language appropriates for the R&RA paragraph (nondefense +
defense, less an amount it designates an emergency requirement, which stays beside), from `request_language.json`;
the report table's 'Defense function' line is re-recorded as `defense_inside` (contained) with the new headline as
its headline, and tagged `defense_portion` inside. Supersede and link, new IDs (`d3b_records.py`).

| FY | Old headline | Defense line | New headline | Source, page | Old figure's source | Defense line old -> new |
|---|---|---|---|---|---|---|
| 2017 | OBS-0727 $6,011,910,000 | $67,520,000 | OBS-1057 $6,079,430,000 | SRC-BUDGET-APP-FY2017-NSF p.1195 | SRC-CRPT-114HRPT605 p.114-115 | OBS-0767 -> OBS-1058 |
| 2018 | OBS-0723 $5,290,650,000 | $71,000,000 | OBS-1059 $5,361,650,000 | SRC-BUDGET-APP-FY2018-NSF p.1083 | SRC-CRPT-115HRPT231 p.120-121 | OBS-0763 -> OBS-1060 |
| 2020 | OBS-0715 $5,591,960,000 | $71,000,000 | OBS-1061 $5,662,960,000 | SRC-BUDGET-APP-FY2020-NSF p.1107 | SRC-CRPT-116HRPT101 p.148-149 | OBS-0755 -> OBS-1062 |
| 2021 | OBS-0711 $6,142,020,000 | $71,000,000 | OBS-1063 $6,213,020,000 | SRC-BUDGET-APP-FY2021-NSF p.1187 | SRC-CRPT-116HRPT455 p.187-188 | OBS-0751 -> OBS-1064 |
| 2022 | OBS-0707 $8,062,710,000 | $77,000,000 | OBS-1065 $8,139,710,000 | SRC-BUDGET-APP-FY2022-NSF p.1205 | SRC-CRPT-117HRPT97 p.213-214 | OBS-0747 -> OBS-1066 |
| 2023 | OBS-0703 $8,335,987,000 | $90,000,000 | OBS-1067 $8,425,987,000 | SRC-BUDGET-APP-FY2023-NSF p.1173 | SRC-CRPT-117HRPT395 p.219-220 | OBS-0743 -> OBS-1068 |
| 2025 | OBS-0695 $7,519,320,000 | $106,000,000 | OBS-1069 $7,625,320,000 (paragraph $8,045,320,000 less the $420,000,000 emergency, beside) | SRC-BUDGET-APP-FY2025-NSF p.1083 | SRC-CRPT-118HRPT582 p.247-248 | OBS-0735 -> OBS-1070 |
| 2026 | OBS-0691 $3,166,840,000 | $109,310,000 | OBS-1071 $3,276,150,000 | SRC-BUDGET-APP-FY2026-NSF p.1007 | SRC-EXPL-FY2026-PB p.129 | OBS-0731 -> OBS-1072 |

Proofs, every cell: the store total equals the new headline; `views_nondefense` equals the old nondefense line (every
basis); the grid shows `defense_inside` contained, `adds_to_headline` false, and no 'defense' part row (no phantom
missing line); VAL-MR passes. Components: the 8 defense_portion components move inside the new headlines (new IDs
CMP-0116 ... CMP-0123; CMP-0015, -0019, -0027, -0031, -0035, -0039, -0047, -0051 retired) and the FY2025 emergency
(CMP-0053 -> CMP-0115, designated/emergency $420,000,000, beside, unchanged). `request_language.json`: only the 8
headline IDs change. The resolved-by-rule status reads "Appendix language controls" for these figures.

Left as they are (the Appendix prints a paragraph total, but it is not the table's nondefense + defense, so the
second proof cannot hold). Owner, 2026-10-14: one review item each, no figure change.

### Review item: NSF R&RA FY2019 request (OBS-0719 $6,079,680,000 + defense OBS-0759 $71,000,000)

Hypothesis (owner): the committee tables use the request as amended after the February 2018 budget agreement (the
addendum), the Appendix the original request. What each document shows:
- FY2019 Budget Appendix, NSF chapter (BUDGET-2019-APP-1-27), p.1091: the proposed R&RA language appropriates
  $4,230,510,000; the schedule's budget authority line 4070 prints 4,231 ($ millions) for 2019. The chapter never
  mentions an addendum or amendment.
- FY2019 Budget volume (BUDGET-2019-BUD), Table S-8 '2019 Discretionary Overview by Major Agency', PDF p.148: NSF
  2017 7.5, 2018 7.4, 2019 request 5.3 ($ billions), -29.5%: the original request.
- S.Rept. 115-275, PDF p.129: NSF 'Budget estimate, 2019' $7,472,000,000; R&RA 'Budget estimate, 2019'
  $6,150,680,000 (= the table's 6,079,680 + 71,000 thousand); comparative table p.158 the same.
- H.Rept. 115-704, PDF p.77: the Committee's $6,651,500,000 for R&RA is "$500,820,000 above the request", so the
  request it uses is $6,150,680,000 (its table pages 128-129 are images).
- The addendum itself ('Addendum to the President's Fiscal Year 2019 Budget', OMB, February 2018) is not on govinfo
  and its White House copy could not be reached from this session (the host is blocked), so its NSF line is not
  read. Secondary sources (FASEB, COSSA, AAU summaries) describe it as raising NSF's request to about $7.47 billion.
- Reading: the documents fit the hypothesis (Appendix and Budget volume: original; both committee reports: the
  $7,472,000,000 NSF figure). The difference, $6,150,680,000 - $4,230,510,000 = $1,920,170,000, would sit under the
  existing `budget_amendment` component in a later brief, once the addendum's own page is read.

### Review item: NSF R&RA FY2024 request (OBS-0699 $8,915,900,000 + defense OBS-0739 $102,000,000)

- FY2024 Budget Appendix, NSF chapter (BUDGET-2024-APP-1-26), p.1141: the proposed R&RA language appropriates
  $9,029,900,000; schedule line 1100 (appropriation) prints 9,030 ($ millions) for 2024, with no transfer line.
- S.Rept. 118-62, PDF p.167: NSF 'Budget estimate, 2024' $11,354,680,000; R&RA 'Budget estimate, 2024'
  $9,017,900,000; comparative table p.219 (R&RA 8,915,900) and p.220 (Defense function 102,000; Subtotal 9,017,900
  thousand). R&RA is $12,000,000 below the Appendix.
- The other NSF accounts: STEM Education, Appendix $1,444,180,000 vs the report's request $1,496,180,000
  (+$52,000,000); MREFC, AOAM, NSB and OIG equal. NSF in all: the report's $11,354,680,000 is $40,000,000 above the
  Appendix accounts' $11,314,680,000. So this is not a scope difference inside R&RA alone: money moves between R&RA
  and STEM Education.
- Amendment: the President's FY2024 budget amendments of May 9, 2023 include "increased amounts for the National
  Science Foundation (NSF) STEM Education account ... fully offset from amounts provided from within NSF and the
  Department of Agriculture" (the transmittal letter, as quoted in search results). The amendment's own pages
  (bidenwhitehouse.archives.gov) could not be reached from this session, so the $12,000,000 is not confirmed as the
  within-NSF offset. If it is, R&RA's -$12,000,000 is a `budget_amendment` too (a later brief).
No year's Appendix language lacks a paragraph total (FY2027 is outside the brief).
