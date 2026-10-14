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
