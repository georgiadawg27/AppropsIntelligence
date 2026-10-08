"""
The site reads data/staged.json: the workbook is built from it (approps_store.reference_workbook),
the export publishes that build as docs/Approps_Pilot_Schema_Loaded.xlsx, and nothing reads a
committed workbook. These tests hold the switch to what the site showed before it.
"""

import json
import tempfile
import unittest
from pathlib import Path

import approps_store as S
import export_static as E

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# grid-state counts and flagged cells, measured on the export from the committed v37 workbook
# just before the switch (the grid's headline cell is flagged when its observation's
# verification_status is "flagged"); then the FY2024 House Labor-HHS draft: its 100 cells moved
# from not yet collected to figures and states, 4 of them flagged; then H.R. 5894: the NEF rescission moved from
# missing to a figure, and CDC Global Health became flagged (its bill-text disagreement); then the owner's
# triage (PR #29): resolved records no longer count, 78 flagged cells -> 5 (71 unverified, 2 auto-validated)
STATE_COUNTS = {
    "LHHS": {"value": 1539, "not_funded": 108, "no_printed_total": 13, "missing": 140, "not_collected": 100,
             "not_enacted": 100},
    "CJS": {"value": 752, "not_funded": 18, "no_printed_total": 0, "missing": 460, "not_collected": 60,
            "not_enacted": 30},
}
FLAGGED_CELLS = {"LHHS": 5, "CJS": 0}


def grid(sc):
    return json.loads((DOCS / "data" / "subcommittees" / f"{sc}.json").read_text())


def headline_observations(g):
    """The observation each grid cell shows (the page's headOf: the budget authority line with
    no component, else the first line), over every row, title total and bill total."""
    rows = g["rows"] + [t["total"] for t in g.get("titles", []) if t.get("total")] + \
        ([g["bill_total"]] if g.get("bill_total") else [])
    for row in rows:
        for key, cell in row["cells"].items():
            if not cell["lines"]:
                continue
            h = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]),
                     cell["lines"][0])
            if h["observations"]:
                yield row, key, h["observations"][0]


