"""
The relational store and its query (approps_store.py, store_schema.sql),
loaded from the committed pilot workbook (reference/..._v13.xlsx).

Run:  python -m unittest tests.test_store -v

Needs openpyxl (loading only).
"""

import contextlib
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

WORKBOOK = ROOT / "reference" / "CJS_Title_III_Science_Pilot_Schema_Loaded_v13.xlsx"
FOUR = ["President's Budget", "House Reported", "Senate Reported", "Enacted"]


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
        self.assertEqual(self.report["rows"], {
            "account": 31, "historical_name": 6, "source_document": 23, "bill_report_reference": 41,
            "appropriations_observation": 977, "account_relationship": 2, "validation_record": 89})

    def test_v13_loads_clean_with_nothing_waived(self):
        self.assertEqual(self.report["waived"], {})
        self.assertEqual(self.report["warnings"], [
            "source_document SRC-CBO-HR8845-FY2027: also_covers entry is not a fiscal year + stage: "
            "'Every account in this Mechanism Coverage Pass (Titles I, II, V, VII of HR-8845)'",
            "bill_report_reference BR-CJS-FY2027-HOUSE: bill_url, report_jes_url blank"])

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
        self.assertEqual(self.report["value_map"],
                         {"Appropriations Observation.extraction_method: 'human_entered' -> 'human-entered'": 977})

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
        # but kept H.Rept. 119-652's pages; v13 fixed them
        self.assertEqual([w for w in self.report["warnings"] if "outside" in w], [])

    def test_blank_request_is_missing_not_zero(self):
        # v13 dropped the $0 rows where the request column prints '---'
        for acct, cells in (("ACC-NASA-STEM-ENGAGEMENT", [(2019, "President's Budget"), (2020, "President's Budget")]),
                            ("ACC-NASA-SPACETECH", [(2019, "President's Budget"), (2019, "House Reported"),
                                                    (2020, "President's Budget")])):
            h = S.history(self.conn, acct)
            self.assertTrue(set(cells) <= set(h["missing_cells"]), acct)


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
        self.assertEqual(len(got), 40)
        self.assertEqual({(o["fiscal_year"], o["stage"]) for o in h["observations"]},
                         {(y, s) for y in range(2017, 2027) for s in FOUR})

    def test_fy2027_is_reported_missing_not_zero(self):
        _, h = self.query("NASA Science")
        self.assertEqual(h["missing_cells"], [(2027, s) for s in FOUR])

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
        self.assertEqual(len(obs), 80)
        by = {(o["fiscal_year"], o["stage"], o["amount_type"]): o["amount"] for o in obs}
        self.assertEqual({t for (_, _, t) in by}, {"budget authority", "supplemental"})
        self.assertEqual(by[(2024, "Enacted", "budget authority")], 7_216_200_000)
        self.assertEqual(by[(2024, "Enacted", "supplemental")], 450_000_000)
        self.assertEqual(by[(2025, "Senate Reported", "supplemental")], 1_212_000_000)

    def test_former_name_reaches_the_same_account(self):
        for text, kind in (("Deep Space Exploration Systems", "exact"), ("Deep Spaee Exploratlon Systems", "ocr_corrected")):
            res, h = self.query(text)
            self.assertEqual((res["account"]["canonical_account_id"], res["match"], res["via"]),
                             ("ACC-NASA-EXPLORATION", kind, "historical_name"))
            self.assertEqual(len(h["observations"]), 80)

    def test_relationship_is_shown_not_merged(self):
        _, h = self.query("Exploration")
        self.assertEqual([(r["relationship_id"], r["other_account_id"]) for r in h["relationships"]],
                         [("REL-0006", "ACC-NASA-EXPLTECH")])
        self.assertNotIn("ACC-NASA-EXPLTECH", {o["canonical_account_id"] for o in h["observations"]})

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
        self.assertEqual([c["canonical_account_id"] for c in res["candidates"]][:3],
                         ["ACC-DOJ-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"])
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
    """v12 has no open identity question, so each test sets one up: the three
    Exploration Technology observations flagged at 0.5 with one account_identity
    finding (the v10 state), under v12's REL-0005 (Space Technology ->
    Exploration Technology, uncertain, unreviewed)."""

    EXPLTECH = ["OBS-0522", "OBS-0523", "OBS-0524"]

    def setUp(self):
        super().setUp()
        with self.conn:
            self.conn.execute("UPDATE appropriations_observation SET verification_status = 'flagged', confidence = 0.5 "
                              "WHERE canonical_account_id = 'ACC-NASA-EXPLTECH'")
            self.conn.execute("INSERT INTO validation_record (validation_id, observation_id, rule_applied, result, "
                              "human_review_status) VALUES ('VAL-ID', 'OBS-0522', 'account_identity', 'flag', 'pending')")

    def test_a_resolution_that_reached_one_observation_is_reported(self):
        self.conn.execute("UPDATE validation_record SET human_review_status = 'resolved' WHERE validation_id = 'VAL-ID'")
        self.assertEqual(S.stale_resolution_warnings(self.conn), [
            "validation_record VAL-ID resolves ACC-NASA-EXPLTECH's identity, but its own observation is still "
            "flagged (OBS-0522); 2 other observation(s) of the account are still flagged with no resolved record: "
            "OBS-0523, OBS-0524"])
        self.assertEqual([w for w in self.report["warnings"] if "resolves" in w], [])       # v12 itself: none

    def test_resolution_reaches_every_observation_it_decides(self):
        spacetech_before = self.conn.execute("SELECT count(*) FROM validation_record v JOIN appropriations_observation o "
                                             "USING (observation_id) WHERE o.canonical_account_id = 'ACC-NASA-SPACETECH'"
                                             ).fetchone()[0]
        s = S.resolve_relationship(self.conn, "REL-0005", "Reviewer", "distinct proposed line")
        self.assertEqual(s["observations"], self.EXPLTECH)
        self.assertEqual(s["records_updated"], ["VAL-ID"])
        self.assertEqual(s["records_created"], ["VAL-REL-0005-OBS-0523", "VAL-REL-0005-OBS-0524"])
        self.assertEqual(self.flagged("ACC-NASA-EXPLTECH"), [])
        self.assertEqual({r[0] for r in self.conn.execute(
            "SELECT confidence FROM appropriations_observation WHERE canonical_account_id = 'ACC-NASA-EXPLTECH'")}, {0.95})
        for oid in s["observations"]:
            r = self.conn.execute("SELECT human_review_status, reviewer, resolution FROM validation_record "
                                  "WHERE observation_id = ? AND rule_applied = 'account_identity'", (oid,)).fetchall()
            self.assertEqual([tuple(x) for x in r], [("resolved", "Reviewer", "distinct proposed line")], oid)
        self.assertEqual(self.conn.execute("SELECT human_reviewed FROM account_relationship "
                                           "WHERE relationship_id = 'REL-0005'").fetchone()[0], 1)
        # the relationship's other account: its 37 verified rows weren't waiting on it
        self.assertEqual(self.conn.execute("SELECT count(*) FROM validation_record v JOIN appropriations_observation o "
                                           "USING (observation_id) WHERE o.canonical_account_id = 'ACC-NASA-SPACETECH'"
                                           ).fetchone()[0], spacetech_before)
        self.assertEqual({r[0] for r in self.conn.execute("SELECT DISTINCT confidence FROM appropriations_observation "
                                                          "WHERE canonical_account_id = 'ACC-NASA-SPACETECH'")}, {1.0})
        self.assertEqual(S.stale_resolution_warnings(self.conn), [])

    def test_another_open_finding_keeps_its_observation_flagged(self):
        self.conn.execute("INSERT INTO validation_record (validation_id, observation_id, rule_applied, result) "
                          "VALUES ('VAL-X', 'OBS-0523', 'table_total', 'fail')")
        self.conn.commit()
        s = S.resolve_relationship(self.conn, "REL-0005", "Reviewer", "distinct proposed line")
        self.assertEqual(s["still_flagged"], ["OBS-0523"])
        self.assertEqual(self.conn.execute("SELECT confidence FROM appropriations_observation "
                                           "WHERE observation_id = 'OBS-0523'").fetchone()[0], 0.5)
        self.assertEqual(self.flagged("ACC-NASA-EXPLTECH"), ["OBS-0523"])

    def test_refused_resolution_changes_nothing(self):
        with self.assertRaises(LookupError):
            S.resolve_relationship(self.conn, "REL-9999", "Reviewer", "x")
        with self.assertRaises(ValueError):
            S.resolve_relationship(self.conn, "REL-0005", "", "x")
        self.assertEqual(self.flagged("ACC-NASA-EXPLTECH"), self.EXPLTECH)


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
        used = {r[0] for r in self.conn.execute("SELECT DISTINCT component FROM appropriations_observation")}
        self.assertEqual(used, {None} | set(accounts.COMPONENTS))

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
        self.assertEqual(len(self.science()), 41)                       # 40 + FY2027 House Reported, no duplicate
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
            {None, "defense"})

    def test_unknown_component_name_is_held(self):
        rows = [dict(r, component="Defense base") if r["canonical_account_id"] == "ACC-NSF-RRA" and r["component"]
                else r for r in self.rows]
        out = S.add_observations(self.conn, rows)
        self.assertEqual(len(out["held"]), 2)
        self.assertEqual({r[0] for r in self.conn.execute(
            "SELECT DISTINCT component FROM appropriations_observation WHERE canonical_account_id = 'ACC-NSF-RRA'")},
            {None, "defense"})

    def test_repeated_fact_in_one_batch_is_refused(self):
        with self.assertRaises(ValueError):
            S.add_observations(self.conn, self.rows + [dict(self.rows[0], observation_id="again")])

    def test_an_account_row_without_an_account_is_refused(self):
        o = {"observation_id": "x", "account_match": "ambiguous", "canonical_account_id": None,
             "account_name_as_written": "Office of Inspector General"}
        with self.assertRaises(ValueError):
            S.observation_row(o, "SRC-CRPT-119HRPT652")


@unittest.skipUnless(openpyxl, "openpyxl not installed")
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
