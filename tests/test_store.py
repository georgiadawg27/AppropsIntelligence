"""
The relational store and its query (approps_store.py, store_schema.sql),
loaded from the committed pilot workbook (reference/, v28 --
approps_store.reference_workbook()).

Run:  python -m unittest tests.test_store -v

Needs openpyxl (loading only).
"""

import contextlib
import csv
import importlib.util
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402

try:
    import openpyxl
except ImportError:                                  # pragma: no cover
    openpyxl = None

WORKBOOK = S.reference_workbook()        # either file-name pattern (approps_store.WORKBOOK_PATTERNS)
FOUR = ["President's Budget", "House Reported", "Senate Reported", "Enacted"]


def review_csv(name):
    with open(ROOT / "reference" / "review" / name, newline="") as f:
        return list(csv.DictReader(f))




def workbook_rows(tab):
    """A tab read straight from the workbook -- independent of the loader."""
    wb = openpyxl.load_workbook(WORKBOOK, data_only=True, read_only=True)
    rows = list(wb[tab].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:] if r[0] is not None]


@unittest.skipUnless(openpyxl, "openpyxl not installed")
class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "approps.db"
        cls.report = S.load(WORKBOOK, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.conn = S.connect(self.db)

    def tearDown(self):
        self.conn.close()

    def query(self, text, agency=None):
        res = S.resolve(self.conn, text, agency)
        return res, (S.history(self.conn, res["account"]["canonical_account_id"]) if res["account"] else None)


class Load(StoreTest):
    def test_all_seven_tabs_load(self):
        # v34: CJS (30 accounts, 870 observations, 197 absences -- as in v28) + Labor-HHS Title II's full
        # account list (100), the FY2023 rows, the CURES account, relationships and historical names
        # FY2024 House Labor-HHS draft (SRC-EXPL-LHHS-FY2024-HOUSE): + 1 document, 1 reference, 101 observations, 2 absences, 473 records; then H.R. 5894's bill text: + 1 document, 1 observation (NEF rescission), 3 records
        self.assertEqual(self.report["rows"], {
            "account": 130, "historical_name": 11, "source_document": 37, "bill_report_reference": 59,
            "appropriations_observation": 2703, "confirmed_absence": 236, "account_relationship": 11,
            "validation_record": 7267, "component": 19})
        # each total's scope is Account.total_scope: 13 agency totals, the Labor-HHS title total, no bill total
        self.assertEqual(dict(self.conn.execute("SELECT ifnull(total_scope, '-'), count(*) FROM account "
                                                "GROUP BY 1").fetchall()), {"-": 116, "agency": 13, "title": 1})
        cjs = lambda table, key: self.conn.execute(
            f"SELECT count(*) FROM {table} t JOIN account a ON a.canonical_account_id = t.{key} "
            "WHERE a.subcommittee = 'CJS'").fetchone()[0]
        self.assertEqual((cjs("appropriations_observation", "canonical_account_id"),
                          cjs("confirmed_absence", "canonical_account_id")), (870, 197))
        # v30 has the Component tab: its kinds and labels come from the workbook, not the code
        self.assertEqual(self.report["tabs_not_in_workbook"], [])
        self.assertNotIn("component_kinds_from", self.report)
        kinds = S.component_kinds(self.conn)
        self.assertEqual((kinds["defense"], kinds["supplemental_act"], kinds["appropriated_in_this_bill"]),
                         ("part", "part", "view"))
        self.assertNotIn("CURES", kinds)                 # v33: an account of its own, no longer a component
        # every component carries a label from a document's own text (accounts.COMPONENT_LABELS)
        labels = dict(self.conn.execute("SELECT component_id, label FROM component").fetchall())
        self.assertEqual((labels["program_level_excluding_arpa_h"], labels["defense"]),
                         ("program level (excluding ARPA-H)", "Defense function"))
        self.assertNotIn(None, labels.values())

    def test_reference_workbook_loads_with_no_warnings(self):
        self.assertEqual((self.report["waived"], self.report["warnings"]), ({}, []))
    def test_new_observations_cite_pages_inside_their_documents(self):
        for oid, page, doc in (("OBS-0986", "229", "SRC-CRPT-118SRPT198"), ("OBS-0987", "191", "SRC-CRPT-116HRPT455"),
                               ("OBS-0988", "193", "SRC-CRPT-116SRPT127"), ("OBS-0989", "152", "SRC-CRPT-116HRPT101"),
                               ("OBS-0990", "152", "SRC-CRPT-116HRPT101")):
            o = self.conn.execute("SELECT source_page, source_document_id FROM appropriations_observation "
                                  "WHERE observation_id = ?", (oid,)).fetchone()
            rng = self.conn.execute("SELECT source_page FROM source_document WHERE document_id = ?", (doc,)).fetchone()[0]
            self.assertEqual(tuple(o), (page, doc))
            self.assertTrue(S.within(page, rng), (oid, page, rng))

    def test_reference_workbook_file_name(self):
        # Approps_Pilot_Schema_Loaded_vNN only (v30 on); the old CJS_Title_III_... name is not a reference workbook
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "CJS_Title_III_Science_Pilot_Schema_Loaded_v29.xlsx").touch()
            (d / "notes.xlsx").touch()
            with self.assertRaises(S.LoadError):
                S.reference_workbook(d)
            (d / "Approps_Pilot_Schema_Loaded_v30.xlsx").touch()
            self.assertEqual(S.reference_workbook(d).name, "Approps_Pilot_Schema_Loaded_v30.xlsx")
            (d / "CJS_Title_III_Science_Pilot_Schema_Loaded_v31.xlsx").touch()            # ignored, whatever its vNN
            self.assertEqual(S.reference_workbook(d).name, "Approps_Pilot_Schema_Loaded_v30.xlsx")
            (d / "Approps_Pilot_Schema_Loaded_v31.xlsx").touch()
            self.assertEqual(S.reference_workbook(d).name, "Approps_Pilot_Schema_Loaded_v31.xlsx")   # the higher vNN

    def test_table_pages_may_be_several_ranges(self):
        # a title's pages and a bill-level section after the grand total
        self.assertTrue(S.within("467", "430-452; 467"))
        self.assertTrue(S.within("440", "430-452; 467"))
        self.assertFalse(S.within("460", "430-452; 467"))
        self.assertFalse(S.within("451-453", "430-452; 467"))

    def test_fy2027_house_links_are_the_verified_packages(self):
        # both fetched live and checked: the report is byte-identical to the
        # CRPT-119hrpt652 govinfo_ingest stored (v37: its Congress.gov PDF, sha256-matched);
        # the bill's first page is H.R. 8845 as reported, May 15, 2026
        r = self.conn.execute("SELECT bill_id, report_id, bill_url, report_jes_url FROM bill_report_reference "
                              "WHERE reference_id = 'BR-CJS-FY2027-HOUSE'").fetchone()
        self.assertEqual(tuple(r), ("H.R.8845", "H.Rept.119-652",
                                    "https://www.govinfo.gov/content/pkg/BILLS-119hr8845rh/pdf/BILLS-119hr8845rh.pdf",
                                    "https://www.congress.gov/119/crpt/hrpt652/CRPT-119hrpt652.pdf"))

    def test_foreign_keys_are_enforced_not_just_declared(self):
        # SQLite ignores REFERENCES unless the connection turns them on
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO historical_name VALUES ('HN-X', 'ACC-NOPE', 'n', 'e', NULL, 1, 1)")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_workbook_booleans_are_booleans(self):
        # 'TRUE' as text would evaluate false in SQLite
        want = sum(1 for o in workbook_rows("Appropriations Observation") if o["offsetting_collections"] == "TRUE")
        self.assertGreater(want, 0)
        got = self.conn.execute("SELECT count(*) FROM appropriations_observation WHERE offsetting_collections").fetchone()[0]
        self.assertEqual(got, want)
        with self.assertRaises(sqlite3.IntegrityError):              # STRICT: no text in an INTEGER column
            self.conn.execute("UPDATE appropriations_observation SET offsetting_collections = 'TRUE'")

    def test_spelling_variant_is_mapped_and_counted(self):
        # v30 spells extraction_method as the Data Dictionary does: nothing to map
        self.assertEqual(self.report["value_map"], {})
        # an older workbook's "human_entered" still loads as "human-entered", counted
        tabs = {t: [] for t, _, _ in S.TABS}
        tabs["Appropriations Observation"] = [(2, {"observation_id": "OBS-X", "canonical_account_id": "ACC-X", "fiscal_year": 2024,
                                                   "stage": "Enacted", "amount": 1000, "amount_type": "budget authority",
                                                   "offsetting_collections": False, "source_document_id": "SRC-X",
                                                   "extraction_method": "human_entered", "confidence": 1,
                                                   "verification_status": "human-verified"})]
        report = {"value_map": {}}
        rows = S.convert_rows(tabs, report)
        self.assertEqual(rows["Appropriations Observation"][0]["extraction_method"], "human-entered")
        self.assertEqual(report["value_map"], {"Appropriations Observation.extraction_method: 'human_entered' -> 'human-entered'": 1})

    def test_dates_are_iso_dates(self):
        self.assertEqual(self.conn.execute("SELECT publication_date FROM source_document "
                                           "WHERE document_id = 'SRC-CRPT-119HRPT652'").fetchone()[0], "2026-05-15")

    def test_bill_report_reference_is_a_real_key(self):
        r = self.conn.execute("SELECT o.bill_id, o.report_id, b.bill_id, b.report_id FROM appropriations_observation o "
                              "JOIN bill_report_reference b ON b.reference_id = o.bill_report_reference_id "
                              "WHERE o.observation_id = 'OBS-0082'").fetchone()
        self.assertEqual(tuple(r), ("S.2354", "S.Rept.119-44", "S.2354", "S.Rept.119-44"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM appropriations_observation "
                                           "WHERE bill_report_reference_id IS NULL").fetchone()[0], 0)

    def test_document_identity_is_the_documents_own(self):
        # v11+ records each document as what it is (v10 recorded the column
        # taken from it); the type/stage check no longer fires
        text = "\n".join(self.report["warnings"])
        self.assertNotIn("recorded at stage", text)
        d = self.conn.execute("SELECT fiscal_year, stage, also_covers FROM source_document "
                              "WHERE document_id = 'SRC-CRPT-119HRPT652'").fetchone()
        self.assertEqual(tuple(d), (2027, "House Reported", "FY2026 Enacted"))

    def test_every_citation_is_inside_its_documents_table_pages(self):
        # v11/v12 re-cited 23 FY2026 Enacted observations to the enacted JES
        # but kept H.Rept. 119-652's pages (fixed in v13); v19's six
        # Other Appropriations citations fell outside Title III-only ranges
        # until v20 recorded the full tables
        self.assertEqual([w for w in self.report["warnings"] if "outside" in w and "effective dates" not in w], [])
        for doc, rng in (("SRC-CRPT-118SRPT62", "212-225"), ("SRC-CRPT-117HRPT395", "210-230"),
                         ("SRC-CRPT-115HRPT704", "120-135"), ("SRC-CRPT-118SRPT198", "220-231"),
                         ("SRC-CRPT-116HRPT455", "178-197"), ("SRC-CRPT-116SRPT127", "186-195"),
                         ("SRC-CRPT-116HRPT101", "139-152")):
            self.assertEqual(self.conn.execute("SELECT source_page FROM source_document WHERE document_id = ?",
                                               (doc,)).fetchone()[0], rng, doc)

    def test_blank_request_is_missing_not_zero(self):
        # v13 dropped the $0 rows where the request column prints '---'; v21
        # recorded STEM's FY2019 one as a confirmed absence (CA-0150, tested
        # above) -- not missing, and still not zero
        h = S.history(self.conn, "ACC-NASA-STEM-ENGAGEMENT")
        self.assertIn((2020, "President's Budget"), h["missing_cells"])
        # Space Technology's blank cells were Exploration Research and
        # Technology's: v24 folded that line into Space Technology
        h = S.history(self.conn, "ACC-NASA-SPACETECH")
        ba = {(o["fiscal_year"], o["stage"]): o["observation_id"] for o in h["observations"]
              if o["amount_type"] == "budget authority" and o["component"] is None}
        self.assertEqual([ba[c] for c in ((2019, "President's Budget"), (2019, "House Reported"), (2020, "President's Budget"))],
                         ["OBS-0524", "OBS-0523", "OBS-0522"])
        self.assertEqual([c for c in h["missing_cells"] if c[0] <= 2026], [])