class SiteReadsData(unittest.TestCase):
    def test_no_committed_workbook(self):
        self.assertEqual(list((ROOT / "reference").glob("*.xlsx")), [])
        self.assertEqual(S.reference_workbook(), S.BUILT_WORKBOOK)

    def test_index_names_the_data(self):
        src = json.loads((DOCS / "data" / "index.json").read_text())["source"]
        self.assertEqual(src["data"], "data/staged.json")
        self.assertEqual(src["version"], "v38")
        self.assertEqual(src["download"], "Approps_Pilot_Schema_Loaded.xlsx")
        import hashlib
        self.assertEqual(src["data_sha256"], hashlib.sha256(S.STAGED.read_bytes()).hexdigest())

    def test_grid_state_counts_unchanged(self):
        for sc, want in STATE_COUNTS.items():
            self.assertEqual(grid(sc)["state_counts"], want, sc)

    def test_flagged_cell_count_unchanged(self):
        for sc, want in FLAGGED_CELLS.items():
            got = sum(1 for _, _, o in headline_observations(grid(sc)) if o["verification_status"] == "flagged")
            self.assertEqual(got, want, sc)

    def test_review_status_comes_with_every_cell(self):
        # Reviewer mode reads human_review_status from each observation's validation records. The owner's
        # triage (PR #29) resolved 452 of the 462 pending records; 10 stay pending
        data = json.loads(S.STAGED.read_text())
        st = [v.get("human_review_status") for v in data["validations"]]
        self.assertEqual((st.count("pending"), st.count("resolved")), (10, 452))
        self.assertEqual(set(st), {"", "pending", "resolved"})
        self.assertEqual(sorted(v["validation_id"] for v in data["validations"] if v["human_review_status"] == "pending"),
                         ["VAL-0089", "VAL-LHHS-01816", "VAL-LHHS-01821", "VAL-LHHS-01845", "VAL-LHHS-01850",
                          "VAL-LHHS-02006", "VAL-LHHS-03009", "VAL-LHHS-04166", "VAL-LHHS-06912", "VAL-LHHS-07178"])
        for v in data["validations"]:
            if v["human_review_status"] == "resolved":
                self.assertTrue(v["resolution"].strip(), v["validation_id"])
                self.assertEqual(v["reviewer"], "owner (group approval 2026-10-08)")
                self.assertIn(v["result"], ("fail", "flag"))
        for sc in ("LHHS", "CJS"):
            for _, _, o in headline_observations(grid(sc)):
                self.assertTrue(all("human_review_status" in v for v in o["validation"]), o["observation_id"])
                self.assertTrue(all(v.get("resolution") for v in o["validation"] if v["human_review_status"] == "resolved"))

    def test_triage_resolutions_follow_the_proposal(self):
        import csv
        data = json.loads(S.STAGED.read_text())
        by_id = {v["validation_id"]: v for v in data["validations"]}
        with open(ROOT / "reference" / "review" / "triage_proposal.csv", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["id"].startswith("VAL-")]
        for r in rows:
            v = by_id[r["id"]]
            if r["proposed_disposition"] == "A":
                self.assertEqual((v["human_review_status"], v["resolution"]), ("resolved", r["reason"]), r["id"])
            elif r["family"] == "cr-estimate":
                self.assertEqual(v["human_review_status"], "resolved")
                self.assertTrue(v["resolution"].startswith("Owner decision: FY2025 was funded by a full-year CR (P.L. 119-4)"))
            else:
                self.assertEqual(v["human_review_status"], "pending", r["id"])

    def test_the_one_verified_cell_with_a_pending_record(self):
        # CJS has no flagged cell; one verified cell still has a record pending review:
        # OBS-0966 (Crime Victims Fund, FY2027 House Reported), VAL-0089's CHIMP question
        found = [(row["account"]["canonical_account_id"], key, o["observation_id"],
                  [v["validation_id"] for v in o["validation"] if v["human_review_status"] == "pending"])
                 for sc in ("LHHS", "CJS") for row, key, o in headline_observations(grid(sc))
                 if o["verification_status"] in ("auto-validated", "human-verified")
                 and any(v["human_review_status"] == "pending" for v in o["validation"])]
        self.assertEqual(found, [("ACC-DOJ-CVF", "2027|House Reported", "OBS-0966", ["VAL-0089"])])


class DraftStages(unittest.TestCase):
    def test_bill_report_reference_draft_column(self):
        data = json.loads(S.STAGED.read_text())
        self.assertEqual({r["draft"] for r in data["bill_report_refs"]}, {"TRUE", "FALSE"})
        self.assertEqual(sorted(r["reference_id"] for r in data["bill_report_refs"] if r["draft"] == "TRUE"),
                         ["BR-CJS-FY2021-SENATE", "BR-CJS-FY2022-SENATE", "BR-CJS-FY2023-SENATE", "BR-CJS-FY2024-HOUSE",
                          "BR-LHHS-FY2023-SENATE", "BR-LHHS-FY2024-HOUSE"])
        import openpyxl
        wb = openpyxl.load_workbook(S.reference_workbook(), read_only=True)
        head = next(wb["Bill Report Reference"].iter_rows(max_row=1, values_only=True))
        self.assertEqual(head[-1], "draft")

    def test_fy2024_house_reference(self):
        data = json.loads(S.STAGED.read_text())
        r = next(r for r in data["bill_report_refs"] if r["reference_id"] == "BR-LHHS-FY2024-HOUSE")
        self.assertEqual((r["lookup_key"], r["bill_id"], r["report_id"], r["draft"]),
                         ("LHHS-2024-House Reported", "H.R.5894", "JES_LHHS_H.R.5894", "TRUE"))
        self.assertEqual(r["bill_url"], "https://www.govinfo.gov/content/pkg/BILLS-118hr5894ih/pdf/BILLS-118hr5894ih.pdf")
        doc = next(d for d in data["source_docs"] if d["document_id"] == "SRC-EXPL-LHHS-FY2024-HOUSE")
        self.assertEqual(r["report_jes_url"], doc["url_or_identifier"])
        self.assertIn("408e33889225be0225d2c1fff15e095f5532df9d5b8fd8dd1c0125dbf0f8ff8d", doc["notes"])
        self.assertEqual((doc["stage"], doc["fiscal_year"], doc["document_type"]), ("House Reported", 2024, "explanatory_statement"))


