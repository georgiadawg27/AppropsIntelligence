"""
The bill and law text check: the parser (legislative_text.py), the comparison and the CI gates
(scripts/check_legislative_text.py).

Offline, every test but the last class: they read only small inline fixtures and the committed files
(data/staged.json, data/legislative_text/). LiveTextReparse is a network test: it downloads each document
again from govinfo and checks the bytes' sha256 and the re-parse against the committed file. It's skipped
unless RUN_NETWORK_TESTS=1 (and GOVINFO_API_KEY) is set:
    RUN_NETWORK_TESTS=1 python -m pytest tests/test_legislative_text.py -m network -v
"""

import copy
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import legislative_text as L  # noqa: E402
import check_legislative_text as C  # noqa: E402

try:
    import pytest  # for the `network` marker; the file still runs under plain unittest
except ImportError:
    pytest = None


def amt(amount, before, after="", proviso=False):
    return {"amount": amount, "block": 1, "proviso": proviso, "phrase": f"{before} [${amount:,}] {after}"}


def para(*amounts):
    return {"paragraph_id": "P0001", "division": None, "title": "TITLE II", "heading_path": ["X"],
            "account_heading": "X", "heading_notes": [], "blocks": 1, "amounts": list(amounts),
            "location": {"position": 1}}


BILL_XML = b"""<?xml version="1.0"?>
<bill><legis-body>
<division><enum>H</enum><header>Labor-HHS</header>
<title><enum>II</enum><header>DEPARTMENT OF HEALTH AND HUMAN SERVICES</header>
<appropriations-major><header>Health Resources and Services Administration</header></appropriations-major>
<appropriations-intermediate><header>primary health care</header>
<text>For carrying out titles II and III of the PHS Act, $1,858,772,000, of which $1,000,000 shall be for
transfer: <italic>Provided</italic>, That no more than $100,000 shall be available for X.</text>
</appropriations-intermediate>
<appropriations-intermediate><header>(including transfer of funds)</header>
<text>For more, $5,000.</text></appropriations-intermediate>
<section><enum>243.</enum><text>In addition to amounts otherwise appropriated by this Act under the heading
<quote>Office of the Secretary&#8212;General Departmental Management</quote> there are hereby appropriated an additional
$350,000,000, to remain available until expended.</text></section>
<section><enum>244.</enum><text>None of the funds may be used for X.</text></section>
</title></division>
<division><enum>A</enum><header>Defense</header>
<title><enum>I</enum><header>MILITARY PERSONNEL</header>
<appropriations-intermediate><header>Military Personnel, Army</header><text>For pay, $9,000,000.</text>
</appropriations-intermediate></title></division>
</legis-body></bill>"""

USLM = b"""<?xml version="1.0"?>
<pLaw xmlns="http://schemas.gpo.gov/xml/uslm"><main>
<division identifier="/divD"><num value="D">DIVISION D</num>
<title><num value="II">TITLE II</num><heading>DEPARTMENT OF HEALTH AND HUMAN SERVICES</heading>
<appropriations level="major"><heading>Centers for Disease Control and Prevention</heading>
<appropriations level="intermediate"><heading>Buildings and facilities</heading>
<page>138 STAT. 640</page>
<content><p>For acquisition of real property, $40,000,000, to remain available until September 30, 2028.</p></content></appropriations></appropriations>
</title></division></main></pLaw>"""

# a paragraph with an enumerated list inside it: the text after the list is still the same paragraph
LIST_XML = b"""<?xml version="1.0"?>
<bill><legis-body>
<title><enum>II</enum><header>DEPARTMENT OF HEALTH AND HUMAN SERVICES</header>
<appropriations-major><header>Administration for Children and Families</header></appropriations-major>
<appropriations-intermediate><header>children and families services programs</header>
<text>For carrying out the Head Start Act, $12,000,000,000, of which:</text>
<paragraph><enum>(1)</enum><text>$275,000,000 shall be available for a cost of living adjustment;</text></paragraph>
<paragraph><enum>(2)</enum><text>$8,000,000 shall be available for X:</text></paragraph>
<continuation-text><proviso><italic>Provided further,</italic></proviso> That $40,011,000 shall be used for the
projects specified in the table titled <quote>Congressionally Directed Spending</quote>.</continuation-text>
</appropriations-intermediate>
<appropriations-intermediate><header>promoting safe and stable families</header>
<text>For carrying out section 436, $345,000,000.</text></appropriations-intermediate>
</title>
</legis-body></bill>"""

