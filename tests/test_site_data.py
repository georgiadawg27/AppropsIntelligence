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
# verification_status is "flagged")
STATE_COUNTS = {
    "LHHS": {"value": 1452, "not_funded": 102, "no_printed_total": 13, "missing": 133, "not_collected": 200,
             "not_enacted": 100},
    "CJS": {"value": 752, "not_funded": 18, "no_printed_total": 0, "missing": 460, "not_collected": 60,
            "not_enacted": 30},
}
FLAGGED_CELLS = {"LHHS": 73, "CJS": 0}


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
        # Reviewer mode reads human_review_status from each observation's validation records;
        # the data is unchanged: 434 records pending
        data = json.loads(S.STAGED.read_text())
        self.assertEqual(sum(v.get("human_review_status") == "pending" for v in data["validations"]), 434)
        self.assertEqual({v.get("human_review_status") for v in data["validations"]}, {"", "pending"})
        for sc in ("LHHS", "CJS"):
            for _, _, o in headline_observations(grid(sc)):
                self.assertTrue(all("human_review_status" in v for v in o["validation"]), o["observation_id"])

    def test_the_one_verified_cell_with_a_pending_record(self):
        # CJS has no flagged cell; one verified cell still has a record pending review:
        # OBS-0966 (Crime Victims Fund, FY2027 House Reported), VAL-0089's CHIMP question
        found = [(row["account"]["canonical_account_id"], key, o["observation_id"],
                  [v["validation_id"] for v in o["validation"] if v["human_review_status"] == "pending"])
                 for sc in ("LHHS", "CJS") for row, key, o in headline_observations(grid(sc))
                 if o["verification_status"] in ("auto-validated", "human-verified")
                 and any(v["human_review_status"] == "pending" for v in o["validation"])]
        self.assertEqual(found, [("ACC-DOJ-CVF", "2027|House Reported", "OBS-0966", ["VAL-0089"])])


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

    def test_cbo_observations_tagged_interim(self):
        data = json.loads(S.STAGED.read_text())
        cbo = sorted(o["observation_id"] for o in data["observations"] if o["source_document_id"] == "SRC-CBO-HR8845-FY2027")
        self.assertEqual(len(cbo), 22)
        tagged = sorted(r["id"] for r in self.rows()
                        if r["reason"] == "interim source: re-source to H.Rpt. 119-652 when the rest of CJS is ingested")
        self.assertEqual(tagged, cbo)


if __name__ == "__main__":
    unittest.main()
