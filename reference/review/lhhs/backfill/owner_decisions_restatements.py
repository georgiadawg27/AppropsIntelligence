"""
The owner's decisions of 2026-10-09 on restated Enacted years. Appends to data/staged.json; an existing figure changes
only by supersession (the old observation stays, marked superseded) or retirement (reference/review/lhhs/
retired_ids.json; IDs never reused).

    python reference/review/lhhs/backfill/owner_decisions_restatements.py           # what would change
    python reference/review/lhhs/backfill/owner_decisions_restatements.py --write   # apply

Standing rule (owner, 2026-10-09; replaces 'one structure per year'): each stage as its own documents print it;
Enacted follows the enacting law; where stages of one year use different structures, the difference is a proposed
relationship with cell notes.

1. FY2019 Strategic National Stockpile. The request and the House bill put the SNS under PHSSEF/ASPR; the Senate bill
   and P.L. 115-245 kept it in CDC's 'Public Health Preparedness and Response' ($1,465,200,000, of which $610,000,000
   for the SNS, p.93). H.Rept. 116-62's FY 2019 Enacted column restates FY2019 in the FY2020 structure. Enacted
   follows the law: CDC PHPR 1,465,200 and PHSSEF 2,021,458 (the law's three PHSSEF paragraphs, pp.108-109:
   $1,026,458,000 + $735,000,000 + $260,000,000; = H.Rept. 116-62's 2,631,458 - 610,000), and the CDC and Office of
   the Secretary totals and program levels with them (derived: +/- 610,000, citing the printed lines). Each new
   figure's corner says what H.Rept. 116-62 prints. A proposed move (request, House) and the actual move (FY2020,
   P.L. 116-94: PHSSEF 'section 319F-2(a)', $705,000,000, p.45) are relationships with cell notes.
2. FY2023 ASPR. P.L. 117-328 funds ASPR's programs under the Office of the Secretary's PHSSEF heading: its four
   paragraphs sum to $3,767,569,000 (pp.419-420; the paragraph-summing rule). S.Rept. 118-84 restates FY2023 in the
   FY2024 structure (ASPR 3,629,677 + PHSSEF 137,892). Enacted follows the law: PHSSEF 3,767,569 and the Office of
   the Secretary total + 3,629,677 (derived); ASPR's three FY2023 Enacted figures are retired (the cell reads 'funded
   within PHSSEF', the restated figure in its corner). ASPR's first year becomes FY2024 (its corner note 'Funded
   within PHSSEF before FY2024.'; the relationships were already effective FY2024).
3. FY2018 NIH Office of the Director, Enacted: the explanatory statement for P.L. 115-141 (Congressional Record,
   2018-03-22, House book 3, p.H2735: Final Bill 'Office of the Director' 1,803,293 + 'Gabriella Miller Kids First
   Research Act (Common Fund add)' 12,600 = 1,815,893) prints our figure: the S.Rept. 115-289 difference is resolved.
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
TODAY = "2026-10-09"
REVIEWER = "owner (2026-10-09)"
CDC_PHPR, PHSSEF, CDC_T, OS_T = ("ACC-HHS-CDC-PREPAREDNESS", "ACC-HHS-OS-PHSSEF", "ACC-HHS-CDC-TOTAL", "ACC-HHS-OS-TOTAL")
ASPR = ("ACC-HHS-ASPR-TOTAL", "ACC-HHS-ASPR-OPER", "ACC-HHS-ASPR-RDP")
H62 = "H.Rept. 116-62 restates FY2019 in the FY2020 structure (SNS under PHSSEF)"
S84 = "S.Rept. 118-84 restates FY2023 in the FY2024 structure (ASPR separate from PHSSEF)"

DOC_117_328 = {
    "document_id": "SRC-PLAW-117PUBL328", "source_agency": "U.S. Congress (public law; govinfo PLAW collection)",
    "url_or_identifier": "https://www.govinfo.gov/content/pkg/PLAW-117publ328/pdf/PLAW-117publ328.pdf",
    "document_type": "public_law", "congress_session": "117-2", "fiscal_year": 2023,
    "publication_date": "2022-12-29", "stage": "Enacted", "retrieval_timestamp": TODAY, "source_page": "419-420",
    "also_covers": "",
    "notes": "Consolidated Appropriations Act, 2023; Division H (Labor-HHS) is PDF pp. 376-454; the Office of the "
             "Secretary's 'Public Health and Social Services Emergency Fund' heading is pp. 419-420. sha256 "
             "6e063f9299ced827... (the file at the link). Page citations are PDF page numbers."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    assert not any(r["relationship_id"] == "REL-LHHS-0017" for r in data["relationships"]), "already applied"
    retired = json.loads(RETIRED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}

    def ids(prefix, field, rows_, width):
        gone = [i for v in retired.values() if isinstance(v, dict) for i in v]
        n = max(int(i[len(prefix):]) for i in [r[field] for r in rows_] + gone
                if i.startswith(prefix) and i[len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = ids("OBS-LHHS-", "observation_id", data["observations"], 4)
    vids = ids("VAL-LHHS-", "validation_id", data["validations"], 5)
    rids = ids("REL-LHHS-", "relationship_id", data["relationships"], 4)
    new_obs, new_val, superseded = [], [], []

    def current(aid, fy, stage, comp=""):
        hit = [o for o in data["observations"] if (o["canonical_account_id"], o["fiscal_year"], o["stage"],
               o["component"] or "") == (aid, fy, stage, comp) and o["verification_status"] != "superseded"
               and o["amount_type"] == "budget authority"]
        assert len(hit) == 1, (aid, fy, stage, comp, hit)
        return hit[0]

    def replace(old, amount, doc, page, section, method, confidence=0.95, headline=""):
        o = dict(old, observation_id=next(oids), amount=amount, source_document_id=doc, source_page=str(page),
                 source_table_or_section=section, extraction_method=method, confidence=confidence,
                 verification_status="", headline_observation_id=headline)
        o.pop("superseded_by_observation_id", None)
        new_obs.append(o)
        old["verification_status"] = "superseded"
        old["superseded_by_observation_id"] = o["observation_id"]
        superseded.append((old["observation_id"], o["observation_id"], old["amount"] // 1000, amount // 1000))
        return o

    def record(o, rule, expected, result="pass"):
        new_val.append({"observation_id": o["observation_id"], "rule_applied": rule, "expected_result": expected,
                        "observed_result": f"{o['amount'] // 1000:,} as recorded", "result": result})

    # 1. FY2019 SNS: Enacted follows P.L. 115-245
    phpr = current(CDC_PHPR, 2019, "Enacted")
    n = replace(phpr, 1_465_200_000, "SRC-PLAW-115PUBL245", 93,
                "Division B, Title II, Centers for Disease Control and Prevention, 'Public Health Preparedness and "
                "Response' (132 Stat. 3073) -- $1,465,200,000, of which $610,000,000 for the Strategic National "
                "Stockpile [law text] -- Note: " + f"{H62}: 855,200 thousand (p.335).", "text-extracted")
    record(n, "source_text", "P.L. 115-245 p.93: '... civilian populations, $1,465,200,000, of which $610,000,000 "
                             "shall remain available until expended for the Strategic National Stockpile'")
    ps = current(PHSSEF, 2019, "Enacted")
    n = replace(ps, 2_021_458_000, "SRC-PLAW-115PUBL245", "108-109",
                "Division B, Title II, Office of the Secretary, 'Public Health and Social Services Emergency Fund' "
                "(132 Stat. 3088-3089) -- its three paragraphs $1,026,458,000 + $735,000,000 + $260,000,000 = "
                "$2,021,458,000 [law text] -- Note: " + f"{H62}: 2,631,458 thousand (p.357).", "text-extracted")
    record(n, "source_text", "P.L. 115-245 pp.108-109, 'Public Health and Social Services Emergency Fund': "
                             "$1,026,458,000 + $735,000,000 + $260,000,000 = $2,021,458,000")
    record(n, "structural", "the law's figure = H.Rept. 116-62's restated 2,631,458 - the SNS's 610,000 (P.L. 115-245 "
                            "p.93, appropriated to CDC) = 2,021,458 (thousands)")
    for aid, sign, page in ((CDC_T, 1, 335), (OS_T, -1, 357)):
        head = current(aid, 2019, "Enacted")
        what = "CDC" if aid == CDC_T else "the Office of the Secretary"
        hn = replace(head, head["amount"] + sign * 610_000_000, "SRC-CRPT-116HRPT62", page,
                     f"{head['source_table_or_section'].split(' -- ')[0]} -- derived, not a printed line: H.Rept. 116-62 "
                     f"p.{page} {head['amount'] // 1000:,} {'+' if sign > 0 else '-'} the Strategic National "
                     f"Stockpile's 610,000 (thousands; P.L. 115-245 p.93 appropriates it to CDC) -- Note: {H62}: "
                     f"{head['amount'] // 1000:,} thousand (p.{page}).", "derived")
        record(hn, "structural", f"headline = H.Rept. 116-62 p.{page} {head['amount'] // 1000:,} "
                                 f"{'+' if sign > 0 else '-'} 610,000 = {hn['amount'] // 1000:,} (thousands): {what} "
                                 "in the law's structure")
        view = current(aid, 2019, "Enacted", "program_level")
        vn = replace(view, view["amount"] + sign * 610_000_000, "SRC-CRPT-116HRPT62", page,
                     f"{view['source_table_or_section'].split(' -- ')[0]} -- derived, not a printed line: H.Rept. "
                     f"116-62 p.{page} program level {view['amount'] // 1000:,} {'+' if sign > 0 else '-'} 610,000 "
                     "(the Strategic National Stockpile, P.L. 115-245 p.93)", "derived", headline=hn["observation_id"])
        record(vn, "structural", f"program level = H.Rept. 116-62 p.{page} {view['amount'] // 1000:,} "
                                 f"{'+' if sign > 0 else '-'} 610,000 = {vn['amount'] // 1000:,} (thousands)")

    rels = []
    note = ("Proposed moving the Strategic National Stockpile (${:,}) to PHSSEF/ASPR; the Senate and the enacted law "
            "kept it in CDC.")
    cells = " ".join(f"Cell note ({aid} FY2019 {st}): {note.format(x)}" for st, x in
                     (("President's Budget", 575_000_000), ("House Reported", 710_000_000)) for aid in (CDC_PHPR, PHSSEF))
    rels.append({"relationship_id": next(rids), "from_account_id": CDC_PHPR, "to_account_id": PHSSEF,
                 "relationship_type": "moved_reclassified", "effective_fiscal_year": "2019",
                 "evidence": "Proposed, not enacted (FY2019 President's Budget and House bill). The request moved the "
                             "Strategic National Stockpile from CDC's 'Public Health Preparedness and Response' to "
                             "PHSSEF/ASPR (H.Rept. 115-862 p.384: 575,000; CDC PHPR 800,000, p.363); H.R. 6470 did "
                             "too ($710,000,000 under PHSSEF, p.93; H.Rept. 115-862 p.384). S. 3158 (p.57: $1,470,000,000, "
                             "of which $610,000,000 for the SNS) and P.L. 115-245 (p.93: $1,465,200,000, of which "
                             "$610,000,000) kept it in CDC. Recorded by the owner's rule (2026-10-09): stages of one "
                             "year with different structures. " + cells,
                 "confidence": 1.0, "human_reviewed": "TRUE"})
    moved = "The Strategic National Stockpile moved from CDC to PHSSEF/ASPR from FY2020 (P.L. 116-94: $705,000,000)."
    fy20 = sorted({(o["canonical_account_id"], o["stage"]) for o in data["observations"]
                   if o["canonical_account_id"] in (CDC_PHPR, PHSSEF) and o["fiscal_year"] == 2020
                   and o["verification_status"] != "superseded"})
    rels.append({"relationship_id": next(rids), "from_account_id": CDC_PHPR, "to_account_id": PHSSEF,
                 "relationship_type": "moved_reclassified", "effective_fiscal_year": "2020",
                 "evidence": "The Strategic National Stockpile moved from CDC's 'Public Health Preparedness and "
                             "Response' to PHSSEF (ASPR) in FY2020: P.L. 116-94 div. A appropriates it under PHSSEF ('For "
                             "expenses necessary to carry out section 319F-2(a) of the PHS Act, $705,000,000', p.45), "
                             "and every FY2020 document prints it there (H.Rept. 116-62 p.356). Through FY2019 the law "
                             "appropriated it to CDC (P.L. 115-245 p.93). " +
                             " ".join(f"Cell note ({aid} FY2020 {st}): {moved}" for aid, st in fy20),
                 "confidence": 1.0, "human_reviewed": "TRUE"})

    # 2. FY2023 ASPR: Enacted follows P.L. 117-328
    ps = current(PHSSEF, 2023, "Enacted")
    n = replace(ps, 3_767_569_000, "SRC-PLAW-117PUBL328", "419-420",
                "Division H, Title II, Office of the Secretary, 'Public Health and Social Services Emergency Fund' "
                "(136 Stat. 4877-4878) -- its four paragraphs $1,647,569,000 + $820,000,000 + $965,000,000 + "
                "$335,000,000 = $3,767,569,000 [law text] -- Note: " + f"{S84}: PHSSEF 137,892 + ASPR 3,629,677 "
                "thousand (pp.389-392).", "text-extracted")
    record(n, "source_text", "P.L. 117-328 pp.419-420, 'Public Health and Social Services Emergency Fund': "
                             "$1,647,569,000 + $820,000,000 + $965,000,000 + $335,000,000 = $3,767,569,000 "
                             "(H.R. 2617 enrolled, div. H: the same four paragraphs)")
    record(n, "structural", "the law's figure = S.Rept. 118-84's restated PHSSEF 137,892 + ASPR 3,629,677 = "
                            "3,767,569 (thousands)")
    head = current(OS_T, 2023, "Enacted")
    hn = replace(head, head["amount"] + 3_629_677_000, "SRC-CRPT-118SRPT84", 392,
                 f"{head['source_table_or_section'].split(' -- ')[0]} -- derived, not a printed line: S.Rept. 118-84 p.392 "
                 f"{head['amount'] // 1000:,} + ASPR's restated 3,629,677 (p.390), funded under the Office of the "
                 f"Secretary's PHSSEF heading in P.L. 117-328 -- Note: {S84}: {head['amount'] // 1000:,} thousand (p.392).",
                 "derived")
    record(hn, "structural", f"headline = S.Rept. 118-84 p.392 {head['amount'] // 1000:,} + 3,629,677 (p.390) = "
                             f"{hn['amount'] // 1000:,} (thousands): the Office of the Secretary in the law's structure")
    gone_obs = [o for o in data["observations"] if o["canonical_account_id"] in ASPR and o["fiscal_year"] == 2023
                and o["stage"] == "Enacted" and o["verification_status"] != "superseded"]
    assert len(gone_obs) == 3 and not any(o.get("headline_observation_id") in {g["observation_id"] for g in gone_obs}
                                          for o in data["observations"])
    gone_ids = {o["observation_id"] for o in gone_obs}
    gone_val = [v for v in data["validations"] if v["observation_id"] in gone_ids]
    restated = {o["canonical_account_id"]: o for o in gone_obs}
    for r in data["relationships"]:
        if r["relationship_id"] in ("REL-LHHS-0009", "REL-LHHS-0010", "REL-LHHS-0016"):
            aid = r["from_account_id"]
            o = restated[aid]
            r["evidence"] = (r["evidence"].replace("Before FY2023 ASPR's programs were funded within PHSSEF.",
                                                   "Through FY2023 ASPR's programs were funded within PHSSEF: P.L. "
                                                   "117-328 appropriates them under its PHSSEF heading (owner, "
                                                   "2026-10-09: Enacted follows the law; ASPR's FY2023 Enacted figures, "
                                                   "S.Rept. 118-84's restatement, retired).")
                             .replace("Note: Funded within PHSSEF before FY2023.", "Note: Funded within PHSSEF before FY2024.")
                             + f" Cell note ({aid} FY2023 Enacted): {S84}: {o['amount'] // 1000:,} thousand "
                               f"(p.{o['source_page']}).")
            assert "before FY2024." in r["evidence"] and r["effective_fiscal_year"] == "2024"

    # 3. FY2018 NIH Office of the Director, Enacted: the explanatory statement prints our figure
    od = [v for v in data["validations"] if v["rule_applied"] == "cross_document" and v["human_review_status"] == "pending"
          and obs[v["observation_id"]]["canonical_account_id"] == "ACC-HHS-NIH-OD"
          and obs[v["observation_id"]]["fiscal_year"] == 2018 and obs[v["observation_id"]]["stage"] == "Enacted"]
    assert len(od) == 1
    od[0].update({"human_review_status": "resolved", "reviewer": REVIEWER, "resolution": (
        "The explanatory statement for P.L. 115-141 (Congressional Record, 2018-03-22, House book 3, p.H2735, "
        "https://www.govinfo.gov/content/pkg/CREC-2018-03-22/pdf/CREC-2018-03-22-house-bk3.pdf PDF p.39, GPO's text "
        "layer) prints the Final Bill column 'Office of the Director' 1,803,293 + 'Gabriella Miller Kids First Research "
        "Act (Common Fund add)' 12,600 = 1,815,893 (thousands): our figure (H.Rept. 115-862's FY 2018 Enacted column). "
        "S.Rept. 115-289's 2018 appropriation column prints another split of NIH (1,814,745).")})

    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    recs = collections.defaultdict(list)
    for v in data["validations"] + new_val:
        recs[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                          v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], recs[o["observation_id"]])
    oid = od[0]["observation_id"]
    obs[oid]["verification_status"] = V.verification_status(obs[oid]["confidence"], recs[oid])
    for old, new, a_, b_ in superseded:
        print(f"superseded {old} {a_:,} -> {new} {b_:,}")
    print(f"{len(new_obs)} observations ({collections.Counter(o['verification_status'] for o in new_obs)}), "
          f"{len(new_val)} validations, {len(rels)} relationships, retired {sorted(gone_ids)} with "
          f"{len(gone_val)} validation records; FY2018 NIH OD {od[0]['validation_id']} resolved "
          f"({obs[oid]['verification_status']})")
    if a.write:
        data["source_docs"].append(DOC_117_328)
        order = [k for k in data["observations"][0] if k != "superseded_by_observation_id"]
        data["observations"] = [o for o in data["observations"] if o["observation_id"] not in gone_ids]
        data["observations"] += [{k: o.get(k, "") for k in order} for o in new_obs]
        vorder = list(data["validations"][0])
        data["validations"] = [v for v in data["validations"] if v["observation_id"] not in gone_ids]
        data["validations"] += [{k: v[k] for k in vorder} for v in new_val]
        data["relationships"] += rels
        why = "FY2023 ASPR Enacted, S.Rept. 118-84's restatement: Enacted follows P.L. 117-328 (owner 2026-10-09)"
        retired.setdefault("observations", {}).update({i: why for i in sorted(gone_ids)})
        retired.setdefault("validations", {}).update({v["validation_id"]: f"a record of a retired observation "
                                                      f"({v['observation_id']})" for v in gone_val})
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        RETIRED.write_text(json.dumps(retired, indent=1) + "\n")
        print("wrote", STAGED.relative_to(ROOT), "and", RETIRED.relative_to(ROOT))


if __name__ == "__main__":
    main()