USLM_LIST = b"""<?xml version="1.0"?>
<pLaw xmlns="http://schemas.gpo.gov/xml/uslm"><main>
<title><num value="II">TITLE II</num><heading>DEPARTMENT OF HEALTH AND HUMAN SERVICES</heading>
<appropriations level="intermediate"><heading>Children and families services programs</heading>
<content><p>For carrying out the Head Start Act, $12,000,000,000, of which:</p>
<paragraph><num value="1">(1)</num><content>$275,000,000 shall be available for a cost of living adjustment;</content></paragraph>
<continuation>Provided further, That $40,011,000 shall be used for projects.</continuation></content></appropriations>
</title></main></pLaw>"""

PDF_PAGES = [
    "SEN. APPRO.\nTITLE II\nDEPARTMENT OF HEALTH AND HUMAN SERVICES\nNATIONAL INSTITUTES OF HEALTH\n"
    "NATIONAL CANCER INSTITUTE\nFor carrying out section 301 and title IV of the PHS Act with respect to\n"
    "cancer, $6,559,912,000, of which $50,000,000 shall remain available until expended.\n"
]


class Parser(unittest.TestCase):
    def test_bill_xml_paragraphs(self):
        paras = L.parse("bill_xml", BILL_XML, ["H"])
        self.assertEqual({p["division"] for p in paras}, {"H"}, "only the named division")
        p = next(p for p in paras if p["account_heading"].startswith("PRIMARY HEALTH CARE"))
        self.assertEqual(p["heading_path"][0], "HEALTH RESOURCES AND SERVICES ADMINISTRATION")
        self.assertEqual([a["amount"] for a in p["amounts"]], [1858772000, 1000000, 100000, 5000])
        self.assertEqual([a["proviso"] for a in p["amounts"]], [False, False, True, False])
        self.assertIn("[$1,858,772,000]", p["amounts"][0]["phrase"])
        self.assertTrue(p["title"].startswith("TITLE II"))
        # a "(including transfer of funds)" heading and its text belong to the heading before it, not a new account
        self.assertNotIn("(INCLUDING TRANSFER OF FUNDS)", [q["account_heading"] for q in paras])

    def test_general_provision_appropriating_an_additional_amount(self):
        paras = L.parse("bill_xml", BILL_XML, ["H"])
        gp = [p for p in paras if p["heading_path"][:1] == [L.GP]]
        self.assertEqual(len(gp), 1, "only the section that appropriates an additional amount")
        self.assertEqual(gp[0]["account_heading"], "SEC. 243")
        self.assertEqual(gp[0]["heading_notes"], ["under the heading: Office of the Secretary\u2014General Departmental Management"])
        self.assertEqual([a["amount"] for a in gp[0]["amounts"]], [350000000])
        acc = {"agency": "Department of Health and Human Services", "bureau": "Office of the Secretary"}
        self.assertEqual([x for x, _ in C.provision_amounts(acc, {"general departmental management"}, paras)], [350000000])
        self.assertEqual(C.provision_amounts(acc, {"buildings and facilities"}, paras), [])

    def test_uslm_paragraph_and_stat_page(self):
        paras = L.parse("uslm", USLM, ["D"])
        self.assertEqual(len(paras), 1)
        p = paras[0]
        self.assertEqual(p["account_heading"], "BUILDINGS AND FACILITIES")
        self.assertEqual(p["heading_path"], ["CENTERS FOR DISEASE CONTROL AND PREVENTION", "BUILDINGS AND FACILITIES"])
        self.assertEqual([a["amount"] for a in p["amounts"]], [40000000])
        self.assertIn("138 STAT. 640", json.dumps(p["location"]))

    def test_paragraph_runs_past_an_enumerated_list(self):
        paras = L.parse("bill_xml", LIST_XML, [])
        p = next(p for p in paras if p["account_heading"].startswith("CHILDREN AND FAMILIES"))
        self.assertEqual([a["amount"] for a in p["amounts"]], [12000000000, 275000000, 8000000, 40011000])
        self.assertIn("Congressionally Directed Spending", p["amounts"][-1]["phrase"])
        q = next(p for p in paras if p["account_heading"].startswith("PROMOTING SAFE"))
        self.assertEqual([a["amount"] for a in q["amounts"]], [345000000], "the next heading starts a new paragraph")

    def test_uslm_paragraph_runs_past_an_enumerated_list(self):
        paras = L.parse("uslm", USLM_LIST, [])
        self.assertEqual(len(paras), 1)
        self.assertEqual([a["amount"] for a in paras[0]["amounts"]], [12000000000, 275000000, 40011000])

    def test_pdf_text_layer(self):
        paras = L.parse_pdf_text(PDF_PAGES)
        p = next(p for p in paras if p["amounts"])
        self.assertEqual(p["account_heading"], "NATIONAL CANCER INSTITUTE")
        self.assertEqual([a["amount"] for a in p["amounts"]], [6559912000, 50000000])
        self.assertEqual(p["location"]["pdf_page"], 1)

    def test_deterministic(self):
        self.assertEqual(L.parse("bill_xml", BILL_XML), L.parse("bill_xml", BILL_XML))

    def test_version_codes(self):
        self.assertEqual(L.version_code("BILLS-119hr5342rh"), "rh")
        self.assertEqual(L.version_code("BILLS-115s1771pcs"), "pcs")
        self.assertEqual(L.version_code("PLAW-117publ328"), "publ")
        self.assertEqual(L.version_code(None), "committee draft")
        self.assertEqual(L.package_of("https://www.govinfo.gov/content/pkg/BILLS-118hr5894rh/pdf/x.pdf"),
                         "BILLS-118hr5894rh")


