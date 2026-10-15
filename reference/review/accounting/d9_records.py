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

    python reference/review/accounting/d9_records.py           # what would change
    python reference/review/accounting/d9_records.py --write   # write it (idempotent)
"""

import argparse
import collections
import json
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
# general provisions (P.L. 117-328 p.426; S. 2624 p.127)
DOC_EXTRA_PAGES = {"SRC-PLAW-117PUBL328": "426", "SRC-BILLS-118S2624RS": "127"}


def doc_pages(data):
    """Record DOC_PAGES on documents whose source_page is blank, and DOC_EXTRA_PAGES: -> the documents changed."""
    out = []
    for d in data["source_docs"]:
        if d["document_id"] in DOC_PAGES and not d["source_page"]:
            d["source_page"] = DOC_PAGES[d["document_id"]]
            out.append(d["document_id"])
        extra = DOC_EXTRA_PAGES.get(d["document_id"])
        if extra and extra not in [x.strip() for x in d["source_page"].split(";")]:
            d["source_page"] = f"{d['source_page']}; {extra}"
            out.append(d["document_id"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = json.loads(STAGED.read_text())
    retired = json.loads(RETIRED.read_text())
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
    if args.write and (obs or cas or life or pages):
        data["observations"] += obs
        data["confirmed_absences"] += cas
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")


if __name__ == "__main__":
    sys.exit(main())