class NasaScienceAcceptance(StoreTest):
    def test_resolves_through_the_extraction_matching_rule(self):
        res, _ = self.query("NASA Science")
        self.assertEqual((res["account"]["canonical_account_id"], res["match"], res["via"], res["agency"]),
                         ("ACC-NASA-SCIENCE", "exact", "canonical", "National Aeronautics and Space Administration"))
        # the same letters-only edit distance and threshold as extraction
        self.assertEqual(self.query("NASA Scince")[0]["match"], "ocr_corrected")

    def test_history_equals_the_workbook_row_for_row(self):
        _, h = self.query("NASA Science")
        got = sorted((o["observation_id"], o["fiscal_year"], o["stage"], o["amount_type"], o["amount"],
                      o["source_document_id"], o["source_page"]) for o in h["observations"])
        want = sorted((o["observation_id"], o["fiscal_year"], o["stage"], o["amount_type"], o["amount"],
                       o["source_document_id"], o["source_page"])
                      for o in workbook_rows("Appropriations Observation")
                      if o["canonical_account_id"] == "ACC-NASA-SCIENCE")
        self.assertEqual(got, want)
        ba = [o for o in h["observations"] if o["amount_type"] == "budget authority"]
        self.assertEqual(len(ba), 40)
        self.assertEqual({(o["fiscal_year"], o["stage"]) for o in ba}, {(y, s) for y in range(2017, 2027) for s in FOUR})
        self.assertEqual({(o["fiscal_year"], o["stage"], o["amount"]) for o in h["observations"]
                          if o["amount_type"] == "rescission"},
                         {(2020, "Enacted", -70_000_000), (2020, "Senate Reported", -70_000_000)})

    def test_fy2027_is_reported_missing_not_zero(self):
        _, h = self.query("NASA Science")
        # only FY2027 House Reported is covered by a document on file (H.Rept. 119-652)
        self.assertEqual(h["missing_cells"], [(2027, "House Reported")])
        g = S.history_grid(h)
        fy27 = {st: [l["state"] for l in c] for st, c in next(r for r in g["rows"] if r["fiscal_year"] == 2027)["cells"].items()}
        self.assertEqual({st: v[0] for st, v in fy27.items()}, {"President's Budget": "not_collected", "House Reported": "missing",
                                                                  "Senate Reported": "not_collected", "Enacted": "not_enacted"})

    def test_values_match_the_printed_documents(self):
        # read off the documents themselves, not the workbook: text layer
        # (Senate), page image by eye (House 115-231 p.120, 116-101 p.148),
        # recorded vision transcription (House 119-652 p.169)
        _, h = self.query("NASA Science")
        cell = {(o["fiscal_year"], o["stage"]): o for o in h["observations"]}
        for (fy, stage), amount, doc, page in (
                ((2024, "Senate Reported"), 7_340_920_000, "SRC-CRPT-118SRPT62", 219),
                ((2024, "President's Budget"), 8_260_800_000, "SRC-CRPT-118SRPT62", 219),
                ((2023, "Enacted"), 7_795_000_000, "SRC-CRPT-118SRPT62", 219),
                ((2026, "Senate Reported"), 7_300_000_000, "SRC-CRPT-119SRPT44", 217),
                ((2020, "House Reported"), 7_161_300_000, "SRC-CRPT-116HRPT101", 148),
                ((2017, "Enacted"), 5_764_900_000, "SRC-CRPT-115HRPT231", 120),
                ((2018, "President's Budget"), 5_711_800_000, "SRC-CRPT-115HRPT231", 120)):
            o = cell[(fy, stage)]
            lo, hi = map(int, o["source_page"].split("-"))
            self.assertEqual((o["amount"], o["source_document_id"]), (amount, doc), (fy, stage))
            self.assertTrue(lo <= page <= hi, (fy, stage, o["source_page"]))

    def test_fy2026_enacted_cites_the_enacted_jes_table_page(self):
        # the uploaded FY2026 JES (fy26_cjs_jes.pdf) prints Science 7,250,000
        # in its Final Bill column on PDF page 128
        _, h = self.query("NASA Science")
        o = {(o["fiscal_year"], o["stage"]): o for o in h["observations"]}[(2026, "Enacted")]
        self.assertEqual((o["amount"], o["source_document_id"]), (7_250_000_000, "SRC-EXPL-FY2026-PB"))
        lo, hi = map(int, o["source_page"].split("-"))
        self.assertTrue(lo <= 128 <= hi, o["source_page"])

    def test_every_value_cites_a_source_document_that_exists(self):
        _, h = self.query("NASA Science")
        docs = {r[0] for r in self.conn.execute("SELECT document_id FROM source_document")}
        self.assertTrue(all(o["source_document_id"] in docs and o["url_or_identifier"] for o in h["observations"]))
        # and that document is, or also covers, the observation's cell
        for o in h["observations"]:
            d = dict(self.conn.execute("SELECT * FROM source_document WHERE document_id = ?",
                                       (o["source_document_id"],)).fetchone())
            self.assertIn((o["fiscal_year"], o["stage"]), S.covered_cells(d)[0], o["observation_id"])


