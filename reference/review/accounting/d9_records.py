"""
D-9 (data fills for the anchored totals, reference/specs/anchored-totals.md): records added to data/staged.json.
Add only, never overwrite: a cell that already holds an observation or a confirmed absence is left as it is, and
no existing figure changes. Every record is listed in reference/review/accounting/review-D9.md.

Part 1, missing account figures (cause missing_figure): the Title II account cells marked "document on file,
figure not yet entered". Each is filled from that stage's bill or law text, or the Budget Appendix's proposed
language for the request, with its printed page (the PDF page of the bill or law; the Appendix's printed page),
or recorded as a confirmed absence that cites the text searched. Never from a table's silence.

Part 2, the Medicare 'Limitation for Title XVIII of the Social Security Act' line (ACC-HHS-GP-MEDICARE-LIMITATION):
its life is set from the evidence. The line is P.L. 119-75 div. B sec. 241 (PDF p.120: CMS shall not apply the
distance requirements of SSA sec. 1820(c)(2)(B)(i)(I) to certain critical access hospitals), scored at 2,000 in the
FY2026 explanatory statement's table (p.296), whose text calls it 'a new provision related to title XVIII of the
Social Security Act' (p.245). No other bill, law or request on file carries it (each searched for
/1820(c)(2)(B)|distance requirement/: H.R. 9029, S. 4942, H.R. 5304, S. 2587, H.R. 9260, P.L. 117-328,
P.L. 118-47, the FY2026 and FY2027 Budget Appendix HHS chapters; the account's note already records FY2021-FY2022).
So the 'unidentified' start override is cleared: the lifecycle derives a new line from FY2026 Enacted, and the
years before fall outside its life instead of reading as missing figures. FY2027 stays as its documents print it
(H.Rept. 119-696 p.396 '---', recorded as 0; no confirmed absence from a table's dash).

Part 6, CJS Title III FY2026 Senate Reported total (owner-approved 2026-10-15): OBS-1436 recorded $33,908,257,000,
the FY2025 column of S.Rept. 119-44's 'Total, Title III, Science' (p.219). The Committee recommendation column
prints 33,909,609 thousand, so OBS-1436 is superseded by a record of $33,909,609,000.

    python reference/review/accounting/d9_records.py           # what would change
    python reference/review/accounting/d9_records.py --write   # write it (idempotent)
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
sys.path.insert(0, str(ROOT))
import ids as IDS  # noqa: E402
import validate_approps as V  # noqa: E402

DATE = "2026-10-15"
STAGE_CHAMBER = {"House Reported": "House", "Senate Reported": "Senate"}

# Part 1, figures: (account, fy, stage, amount, document, page, where and what the text prints)
FIGURES = [
    ("ACC-HHS-CDC-PROGRAM-SUPPORT", 2025, "House Reported", 511570000, "SRC-BILLS-118HR9029RH", "64",
     "Title II, Centers for Disease Control and Prevention -- printed in H.R. 9029 under 'CDC-Wide Activities and "
     "Program Support': '... for the Centers for Disease Control and Prevention, $511,570,000, of which $360,000,000 "
     "shall remain available through September 30, 2026, for public health infrastructure and capacity'; bill or "
     "law text controls over the report table for the same scope (owner 2026-10-11) -- Note: H.Rept. 118-585 p.316 "
     "prints no budget-authority line for the account, only 'Subtotal, CDC-Wide Activities' (511,570) in "
     "parentheses in the Bill column (the Prevention and Public Health Fund line '---'); its narrative (p.80) gives "
     "the Committee Recommendation as $511,570,000 [bill text] (D-9)"),
    ("ACC-HHS-NIH-NIDCD", 2023, "Senate Reported", 530847000, "SRC-BILLS-117S4659IS", "74",
     "Title II, National Institutes of Health -- printed in S. 4659 under 'National Institute on Deafness and Other "
     "Communication Disorders': '... with respect to deafness and other communication disorders, $530,847,000' -- "
     "Note: the FY2023 Senate explanatory statement's narrative (p.127) gives the Committee recommendation as "
     "$530,847,000 [bill text] (D-9)"),
    ("ACC-HHS-OS-ONC", 2023, "Enacted", 0, "SRC-PLAW-117PUBL328", "419",
     "Division H, Title II, Office of the Secretary -- printed in P.L. 117-328 under 'Office of the National "
     "Coordinator for Health Information Technology': no new budget authority; the paragraph's whole amount, "
     "$66,238,000, 'shall be from amounts made available under section 241 of the PHS Act' (headline $0 as the law "
     "prints it; owner 2026-10-11) [law text] (D-9)"),
    ("ACC-HHS-OS-ONC", 2024, "President's Budget", 0, "SRC-BUDGET-APP-FY2024-HHS", "469",
     "Office of the Secretary -- the FY2024 Budget Appendix's proposed language under 'Office of the National "
     "Coordinator for Health Information Technology': no new budget authority; the paragraph's whole amount, "
     "$103,614,000, 'shall be from amounts made available under section 241 of the PHS Act' (headline $0 as the "
     "language prints it, as for FY2025's request; owner 2026-10-11) [Appendix text] (D-9)"),
    ("ACC-HHS-GP-MEDICARE-OPERATIONS", 2023, "Enacted", 455000000, "SRC-PLAW-117PUBL328", "426",
     "Division H, Title II, General Provisions, sec. 227 (Medicare Operations): 'the Secretary of Health and Human "
     "Services may transfer up to $455,000,000 to such account [CMS, Program Management] from the Federal Hospital "
     "Insurance Trust Fund and the Federal Supplementary Medical Insurance Trust Fund to support program management "
     "activity related to the Medicare Program' [law text] (D-9)"),
    ("ACC-HHS-GP-MEDICARE-OPERATIONS", 2024, "Senate Reported", 455000000, "SRC-BILLS-118S2624RS", "127",
     "Title II, General Provisions, sec. 227 (Medicare Operations) -- printed in S. 2624: 'the Secretary of Health "
     "and Human Services may transfer up to $455,000,000 to such account [CMS, Program Management] from the Federal "
     "Hospital Insurance Trust Fund and the Federal Supplementary Medical Insurance Trust Fund' [bill text] (D-9)"),
]

# Part 1, confirmed absences: (account, fy, stage, document, evidence)
ABSENCES = [
    # Public Health and Social Services Emergency Fund: a heading in P.L. 117-328 (FY2023) only
    ("ACC-HHS-OS-PHSSEF", 2025, "Senate Reported", "SRC-BILLS-118S4942RS",
     "S. 4942 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text "
     "searched for /Social Services Emergency Fund/: no mention). S.Rept. 118-207 p.241: 'The Committee "
     "recommendation does not include funding for the Public Health and Social Services Emergency Fund [PHSSEF]. "
     "Funding for activities previously funded within PHSSEF were reorganized in fiscal year 2024' (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2025, "Enacted", "SRC-PLAW-119PUBL4",
     "P.L. 119-4 div. A sec. 1101 (the FY2025 full-year CR) continues the FY2024 Labor-HHS act, P.L. 118-47 "
     "division D, which has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole "
     "text searched for /Social Services Emergency Fund/: no mention) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text "
     "searched for /Social Services Emergency Fund/: no mention) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text "
     "searched for /Social Services Emergency Fund/: no mention) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2026, "Enacted", "SRC-PLAW-119PUBL75",
     "P.L. 119-75 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text "
     "searched for /Social Services Emergency Fund/: no mention) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2026, "President's Budget", "SRC-BUDGET-APP-FY2026-HHS",
     "The FY2026 Budget Appendix proposes no appropriations language for the Public Health and Social Services "
     "Emergency Fund: its entry (p.387) is schedules only ('includes balances from emergency supplemental bills and "
     "appropriations for [ASPR] prior to the enactment of separate accounts for ASPR in 2024'), with a legislative "
     "proposal schedule and no language; the general provision on p.399 concerns amounts under the heading in P.L. "
     "117-159, not a new appropriation (HHS chapter pp. 325-400 searched) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2027, "House Reported", "SRC-BILLS-119HR9260RH",
     "H.R. 9260 has no 'Public Health and Social Services Emergency Fund' heading or appropriation (whole text "
     "searched for /Social Services Emergency Fund/: no mention) (D-9)."),
    ("ACC-HHS-OS-PHSSEF", 2027, "President's Budget", "SRC-BUDGET-APP-FY2027-HHS",
     "The FY2027 Budget Appendix proposes no appropriations language for the Public Health and Social Services "
     "Emergency Fund: its entry (pp. 489-490) is schedules only, holding balances from before ASPR's separate "
     "accounts in 2024 (HHS chapter pp. 427-502 searched) (D-9)."),
    # Covered Countermeasures Process Fund: a heading in P.L. 117-328 and P.L. 118-47 only
    ("ACC-HHS-HRSA-CCPF", 2026, "Enacted", "SRC-PLAW-119PUBL75",
     "P.L. 119-75 never mentions the Covered Countermeasures Process Fund (whole text searched for /Covered "
     "Countermeasure/). The joint explanatory statement (CREC 2026-01-22, p.H1353 granule, PDF p.239): 'The "
     "agreement does not include additional administrative funding for this account due to the availability of "
     "sufficient carryover funds to support this activity' (D-9)."),
    ("ACC-HHS-HRSA-CCPF", 2027, "House Reported", "SRC-BILLS-119HR9260RH",
     "H.R. 9260 never mentions the Covered Countermeasures Process Fund (whole text searched for /Covered "
     "Countermeasure/) (D-9)."),
    ("ACC-HHS-HRSA-CCPF", 2027, "President's Budget", "SRC-BUDGET-APP-FY2027-HHS",
     "The FY2027 Budget Appendix proposes no appropriations language for the Covered Countermeasure Process Fund: "
     "its entry (p.440) is schedules only (line 1100 Appropriation blank for 2027 est.), with a legislative "
     "proposal schedule moving its unobligated balance to another account (HHS chapter pp. 427-502 searched) "
     "(D-9)."),
    # Office of the National Coordinator for Health IT
    ("ACC-HHS-OS-ONC", 2026, "President's Budget", "SRC-BUDGET-APP-FY2026-HHS",
     "The FY2026 Budget Appendix proposes no appropriations language under the 'Office of the National Coordinator "
     "for Health Information Technology' heading: its entry (p.385) is schedules only, with the note that the "
     "account operates under P.L. 119-4. The request funds health IT policy inside General Departmental "
     "Management's proposed language (p.383: 'of the amounts made available under this heading, $30,000,000 shall "
     "be for necessary expenses of activities related to advancing health information technology policy') (D-9)."),
    # Medicare Operations (the Title II general provision transferring trust funds to CMS Program Management)
    ("ACC-HHS-GP-MEDICARE-OPERATIONS", 2023, "House Reported", "SRC-BILLS-117HR8295RH",
     "H.R. 8295 has no section letting the Secretary transfer Medicare trust fund amounts to 'Centers for Medicare & "
     "Medicaid Services, Program Management' (whole text searched for /may transfer up to/ and /Federal "
     "Hospital/: its six mentions, pp. 82, 84, 97, 102, 115 and 186, are other headings and sections) (D-9)."),
    ("ACC-HHS-GP-MEDICARE-OPERATIONS", 2023, "President's Budget", "SRC-BUDGET-APP-FY2023-HHS",
     "The FY2023 Budget Appendix's proposed HHS language has no section letting the Secretary transfer Medicare "
     "trust fund amounts to CMS Program Management (HHS chapter pp. 423-496 searched for /may transfer up to/ and "
     "/program management activity related to the Medicare/; its sec. 227 (p.494) is a notification provision) "
     "(D-9)."),
    ("ACC-HHS-GP-MEDICARE-OPERATIONS", 2024, "President's Budget", "SRC-BUDGET-APP-FY2024-HHS",
     "The FY2024 Budget Appendix brackets the FY2023 law's sec. 227 ($455,000,000 transfer) for deletion (p.483) "
     "and proposes no such section (D-9)."),
    # The FY2026 request's proposed NIH institutes: the committees and the FY2027 House bill fund NIH under the
    # existing institutes' headings
    ("ACC-HHS-NIH-BODY-SYSTEMS", 2025, "Senate Reported", "SRC-BILLS-118S4942RS",
     "S. 4942 has no 'National Institute on Body Systems Research' heading: it funds NIH under the existing "
     "institutes' headings (NIH, pp. 70-76: NHLBI and NIDDK p.71, NIAMS p.73) (whole text searched for /Body Systems/) (D-9)."),
    ("ACC-HHS-NIH-NEUROSCIENCE", 2025, "Senate Reported", "SRC-BILLS-118S4942RS",
     "S. 4942 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the "
     "existing institutes' headings (NIH, pp. 70-76: NINDS p.71, NEI p.72) (whole text searched for /Neuroscience and Brain/) "
     "(D-9)."),
    ("ACC-HHS-NIH-BODY-SYSTEMS", 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'National Institute on Body Systems' heading: it funds NIH under the existing institutes' "
     "headings (NIH, pp. 65-71) (whole text searched for /Body Systems/) (D-9)."),
    ("ACC-HHS-NIH-NEUROSCIENCE", 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the "
     "existing institutes' headings (NIH, pp. 65-71) (whole text searched for /Neuroscience and Brain/) (D-9)."),
    ("ACC-HHS-NIH-CHILD-WOMEN-SENSORY", 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'National Institute for Child and Women's Health, Sensory Disorders and Communication' "
     "heading: it funds NICHD and NIDCD under their own headings (pp. 67-68) (whole text searched for /Child and "
     "Women/) (D-9)."),
    ("ACC-HHS-NIH-BEHAVIORAL-HEALTH", 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'National Institute of Behavioral Health' heading: it funds NIMH, NIDA and NIAAA under their "
     "own headings (p.69) (whole text searched for /Institute of Behavioral Health/) (D-9)."),
    ("ACC-HHS-NIH-BODY-SYSTEMS", 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'National Institute on Body Systems' heading: it funds NIH under the existing institutes' "
     "headings (NIH, pp. 74-81) (whole text searched for /Body Systems/) (D-9)."),
    ("ACC-HHS-NIH-NEUROSCIENCE", 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'National Institute on Neuroscience and Brain Research' heading: it funds NIH under the "
     "existing institutes' headings (NIH, pp. 74-81) (whole text searched for /Neuroscience and Brain/) (D-9)."),
    ("ACC-HHS-NIH-CHILD-WOMEN-SENSORY", 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'National Institute for Child and Women's Health, Sensory Disorders and Communication' "
     "heading: it funds NICHD and NIDCD under their own headings (pp. 77-78) (whole text searched for /Child and "
     "Women/) (D-9)."),
    ("ACC-HHS-NIH-BEHAVIORAL-HEALTH", 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'National Institute of Behavioral Health' heading: it funds NIMH, NIDA and NIAAA under their "
     "own headings (pp. 78-79) (whole text searched for /Institute of Behavioral Health/) (D-9)."),
    ("ACC-HHS-NIH-SUBSTANCE-USE", 2027, "House Reported", "SRC-BILLS-119HR9260RH",
     "H.R. 9260 has no 'National Institute of Substance Use and Addiction Research' heading: it funds NIDA (p.70) "
     "and NIAAA (p.70) under their own headings (whole text searched for /Substance Use and Addiction/) (D-9)."),
]


LIMITATION = "ACC-HHS-GP-MEDICARE-LIMITATION"
LIMITATION_NOTE = (
    "A general-provision line: P.L. 119-75 Division B sec. 241 (PDF p.120, 140 Stat. 293): CMS shall not apply the "
    "distance requirements of SSA sec. 1820(c)(2)(B)(i)(I) to certain critical access hospitals -- a title XVIII "
    "provision the FY2026 explanatory statement calls 'a new provision related to title XVIII of the Social Security "
    "Act' (p.245) and its table scores at 2,000 (p.296). First printed FY2026 Enacted; its life starts there (D-9, "
    "2026-10-15): no other bill, law or request on file carries it (searched for '1820(c)(2)(B)' and 'distance "
    "requirement': the FY2021-FY2022 bills, Appendix language and laws; P.L. 117-328, P.L. 118-47, H.R. 9029, "
    "S. 4942, H.R. 5304, S. 2587, H.R. 9260 and the FY2026-FY2027 Budget Appendix HHS chapters). H.Rept. 119-696 "
    "p.396 prints '---' in the FY2027 columns (recorded as printed). Vision read H.Rept. 119-696's label as "
    "'mitation ...' (the page image prints 'Limitation ...'). Its confirmed absences, which rested on tables' "
    "silence, were retired 2026-10-08 (owner).")


def part2(data):
    """The limitation line's life: clear the 'unidentified' start override, note the evidence. -> changed?"""
    a = next(x for x in data["accounts"] if x["canonical_account_id"] == LIMITATION)
    new = dict(a, start_basis="", start_basis_reason="", notes=LIMITATION_NOTE)
    if new == a:
        return False
    a.update(new)
    return True


