"""
The synthetic subcommittee SYNLB (tests/test_any_subcommittee.py): two accounts, one fiscal year, every stage, with
bill and law text. Invented names and amounts -- no real figure. build(root) lays out a data root the pipeline reads
with APPROPS_DATA_ROOT=root (paths.py): data/staged/, data/legislative_text/ (parsed by the repository's own
parser, legislative_text.parse, from the bill XML below) and an empty reference/review/accounting/.

The House bill carries only the House's account and the Senate bill only the Senate's (Legislative Branch's custom;
rules/SYNLB.py). The House bill's paragraph also prints one phrase no rule reads ('derived from the Synthetic
Member Gift Fund'): it must land in review.
"""

import hashlib
import json
from pathlib import Path

import legislative_text as L
import staged_data

HERE = Path(__file__).resolve().parent
RULES = HERE / "rules"
CODE = "SYNLB"
FY = 2031
HOST = "https://example.invalid/synthetic"
MEMBER, SENATOR = "ACC-SYNLB-MEMBER-OPS", "ACC-SYNLB-SENATOR-OPS"


def bill_xml(title_text, paras):
    """paras: [(agency heading, account heading, paragraph text)]"""
    body = "".join(f"<appropriations-major><header>{ag}</header></appropriations-major>"
                   f"<appropriations-small><header>{acc}</header><text>{t}</text></appropriations-small>"
                   for ag, acc, t in paras)
    return (f'<?xml version="1.0"?><bill><legis-body><title><enum>I</enum><header>{title_text}</header>'
            f"{body}</title></legis-body></bill>").encode()


EMERGENCY = (", of which $5,000,000 is designated by the Congress as being for an emergency requirement pursuant to "
             "section 251(b)(2)(A)(i) of the Balanced Budget and Emergency Deficit Control Act of 1985")
DOCS = {
    # document_id: (stage, package, the bill XML)
    "SRC-BILLS-121HR9999RH": ("House Reported", "BILLS-121hr9999rh", bill_xml("SYNTHETIC LEGISLATIVE BRANCH", [
        ("SYNTHETIC HOUSE OFFICE", "MEMBER OPERATIONS",
         f"For member operations, $100,000,000{EMERGENCY}: Provided, That $2,000,000 shall be derived from the "
         "Synthetic Member Gift Fund for visitor programs.")])),
    "SRC-BILLS-121S9998RS": ("Senate Reported", "BILLS-121s9998rs", bill_xml("SYNTHETIC LEGISLATIVE BRANCH", [
        ("SYNTHETIC SENATE OFFICE", "SENATOR OPERATIONS", "For senator operations, $80,000,000.")])),
    "SRC-PLAW-121PUBL999": ("Enacted", "PLAW-121publ999", bill_xml("SYNTHETIC LEGISLATIVE BRANCH", [
        ("SYNTHETIC HOUSE OFFICE", "MEMBER OPERATIONS", f"For member operations, $101,000,000{EMERGENCY}."),
        ("SYNTHETIC SENATE OFFICE", "SENATOR OPERATIONS", "For senator operations, $81,000,000.")])),
}
# (account, stage, amount, source document); the House bill has no Senate figure and the Senate bill no House one
FIGURES = [
    (MEMBER, "President's Budget", 95000000, "SRC-BUDGET-APP-FY2031-SYN"),
    (SENATOR, "President's Budget", 78000000, "SRC-BUDGET-APP-FY2031-SYN"),
    (MEMBER, "House Reported", 100000000, "SRC-BILLS-121HR9999RH"),
    (SENATOR, "Senate Reported", 80000000, "SRC-BILLS-121S9998RS"),
    (MEMBER, "Enacted", 101000000, "SRC-PLAW-121PUBL999"),
    (SENATOR, "Enacted", 81000000, "SRC-PLAW-121PUBL999"),
]
CHAMBER = {"President's Budget": "N/A", "House Reported": "House", "Senate Reported": "Senate", "Enacted": "N/A"}
REF = {"President's Budget": "PB", "House Reported": "HOUSE", "Senate Reported": "SENATE", "Enacted": "ENACTED"}


def account(aid, name, agency, order):
    return {"canonical_account_id": aid, "canonical_name": name, "agency": agency, "bureau": "",
            "treasury_account_symbol": "", "status": "active", "historical_names": "", "historical_identifiers": "",
            "fund_type": "general", "subcommittee": CODE, "notes": "synthetic test account (no real figures)",
            "title": "Title I", "display_order": order, "total_scope": "", "parent_account_id": ""}


def source_doc(doc_id, kind, stage, url, notes):
    return {"document_id": doc_id, "source_agency": "synthetic", "url_or_identifier": url, "document_type": kind,
            "congress_session": "121-1", "fiscal_year": FY, "publication_date": "2030-07-01", "stage": stage,
            "retrieval_timestamp": "", "source_page": "", "also_covers": "", "notes": notes}


