"""
FY2024 House Labor-HHS, from the House subcommittee draft: the explanatory materials for H.R. 5894
(House Committee on Appropriations, print date Nov. 2, 2023; the full committee never reported a FY2024
Labor-HHS bill). Appends to data/staged.json; never changes an existing row or ID.

    python reference/review/lhhs/fy2024_house/build_rows.py           # report only: what would be added
    python reference/review/lhhs/fy2024_house/build_rows.py --write   # append to data/staged.json

Reads extractions/<package>.title-ii.json (extract_approps.py --title "TITLE II", vision: the
comparative statement, PDF pp. 250-284, is image-only).

How each figure is found -- the rules every Labor-HHS stage was built with:
  - every Labor-HHS fact on file (account x amount_type x component) has the labels it was printed
    under in the committee tables (source_table_or_section: "printed as '<label>'"); the same label,
    cleaned the same way (match.clean), is looked up in this table's "Bill" column (FY2024 House),
    inside the agency section the account belongs to (rows from one agency total to the next);
    exact label matches only -- anything not found is listed, never guessed;
  - a contained / view line (CURES, program level, ...) points at the headline it sits beside
    (headline_observation_id, same document);
  - validation records: the extractor's own checks on the row (table_total, structural, source_text,
    unit, semantic), then cross_document; verification_status is validate_approps.verification_status
    (the standard rule); every fail / flag gets human_review_status pending.
Cross-document (report, review/cross_document_fy2024_house.csv): this table's FY 2023 Enacted column
against our FY2023 Enacted figures and its FY 2024 Request column against our FY2024 President's Budget
figures (same fact). The new observations' own records: the agency sums (our accounts inside an agency
total), the NIH institutes to the NIH total, and the Title II total.
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

import validate_approps as V  # noqa: E402

PKG = "MANUAL-LHHS-FY2024-HouseReported-explanatory_statement-408e3388"
SHA256 = "408e33889225be0225d2c1fff15e095f5532df9d5b8fd8dd1c0125dbf0f8ff8d"
SRC = "SRC-EXPL-LHHS-FY2024-HOUSE"
BR = "BR-LHHS-FY2024-HOUSE"
URL = "https://appropriations.house.gov/sites/evo-subsites/republicans-appropriations.house.gov/files/FY24-LHHS-Explanatory-Materials.pdf"
BILL_URL = "https://www.govinfo.gov/content/pkg/BILLS-118hr5894ih/pdf/BILLS-118hr5894ih.pdf"   # sha256 67108f5b... (govinfo_ingest)
TODAY = "2026-10-08"
COL = "Bill"
PAGES = "250-284"
# the House committee tables: their labels decide between two labels that match different rows (most recent first)
HOUSE_TABLES = ["SRC-CRPT-119HRPT696", "SRC-CRPT-119HRPT271", "SRC-CRPT-118HRPT585", "SRC-CRPT-117HRPT403"]
# lines the table does not print, and H.R. 5894 provides none of either (its text searched): confirmed absences,
# as for the House reports' FY2026-27 and the Senate reports' (CA-LHHS-0019..0023, 0012..0018)
ABSENT = {
    ("ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", "rescission"): (
        "Adoption Incentives (rescission)", "/Adoption Incentives/"),
    ("ACC-HHS-GP-MEDICARE-LIMITATION", "budget authority"): (
        "Limitation for Title XVIII of the Social Security Act", "/Title XVIII|1834\\(l\\)/"),
}
STAGED = ROOT / "data" / "staged.json"
DRAFT_NOTE = ("House subcommittee draft; the full committee never reported a FY2024 Labor-HHS bill; accompanies H.R. 5894 "
              "(introduced 2023); the July 14, 2023 subcommittee mark is an earlier version")

# ---- the label rules (as the earlier Labor-HHS stages: reference/review/lhhs; scratch match.py) -------------
AGENCY_TOTALS = [  # (agency key, regex on the cleaned label)
    ("AHA", r"^total, administration for a healthy america$"),
    ("HRSA", r"^total, health resources and services administration$"),
    ("CDC", r"^total, centers for disease control and prevention$"),
    ("NIH", r"^total, national institutes of health( \(?with cures act funding\)?)?$"),
    ("SAMHSA", r"^total, (samhsa|substance abuse and mental health services administration)$"),
    ("AHRQ", r"^total, (ahrq|agency for healthcare research and quality)( program level)?$"),
    ("CMS", r"^total, centers for medicare (and|&) medicaid services$"),
    ("ACF", r"^total, administration for children and famili+es$"),
    ("ACL", r"^total, administration for community living$"),
    ("ASPR", r"^total, (administration|office of the assistant secretary) for (strategic )?preparedness and response$"),
    ("OS", r"^total, office of the secretary$"),
]
AGENCY_KEY = {"Health Resources and Services Administration": "HRSA", "Centers for Disease Control and Prevention": "CDC",
              "National Institutes of Health": "NIH", "Substance Abuse and Mental Health Services Administration": "SAMHSA",
              "Agency for Healthcare Research and Quality": "AHRQ", "Centers for Medicare & Medicaid Services": "CMS",
              "Administration for Children and Families": "ACF", "Administration for Community Living": "ACL",
              "Administration for Strategic Preparedness and Response": "ASPR", "Office of the Secretary": "OS",
              "Administration for a Healthy America": "AHA", "Department of Health and Human Services": "TAIL"}


def clean(label):
    s = (label or "").strip()
    s = re.sub(r"[‐-―−–]", "-", s).replace("&", "and").replace("’", "'")
    s = re.sub(r"\s*\d+/\s*$", "", s)
    s = re.sub(r"(CURES Act)\s*\d+$", r"\1", s)
    s = re.sub(r"\s*\((NIH|NCI|NHLBI|NIDCR|NIDDK|NINDS|NIAID|NIGMS|NICHD|NEI|NIEHS|NIA|NIAMS|NIDCD|NINR|NIAAA|NIDA|NIMH|"
               r"NHGRI|NIBIB|NCCIH|NIMHD|FIC|NLM|NCATS|ARPA-H|ARPA-H\)\.|PRNS|LIHEAP|NA|Trust funds|Title XX|Title X)\)", "", s)
    s = re.sub(r"\s*/\d+\s*$", "", s)
    s = re.sub(r"\s*[\[(][A-Za-z0-9-]{2,8}[\])]?\s*$", "", s)
    s = re.sub(r"[.\s]+$", "", s)
    return re.sub(r"\s+", " ", s).lower()


PRINTED = re.compile(r"printed as (['\"])(.*?)\1")


def load_extraction():
    x = json.loads((ROOT / "extractions" / f"{PKG}.title-ii.json").read_text())
    recs = defaultdict(list)
    for r in x["validation_records"]:
        recs[r["observation_id"]].append(r)
    by_col = defaultdict(list)
    for o in x["observations"]:
        by_col[o["column_header"]].append(dict(o, _clean=clean(o["account_name_as_written"]), _records=recs[o["observation_id"]]))
    sections = {}
    for col, rows in by_col.items():
        rows.sort(key=lambda f: f["node_id"])
        secs, cur = [], []
        for f in rows:
            cur.append(f)
            ag = next((a for a, rx in AGENCY_TOTALS if re.match(rx, f["_clean"])), None)
            if ag:
                secs.append((ag, cur))
                cur = []
        secs.append(("TAIL", cur))
        sections[col] = secs
    return x, sections


def rows_for(sections, col, agency):
    """The rows of the account's agency section; 'TAIL' (department-wide lines) searches the whole table."""
    secs = sections[col]
    if agency == "TAIL":
        return [f for _, rows in secs for f in rows]
    return [f for a, rows in secs if a == agency for f in rows]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    raw = STAGED.read_text()
    data = json.loads(raw)
    acct = {x["canonical_account_id"]: x for x in data["accounts"] if x["subcommittee"] == "LHHS"}
    comps = {c["component_id"]: c for c in data["components"]}
    obs_all = data["observations"]
    lhhs_obs = [o for o in obs_all if o["canonical_account_id"] in acct]

    # 1. facts and the labels they were printed under, from every Labor-HHS observation on file
    facts = defaultdict(lambda: {"labels": defaultdict(int), "house": []})
    for o in lhhs_obs:
        m = PRINTED.search(o["source_table_or_section"] or "")
        if not m:
            continue
        key = (o["canonical_account_id"], o["amount_type"], o["component"] or "")
        facts[key]["labels"][clean(m.group(2))] += 1
        if o["source_document_id"] in HOUSE_TABLES:
            facts[key]["house"].append((HOUSE_TABLES.index(o["source_document_id"]), clean(m.group(2))))
    x, sections = load_extraction()

    def find(key, col):
        aid = key[0]
        ag = AGENCY_KEY[acct[aid]["agency"]]
        pool = rows_for(sections, col, ag)
        hits = [f for f in pool if f["_clean"] in facts[key]["labels"]]
        if not hits and ag != "TAIL":                 # printed outside its agency's section (e.g. ARPA-H under OS in FY2023)
            pool = rows_for(sections, col, "TAIL")
            hits = [f for f in pool if f["_clean"] in facts[key]["labels"]]
        if len({h["amount"] for h in hits}) > 1:
            # two labels for the fact match different rows (e.g. 'Formula Grants' and 'Total, LIHEAP, program level'):
            # take the label the House committee tables print it under, the most recent House table first
            for _, lab in sorted(facts[key]["house"]):
                pick = [h for h in hits if h["_clean"] == lab]
                if pick:
                    hits = pick
                    break
        if len({h["amount"] for h in hits}) > 1:
            # one label in several sub-sections (SAMHSA's 'Programs of Regional and National Significance' under
            # Mental Health, Treatment and Prevention): the row whose printed path names the account
            words = [w for w in re.findall(r"[a-z]+", acct[aid]["canonical_name"].lower()) if len(w) > 4]
            pick = [h for h in hits if all(w in h["account_path"].lower() for w in words[-1:])]
            if pick:
                hits = pick
        # still several rows with different amounts: ambiguous -- reported, not guessed
        return hits

    found, ambiguous, missing = {}, {}, []
    for key in sorted(facts):
        hits = find(key, COL)
        if len(hits) == 1:
            found[key] = hits[0]
        elif len(hits) > 1:
            # a total printed twice (e.g. a section subtotal repeated as the agency total): identical amounts are one row
            if len({h["amount"] for h in hits}) == 1:
                found[key] = hits[0]
            else:
                ambiguous[key] = hits
        else:
            missing.append(key)

    # 2. observations
    def next_id(prefix, rows, field, width):
        n = max(int(r[field].rsplit("-", 1)[1]) for r in rows if r[field].startswith(prefix))
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    obs_ids = next_id("OBS-LHHS-", obs_all, "observation_id", 4)
    val_ids = next_id("VAL-LHHS-", data["validations"], "validation_id", 5)
    new_obs, new_val = [], []
    by_key = {}
    for key in sorted(found, key=lambda k: (k[0], k[2] != "", k[1] != "budget authority", k[1], k[2])):
        aid, amount_type, component = key
        f = found[key]
        oid = next(obs_ids)
        o = {"observation_id": oid, "canonical_account_id": aid, "fiscal_year": 2024, "stage": "House Reported", "chamber": "House",
             "amount": f["amount"] or 0, "amount_type": amount_type, "component": component, "headline_observation_id": "",
             "offsetting_collections": "FALSE", "transfer_link_account_id": "", "source_document_id": SRC,
             "source_page": str(f["source_page"]),
             "source_table_or_section": (f"Title II, {acct[aid]['agency']} -- printed as {f['account_name_as_written'].strip()!r} "
                                         f"[{COL}]. Source: the House subcommittee draft's explanatory materials for H.R. 5894 "
                                         "(the full committee never reported a FY2024 Labor-HHS bill)."),
             "extraction_method": f.get("extraction_method") or "AI-extracted",
             "confidence": f.get("extraction_confidence") or 0.95, "verification_status": ""}
        by_key[key] = o
        new_obs.append(o)
        for r in f["_records"]:
            if r["rule_applied"] in ("table_total", "structural", "source_text", "unit", "semantic"):
                new_val.append({"validation_id": None, "observation_id": oid, "rule_applied": r["rule_applied"],
                                "expected_result": r["expected_result"], "observed_result": r["observed_result"], "result": r["result"]})
    # contained / view lines point at the account's headline in the same document
    for key, o in by_key.items():
        aid, amount_type, component = key
        if component and comps.get(component, {}).get("kind") in ("contained", "view"):
            head = by_key.get((aid, "budget authority", ""))
            if head:
                o["headline_observation_id"] = head["observation_id"]

    # 3. sum checks on the new observations. An agency total = its member accounts (approps_store.agency_members:
    #    same agency and title, not a total, no parent), each at the figure the printed total adds: Medicaid's
    #    'appropriated in this bill' (this year + next year's advance), otherwise the account's headline. A member
    #    this table does not print at all (a later year's proposal, e.g. the FY2027 NIH institutes) is left out and
    #    named. The Title II total = the agency totals + the department-wide lines (signed) - the CURES line, as
    #    every Labor-HHS title total reconciles (reference/review/lhhs/title_ii_totals.py).
    def amt(aid, component=""):
        o = by_key.get((aid, "budget authority", component))
        return o["amount"] if o else None
    printed = {k[0] for k in found}
    members = defaultdict(list)
    for aid, x_ in acct.items():
        if x_.get("total_scope") or x_.get("parent_account_id"):
            continue
        members[(x_["agency"], x_.get("title"))].append(aid)
    sum_lines = []

    def add_sum(tot_id, parts_vals, not_printed, what):
        got, want = sum(v for _, v in parts_vals), amt(tot_id)
        exp = (f"{what} = {got // 1000:,} (thousands): " + " + ".join(f"{p} {v // 1000:,}" for p, v in parts_vals)
               + (f"; not printed in this table, so not in the sum: {', '.join(not_printed)}" if not_printed else ""))
        res = "pass" if got == want else "flag"
        new_val.append({"validation_id": None, "observation_id": by_key[(tot_id, "budget authority", "")]["observation_id"],
                        "rule_applied": "table_total", "expected_result": exp,
                        "observed_result": f"{want // 1000:,} as printed ('{found[(tot_id, 'budget authority', '')]['account_name_as_written'].strip()}')",
                        "result": res})
        sum_lines.append((tot_id, len(parts_vals), got, want, not_printed, res))

    for tot_id, x_ in acct.items():
        if x_.get("total_scope") != "agency" or (tot_id, "budget authority", "") not in by_key:
            continue
        mem = members[(x_["agency"], x_.get("title"))]
        if not mem:
            continue                                  # an agency printed as one heading (AHRQ, ACL): nothing to add
        vals, lack = [], []
        for m in mem:
            v = amt(m, "appropriated_in_this_bill") if amt(m, "appropriated_in_this_bill") is not None else amt(m)
            if v is None:
                if m in printed:
                    vals.append((m, 0))
                lack.append(m)
            else:
                vals.append((m, v))
        missing_printed = [m for m in lack if m in printed]
        what = (f"the NIH institutes and other NIH accounts ({len(vals)})" if x_["agency"] == "National Institutes of Health"
                else f"the {len(vals)} {x_['agency']} accounts")
        add_sum(tot_id, vals, [m for m in lack if m not in printed], what)
        if missing_printed:
            new_val[-1]["result"] = "flag"
    title_id = next((aid for aid, x_ in acct.items() if x_.get("total_scope") == "title"), None)
    if title_id and (title_id, "budget authority", "") in by_key:
        parts = [(aid, amt(aid)) for aid, x_ in acct.items() if x_.get("total_scope") == "agency" and amt(aid) is not None]
        for aid, x_ in acct.items():
            if x_["agency"] == "Department of Health and Human Services" and not x_.get("total_scope"):
                for (k_aid, k_type, k_comp), o in by_key.items():
                    if k_aid == aid and not k_comp:
                        parts.append((aid, o["amount"]))
        cures = amt("ACC-HHS-NIH-CURES")
        if cures:
            parts.append(("minus ACC-HHS-NIH-CURES", -cures))
        add_sum(title_id, parts, [], "the agency totals + the department-wide lines - the CURES Act line")

    # 4. cross-document: this table's FY2023 Enacted and FY2024 Request columns against our figures (report),
    #    and this document's Bill column against no other document (none prints FY2024 House)
    ours = {(o["canonical_account_id"], o["fiscal_year"], o["stage"], o["amount_type"], o["component"] or ""): o for o in lhhs_obs}
    cross = []
    for col, fy, stage in (("FY 2023 Enacted", 2023, "Enacted"), ("FY 2024 Request", 2024, "President's Budget")):
        for key in sorted(facts):
            have = ours.get((key[0], fy, stage, key[1], key[2]))
            if not have:
                continue
            hits = find(key, col)
            vals = {h["amount"] or 0 for h in hits}
            if len(vals) != 1:
                cross.append([col, *key, have["observation_id"], have["amount"], "", "", "not printed" if not hits else "ambiguous"])
                continue
            h = hits[0]
            cross.append([col, *key, have["observation_id"], have["amount"], h["amount"] or 0, h["source_page"],
                          "agree" if (h["amount"] or 0) == have["amount"] else "differ"])

    # 5. statuses, IDs, pending review
    checks = defaultdict(list)
    for v in new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    for v in new_val:
        v["validation_id"] = next(val_ids)
        v["human_review_status"] = "pending" if v["result"] in ("fail", "flag") else ""
        v["reviewer"], v["resolution"] = "", ""
    order = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
             "human_review_status", "reviewer", "resolution"]
    new_val = [{k: v[k] for k in order} for v in new_val]

    # 6. the source document and the Bill Report Reference row
    doc = {"document_id": SRC, "source_agency": "House Committee on Appropriations", "url_or_identifier": URL,
           "document_type": "explanatory_statement", "congress_session": "118-1", "fiscal_year": 2024, "publication_date": "2023-11-02",
           "stage": "House Reported", "retrieval_timestamp": "2026-10-08 00:00:00", "source_page": PAGES,
           "also_covers": "FY2023 Enacted; FY2024 President's Budget",
           "notes": f"{DRAFT_NOTE}. sha256 {SHA256}. Page citations are PDF page numbers (the Title II comparative "
                    f"statement is PDF pp. {PAGES}, image-only, read by vision)."}
    brr = {"reference_id": BR, "subcommittee": "LHHS", "fiscal_year": 2024, "stage": "House Reported", "bill_id": "H.R.5894",
           "report_id": "JES_LHHS_H.R.5894", "bill_url": BILL_URL, "report_jes_url": URL, "lookup_key": "LHHS-2024-House Reported",
           "notes": "", "vehicle_bill_id": "", "division": "", "enactment_date": "", "funding_type": "", "draft": "TRUE"}

    # 6b. confirmed absences
    ca_ids = next_id("CA-LHHS-", data["confirmed_absences"], "confirmed_absence_id", 4)
    new_ca = []
    for (aid, amount_type), (label, rx) in ABSENT.items():
        if any(k[0] == aid for k in found):
            continue
        new_ca.append({"confirmed_absence_id": next(ca_ids), "canonical_account_id": aid, "fiscal_year": 2024, "stage": "House Reported",
                       "amount_type": amount_type, "component": "", "source_document_id": SRC,
                       "evidence": (f"The House subcommittee draft's explanatory materials for H.R. 5894: the Title II comparative "
                                    f"table, PDF pp. {PAGES}, and the bill-wide sections after it (pp. 312-313: the Disaster Relief "
                                    f"Supplemental Appropriations Act, 2023 and the grand total), searched in full for a {label!r} line "
                                    "in every column: none (the table pages are image-only, read by vision; pp. 312-313 read from the "
                                    f"page images). H.R. 5894 as introduced (govinfo BILLS-118hr5894ih, text searched for {rx}) "
                                    "provides none either."),
                       "confirmed_date": TODAY})

    # ---- report
    flagged = [o for o in new_obs if o["verification_status"] == "flagged"]
    print(f"facts on file: {len(facts)}; found in the Bill column: {len(found)}; ambiguous: {len(ambiguous)}; not printed: {len(missing)}")
    print(f"new observations {len(new_obs)} ({sum(o['verification_status'] == 'auto-validated' for o in new_obs)} auto-validated, "
          f"{sum(o['verification_status'] == 'unverified' for o in new_obs)} unverified, {len(flagged)} flagged); "
          f"validations {len(new_val)} ({sum(v['human_review_status'] == 'pending' for v in new_val)} pending)")
    for key, hits in ambiguous.items():
        print("  AMBIGUOUS", key, [(h["account_name_as_written"], h["amount"], h["source_page"]) for h in hits])
    accts_found = {k[0] for k in found}
    print("  accounts with no FY2024 House figure:", sorted(set(acct) - accts_found))
    for t in sum_lines:
        print("  SUM", t[0], f"{t[1]} accounts", f"{t[2] // 1000:,} vs {t[3] // 1000:,}", t[4], t[5])
    with open(HERE / "cross_document_fy2024_house.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["column", "canonical_account_id", "amount_type", "component", "our_observation_id", "our_amount",
                    "this_document_amount", "pdf_page", "result"])
        w.writerows(cross)
    for col in ("FY 2023 Enacted", "FY 2024 Request"):
        rs = [r for r in cross if r[0] == col]
        print(f"  cross-document {col}: {sum(r[-1] == 'agree' for r in rs)} agree / {sum(r[-1] == 'differ' for r in rs)} differ"
              f" / {sum(r[-1] in ('not printed', 'ambiguous') for r in rs)} not compared, of {len(rs)}")
    if a.write:
        assert not any(o["observation_id"] in {x["observation_id"] for x in obs_all} for o in new_obs)
        data["source_docs"].append(doc)
        data["bill_report_refs"].append(brr)
        data["observations"].extend(new_obs)
        data["validations"].extend(new_val)
        data["confirmed_absences"].extend(new_ca)
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("appended to", STAGED.relative_to(ROOT))
    print(f"confirmed absences {len(new_ca)}:", [(c['confirmed_absence_id'], c['canonical_account_id']) for c in new_ca])
    return {"obs": new_obs, "val": new_val, "ca": new_ca, "missing": missing, "ambiguous": ambiguous, "cross": cross, "flagged": flagged}


if __name__ == "__main__":
    main()