class Comparison(unittest.TestCase):
    def obs(self, amount):
        return {"amount": amount}

    def test_headline_is_the_main_amount_not_of_which(self):
        p = para(amt(500, "For expenses,", ", of which"), amt(20, "of which", "shall be for transfer to X"))
        self.assertEqual(C.compare(self.obs(500), p, [])[0], "match")
        r, text, _ = C.compare(self.obs(480), p, [])
        self.assertNotEqual(r, "match")

    def test_mismatch_reports_the_text_figure(self):
        p = para(amt(500, "For expenses,", ", to remain available until expended."))
        r, text, _ = C.compare(self.obs(999), p, [])
        self.assertEqual((r, text), ("mismatch", 500))

    def test_no_amount_is_no_paragraph(self):
        self.assertEqual(C.compare(self.obs(5), para(), [])[0], "no paragraph")

    def test_advance_and_section_241_derivations(self):
        p = para(amt(1000, "For grants,", ", of which"),
                 amt(100, "of which", "shall be from funds available under section 241 of the PHS Act"),
                 amt(300, "For making payments for the first quarter of fiscal year 2025,", "."))
        self.assertFalse(C.is_main_candidate(p["amounts"][2]))
        self.assertEqual(C.compare(self.obs(900), p, [])[0], "match")     # less the 241 tap
        self.assertEqual(C.compare(self.obs(1300), p, [])[0], "match")    # plus the advance

    def test_recorded_part_component(self):
        p = para(amt(1000, "For research,", ", to remain available"))
        cell = [("OBS-X", "defense", "part", 50)]
        res, _, how = C.compare(self.obs(950), p, [], cell)
        self.assertEqual(res, "match")
        self.assertIn("derived as recorded", how)

    def test_main_less_or_plus_other_amounts_is_ambiguous(self):
        # another scope: the main amount less a transfer and plus an amount the paragraph also appropriates
        p = para(amt(1000, "For research,", ", of which"), amt(300, "of which", "shall be derived by transfer from X"),
                 amt(12, "In addition,", "is appropriated", proviso=True))
        res, _, how = C.compare(self.obs(712), p, [])
        self.assertEqual(res, "ambiguous")
        self.assertIn("- $300 + $12", how)

    def test_mismatch_reports_the_residual_after_the_advance(self):
        p = para(amt(7012, "For carrying out title IV-E,", "."),
                 amt(3000, "For carrying out title IV-E, for the first quarter of fiscal year 2022,", "."))
        res, text, how = C.compare(self.obs(10011), p, [])
        self.assertEqual((res, text), ("mismatch", 7012))
        self.assertIn("next year's advance", how)
        self.assertIn("ours - that = -1", how)

    def test_emergency_and_recorded_part_together(self):
        p = para(amt(7608, "For research;", ", to remain available"),
                 amt(420, "Provided, That of the amounts made available under this heading,",
                     "is designated by the Congress as being for an emergency requirement", proviso=True))
        self.assertEqual(C.compare(self.obs(7086), p, [], [("OBS-X", "defense", "part", 102)])[0], "match")

    def test_ours_printed_but_not_main_is_ambiguous(self):
        p = para(amt(1000, "For research,", ", of which"), amt(77, "of which", "shall be for the Office of X"))
        self.assertEqual(C.compare(self.obs(77), p, [])[0], "ambiguous")