class ExplorationAcceptance(StoreTest):
    def test_base_and_supplemental_split(self):
        _, h = self.query("NASA Exploration")
        obs = h["observations"]
        self.assertEqual(len(obs), 50)
        self.assertEqual(len(h["absences"]), 31)
        by = {(o["fiscal_year"], o["stage"], o["amount_type"]): o["amount"] for o in obs}
        self.assertEqual({t for (_, _, t) in by}, {"budget authority", "supplemental", "other"})
        self.assertEqual(by[(2024, "Enacted", "budget authority")], 7_216_200_000)
        self.assertEqual(by[(2024, "Enacted", "supplemental")], 450_000_000)
        self.assertEqual(by[(2025, "Senate Reported", "supplemental")], 1_212_000_000)

    def test_former_name_reaches_the_same_account(self):
        for text, kind in (("Deep Space Exploration Systems", "exact"), ("Deep Spaee Exploratlon Systems", "ocr_corrected")):
            res, h = self.query(text)
            self.assertEqual((res["account"]["canonical_account_id"], res["match"], res["via"]),
                             ("ACC-NASA-EXPLORATION", kind, "historical_name"))
            self.assertEqual(len(h["observations"]), 50)

    def test_exploration_technology_is_a_former_name_of_space_technology(self):
        # v24: REL-0005 resolved by folding the line in (FY2019 Budget
        # Appendix: the request renamed Space Technology "Exploration Research
        # and Technology"); no relationship is left open
        # (v30's four relationships are Labor-HHS's, all to the proposed AHA account)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM account_relationship r JOIN account a ON "
                                           "a.canonical_account_id IN (r.from_account_id, r.to_account_id) "
                                           "WHERE a.subcommittee = 'CJS'").fetchone()[0], 0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM account WHERE canonical_account_id = 'ACC-NASA-EXPLTECH'").fetchone())
        for text in ("Exploration Research and Technology", "Exploration Technology"):
            res, h = self.query(text)
            self.assertEqual((res["account"]["canonical_account_id"], res["via"]), ("ACC-NASA-SPACETECH", "historical_name"), text)
            self.assertEqual(h["relationships"], [])
            self.assertTrue({"OBS-0522", "OBS-0523", "OBS-0524", "OBS-0990"} <= {o["observation_id"] for o in h["observations"]})

    def test_retracted_relationship_is_gone(self):
        # v21 retracted REL-0006: Exploration has real, human-verified figures
        # in the three cells its premise said were blank
        _, h = self.query("Exploration")
        self.assertEqual(h["relationships"], [])
        verified = {(o["fiscal_year"], o["stage"]): o["verification_status"] for o in h["observations"]
                    if o["amount_type"] == "budget authority" and o["component"] is None}
        for cell in ((2019, "President's Budget"), (2019, "House Reported"), (2020, "President's Budget")):
            self.assertEqual(verified[cell], "human-verified")

    def test_stem_fy2019_request_is_a_real_zero(self):
        # v26: CA-0150 became OBS-0996 -- the FY2019 budget proposed ending the
        # Office of Education, so the request is a deliberate $0: a printed zero is
        # "not funded" (cell_state), with its citation
        g = S.history_grid(S.history(self.conn, "ACC-NASA-STEM-ENGAGEMENT"))
        row = next(r for r in g["rows"] if r["fiscal_year"] == 2019)
        line = row["cells"]["President's Budget"][0]
        self.assertEqual((line["state"], [(o["observation_id"], o["amount"]) for o in line["observations"]], line["absence"]),
                         ("not_funded", [("OBS-0996", 0)], None))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM confirmed_absence WHERE confirmed_absence_id = 'CA-0150'").fetchone())
        # cited to the page that prints it (v28): the FY2019 Budget Appendix p.1084, account 080-0128
        self.assertEqual(tuple(self.conn.execute("SELECT source_document_id, source_page FROM appropriations_observation "
                                                 "WHERE observation_id = 'OBS-0996'").fetchone()), ("SRC-BUDGET-APP-FY2019", "1084"))

    def test_matching_pool_reads_review_state_from_the_store(self):
        self.conn.execute("UPDATE historical_name SET human_reviewed = 0 WHERE historical_name_id = 'HN-0001'")
        try:
            self.assertIsNone(self.query("Deep Space Exploration Systems")[0]["account"])
        finally:
            self.conn.rollback()


class Resolve(StoreTest):
    def test_same_name_in_two_agencies_is_not_guessed(self):
        res, _ = self.query("Office of Inspector General")
        self.assertEqual((res["match"], res["account"]), ("ambiguous", None))
        self.assertEqual([c["canonical_account_id"] for c in res["candidates"]][:4],
                         ["ACC-DOJ-OIG", "ACC-HHS-OS-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"])      # v33: HHS's too
        self.assertEqual(self.query("NSF Office of Inspector General")[0]["account"]["canonical_account_id"], "ACC-NSF-OIG")
        self.assertEqual(self.query("Office of Inspector General", agency="NASA")[0]["account"]["canonical_account_id"],
                         "ACC-NASA-OIG")

    def test_agency_alone_is_its_agency_total(self):
        self.assertEqual(self.query("NASA")[0]["account"]["canonical_account_id"], "ACC-NASA-TOTAL")

    def test_cli_exit_codes(self):
        for args, code in ((["NASA Science", "--json"], 0), (["Office of Inspector General"], 2),
                           (["Nothing Like Any Account"], 1)):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(S.main(["history", *args, "--db", str(self.db)]), code, args)


class StoreCopyTest(StoreTest):
    """Tests that write: each gets its own copy of the loaded database."""

    def setUp(self):
        self.path = Path(self.tmp.name) / f"{self.id().rsplit('.', 1)[-1]}.db"
        shutil.copy(self.db, self.path)
        self.conn = S.connect(self.path)

    def flagged(self, account):
        return [r[0] for r in self.conn.execute(
            "SELECT observation_id FROM appropriations_observation WHERE canonical_account_id = ? "
            "AND verification_status = 'flagged' ORDER BY 1", (account,))]