def staged(vocabulary):
    docs = [source_doc("SRC-BUDGET-APP-FY2031-SYN", "budget_appendix", "President's Budget",
                       f"{HOST}/BUDGET-2031-APP.pdf", "synthetic Budget Appendix")]
    refs = [{"reference_id": f"BR-{CODE}-FY{FY}-PB", "subcommittee": CODE, "fiscal_year": FY,
             "stage": "President's Budget", "bill_id": "PREX 2.8:2031/APP", "report_id": "N/A",
             "bill_url": f"{HOST}/BUDGET-2031-APP.pdf", "report_jes_url": "N/A",
             "lookup_key": f"{CODE}-{FY}-President's Budget", "notes": "", "vehicle_bill_id": "", "division": "",
             "enactment_date": "", "funding_type": "", "draft": "FALSE"}]
    for doc_id, (stage, pkg, _) in DOCS.items():
        url = f"{HOST}/{pkg}.pdf"
        docs.append(source_doc(doc_id, "public_law" if stage == "Enacted" else "bill", stage, url,
                               f"synthetic {stage} text"))
        enacted = stage == "Enacted"
        refs.append({"reference_id": f"BR-{CODE}-FY{FY}-{REF[stage]}", "subcommittee": CODE, "fiscal_year": FY,
                     "stage": stage, "bill_id": {"House Reported": "H.R.9999", "Senate Reported": "S.9998",
                                                 "Enacted": "P.L.121-999"}[stage],
                     "report_id": "N/A", "bill_url": url, "report_jes_url": "N/A",
                     "lookup_key": f"{CODE}-{FY}-{stage}", "notes": "", "vehicle_bill_id": "", "division": "",
                     "enactment_date": "2030-10-01" if enacted else "",
                     "funding_type": "standalone" if enacted else "", "draft": "FALSE"})
    obs = []
    for i, (aid, stage, amount, doc) in enumerate(FIGURES, 1):
        obs.append({"observation_id": f"OBS-{CODE}-{i:04d}", "canonical_account_id": aid, "fiscal_year": FY,
                    "stage": stage, "chamber": CHAMBER[stage], "amount": amount, "amount_type": "budget authority",
                    "component": "", "headline_observation_id": "", "offsetting_collections": "FALSE",
                    "transfer_link_account_id": "", "source_document_id": doc, "source_page": "1",
                    "source_table_or_section": "synthetic paragraph", "extraction_method": "human-entered",
                    "confidence": 1.0, "verification_status": "unverified"})
    return {"accounts": [account(MEMBER, "Member Operations", "Synthetic House Office", 1),
                         account(SENATOR, "Senator Operations", "Synthetic Senate Office", 2)],
            "observations": obs, "source_docs": docs, "validations": [], "relationships": [],
            "bill_report_refs": refs, "historical_names_tab": [], "confirmed_absences": [],
            "components": vocabulary, "observation_components": [], "observation_components_retired": [],
            "account_attributes": [], "account_attribute_notes": [], "account_attributes_retired": []}


# the President's Budget: each account's proposed language in the synthetic Appendix, as
# reference/review/accounting/request_language.py records it (status read)
REQUEST = {MEMBER: ("SYNTHETIC HOUSE OFFICE", "MEMBER OPERATIONS", "For member operations, $95,000,000."),
           SENATOR: ("SYNTHETIC SENATE OFFICE", "SENATOR OPERATIONS", "For senator operations, $78,000,000.")}


def request_language(data):
    heads = {o["canonical_account_id"]: o["observation_id"] for o in data["observations"]
             if o["stage"] == "President's Budget"}
    out = []
    for aid, (ag, acc, text) in REQUEST.items():
        p = L.parse("bill_xml", bill_xml("SYNTHETIC APPENDIX", [(ag, acc, text)]))[0]
        p.update(location={"pages": "p.1"}, text=[text], language="request")
        out.append({"account": aid, "subcommittee": CODE, "fiscal_year": FY, "headline_observation_id": heads[aid],
                    "status": "read", "document_id": "SRC-BUDGET-APP-FY2031-SYN", "paragraph": p})
    return {"note": "synthetic (tests/fixtures/any_subcommittee)", "page_images": [], "documents": [],
            "accounts": out}


def build(root, vocabulary, request=True):
    """Lay out the synthetic data root under root; vocabulary: the Component vocabulary (the repository's);
    request False: no request language (the President's Budget stage then stops on a missing input)."""
    root = Path(root)
    (root / "data" / "legislative_text").mkdir(parents=True, exist_ok=True)
    (root / "reference" / "review" / "accounting").mkdir(parents=True, exist_ok=True)
    data = staged(vocabulary)
    staged_data.save(data, root)
    if request:
        (root / "reference" / "review" / "accounting" / "request_language.json").write_text(
            json.dumps(request_language(data), indent=1))
    (root / "data" / "legislative_text" / "exemptions.json").write_text(json.dumps({"exemptions": []}))
    for doc_id, (stage, pkg, raw) in DOCS.items():
        paras = L.parse("bill_xml", raw)
        text = {"document_id": doc_id, "source_url": f"{HOST}/{pkg}.pdf",
                "retrieved_from": {"package": pkg, "format": "bill_xml", "download": f"{HOST}/{pkg}.xml"},
                "version_code": L.version_code(pkg), "version": "synthetic", "stage": stage, "fiscal_year": FY,
                "subcommittees": [CODE], "references": [f"BR-{CODE}-FY{FY}-{REF[stage]}"], "divisions": None,
                "retrieved": "2030-07-01", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "parser_version": L.parser_version(), "paragraph_count": len(paras),
                "amount_count": sum(len(p["amounts"]) for p in paras), "paragraphs": paras}
        (root / "data" / "legislative_text" / f"{doc_id}.json").write_text(L.dump(text))
    return root