class CommittedData(unittest.TestCase):
    """The committed data: every required document has text or an exemption, every file is current, and the
    results written into data/staged.json are this check's results."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(L.STAGED.read_text())
        cls.texts = C.load_texts(L.TEXT_DIR)
        cls.exempt = C.load_exempt(L.TEXT_DIR)

    def test_coverage_is_clean(self):
        self.assertEqual(C.coverage(self.data, self.texts, self.exempt), [])

    def test_ci_passes(self):
        self.assertEqual(C.main(["--ci"]), 0)

    def test_every_exemption_has_a_reason(self):
        for e in self.exempt.values():
            self.assertTrue(e.get("reason", "").strip(), e["id"])

    def test_missing_text_fails(self):
        doc = sorted(self.texts)[0]
        texts = {k: v for k, v in self.texts.items() if k != doc}
        self.assertTrue(any(p.startswith(doc) and "no text file" in p
                            for p in C.coverage(self.data, texts, self.exempt)))

    def test_stale_or_reasonless_exemption_fails(self):
        exempt = {**self.exempt, "SRC-NOT-A-DOC": {"id": "SRC-NOT-A-DOC", "reason": "x"}}
        self.assertTrue(any("names no document" in p for p in C.coverage(self.data, self.texts, exempt)))
        eid = next(iter(self.exempt))
        exempt = {**self.exempt, eid: {"id": eid, "reason": " "}}
        self.assertTrue(any(f"exemption {eid}: no reason" in p for p in C.coverage(self.data, self.texts, exempt)))

    def test_other_parser_version_fails(self):
        doc = sorted(self.texts)[0]
        texts = {**self.texts, doc: {**self.texts[doc], "parser_version": "0000000000000000"}}
        self.assertTrue(any(p.startswith(doc) and "parser" in p for p in C.coverage(self.data, texts, self.exempt)))

    def test_no_key_in_stored_urls(self):
        for doc_id, t in self.texts.items():
            self.assertNotIn("api_key", json.dumps(t["retrieved_from"]) + t["source_url"], doc_id)

    def test_check_never_changes_a_figure(self):
        data = copy.deepcopy(self.data)
        before = [(o["observation_id"], o["amount"]) for o in data["observations"]]
        C.merged(data, C.check(data, self.texts, self.exempt))
        self.assertEqual([(o["observation_id"], o["amount"]) for o in data["observations"]], before)

    def test_records_follow_the_rule(self):
        vals = [v for v in self.data["validations"] if v["validation_id"].startswith(C.PREFIX)]
        self.assertTrue(vals)
        obs = {o["observation_id"]: o for o in self.data["observations"]}
        for v in vals:
            o = obs[v["observation_id"]]
            self.assertEqual(v["rule_applied"], L.RULE_BY_STAGE[o["stage"]], v["validation_id"])
            if v["result"] in ("fail", "flag"):
                self.assertNotEqual(v["human_review_status"], "", v["validation_id"])


def _network_test(cls):
    if pytest:
        cls = pytest.mark.network(cls)
    cls = unittest.skipUnless(os.environ.get("GOVINFO_API_KEY") or (ROOT / ".env").exists(),
                              "GOVINFO_API_KEY not set -- live govinfo test")(cls)
    return unittest.skipUnless(os.environ.get("RUN_NETWORK_TESTS") == "1",
                               "network test -- set RUN_NETWORK_TESTS=1 to run it")(cls)


@_network_test
class LiveTextReparse(unittest.TestCase):
    """Each committed text file is what its saved download gives: the same bytes (sha256) and the same parse."""

    def test_download_and_reparse(self):
        import fetch_legislative_text as F
        key = F.api_key()
        reqs, _ = L.required_documents(json.loads(L.STAGED.read_text()))
        for doc_id, t in sorted(C.load_texts(L.TEXT_DIR).items()):
            with self.subTest(doc_id):
                rf = t["retrieved_from"]
                host = rf["download"].split("/")[2]
                raw = F.get(rf["download"], "test", key if host == "api.govinfo.gov" else None)
                self.assertIsNotNone(raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), t["sha256"])
                self.assertEqual(L.parse(rf["format"], raw, F.divisions(reqs[doc_id])), t["paragraphs"])


if __name__ == "__main__":
    unittest.main()