class ResolutionCascade(StoreCopyTest):
    """The store has no open identity question since v24, so each test sets one
    up the way v10 had it: the three former Exploration Research and
    Technology figures (now Space Technology's) flagged at 0.5 with one
    account_identity finding, under an unreviewed relationship to
    Exploration (REL-T)."""

    EXPLTECH = ["OBS-0522", "OBS-0523", "OBS-0524"]

    def setUp(self):
        super().setUp()
        with self.conn:
            self.conn.execute("UPDATE appropriations_observation SET verification_status = 'flagged', confidence = 0.5 "
                              "WHERE observation_id IN ('OBS-0522', 'OBS-0523', 'OBS-0524')")
            self.conn.execute("INSERT INTO validation_record (validation_id, observation_id, rule_applied, result, "
                              "human_review_status) VALUES ('VAL-ID', 'OBS-0522', 'account_identity', 'flag', 'pending')")
            self.conn.execute("INSERT INTO account_relationship (relationship_id, from_account_id, to_account_id, "
                              "relationship_type, effective_fiscal_year, evidence, confidence, human_reviewed) VALUES "
                              "('REL-T', 'ACC-NASA-EXPLORATION', 'ACC-NASA-SPACETECH', 'uncertain', 2019, 'test', 0.2, 0)")

    def test_a_resolution_that_reached_one_observation_is_reported(self):
        self.conn.execute("UPDATE validation_record SET human_review_status = 'resolved' WHERE validation_id = 'VAL-ID'")
        self.assertEqual(S.stale_resolution_warnings(self.conn), [
            "validation_record VAL-ID resolves ACC-NASA-SPACETECH's identity, but its own observation is still "
            "flagged (OBS-0522); 2 other observation(s) of the account are still flagged with no resolved record: "
            "OBS-0523, OBS-0524"])
        self.assertEqual([w for w in self.report["warnings"] if "resolves" in w], [])       # the workbook itself: none

    def test_resolution_reaches_every_observation_it_decides(self):
        exploration_before = self.conn.execute("SELECT count(*) FROM validation_record v JOIN appropriations_observation o "
                                               "USING (observation_id) WHERE o.canonical_account_id = 'ACC-NASA-EXPLORATION'"
                                               ).fetchone()[0]
        s = S.resolve_relationship(self.conn, "REL-T", "Reviewer", "renamed line")
        self.assertEqual(s["observations"], self.EXPLTECH)
        self.assertEqual(s["records_updated"], ["VAL-ID"])
        self.assertEqual(s["records_created"], ["VAL-REL-T-OBS-0523", "VAL-REL-T-OBS-0524"])
        self.assertEqual(self.flagged("ACC-NASA-SPACETECH"), [])
        self.assertEqual({r[0] for r in self.conn.execute(
            "SELECT confidence FROM appropriations_observation WHERE observation_id IN ('OBS-0522', 'OBS-0523', 'OBS-0524')")},
            {0.95})
        for oid in s["observations"]:
            r = self.conn.execute("SELECT human_review_status, reviewer, resolution FROM validation_record "
                                  "WHERE observation_id = ? AND rule_applied = 'account_identity'", (oid,)).fetchall()
            self.assertEqual([tuple(x) for x in r], [("resolved", "Reviewer", "renamed line")], oid)
        self.assertEqual(self.conn.execute("SELECT human_reviewed FROM account_relationship "
                                           "WHERE relationship_id = 'REL-T'").fetchone()[0], 1)
        # verified rows weren't waiting on it: the other account's, and the
        # rest of Space Technology's
        self.assertEqual(self.conn.execute("SELECT count(*) FROM validation_record v JOIN appropriations_observation o "
                                           "USING (observation_id) WHERE o.canonical_account_id = 'ACC-NASA-EXPLORATION'"
                                           ).fetchone()[0], exploration_before)
        for acct in ("ACC-NASA-EXPLORATION", "ACC-NASA-SPACETECH"):
            self.assertEqual({r[0] for r in self.conn.execute(
                "SELECT DISTINCT confidence FROM appropriations_observation WHERE canonical_account_id = ? "
                "AND observation_id NOT IN ('OBS-0522', 'OBS-0523', 'OBS-0524')", (acct,))}, {1.0}, acct)
        self.assertEqual(S.stale_resolution_warnings(self.conn), [])

    def test_another_open_finding_keeps_its_observation_flagged(self):
        self.conn.execute("INSERT INTO validation_record (validation_id, observation_id, rule_applied, result) "
                          "VALUES ('VAL-X', 'OBS-0523', 'table_total', 'fail')")
        self.conn.commit()
        s = S.resolve_relationship(self.conn, "REL-T", "Reviewer", "renamed line")
        self.assertEqual(s["still_flagged"], ["OBS-0523"])
        self.assertEqual(self.conn.execute("SELECT confidence FROM appropriations_observation "
                                           "WHERE observation_id = 'OBS-0523'").fetchone()[0], 0.5)
        self.assertEqual(self.flagged("ACC-NASA-SPACETECH"), ["OBS-0523"])

    def test_refused_resolution_changes_nothing(self):
        with self.assertRaises(LookupError):
            S.resolve_relationship(self.conn, "REL-9999", "Reviewer", "x")
        with self.assertRaises(ValueError):
            S.resolve_relationship(self.conn, "REL-T", "", "x")
        self.assertEqual(self.flagged("ACC-NASA-SPACETECH"), self.EXPLTECH)


