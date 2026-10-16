# D-3c: confirmed absences re-cited to bill or law text

Rule (CLAUDE.md, Data): a confirmed absence cites bill or law text, never a table's silence; for the request stage,
the Budget Appendix's proposed language. D-3b listed 218 live absences citing a report or explanatory statement
(`absence_audit.csv`). `d3c_absences.py` checked each against its own stage's text; the outcome per absence is in
`d3c_absences.csv`.

| outcome | President's Budget | House Reported | Senate Reported | Enacted | total |
|---|---|---|---|---|---|
| confirmed by text, re-cited | 60 | 59 | 40 | 56 | 215 |
| left for review | 1 | 1 | 1 | 0 | 3 |

Confirmed: CJS 197 (NASA rescission 68, NASA supplemental 62, NSF supplemental 67), Labor-HHS 18. Each one is
superseded, not overwritten: a new absence (CA-0199 ... CA-0395, CA-LHHS-0174 ... CA-LHHS-0191) holds the same fact
and cites the text, its evidence ending "(D-3c; supersedes <old ID>, which cited <report>)"; the old ID is on
`reference/review/lhhs/retired_ids.json`. No figure, observation or cell state changes (the CJS and Labor-HHS grid
counts are the same before and after).

## How the text is read

- Text: the committed download of each bill or law (its sha256 equals `data/legislative_text/<id>.json`'s); for an
  enacted law only the subcommittee's division. FY2025 Enacted is P.L. 119-4 division A (the full-year CR, pinned
  by sha256 in the script) and the FY2024 division it continues (P.L. 118-42 div. C, P.L. 118-47 div. D); the
  absence cites P.L. 119-4. The request is FY N's Budget Appendix chapter for the agency (NASA, NSF, HHS), proposed
  language only (bracketed text dropped), read by `request_language.chapter_paragraphs`.
- Every heading and section is a unit with its heading path. A unit names the account when it is the account's own
  heading under its agency's heading, quotes the account's name (with the agency named in the provision or its
  path for 'Science' and 'Exploration'), or prints its Treasury symbol (`appendix_map.csv`, e.g. 080X0115).
- Supplemental (the regular act's emergency amounts; separate supplemental acts are `supplemental_act` lines, as
  CA-0153's evidence already says): no unit naming the account carries an emergency or disaster designation,
  'additional amount for', 'appropriated an additional $' or 'in addition to amounts otherwise appropriated'.
  Rescission: none carries 'rescind', 'rescission', 'cancel(l)ed' or 'permanently reduced'. The marker must sit in
  the sentence naming the account or the one before or after it; on the account's own heading, anywhere in it.
- A unit of the agency carrying a marker without naming any of its accounts routes the absence to review.
- Read as no marker: 'amounts that have expired but have not been cancelled ... shall remain available' (the Space
  Shuttle closeout provision keeps balances available; it cancels nothing); STEM Engagement's 'an additional amount
  of up to $1,000,000 each from amounts made available ... under the headings "Science", ...'; an Appendix heading
  note that only reprints enacted supplemental language ('(Disaster Relief Supplemental Appropriations Act, 2025.)').
- The heading must be found, or the absence goes to review. One name added: H.R. 5952 (FY2019 House) prints
  'Low-Earth Orbit and Spaceflight Operations' for Space Operations.
- Labor-HHS: one search per account over the whole text (AHA: /Healthy America/; ARPA-H: its name; Adoption
  Incentives: 'adoption' in a unit that rescinds or cancels; Medicare Operations: the trust fund transfer
  section, as CA-LHHS-0043; NEF FY2025 emergency rescission: an emergency-designated NEF rescission). The ARPA-H
  FY2021 absence keeps 'did not exist' (the store reads it as outside the account's life): its heading is first
  printed in P.L. 117-103 div. H, 136 Stat. 465.

Checked against the recorded figures: the same check finds the marker for all 17 recorded non-zero supplemental
and rescission lines of these six accounts that are not `supplemental_act` (e.g. FY2025 request Space Operations
cancellation via 080X0115, FY2020 Science -$70,000,000 in S. 2584 sec. 523 and P.L. 116-93, FY2024 Senate
Exploration $280,000,000, FY2025 request Construction $296,000,000).

## For review (3, unchanged)

ASPR (Administration for Strategic Preparedness and Response, agency total), FY2023 President's Budget
(CA-LHHS-0030), House Reported (CA-LHHS-0031) and Senate Reported (CA-LHHS-0033). The FY2023 Appendix (HHS p.483),
H.R. 8295 and S. 4659 print no ASPR heading, but appropriate ASPR's programs (BARDA among them) under the Office of
the Secretary's 'Public Health and Social Services Emergency Fund'. Whether the account is absent before FY2024 is a
question of its scope (which headings make up ASPR), which the text's silence does not settle. Options: keep the
absences citing the reports (an exception to the rule), retire them (the cells become missing), or record
FY2023 from the PHSSEF heading's ASPR programs under a scope ruling.

## Outside this brief (no change)

- P.L. 117-2 (American Rescue Plan) sec. 7502 appropriates $600,000,000 to the National Science Foundation for
  FY2021 without naming an account; no FY2021 `supplemental_act` line is recorded for NSF. CA-0124's scope (the
  regular act's emergency line) is unaffected.
- Supplemental requests sent apart from the Budget (and budget amendments) are not in the Appendix; the request
  absences cover the Budget as printed.

    python reference/review/accounting/d3c_absences.py --check   # the outcomes and the re-cited evidence still follow from the text
