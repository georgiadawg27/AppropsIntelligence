"""
Two FY2024 House Labor-HHS additions from H.R. 5894's bill text (as introduced). Appends to
data/staged.json; never changes an existing row or ID.

    python reference/review/lhhs/fy2024_house/bill_text_rows.py           # what would be added
    python reference/review/lhhs/fy2024_house/bill_text_rows.py --write   # append

1. SRC-BILLS-118HR5894IH: H.R. 5894 as introduced, govinfo BILLS-118hr5894ih (the content PDF
   whose sha256 equals the stored file's, as public_links.py verifies every link).
2. The Nonrecurring Expenses Fund rescission, FY2024 House: sec. 236 rescinds $1,000,000,000
   (PDF p.118). The explanatory materials' table prints no NEF line, so the bill text is the
   one source: verification_status by the standard rule (unverified -- nothing confirms it).
3. CDC Global Health, FY2024 House (OBS-LHHS-1757, 370,722 thousand, kept): a cross_document
   record against the bill text's $370,772,000 -- a flag, pending review. The page shows it as a
   note in the cell's corner (approps_store: a pending cross_document flag against bill text).
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
PDF = ROOT / "document_store" / "BILLS-118hr5894ih.pdf"
SHA256 = "67108f5ba780b2e43443bb6bd6d40406c8b668cfc9e7b83d237f5419254c28f1"
SRC = "SRC-BILLS-118HR5894IH"
URL = "https://www.govinfo.gov/content/pkg/BILLS-118hr5894ih/pdf/BILLS-118hr5894ih.pdf"
TODAY = "2026-10-08"
NEF_PAGE = "118"
GLOBAL_HEALTH = "OBS-LHHS-1757"


def bill_text():
    """PDF page -> its text as one line: the bill's line numbers dropped, hyphenated breaks joined."""
    import fitz
    doc = fitz.open(PDF)
    out = {}
    for i in range(doc.page_count):
        lines = [ln.strip() for ln in doc[i].get_text().splitlines() if not re.fullmatch(r"\s*\d{1,2}\s*", ln)]
        out[i + 1] = re.sub(r"\s+", " ", re.sub(r"-\s+(?=[a-z])", "", " ".join(lines)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if PDF.exists():
        assert hashlib.sha256(PDF.read_bytes()).hexdigest() == SHA256, "not the stored H.R. 5894"
        pages = bill_text()
        p118 = pages[int(NEF_PAGE)]
        assert "SEC. 236." in p118 and "$1,000,000,000 are hereby rescinded" in p118, "sec. 236 not on p.118"
        gh = next(t for t in pages.values() if "GLOBAL HEALTH" in t)
        assert "$370,772,000" in gh, "Global Health's amount not in the bill text"
    data = json.loads(STAGED.read_text())
    have_ids = {o["observation_id"] for o in data["observations"]}
    if any(d["document_id"] == SRC for d in data["source_docs"]):
        print("already added")
        return
    nobs = max(int(o["observation_id"].rsplit("-", 1)[1]) for o in data["observations"] if o["observation_id"].startswith("OBS-LHHS-"))
    nval = max(int(v["validation_id"].rsplit("-", 1)[1]) for v in data["validations"] if v["validation_id"].startswith("VAL-LHHS-"))
    oid = f"OBS-LHHS-{nobs + 1:04d}"
    doc = {"document_id": SRC, "source_agency": "U.S. House of Representatives (bill; govinfo BILLS collection)", "url_or_identifier": URL,
           "document_type": "bill", "congress_session": "118-1", "fiscal_year": 2024, "publication_date": "2023-10-25",
           "stage": "House Reported", "retrieval_timestamp": "2026-09-27 22:13:13", "source_page": NEF_PAGE, "also_covers": "",
           "notes": ("H.R. 5894 as introduced (BILLS-118hr5894ih): the FY2024 Labor-HHS bill the House subcommittee draft "
                     "accompanies; the full committee never reported it. Same dollar amounts as the July 14, 2023 subcommittee "
                     f"mark. sha256 {SHA256}. Page citations are PDF page numbers.")}
    obs = {"observation_id": oid, "canonical_account_id": "ACC-HHS-GP-NEF-RESCISSION", "fiscal_year": 2024, "stage": "House Reported",
           "chamber": "House", "amount": -1_000_000_000, "amount_type": "rescission", "component": "", "headline_observation_id": "",
           "offsetting_collections": "FALSE", "transfer_link_account_id": "", "source_document_id": SRC, "source_page": NEF_PAGE,
           "source_table_or_section": ("Title II, General Provisions, sec. 236 (RESCISSION) -- printed as 'Of the unobligated "
                                       "balances in the \"Nonrecurring Expenses Fund\" established in section 223 of division G of "
                                       "Public Law 110-161, $1,000,000,000 are hereby rescinded' [bill text, as introduced]. The "
                                       "explanatory materials' comparative table prints no Nonrecurring Expenses Fund line."),
           "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": ""}
    vals = [
        {"observation_id": oid, "rule_applied": "source_text",
         "expected_result": "amount '$1,000,000,000' and 'Nonrecurring Expenses Fund' in sec. 236 of the bill text",
         "observed_result": f"PDF p.{NEF_PAGE}: 'SEC. 236. Of the unobligated balances in the \"Nonrecurring Expenses Fund\" ... "
                            "$1,000,000,000 are hereby rescinded not later than September 30, 2024'", "result": "pass"},
        {"observation_id": oid, "rule_applied": "unit", "expected_result": "dollars as printed in the bill text ($1,000,000,000)",
         "observed_result": "-1,000,000,000 dollars (a rescission, signed)", "result": "pass"},
        {"observation_id": GLOBAL_HEALTH, "rule_applied": "cross_document",
         "expected_result": "H.R. 5894 bill text prints $370,772,000",
         "observed_result": "the explanatory materials' table (and its change column) print 370,722 thousand", "result": "flag"},
    ]
    for i, v in enumerate(vals, start=1):
        v.update({"validation_id": f"VAL-LHHS-{nval + i:05d}", "human_review_status": "pending" if v["result"] in ("fail", "flag") else "",
                  "reviewer": "", "resolution": ""})
    order = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
             "human_review_status", "reviewer", "resolution"]
    vals = [{k: v[k] for k in order} for v in vals]
    checks = lambda o_id: [(v["rule_applied"], v["result"], v["expected_result"])
                           for v in data["validations"] + vals if v["observation_id"] == o_id]
    obs["verification_status"] = V.verification_status(obs["confidence"], checks(oid))
    gh = next(o for o in data["observations"] if o["observation_id"] == GLOBAL_HEALTH)
    gh_status = V.verification_status(gh["confidence"], checks(GLOBAL_HEALTH))
    print(f"{SRC}; {oid} NEF rescission -1,000,000,000 ({obs['verification_status']}); "
          f"{', '.join(v['validation_id'] for v in vals)}; {GLOBAL_HEALTH}: {gh['verification_status']} -> {gh_status}")
    if a.write:
        assert oid not in have_ids
        data["source_docs"].append(doc)
        data["observations"].append(obs)
        data["validations"].extend(vals)
        # the standard rule: a pending cross_document flag makes the figure flagged
        gh["verification_status"] = gh_status
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("appended to", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