# Part 8 (owner, 2026-10-15: a line counts in the total the table prints it under; new accounts in this PR): lines the
# tables print inside an agency total that no account held
OPIOID = "ACC-HHS-OS-STATE-OPIOID-RESPONSE"
CX = "ACC-HHS-OS-CUSTOMER-EXPERIENCE"
ASPR_MP = "ACC-HHS-ASPR-MANUFACTURING"
BHIBG = "ACC-HHS-SAMHSA-BH-INNOVATION-BG"
NEW_ACCOUNTS = [
    (OPIOID, "Account for the State Response to the Opioid Abuse Crisis, CURES Act", "Office of the Secretary",
     "inactive",
     "21st Century Cures Act sec. 1003 grants to states, $500,000,000 a year: FY2017 (P.L. 114-254 div. A, sec. 195 of "
     "the Continuing Appropriations Act, 2017, 130 Stat. 1021) and FY2018 (P.L. 115-141 div. H, 132 Stat. 734). The "
     "tables print it inside 'Total, Office of the Secretary' (H.Rept. 115-244 p.234, S.Rept. 115-150 p.241, H.Rept. "
     "115-862 p.383). No FY2019 bill or law on file has the heading (P.L. 115-245, H.R. 6470, S. 3158 searched for "
     "/Opioid Abuse Crisis|1003\\(c\\)/). Budget class not recorded (no Appendix schedule on file). Added in D-9 (owner, "
     "2026-10-15)."),
    (CX, "Customer Experience", "Office of the Secretary", "proposed",
     "Request only: the Senate tables print it in the budget-estimate column inside 'Total, Office of the Secretary' "
     "(S.Rept. 118-207 p.451, FY2025 14,000; S.Rept. 118-84 p.391, FY2024 20,000), with no committee or enacted figure. "
     "Budget class not recorded. Added in D-9 (owner, 2026-10-15)."),
    (ASPR_MP, "Manufacturing and Production", "Administration for Strategic Preparedness and Response", "proposed",
     "FY2024 only: the request's 'Pandemic Preparedness and Biodefense' (S.Rept. 118-84 p.390, budget estimate 400,000) "
     "and S. 2624's 'MANUFACTURING AND PRODUCTION' heading ($75,000,000, designated an emergency requirement; PDF "
     "p.107), printed inside 'Total, ASPR'. Not in H.R. 5894 or P.L. 118-47. Budget class not recorded. Added in D-9 "
     "(owner, 2026-10-15)."),
    (BHIBG, "Behavioral Health Innovation Block Grant", "Substance Abuse and Mental Health Services Administration",
     "proposed",
     "FY2026 and FY2027 requests only: the House tables print it in the request column inside 'Total, SAMHSA' "
     "(H.Rept. 119-271 p.348, 4,025,411; H.Rept. 119-696 p.379, 4,520,411; each with an evaluation-tap line beside, "
     "program level 4,125,650 and 4,620,650). No House or Senate bill on file has it. Budget class not recorded. Added "
     "in D-9 (owner, 2026-10-15)."),
]
FIGURES += [
    (OPIOID, 2017, "Enacted", 500000000, "SRC-PLAW-114PUBL254", "17",
     "P.L. 114-254 div. A sec. 101(3), adding sec. 195 to the Continuing Appropriations Act, 2017 (130 Stat. 1021): "
     "'an additional amount for fiscal year 2017 of $500,000,000, to remain available until expended, is provided for "
     "\'Department of Health and Human Services--Office of the Secretary--Account For the State Response to the "
     "Opioid Abuse Crisis\''; bill or law text controls over the report table for the same scope (owner 2026-10-11) -- Note: H.Rept. 115-244 p.234 prints 500,000 in the FY 2017 Enacted column "
     "[law text] (D-9)"),
    (OPIOID, 2018, "President's Budget", 500000000, "SRC-CRPT-115HRPT244", "234",
     "Title II, Office of the Secretary -- printed as 'Account for the State Response to the Opioid Abuse Crisis, "
     "CURES Act 2/' [FY 2018 Request column] 500,000; S.Rept. 115-150 p.241 prints the same budget estimate (the "
     "FY2018 Budget Appendix is not on file) (D-9)"),
    (OPIOID, 2018, "House Reported", 500000000, "SRC-BILLS-115HR3358RH", "81",
     "Title II, Office of the Secretary -- printed in H.R. 3358 under 'STATE RESPONSE TO THE OPIOID ABUSE CRISIS "
     "ACCOUNT, CURES ACT': 'For necessary expenses to carry out the purposes described under section 1003(c) of the "
     "21st Century Cures Act, $500,000,000, to remain available until expended'; bill or law text controls over the report table for the same scope (owner 2026-10-11) -- Note: H.Rept. 115-244 p.234 "
     "prints 500,000 in the Bill column [bill text] (D-9)"),
    (OPIOID, 2018, "Senate Reported", 500000000, "SRC-BILLS-115S1771PCS", "85",
     "Title II, Office of the Secretary -- printed in S. 1771 under 'ACCOUNT FOR THE STATE RESPONSE TO THE OPIOID "
     "ABUSE CRISIS, CURES ACT': '... described in section 1003(c) of the 21st Century Cures Act, $500,000,000 to "
     "remain available until expended'; bill or law text controls over the report table for the same scope (owner 2026-10-11) -- Note: S.Rept. 115-150 p.241 prints 500,000 in the Committee "
     "recommendation column [bill text] (D-9)"),
    (OPIOID, 2018, "Enacted", 500000000, "SRC-PLAW-115PUBL141", "388",
     "Division H, Title II, Office of the Secretary -- printed in P.L. 115-141 (132 Stat. 734) under 'ACCOUNT FOR THE "
     "STATE RESPONSE TO THE OPIOID ABUSE CRISIS, CURES ACT': '... in section 1003(c) of the 21st Century Cures Act, "
     "$500,000,000, to remain available until expended'; bill or law text controls over the report table for the same scope (owner 2026-10-11) -- Note: H.Rept. 115-862 p.383 prints 500,000 in the "
     "FY 2018 Enacted column [law text] (D-9)"),
    (CX, 2025, "President's Budget", 14000000, "SRC-CRPT-118SRPT207", "451",
     "Title II, Office of the Secretary -- printed as 'Customer Experience' [Budget estimate column] 14,000 (2024 "
     "appropriation and Committee recommendation columns blank) (D-9)"),
    (CX, 2024, "President's Budget", 20000000, "SRC-CRPT-118SRPT84", "391",
     "Title II, Office of the Secretary -- printed as 'Customer Experience' [Budget estimate column] 20,000 (2023 "
     "appropriation and Committee recommendation columns blank; owner-approved 2026-10-15) (D-9)"),
    (ASPR_MP, 2024, "President's Budget", 400000000, "SRC-CRPT-118SRPT84", "390",
     "Title II, ASPR -- printed as 'Pandemic Preparedness and Biodefense' under 'Manufacturing and Production' "
     "[Budget estimate column] 400,000 ('Subtotal, Manufacturing and Production' 400,000); the narrative (p.194) "
     "prints 'Budget estimate, 2024 ... $400,000,000' (D-9)"),
    (ASPR_MP, 2024, "Senate Reported", 75000000, "SRC-BILLS-118S2624RS", "107",
     "Title II, ASPR -- printed in S. 2624 under 'MANUFACTURING AND PRODUCTION': '... in the event of a public health "
     "emergency declaration or significant potential for a public health emergency, $75,000,000, to remain available "
     "until September 30, 2025: Provided, That such amount is designated by the Congress as being for an emergency "
     "requirement'; bill or law text controls over the report table for the same scope (owner 2026-10-11) -- Note: S.Rept. 118-84 p.390 prints 'Pandemic Preparedness and Biodefense (emergency)' 75,000 "
     "in the Committee recommendation column [bill text] (D-9)"),
    (BHIBG, 2026, "President's Budget", 4025411000, "SRC-CRPT-119HRPT271", "348",
     "Title II, SAMHSA -- printed as 'Behavioral Health Innovation Block Grant' [FY 2026 Request column] 4,025,411 "
     "(evaluation tap funding (100,239) and program level 4,125,650 beside), inside 'Total, SAMHSA' 5,562,611 (page "
     "image) (D-9)"),
    (BHIBG, 2027, "President's Budget", 4520411000, "SRC-CRPT-119HRPT696", "379",
     "Title II, SAMHSA -- printed as 'Behavioral Health Innovation Block Grant' [FY 2027 Request column] 4,520,411 "
     "(evaluation tap funding (100,239) and program level 4,620,650 beside), inside 'Total, SAMHSA' 6,552,543 (page "
     "image) (D-9)"),
]
ABSENCES += [
    (CX, 2025, "House Reported", "SRC-BILLS-118HR9029RH",
     "H.R. 9029 has no 'Customer Experience' heading or appropriation (whole text searched for /Customer Experience/: "
     "no mention) (D-9)."),
    (CX, 2025, "Senate Reported", "SRC-BILLS-118S4942RS",
     "S. 4942 has no 'Customer Experience' heading or appropriation (whole text searched for /Customer Experience/: no "
     "mention) (D-9)."),
    (CX, 2024, "House Reported", "SRC-BILLS-118HR5894IH",
     "H.R. 5894 has no 'Customer Experience' heading or appropriation (whole text searched for /Customer Experience/: "
     "no mention) (D-9)."),
    (CX, 2024, "Senate Reported", "SRC-BILLS-118S2624RS",
     "S. 2624 has no 'Customer Experience' heading or appropriation (whole text searched for /Customer Experience/: no "
     "mention) (D-9)."),
    (ASPR_MP, 2024, "House Reported", "SRC-BILLS-118HR5894IH",
     "H.R. 5894 has no 'Manufacturing and Production' heading or appropriation under ASPR (whole text searched for "
     "/Manufacturing and Production|Pandemic Preparedness and Biodefense/: no mention) (D-9)."),
    (BHIBG, 2026, "House Reported", "SRC-BILLS-119HR5304RH",
     "H.R. 5304 has no 'Behavioral Health Innovation Block Grant' heading or appropriation (whole text searched for "
     "/Innovation Block Grant/: no mention) (D-9)."),
    (BHIBG, 2026, "Senate Reported", "SRC-BILLS-119S2587RS",
     "S. 2587 has no 'Behavioral Health Innovation Block Grant' heading or appropriation (whole text searched for "
     "/Innovation Block Grant/: no mention) (D-9)."),
    (BHIBG, 2027, "House Reported", "SRC-BILLS-119HR9260RH",
     "H.R. 9260 has no 'Behavioral Health Innovation Block Grant' heading or appropriation (whole text searched for "
     "/Innovation Block Grant/: no mention) (D-9)."),
]