class DownloadWorkbook(unittest.TestCase):
    def test_published_where_the_link_points(self):
        self.assertTrue((DOCS / "Approps_Pilot_Schema_Loaded.xlsx").is_file())
        self.assertEqual(E.DOWNLOAD, "Approps_Pilot_Schema_Loaded.xlsx")

    def test_rebuilding_twice_gives_a_byte_identical_file(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i in (1, 2):
                built = S.build_workbook(Path(d) / f"build{i}.xlsx")       # a fresh build each time
                out = Path(d) / f"published{i}.xlsx"
                E.publish_workbook(built, out, E.committed_date(S.STAGED))
                files.append(out.read_bytes())
            self.assertNotEqual((Path(d) / "build1.xlsx").read_bytes(), (Path(d) / "build2.xlsx").read_bytes(),
                                "openpyxl stamps the build time -- the publish step is what makes it stable")
            self.assertEqual(files[0], files[1])
            self.assertEqual(files[0], (DOCS / "Approps_Pilot_Schema_Loaded.xlsx").read_bytes())


class ReviewList(unittest.TestCase):
    def rows(self):
        import csv
        with open(ROOT / "reference" / "review" / "review_list.csv", newline="") as f:
            return list(csv.DictReader(f))

    def test_aha_link_item_removed(self):
        self.assertFalse([r for r in self.rows() if r["id"] == "SRC-CJ-AHA-FY2026"])

    def test_page_check_sheet(self):
        import csv
        with open(ROOT / "reference" / "review" / "page_check_sheet.csv", newline="") as f:
            sheet = list(csv.DictReader(f))
        page_items = [r for r in self.rows() if r["reason"] == "page not confirmed by text search"]
        self.assertEqual((len(page_items), len(sheet)), (140, 129))      # less the 11 derived figures (resolved)
        self.assertEqual(sum(bool(r["nearby_page"]) for r in sheet), 13)
        data = json.loads(S.STAGED.read_text())
        obs = {o["observation_id"]: o for o in data["observations"]}
        docs = {d["document_id"]: d["url_or_identifier"] for d in data["source_docs"]}
        import re
        for r in sheet:
            o = obs[r["observation_id"]]
            first = min(int(x) for x in re.findall(r"\d+", o["source_page"]))
            self.assertEqual(r["pdf_link"], f"{docs[o['source_document_id']]}#page={first}")
            self.assertTrue(r["pdf_link"].split("#")[0].endswith(".pdf"))
            self.assertEqual((int(r["fiscal_year"]), r["stage"]), (o["fiscal_year"], o["stage"]))
            if r["nearby_page"]:
                near = re.findall(r"\d+", r["nearby_page"])[0]
                self.assertTrue(r["nearby_link"].endswith(f"#page={near}"))
        page = (DOCS / "review" / "page-checks.html").read_text()
        self.assertEqual(page.count('data-testid="page-check-row"'), 129)
        self.assertNotIn("api_key", page)

    def test_cbo_observations_tagged_interim(self):
        data = json.loads(S.STAGED.read_text())
        cbo = sorted(o["observation_id"] for o in data["observations"] if o["source_document_id"] == "SRC-CBO-HR8845-FY2027")
        self.assertEqual(len(cbo), 22)
        tagged = sorted(r["id"] for r in self.rows()
                        if r["reason"] == "interim source: re-source to H.Rpt. 119-652 when the rest of CJS is ingested")
        self.assertEqual(tagged, cbo)


if __name__ == "__main__":
    unittest.main()