class FactKey(StoreCopyTest):
    """(canonical_account_id, fiscal_year, stage, amount_type, component,
    transfer_link_account_id) -- section text is not identity."""

    def test_v13_has_no_collisions(self):
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM appropriations_observation GROUP BY canonical_account_id, fiscal_year, "
            "stage, amount_type, ifnull(component, ''), ifnull(transfer_link_account_id, '') HAVING count(*) > 1)"
        ).fetchone()[0], 0)
        # the 42 facts that collided without component / transfer link
        rows = self.conn.execute("SELECT canonical_account_id, component, transfer_link_account_id FROM "
                                 "appropriations_observation WHERE canonical_account_id IN ('ACC-NSF-RRA', 'ACC-DOJ-CVF') "
                                 "AND amount_type IN ('budget authority', 'transfer', 'rescission')").fetchall()
        self.assertEqual(sum(1 for r in rows if r[1] == "defense"), 40)
        self.assertEqual({(r[1], r[2]) for r in rows if r[0] == "ACC-DOJ-CVF"},
                         {(None, "ACC-DOJ-VAWA"), (None, "ACC-DOJ-OIG"), ("chimp", None), ("chimp_pop_up", None)})

    def test_components_are_the_canonical_vocabulary(self):
        import accounts
        used = lambda sub: {r[0] for r in self.conn.execute(
            "SELECT DISTINCT o.component FROM appropriations_observation o JOIN account a USING (canonical_account_id) "
            "WHERE a.subcommittee = ?", (sub,))}
        # CJS uses every part component but 'emergency'; the breakdowns (CURES, parallel scopes) are Labor-HHS's
        self.assertEqual(used("CJS"), {None} | (set(accounts.VOCABULARY) - accounts.BREAKDOWN_COMPONENTS))
        self.assertLessEqual(used("LHHS") - {None}, set(accounts.VOCABULARY) | {"emergency"})
        # v33: the CURES Act money is its own account, no longer a component of NIH's total
        self.assertNotIn("CURES", used("LHHS"))
        self.assertIn("program_level_excluding_arpa_h", used("LHHS"))
        self.assertFalse(accounts.BREAKDOWN_COMPONENTS & (set(accounts.COMPONENTS) | set(accounts.STRUCTURAL_COMPONENTS)))
        # structural components are never reached from a printed label
        for c in accounts.STRUCTURAL_COMPONENTS:
            self.assertEqual(accounts.match_component(c.replace("_", " "))[1], "unmatched")

    def test_same_fact_with_other_section_text_is_refused(self):
        # a base line (component NULL): a plain UNIQUE would let this in
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO appropriations_observation (observation_id, canonical_account_id, fiscal_year, stage, "
                "amount, amount_type, offsetting_collections, source_document_id, source_table_or_section, "
                "extraction_method, confidence, verification_status) SELECT 'DUP', canonical_account_id, fiscal_year, "
                "stage, amount, amount_type, 0, source_document_id, 'other words', extraction_method, confidence, "
                "verification_status FROM appropriations_observation WHERE observation_id = 'OBS-0081'")

    def test_blank_component_is_null_not_empty(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE appropriations_observation SET component = '' WHERE observation_id = 'OBS-0081'")


class ConfirmedAbsenceRules(StoreCopyTest):
    """value / not applicable / missing -- a fact is observed or confirmed
    absent, never both (v16's 133 Confirmed Absence rows)."""

    def absent(self, cid, acct, fy, stage, amount_type, component=None, doc="SRC-CRPT-114SRPT239"):
        self.conn.execute("INSERT INTO confirmed_absence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (cid, acct, fy, stage, amount_type, component, doc, "test", "2026-09-25"))

    def test_absence_of_an_observed_fact_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:          # FY2024 Enacted Exploration supplemental: 450,000,000
            self.absent("CA-X", "ACC-NASA-EXPLORATION", 2024, "Enacted", "supplemental", doc="SRC-CRPT-118HRPT582")
        self.assertIn("contradicts an observation", str(cm.exception))

    def test_observation_of_an_absent_fact_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:          # CA-0027: FY2017 Senate Exploration supplemental
            self.conn.execute("INSERT INTO appropriations_observation (observation_id, canonical_account_id, fiscal_year, "
                              "stage, amount, amount_type, offsetting_collections, source_document_id, extraction_method, "
                              "confidence, verification_status) VALUES ('OBS-X', 'ACC-NASA-EXPLORATION', 2017, "
                              "'Senate Reported', 0, 'supplemental', 0, 'SRC-CRPT-114SRPT239', 'human-entered', 1, "
                              "'human-verified')")
        self.assertIn("contradicts a confirmed absence", str(cm.exception))
        with self.assertRaises(sqlite3.IntegrityError):                # one absence per fact
            self.absent("CA-X", "ACC-NASA-EXPLORATION", 2017, "Senate Reported", "supplemental")

    def test_every_absence_is_not_applicable_with_its_evidence(self):
        n = npt = 0
        totals = {r[0] for r in self.conn.execute("SELECT canonical_account_id FROM account WHERE total_scope IS NOT NULL")}
        for (acct,) in self.conn.execute("SELECT DISTINCT canonical_account_id FROM confirmed_absence").fetchall():
            for row in S.history_grid(S.history(self.conn, acct))["rows"]:
                for cells in row["cells"].values():
                    for line in cells:
                        if line["absence"]:
                            # "no printed total": an absent headline (budget authority, no component) the document
                            # still prints the account for -- a total, or a cell with another of its lines
                            headline = line["amount_type"] == "budget authority" and line["component"] is None
                            beside = any(l["observations"] for l in cells)
                            printed = headline and (acct in totals or beside)
                            self.assertEqual(line["state"], "no_printed_total" if printed else "not_funded", (acct, row["fiscal_year"]))
                            npt += printed
                            n += 1
                            self.assertTrue(line["absence"]["evidence"] and line["absence"]["source_document_id"])
                            self.assertEqual(line["observations"], [])
        self.assertEqual(n, 236)                         # v33: CJS's 197 + Labor-HHS's 37; + 2 for FY2024 House
        self.assertEqual(npt, 13)

    def test_grid_has_three_states(self):
        g = S.history_grid(S.history(self.conn, "ACC-NASA-EXPLORATION"))
        cell = lambda fy, st: {l["amount_type"]: l for l in
                               next(r for r in g["rows"] if r["fiscal_year"] == fy)["cells"][st]}
        fy17 = cell(2017, "Senate Reported")
        self.assertEqual((fy17["budget authority"]["state"], fy17["supplemental"]["state"]), ("value", "not_funded"))
        self.assertEqual(fy17["supplemental"]["absence"]["confirmed_absence_id"], "CA-0027")
        self.assertEqual(cell(2024, "Enacted")["supplemental"]["state"], "value")
        # FY2027: House Reported is covered (H.Rept. 119-652) but has no supplemental line -> missing; no
        # enacted law yet; no FY2027 request or Senate document on file
        self.assertEqual({l["state"] for l in cell(2027, "Enacted").values()}, {"not_enacted"})
        self.assertEqual(cell(2027, "House Reported")["supplemental"]["state"], "missing")
        self.assertEqual({l["state"] for l in cell(2027, "Senate Reported").values()}, {"not_collected"})
        # a series known only from absences in a cell still shows (SPACEOPS rescission)
        g = S.history_grid(S.history(self.conn, "ACC-NASA-SPACEOPS"))
        c17 = {l["amount_type"]: l["state"] for l in
               next(r for r in g["rows"] if r["fiscal_year"] == 2017)["cells"]["Senate Reported"]}
        self.assertEqual(c17, {"budget authority": "value", "rescission": "not_funded"})


class GridStates(StoreTest):
    """Every cell is in exactly one of six states (approps_store.CELL_STATES),
    computed from what is on file, never stored."""

    def state(self, account, fy, stage, amount_type="budget authority", component=None):
        g = S.history_grid(S.history(self.conn, account))
        row = next(r for r in g["rows"] if r["fiscal_year"] == fy)
        return next(l for l in row["cells"][stage] if l["amount_type"] == amount_type and l["component"] == component)

    def test_a_figure(self):
        line = self.state("ACC-HHS-AHRQ-TOTAL", 2024, "Enacted")
        self.assertEqual((line["state"], line["observations"][0]["amount"]), ("value", 369_000_000))

    def test_a_dash_printed_zero_is_not_funded(self):
        # H.Rept. 118-585 prints AHRQ's FY2025 House line as '---', recorded as an observation of 0
        line = self.state("ACC-HHS-AHRQ-TOTAL", 2025, "House Reported")
        self.assertEqual((line["state"], [o["observation_id"] for o in line["observations"]]), ("not_funded", ["OBS-LHHS-0098"]))

    def test_a_confirmed_absence_is_not_funded_with_its_evidence(self):
        line = self.state("ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", 2026, "Senate Reported", "rescission")
        self.assertEqual((line["state"], line["absence"]["confirmed_absence_id"]), ("not_funded", "CA-LHHS-0011"))
        self.assertTrue(line["absence"]["evidence"])

    def test_covered_but_unrecorded_is_missing(self):
        # FY2025 Enacted is covered for Labor-HHS (H.Rept. 119-271 also_covers it); v32 recorded NIH's figure
        # there, and the Medicare limitation general provision has none yet
        self.assertEqual(self.state("ACC-HHS-NIH-TOTAL", 2025, "Enacted")["state"], "value")
        line = S.headline_state(next(r for r in S.history_grid(S.history(self.conn, "ACC-HHS-GP-MEDICARE-LIMITATION"))["rows"]
                                     if r["fiscal_year"] == 2025)["cells"]["Enacted"])
        self.assertEqual(line, "missing")

    def test_a_year_with_no_documents_is_not_yet_collected_not_missing(self):
        # no Labor-HHS document on file covers FY2027 Senate Reported (no Senate FY2027 bill is on file; the
        # FY2024 House column, the earlier example, now holds the House subcommittee draft's figures)
        line = self.state("ACC-HHS-NIH-TOTAL", 2027, "Senate Reported")
        self.assertEqual(line["state"], "not_collected")
        self.assertNotIn((2027, "Senate Reported"), S.history(self.conn, "ACC-HHS-NIH-TOTAL")["missing_cells"])

    def test_no_enacted_law_yet(self):
        self.assertEqual(self.state("ACC-HHS-NIH-TOTAL", 2027, "Enacted")["state"], "not_enacted")
        self.assertEqual(self.state("ACC-NASA-SCIENCE", 2027, "Enacted")["state"], "not_enacted")

    def test_every_line_has_exactly_one_state(self):
        for (acct,) in self.conn.execute("SELECT canonical_account_id FROM account").fetchall():
            for row in S.history_grid(S.history(self.conn, acct))["rows"]:
                for lines in row["cells"].values():
                    for l in lines:
                        self.assertIn(l["state"], S.CELL_STATES, acct)

    def test_state_counts_per_subcommittee(self):
        for sub in ("CJS", "LHHS"):
            g = S.subcommittee_grid(self.conn, sub)
            counts = g["state_counts"]
            self.assertEqual(set(counts), set(S.CELL_STATES))
            n_rows = self.conn.execute("SELECT count(*) FROM account WHERE subcommittee = ?", (sub,)).fetchone()[0]
            self.assertEqual(sum(counts.values()), n_rows * len(g["fiscal_years"]) * len(g["stages"]))
            self.assertTrue(all(counts[st] > 0 for st in ("value", "not_funded", "missing", "not_collected", "not_enacted")))


HOUSE_PDF = ROOT / "document_store" / "CRPT-119hrpt652.pdf"
VISION_FIXTURES = ROOT / "tests" / "fixtures" / "vision_cache"


@unittest.skipUnless(HOUSE_PDF.exists(), "CRPT-119hrpt652.pdf not present")
class PipelineToStore(StoreCopyTest):
    """The vision path's Title III output goes into the store that already
    holds v12, through the same compare as advance-copy reconciliation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import extract_approps as ex
        with tempfile.TemporaryDirectory() as out:
            r = ex.run(HOUSE_PDF, title="TITLE III", cache_dir=VISION_FIXTURES, offline=True, out_dir=out, verbose=False)
        cls.result = r
        cls.rows = [row for row in (S.observation_row(o, "SRC-CRPT-119HRPT652") for o in r["observations"]) if row]

    def science(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM appropriations_observation WHERE canonical_account_id = 'ACC-NASA-SCIENCE' "
            "ORDER BY fiscal_year, stage")]

    def test_every_account_row_crosses_the_boundary(self):
        # every line item, plus the agency totals (accounts in the pilot); no
        # other rollup and no memo line
        lines = {o["observation_id"] for o in self.result["observations"] if not (o["is_rollup"] or o["is_memo"])}
        extra = {row["canonical_account_id"] for row in self.rows if row["observation_id"] not in lines}
        self.assertTrue(lines <= {row["observation_id"] for row in self.rows})
        self.assertEqual(extra, {"ACC-NASA-TOTAL", "ACC-NSF-TOTAL"})

    def test_same_facts_confirm_new_facts_add_nothing_twice(self):
        before = self.conn.execute("SELECT count(*) FROM appropriations_observation").fetchone()[0]
        out = S.add_observations(self.conn, self.rows)
        self.assertEqual({k: len(v) for k, v in out.items()}, {"confirmed": 19, "conflicting": 0, "added": 19, "held": 0})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM appropriations_observation").fetchone()[0], before + 19)
        sci = {(o["fiscal_year"], o["stage"]): o for o in self.science()}
        self.assertEqual(len(self.science()), 44)                       # 43 + FY2027 House Reported, no duplicate
        self.assertEqual((sci[(2026, "Enacted")]["observation_id"], sci[(2026, "Enacted")]["amount"]),
                         ("OBS-0081", 7_250_000_000))                   # the stored row, confirmed
        rec = self.conn.execute("SELECT result, observed_result FROM validation_record WHERE observation_id = 'OBS-0081' "
                                "AND rule_applied = 'cross_document'").fetchone()
        self.assertEqual(rec[0], "pass")
        self.assertIn("7,250,000,000 per SRC-CRPT-119HRPT652", rec[1])
        new = sci[(2027, "House Reported")]
        self.assertEqual((new["source_document_id"], new["chamber"], new["amount"] % 1000), ("SRC-CRPT-119HRPT652", "House", 0))

    def test_different_value_for_a_stored_fact_is_flagged_not_stored(self):
        rows = [dict(r, amount=r["amount"] + 1000) if r["canonical_account_id"] == "ACC-NASA-SCIENCE"
                and r["fiscal_year"] == 2026 else r for r in self.rows]
        out = S.add_observations(self.conn, rows)
        self.assertEqual(out["conflicting"], ["OBS-0081"])
        sci = {(o["fiscal_year"], o["stage"]): o for o in self.science()}
        self.assertEqual((sci[(2026, "Enacted")]["amount"], sci[(2026, "Enacted")]["verification_status"]),
                         (7_250_000_000, "flagged"))
        rec = self.conn.execute("SELECT result, human_review_status, observed_result FROM validation_record "
                                "WHERE observation_id = 'OBS-0081' AND rule_applied = 'cross_document'").fetchone()
        self.assertEqual(tuple(rec[:2]), ("flag", "pending"))
        self.assertIn("7,250,001,000", rec[2])

    def test_printed_component_maps_to_the_canonical_one(self):
        # printed 'Defense function' -> 'defense' (accounts.match_component):
        # FY2026 Enacted confirms v13's row, FY2027 House Reported is new
        defense = [r for r in self.rows if r["canonical_account_id"] == "ACC-NSF-RRA" and r["component"]]
        self.assertEqual({(r["fiscal_year"], r["stage"], r["component"]) for r in defense},
                         {(2026, "Enacted", "defense"), (2027, "House Reported", "defense")})
        out = S.add_observations(self.conn, self.rows)
        self.assertIn("OBS-0728", out["confirmed"])                     # FY2026 Enacted R&RA defense, 118,800,000
        self.assertEqual(out["held"], [])
        self.assertEqual({r[0] for r in self.conn.execute(
            "SELECT DISTINCT component FROM appropriations_observation WHERE canonical_account_id = 'ACC-NSF-RRA'")},
            {None, "defense", "supplemental_act"})

    def test_unknown_component_name_is_held(self):
        rows = [dict(r, component="Defense base") if r["canonical_account_id"] == "ACC-NSF-RRA" and r["component"]
                else r for r in self.rows]
        out = S.add_observations(self.conn, rows)
        self.assertEqual(len(out["held"]), 2)
        self.assertEqual({r[0] for r in self.conn.execute(
            "SELECT DISTINCT component FROM appropriations_observation WHERE canonical_account_id = 'ACC-NSF-RRA'")},
            {None, "defense", "supplemental_act"})

    def test_repeated_fact_in_one_batch_is_refused(self):
        with self.assertRaises(ValueError):
            S.add_observations(self.conn, self.rows + [dict(self.rows[0], observation_id="again")])

    def test_an_account_row_without_an_account_is_refused(self):
        o = {"observation_id": "x", "account_match": "ambiguous", "canonical_account_id": None,
             "account_name_as_written": "Office of Inspector General"}
        with self.assertRaises(ValueError):
            S.observation_row(o, "SRC-CRPT-119HRPT652")


@unittest.skipUnless(openpyxl, "openpyxl not installed")
class Coverage(StoreCopyTest):
    """What each account's records cover, computed (reference/review/coverage.py) --
    Account.effective_start / effective_end were removed in v33; nothing proposes a date."""

    def rows(self):
        spec = importlib.util.spec_from_file_location("coverage", ROOT / "reference" / "review" / "coverage.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.coverage(self.conn)

    def test_committed_report_is_what_the_committed_workbook_gives(self):
        self.assertEqual([{k: str(v) for k, v in r.items()} for r in self.rows()], review_csv("coverage.csv"))

    def test_first_and_last_observed_years_and_unchecked_cells(self):
        rows = {r["canonical_account_id"]: r for r in self.rows()}
        self.assertEqual(len(rows), 130)
        self.assertEqual((rows["ACC-NASA-SCIENCE"]["first_observed_fy"], rows["ACC-NASA-SCIENCE"]["last_observed_fy"]), (2017, 2026))   # FY2027: House report on file, figure not yet recorded
        # the FY2027-only mechanism accounts: every earlier cell the CJS reports cover is unchecked work
        self.assertEqual(rows["ACC-DOJ-CVF"]["first_observed_fy"], 2027)
        self.assertIn("FY2017 Enacted", rows["ACC-DOJ-CVF"]["unchecked"])
        # a cell with a figure or a confirmed absence is never unchecked
        seen = {(r[0], r[1]) for r in self.conn.execute(
            "SELECT fiscal_year, stage FROM appropriations_observation WHERE canonical_account_id = 'ACC-NASA-SCIENCE' "
            "UNION SELECT fiscal_year, stage FROM confirmed_absence WHERE canonical_account_id = 'ACC-NASA-SCIENCE'")}
        self.assertFalse([c for c in seen if f"FY{c[0]} {c[1]}" in rows["ACC-NASA-SCIENCE"]["unchecked"].split("; ")])
        self.assertNotIn("effective", " ".join(next(iter(rows.values()))))

    def test_fiscal_year_of(self):
        self.assertEqual([S.fiscal_year_of(d) for d in ("2016-10-01", "2017-09-30", "2020-09-30", "2026-10-01")],
                         [2017, 2017, 2020, 2027])


class AfterLastRecord(StoreCopyTest):
    """An account's cells run from its first record to the last fiscal year on
    file for any account: no stored end date cuts them off (v33 has none)."""

    def test_open_accounts_are_unchanged(self):
        h = S.history(self.conn, "ACC-NASA-SPACETECH")
        self.assertEqual(max(y for y, _ in h["missing_cells"]), 2027)


class NoPrintedTotal(StoreCopyTest):
    """A confirmed absence where the document still prints the account -- the
    account is a total (total_scope), or the same cell holds another of its
    lines -- is "no printed total", not "not funded"."""

    def head(self, account, fy, stage, amount_type="budget authority"):
        g = S.history_grid(S.history(self.conn, account))
        row = next(r for r in g["rows"] if r["fiscal_year"] == fy)
        return next(l for l in row["cells"][stage] if l["amount_type"] == amount_type and l["component"] is None)

    def test_fy2023_medicaid_headline_and_aspr_total(self):
        # v33's FY2023 rows: Medicaid's new advance printed beside its missing headline (S.Rept. 118-84,
        # FY2023 Enacted), and the ASPR total the FY2023 House report doesn't print
        line = self.head("ACC-HHS-CMS-MEDICAID", 2023, "Enacted")
        self.assertEqual((line["state"], line["absence"]["confirmed_absence_id"]), ("no_printed_total", "CA-LHHS-0005"))
        self.assertEqual(self.head("ACC-HHS-CMS-MEDICAID", 2023, "Enacted", "advance")["state"], "value")
        line = self.head("ACC-HHS-ASPR-TOTAL", 2023, "House Reported")
        self.assertEqual((line["state"], line["absence"]["confirmed_absence_id"]), ("no_printed_total", "CA-LHHS-0031"))

    def test_a_rescission_line_with_nothing_beside_it_is_still_none(self):
        line = self.head("ACC-HHS-GP-ADOPTION-INCENTIVES-RESCISSION", 2026, "House Reported", "rescission")
        self.assertEqual((line["state"], bool(line["absence"])), ("not_funded", True))

    def test_counted_apart(self):
        counts = S.subcommittee_grid(self.conn, "LHHS")["state_counts"]
        self.assertEqual(list(counts), list(S.CELL_STATES))
        # the FY2024 House column (100 cells) moved from not yet collected to the draft's figures and states; then the NEF rescission from H.R. 5894 (missing -> value)
        self.assertEqual(counts, {"value": 1539, "not_funded": 108, "no_printed_total": 13, "missing": 140,
                                  "not_collected": 100, "not_enacted": 100})


class ComponentStage(StoreCopyTest):
    """supplemental_act exists only at Enacted, budget_amendment only at
    President's Budget: elsewhere the line isn't listed (not missing)."""

    def lines(self, acct):
        g = S.history_grid(S.history(self.conn, acct))
        return [(row["fiscal_year"], st, line) for row in g["rows"] for st, cell in row["cells"].items() for line in cell]

    def test_structural_lines_only_in_their_stage(self):
        seen = set()
        for acct in ("ACC-NASA-EXPLORATION", "ACC-NASA-SPACETECH", "ACC-NASA-SCIENCE", "ACC-NASA-CONSTRUCTION",
                     "ACC-NASA-SAFETY-SECURITY", "ACC-NSF-RRA", "ACC-NSF-MREFC", "ACC-NSF-AGENCY-OPS", "ACC-NSF-STEM-EDUCATION"):
            for fy, st, line in self.lines(acct):
                if line["component"] in ("supplemental_act", "budget_amendment"):
                    seen.add(line["component"])
                    self.assertEqual(st, {"supplemental_act": "Enacted", "budget_amendment": "President's Budget"}[line["component"]],
                                     (acct, fy, st))
        self.assertEqual(seen, {"supplemental_act", "budget_amendment"})
        # the unstructured series are still listed in every column
        self.assertEqual({st for _, st, l in self.lines("ACC-NASA-CONSTRUCTION")
                          if l["amount_type"] == "supplemental" and l["component"] is None}, set(FOUR))

    def test_one_recorded_at_another_stage_shows_and_is_flagged(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(appropriations_observation)")]
        sel = ", ".join({"observation_id": "'OBS-X'", "stage": "'House Reported'", "chamber": "'House'"}.get(c, c) for c in cols)
        self.conn.execute(f"INSERT INTO appropriations_observation ({', '.join(cols)}) SELECT {sel} "
                          "FROM appropriations_observation WHERE observation_id = 'OBS-0660'")
        shown = [(fy, st) for fy, st, l in self.lines("ACC-NASA-CONSTRUCTION")
                 if l["component"] == "supplemental_act" and l["observations"]]
        self.assertIn((2023, "House Reported"), shown)
        self.assertIn("OBS-X: component 'supplemental_act' at stage 'House Reported' -- a supplemental_act line only "
                      "exists at Enacted", S.data_quality_warnings(self.conn))


class V22Merged(StoreTest):
    """reference/review/*_v22.csv -- sent for the workbook, merged in v23/v24 --
    are in the store as sent."""

    def test_the_48_absences_are_in_the_store_as_sent(self):
        rows = review_csv("confirmed_absence_rows_v22.csv")
        self.assertEqual((len(rows), rows[0]["confirmed_absence_id"], rows[-1]["confirmed_absence_id"]), (48, "CA-0151", "CA-0198"))
        for r in rows:
            got = self.conn.execute("SELECT canonical_account_id, fiscal_year, stage, amount_type, component, "
                                    "source_document_id, evidence FROM confirmed_absence WHERE confirmed_absence_id = ?",
                                    (r["confirmed_absence_id"],)).fetchone()
            self.assertEqual(tuple(got), (r["canonical_account_id"], int(r["fiscal_year"]), r["stage"], r["amount_type"],
                                          r["component"] or None, r["source_document_id"], r["evidence"]), r["confirmed_absence_id"])

    def test_only_the_unchecked_cells_are_still_missing(self):
        unchecked = {(r["canonical_account_id"], int(r["fiscal_year"]), r["stage"], r["amount_type"])
                     for r in review_csv("confirmed_absence_review_v22.csv") if r["verdict"] == "UNCHECKED"}
        self.assertEqual(len(unchecked), 8)
        still = set()
        for acct, t in (("ACC-NASA-SCIENCE", "rescission"), ("ACC-NASA-SPACEOPS", "rescission"), ("ACC-NSF-RRA", "supplemental"),
                        ("ACC-NASA-CONSTRUCTION", "supplemental"), ("ACC-NSF-MREFC", "supplemental")):
            for row in S.history_grid(S.history(self.conn, acct))["rows"]:
                for st, cell in row["cells"].items():
                    for l in cell:
                        if l["amount_type"] == t and l["component"] is None and l["state"] == "missing" \
                                and 2017 <= row["fiscal_year"] <= 2026:
                            still.add((acct, row["fiscal_year"], st, t))
        self.assertEqual(still, unchecked)          # only the cells whose documents this environment can't reach

    def test_the_missing_figures_are_in_with_their_citations(self):
        for r in review_csv("missing_observations_v22.csv"):
            got = self.conn.execute(
                "SELECT amount, source_document_id, source_page FROM appropriations_observation WHERE canonical_account_id = ? "
                "AND fiscal_year = ? AND stage = ? AND amount_type = ? AND component IS ?",
                (r["canonical_account_id"], int(r["fiscal_year"]), r["stage"], r["amount_type"], r["component"])).fetchall()
            self.assertEqual([tuple(g) for g in got], [(int(r["amount_dollars"]), r["source_document_id"], r["source_page"])],
                             r["canonical_account_id"])
        # the page it needed: H.Rept. 119-272's full table
        self.assertEqual(self.conn.execute("SELECT source_page FROM source_document WHERE document_id = 'SRC-CRPT-119HRPT272'"
                                           ).fetchone()[0], "244-262")


class LoadRefuses(unittest.TestCase):
    """Each mutation is something the loader must refuse rather than store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def mutate(self, edit, data_only=True):
        wb = openpyxl.load_workbook(WORKBOOK, data_only=data_only)
        edit(wb)
        path = self.dir / "wb.xlsx"
        wb.save(path)
        return path

    def cell(self, wb, tab, row_id, col):
        ws = wb[tab]
        head = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            if row[0].value == row_id:
                return row[head.index(col)]
        raise KeyError(row_id)

    def refuse(self, path, text):
        with self.assertRaises(S.LoadError) as cm:
            S.load(path, self.dir / "x.db")
        self.assertIn(text, str(cm.exception))
        self.assertFalse((self.dir / "x.db").exists())

    def test_unknown_enum_value(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Appropriations Observation", "OBS-0082",
                                                        "verification_status"), "value", "human_verified"))
        self.refuse(path, "CHECK constraint failed")

    def test_a_workbook_without_effective_dates_loads(self):
        # v33 drops Account.effective_start / effective_end; a workbook still carrying them loads the same
        head = [c.value for c in openpyxl.load_workbook(WORKBOOK, read_only=True)["Account"][1]]
        self.assertNotIn("effective_start", head)

        def add(wb):
            ws = wb["Account"]
            n = len([c.value for c in ws[1]])
            for i, col in enumerate(("effective_start", "effective_end"), start=1):
                ws.cell(row=1, column=n + i, value=col)
                ws.cell(row=2, column=n + i, value="2016-10-01" if i == 1 else None)
        db = Path(self.tmp.name) / "dates.db"
        with contextlib.redirect_stdout(io.StringIO()):
            report = S.load(self.mutate(add), db)
        self.assertEqual(report["rows"]["account"], 130)
        conn = S.connect(db, readonly=True)
        self.assertNotIn("effective_start", [r[1] for r in conn.execute("PRAGMA table_info(account)")])
        conn.close()

    def test_parent_account_id_loads(self):
        # v33 carries Account.parent_account_id: a program line's heading account
        self.assertEqual(dict(self.conn_of(S.load, WORKBOOK)), {
            "ACC-HHS-HRSA-HEALTH-CENTERS": "ACC-HHS-HRSA-PRIMARY-CARE", "ACC-HHS-ACF-HEAD-START": "ACC-HHS-ACF-CFSP"})

    def conn_of(self, load, path):
        db = Path(self.tmp.name) / "parent.db"
        with contextlib.redirect_stdout(io.StringIO()):
            load(path, db)
        conn = S.connect(db, readonly=True)
        try:
            return conn.execute("SELECT canonical_account_id, parent_account_id FROM account "
                                "WHERE parent_account_id IS NOT NULL").fetchall()
        finally:
            conn.close()

    def test_a_parent_that_is_not_an_account_is_refused(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Account", "ACC-NASA-SCIENCE", "parent_account_id"),
                                              "value", "ACC-NOT-THERE"))
        self.refuse(path, "FOREIGN KEY constraint failed")

    def test_a_workbook_without_total_scope_is_refused(self):
        # without the column every total would load as a plain account, silently
        def drop(wb):
            ws = wb["Account"]
            ws.delete_cols([c.value for c in ws[1]].index("total_scope") + 1)
        self.refuse(self.mutate(drop), "missing ['total_scope']")

    def test_unknown_total_scope(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Account", "ACC-NASA-TOTAL", "total_scope"), "value", "grand"))
        self.refuse(path, "not a total scope")

    def test_dangling_reference(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Appropriations Observation", "OBS-0082",
                                                        "source_document_id"), "value", "SRC-NOT-THERE"))
        self.refuse(path, "FOREIGN KEY constraint failed")

    def test_boolean_that_is_not_one(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Historical Name", "HN-0001", "human_reviewed"),
                                              "value", "yes"))
        self.refuse(path, "not a boolean")

    def test_waived_check_still_reports_its_problems(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Account", "ACC-NASA-EXPLORATION", "historical_names"),
                                              "value", None))
        report = S.load(path, self.dir / "x.db", waive=("historical_names_display",))
        self.assertEqual(report["waived"], {"historical_names_display": [
            "ACC-NASA-EXPLORATION: Account.historical_names [] != Historical Name ['Deep Space Exploration Systems']"]})

    def test_full_table_ranges_clear_the_page_warnings(self):
        # the full comparative-table ranges (as v20 records them) clear every
        # page warning; a page outside its document's table still warns
        def edit(wb):
            for doc, rng in (("SRC-CRPT-118SRPT62", "212-225"), ("SRC-CRPT-117HRPT395", "210-230"),
                             ("SRC-CRPT-115HRPT704", "120-135")):
                self.cell(wb, "Source Document", doc, "source_page").value = rng
            self.cell(wb, "Appropriations Observation", "OBS-0987", "source_page").value = "300"      # outside
        report = S.load(self.mutate(edit), self.dir / "x.db")
        self.assertEqual(report["warnings"],
                         ["observation OBS-0987: source_page '300' is outside SRC-CRPT-116HRPT455's table pages '178-197'"])

    def test_display_list_drift(self):
        path = self.mutate(lambda wb: setattr(self.cell(wb, "Account", "ACC-NASA-EXPLORATION", "historical_names"),
                                              "value", "Deep Space Exploration Systems (FY2024 JES and earlier)"))
        self.refuse(path, "Account.historical_names")

    def test_formula_values_lost_on_save(self):
        # openpyxl (or any tool that doesn't recalculate) drops the cached
        # results of the bill_id/report_id lookup formulas on save
        path = self.mutate(lambda wb: None, data_only=False)
        self.refuse(path, "Bill Report Reference says")

    def test_failed_load_leaves_the_existing_database(self):
        db = self.dir / "x.db"
        S.load(WORKBOOK, db)
        before = db.read_bytes()
        bad = self.mutate(lambda wb: setattr(self.cell(wb, "Appropriations Observation", "OBS-0082",
                                                       "source_document_id"), "value", "SRC-NOT-THERE"))
        with self.assertRaises(S.LoadError):
            S.load(bad, db)
        self.assertEqual(db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