def new_accounts(data):
    """Add NEW_ACCOUNTS that are not there yet (Title II, after the last display order): -> the IDs added."""
    have = {a["canonical_account_id"] for a in data["accounts"]}
    order = list(data["accounts"][0])
    top = max(a["display_order"] for a in data["accounts"] if isinstance(a["display_order"], int))
    out = []
    for aid, name, agency, status, notes in NEW_ACCOUNTS:
        if aid in have:
            continue
        top += 1
        rec = {k: "" for k in order}
        rec.update({"canonical_account_id": aid, "canonical_name": name, "agency": agency, "bureau": name,
                    "status": status, "fund_type": "general", "subcommittee": "LHHS", "notes": notes,
                    "title": "Title II", "display_order": top, "total_scope": "", "parent_account_id": ""})
        data["accounts"].append(rec)
        out.append(aid)
    return out


def live_cells(data):
    """(account, fy, stage) of every headline budget-authority cell that already holds an observation or an
    absence."""
    held = set()
    for o in data["observations"]:
        if o["verification_status"] != "superseded" and o["amount_type"] == "budget authority" and not o["component"]:
            held.add((o["canonical_account_id"], int(o["fiscal_year"]), o["stage"]))
    for c in data["confirmed_absences"]:
        if c["amount_type"] == "budget authority" and not c["component"]:
            held.add((c["canonical_account_id"], int(c["fiscal_year"]), c["stage"]))
    return held


def part1(data, retired):
    docs = {s["document_id"] for s in data["source_docs"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    held = live_cells(data)
    obs_alloc = IDS.Allocator([o["observation_id"] for o in data["observations"]] + list(retired.get("observations", {})))
    ca_alloc = IDS.Allocator([c["confirmed_absence_id"] for c in data["confirmed_absences"]]
                             + list(retired.get("confirmed_absences", {})))
    added_obs, added_ca, skipped = [], [], []
    for aid, fy, st, amount, doc, page, where in FIGURES:
        assert aid in acc and doc in docs, (aid, doc)
        if (aid, fy, st) in held:
            skipped.append((aid, fy, st, "already held"))
            continue
        added_obs.append({
            "observation_id": obs_alloc.next("OBS", acc[aid]["subcommittee"]), "canonical_account_id": aid,
            "fiscal_year": fy, "stage": st, "chamber": STAGE_CHAMBER.get(st, "N/A"), "bill_id": "", "report_id": "",
            "amount": amount, "amount_type": "budget authority", "offsetting_collections": "FALSE",
            "transfer_link_account_id": "", "source_document_id": doc, "source_page": page,
            "source_table_or_section": where, "extraction_method": "text-extracted", "confidence": 0.95,
            # a figure the bill-text rule decides is 'resolved-by-rule' (validate_approps.observation_status)
            "verification_status": V.RESOLVED_BY_RULE if V.TEXT_RULE_MARK in where else "unverified",
            "component": "", "headline_observation_id": ""})
    for aid, fy, st, doc, evidence in ABSENCES:
        assert aid in acc and doc in docs, (aid, doc)
        if (aid, fy, st) in held:
            skipped.append((aid, fy, st, "already held"))
            continue
        added_ca.append({
            "confirmed_absence_id": ca_alloc.next("CA", acc[aid]["subcommittee"]), "canonical_account_id": aid,
            "fiscal_year": fy, "stage": st, "amount_type": "budget authority", "component": "",
            "source_document_id": doc, "evidence": evidence, "confirmed_date": DATE})
    return added_obs, added_ca, skipped


# the table pages of a document a part 1 figure cites, where the document had none: H.R. 9029 (RH) Title II, from
# 'TITLE II' (PDF p.53) to the page before 'TITLE III' (p.129)
DOC_PAGES = {"SRC-BILLS-118HR9029RH": "53-128"}
# pages a part 1 figure cites outside its document's recorded table pages, added to them: the Medicare Operations
# general provisions (P.L. 117-328 p.426; S. 2624 p.127); part 8: sec. 195 (P.L. 114-254 p.17), S. 2624's ASPR
# 'MANUFACTURING AND PRODUCTION' heading (p.107)
DOC_EXTRA_PAGES = {"SRC-PLAW-117PUBL328": ["426"], "SRC-BILLS-118S2624RS": ["127", "107"],
                   "SRC-PLAW-114PUBL254": ["17"]}


def doc_pages(data):
    """Record DOC_PAGES on documents whose source_page is blank, and DOC_EXTRA_PAGES: -> the documents changed."""
    out = []
    for d in data["source_docs"]:
        if d["document_id"] in DOC_PAGES and not d["source_page"]:
            d["source_page"] = DOC_PAGES[d["document_id"]]
            out.append(d["document_id"])
        for extra in DOC_EXTRA_PAGES.get(d["document_id"], []):
            if extra not in [x.strip() for x in d["source_page"].split(";")]:
                d["source_page"] = f"{d['source_page']}; {extra}"
                out.append(d["document_id"])
    return out


# (old observation, the figure its column prints, the column's text in the document): read from the text layer
SUPERSEDE = [("OBS-1436", 33909609000, "SRC-CRPT-119SRPT44", "219", "Total, Title III, Science",
              "Committee recommendation column; OBS-1436 held the FY2025 column's 33,908,257 thousand "
              "(owner-approved 2026-10-15, D-9 part 6)")]


def part6(data, retired, pending=()):
    """Supersede the observations in SUPERSEDE: -> [(old, new)] (idempotent)."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    alloc = IDS.Allocator([o["observation_id"] for o in list(data["observations"]) + list(pending)]
                          + list(retired.get("observations", {})))
    out = []
    for old_id, amount, doc, page, label, why in SUPERSEDE:
        old = obs[old_id]
        assert (old["source_document_id"], old["source_page"]) == (doc, page), old_id
        if old["verification_status"] == "superseded":
            continue                                          # done already (the text check ran then)
        text = (ROOT / "document_store" / f"{doc}.txt").read_text()
        line = next(x for x in text.splitlines() if label in x)
        printed = [int(n.replace(",", "")) * 1000 for n in re.findall(r"\d{1,3}(?:,\d{3})+", line)]
        if amount not in printed:
            raise SystemExit(f"STOP: {doc} prints {printed} on '{label}', not {amount:,}")
        new = dict(old, observation_id=alloc.next("OBS", "CJS"), amount=amount,
                   source_table_or_section=f"{old['source_table_or_section']} -- supersedes {old_id}: {why}")
        new.pop("superseded_by_observation_id", None)
        old["verification_status"] = "superseded"
        old["superseded_by_observation_id"] = new["observation_id"]
        out.append((old, new))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = json.loads(STAGED.read_text())
    retired = json.loads(RETIRED.read_text())
    added_accounts = new_accounts(data)
    print(f"part 8: accounts added {added_accounts or 'none (already there)'}")
    obs, cas, skipped = part1(data, retired)
    print(f"part 1: observations added {len(obs)}, confirmed absences added {len(cas)}, skipped {len(skipped)}")
    for o in obs:
        print(f"  {o['observation_id']} {o['canonical_account_id']} FY{o['fiscal_year']} {o['stage']}: "
              f"${o['amount']:,} ({o['source_document_id']} p.{o['source_page']})")
    for c in cas:
        print(f"  {c['confirmed_absence_id']} {c['canonical_account_id']} FY{c['fiscal_year']} {c['stage']} "
              f"({c['source_document_id']})")
    for s in skipped:
        print("  skipped", *s)
    by = collections.Counter(o["canonical_account_id"] for o in obs) + collections.Counter(
        c["canonical_account_id"] for c in cas)
    print("  by account:", dict(sorted(by.items())))
    life = part2(data)
    print(f"part 2: {LIMITATION} start override {'cleared, note updated' if life else 'already cleared'}")
    pages = doc_pages(data)
    print(f"document table pages recorded: {pages or 'none needed'}")
    sup = part6(data, retired, obs)
    for old, new in sup:
        print(f"part 6: {old['observation_id']} ${old['amount']:,} -> {new['observation_id']} ${new['amount']:,} "
              f"({new['source_document_id']} p.{new['source_page']})")
    if args.write and (added_accounts or obs or cas or life or pages or sup):
        data["observations"] += obs + [n for _, n in sup]
        data["confirmed_absences"] += cas
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")


if __name__ == "__main__":
    sys.exit(main())
