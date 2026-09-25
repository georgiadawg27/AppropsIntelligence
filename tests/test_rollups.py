"""
Rollup classification and account-rollup grouping.

Run:  python -m unittest tests.test_rollups -v

Rollups are recognised by label text only (leading Total / Subtotal / Grand
total, a trailing "<account> Total", the exact label "Direct appropriation"),
never by indentation or a model-judged rule line. Account rollups sum one
account's own lines, and when that grouping comes from model-read
indentation, the rollup's arithmetic is what confirms it.

The whole-table tests use tests/fixtures/vision_cache_recorded: live Claude
transcriptions of all 17 comparative-table pages of H.Rept. 119-652
(recorded 2026-09-25, with the classify pass for all 38 image-only pages),
so they run offline.
"""

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import extract_approps as ex  # noqa: E402
import validate_approps as va  # noqa: E402

STORE = ROOT / "document_store"
RECORDED = ROOT / "tests" / "fixtures" / "vision_cache_recorded"
COLS = ["FY 2026 Enacted", "Bill", "Bill vs. Enacted"]


def row(label, enacted="", bill="", indent=0, rule="none"):
    delta = ""
    if enacted and bill and not enacted.startswith("(") and "---" not in (enacted, bill):
        d = int(bill.replace(",", "")) - int(enacted.replace(",", ""))
        delta = "---" if d == 0 else f"{d:+,}"
    return {"label": label, "indent": indent, "is_heading": not (enacted or bill), "rule_above": rule,
            "values": [enacted, bill, delta], "raw_text": f"{label} {enacted} {bill} {delta}"}


def run_rows(rows, source="claude_api"):
    """Hierarchy + observations + validation for a synthetic page."""
    parsed = [(1, r, [ex.parse_cell(v) for v in r["values"]]) for r in rows]
    ex.Node._seq = 0
    nodes = ex.build_hierarchy(parsed, table_starts_with_title=True)
    cols = ex.classify_columns(COLS, 2027, "House Reported")
    page_meta = {1: {"extraction_method": "AI-extracted", "source": source, "units_declared": "(Amounts in thousands)",
                     "units_parsed": "thousands"}}
    doc = ex.describe_package("CRPT-119hrpt652")
    obs = ex.build_observations(nodes, cols, "thousands", page_meta, doc, "T")
    records, summary = va.validate(nodes, cols, obs, page_meta, "thousands")
    return nodes, obs, records, summary


def by_label(nodes, label):
    return [n for n in nodes if n.label == label]


class LabelOnlyClassification(unittest.TestCase):
    def kind(self, label, rule="none"):
        r = row(label, "10", "10", rule=rule)
        return ex.classify_row(r, [ex.parse_cell(v) for v in r["values"]])

    def test_keyword_forms_seen_in_real_tables(self):
        for label, kind in (("Total, National Science Foundation", "total"),
                            ("Total Department of Justice", "total"),        # no comma (S.Rept. 119-44)
                            ("Total , Salaries and expenses", "total"),      # stray space (S.Rept. 118-62)
                            ("Subtotal", "subtotal"),
                            ("Subtotal, Exploration", "subtotal"),
                            ("Grand total", "grand_total"),
                            ("Grand total excluding Other Appropriations", "grand_total"),
                            ("Direct appropriation", "subtotal"),
                            ("OIG Total", "subtotal")):
            self.assertEqual(self.kind(label), kind, label)

    def test_a_rule_line_no_longer_makes_a_subtotal(self):
        self.assertEqual(self.kind("Science", rule="single"), "line")
        self.assertEqual(self.kind("Direct appropriations for something", rule="single"), "line")

    def test_account_rollup_labels(self):
        self.assertTrue(ex.is_account_rollup("Direct appropriation"))
        self.assertTrue(ex.is_account_rollup("OIG Total"))
        for label in ("Total, Legal Activities", "Subtotal", "Grand total", "Totalizer program",
                      "Direct appropriations", "Science"):
            self.assertFalse(ex.is_account_rollup(label), label)


class AccountRollupGrouping(unittest.TestCase):
    def test_house_style_offset_nested_under_its_account(self):
        # H.Rept. 119-652 p162: EOIR net of a fee-account transfer
        nodes, obs, records, s = run_rows([
            row("TITLE II - DEPARTMENT OF JUSTICE"),
            row("Office of Inspector General", "139,000", "139,000"),
            row("Executive Office for Immigration Review", "800,000", "800,000"),
            row("Transfer from immigration examinations fee account", "-10,000", "-10,000", indent=1),
            row("Direct appropriation", "790,000", "790,000", indent=2),
        ])
        da = by_label(nodes, "Direct appropriation")[0]
        self.assertEqual([c.label for c in da.children],
                         ["Executive Office for Immigration Review", "Transfer from immigration examinations fee account"])
        self.assertEqual(da.match, "single_account_nested")
        self.assertEqual(da.path, ["Executive Office for Immigration Review", "Direct appropriation"])
        self.assertEqual(s["failures"], 0)
        nest = [r for r in records if "model-read indent" in r["expected_result"]]
        self.assertTrue(nest and all(r["result"] == "pass" for r in nest))   # confirmed by the rollup
        transfer = [o for o in obs if o["account_name_as_written"].startswith("Transfer from")]
        self.assertTrue(all(o["verification_reason"] is None for o in transfer))

    def test_drifted_indent_fails_loudly(self):
        # same rows, but the offset read at the account's indent: no nesting,
        # so the rollup takes every line since the heading and doesn't add up
        nodes, obs, records, s = run_rows([
            row("TITLE II - DEPARTMENT OF JUSTICE"),
            row("Office of Inspector General", "139,000", "139,000"),
            row("Executive Office for Immigration Review", "800,000", "800,000"),
            row("Transfer from immigration examinations fee account", "-10,000", "-10,000", indent=0),
            row("Direct appropriation", "790,000", "790,000", indent=2),
        ])
        self.assertGreater(s["failures"], 0)
        da = [o for o in obs if o["account_name_as_written"] == "Direct appropriation"]
        self.assertTrue(all(o["verification_status"] == "flagged" for o in da))

    def test_senate_style_flat_lines(self):
        # S.Rept. 119-44 p211: ITA, nothing nested, all three lines net
        nodes, obs, records, s = run_rows([
            row("International Trade Administration"),
            row("Operations and administration", "573,000", "605,000"),
            row("Operations and Administration (emergency)", "50,000", "---"),
            row("Offsetting fee collections", "-12,000", "-12,000"),
            row("Direct appropriation", "611,000", "593,000", indent=3),
        ], source="text_layer")
        da = by_label(nodes, "Direct appropriation")[0]
        self.assertEqual(len(da.children), 3)
        self.assertEqual(da.match, "single_account")
        self.assertEqual(s["failures"], 0)

    def test_account_total_takes_its_account_not_the_memo(self):
        # H.Rept. 119-652 p161: OIG Total, then the frame's own total
        nodes, obs, records, s = run_rows([
            row("Departmental Management"),
            row("Salaries and expenses", "92,500", "87,700"),
            row("Renovation and Modernization", "1,142", "1,142"),
            row("Office of Inspector General", "48,000", "48,000"),
            row("(by transfer)", "---", "(2,450)", indent=1),
            row("OIG Total", "48,000", "48,000", indent=1),
            row("Total, Departmental Management", "141,642", "136,842", indent=1),
        ])
        oig = by_label(nodes, "OIG Total")[0]
        self.assertEqual([c.label for c in oig.children], ["Office of Inspector General"])
        total = by_label(nodes, "Total, Departmental Management")[0]
        self.assertEqual([c.label for c in total.children], ["Salaries and expenses", "Renovation and Modernization", "OIG Total"])
        self.assertEqual(s["failures"], 0)

    def test_a_bare_subtotal_still_cannot_confirm_nesting(self):
        # NSF: Defense function indented under R&RA, closed by a bare Subtotal
        nodes, obs, records, s = run_rows([
            row("National Science Foundation"),
            row("Research and related activities", "7,057,700", "6,321,069"),
            row("Defense function", "118,800", "119,071", indent=1),
            row("Subtotal", "7,176,500", "6,440,140", indent=1),
        ])
        self.assertEqual(s["failures"], 0)
        defense = [o for o in obs if o["account_name_as_written"] == "Defense function"]
        self.assertEqual({o["verification_reason"] for o in defense}, {"hierarchy from model-read indent only"})


class GrandTotals(unittest.TestCase):
    """A grand total never counts another grand total as a child. Before the
    fix the second one summed the first -- double on most tables, and on S.Rept.
    119-44 a pass that only compared one printed grand total with the other."""

    def rows(self):
        return [row("TITLE I - COMMERCE"), row("Salaries", "100", "90"), row("Total, title I, Commerce", "100", "90"),
                row("TITLE II - JUSTICE"), row("Salaries", "50", "60"), row("Total, title II, Justice", "50", "60"),
                row("OTHER APPROPRIATIONS"), row("Disaster relief", "30", "---"),
                row("Total, Other Appropriations", "30", "---"),
                row("Grand total", "180", "150"),
                row("Grand total excluding Other Appropriations", "150", "150")]

    def test_children(self):
        nodes, obs, records, s = run_rows(self.rows())
        g1, g2 = by_label(nodes, "Grand total")[0], by_label(nodes, "Grand total excluding Other Appropriations")[0]
        self.assertEqual([c.label for c in g1.children],
                         ["Total, title I, Commerce", "Total, title II, Justice", "Total, Other Appropriations"])
        self.assertEqual([c.label for c in g2.children], ["Total, title I, Commerce", "Total, title II, Justice"])
        self.assertEqual(s["failures"], 0)


class GrandTotalFalsePassSRpt119_44(unittest.TestCase):
    """The minimal slice of S.Rept. 119-44's real table (rows as text_tables.py
    extracts them, pp.211-223) on which the old rule passed "Grand total
    excluding Other Appropriations" [Committee recommendation]: its children
    were the IIJA total (blank) plus the grand total above it, which equals
    it in that column. Only the grand total can make it add up here, so it
    must not pass."""

    ROWS = [(211, {"label": "TITLE I\u2014DEPARTMENT OF COMMERCE", "indent": 19, "is_heading": True, "rule_above": "none",
                   "values": ["", "", ""], "raw_text": "TITLE I\u2014DEPARTMENT OF COMMERCE"}),
            (219, {"label": "TITLE V\u2014GENERAL PROVISIONS", "indent": 20, "is_heading": True, "rule_above": "none",
                   "values": ["", "", ""], "raw_text": "TITLE V\u2014GENERAL PROVISIONS"}),
            (222, {"label": "Total, Infrastructure Investment and Jobs Act, 2022", "indent": 3, "is_heading": False,
                   "rule_above": "single", "values": ["....", "....", "...."],
                   "raw_text": "Total, Infrastructure Investment and Jobs Act, 2022 .... .... ...."}),
            (223, {"label": "Grand total", "indent": 0, "is_heading": False, "rule_above": "none",
                   "values": ["75,587,164", "82,648,000", "+7,060,836"],
                   "raw_text": "Grand total 75,587,164 82,648,000 +7,060,836"}),
            (223, {"label": "Grand total excluding Other Appropriations", "indent": 0, "is_heading": False,
                   "rule_above": "none", "values": ["72,200,500", "82,648,000", "+10,447,500"],
                   "raw_text": "Grand total excluding Other Appropriations 72,200,500 82,648,000 +10,447,500"})]
    HEADERS = ["2025 appropriation", "Committee recommendation",
               "Senate Committee recommendation compared with (+ or -) 2025 appropriation"]

    def test_does_not_pass_by_counting_the_grand_total_above(self):
        parsed = [(p, r, [ex.parse_cell(v) for v in r["values"]]) for p, r in self.ROWS]
        ex.Node._seq = 0
        nodes = ex.build_hierarchy(parsed, table_starts_with_title=True)
        excl = by_label(nodes, "Grand total excluding Other Appropriations")[0]
        self.assertNotIn("grand_total", {c.kind for c in excl.children})
        cols = ex.classify_columns(self.HEADERS, 2026, "Senate Reported")
        meta = {p: {"source": "text_layer", "extraction_method": "text-extracted", "units_declared": "[In thousands of dollars]",
                    "units_parsed": "thousands"} for p, _ in self.ROWS}
        obs = ex.build_observations(nodes, cols, "thousands", meta, ex.describe_package("CRPT-119srpt44"), "T")
        _, s = va.validate(nodes, cols, obs, meta, "thousands")
        line = next(l for l in s["rollup_lines"] if "excluding" in l and "[Committee recommendation]" in l)
        self.assertFalse(line.startswith("PASS"), line)


class RecordedHouseTable(unittest.TestCase):
    """All 17 table pages of H.Rept. 119-652 as Claude actually read them."""

    @classmethod
    def setUpClass(cls):
        if not (STORE / "CRPT-119hrpt652.pdf").exists():
            raise unittest.SkipTest("CRPT-119hrpt652.pdf not present")
        with tempfile.TemporaryDirectory() as out:
            cls.result = ex.run(STORE / "CRPT-119hrpt652.pdf", cache_dir=RECORDED, offline=True, out_dir=out, verbose=False)

    def records_for(self, label):
        ids = {o["observation_id"] for o in self.result["observations"] if o["account_name_as_written"] == label}
        return [r for r in self.result["validation_records"] if r["observation_id"] in ids and "sum of" in r["expected_result"]]

    def test_every_account_rollup_adds_up(self):
        for label, n in (("Direct appropriation", 8), ("OIG Total", 2)):     # rows x 2 value columns
            recs = self.records_for(label)
            self.assertEqual(len(recs), n, label)
            self.assertTrue(all(r["result"] == "pass" for r in recs), label)

    def test_rollups_they_feed_now_reconcile(self):
        for label in ("Total, title I, Department of Commerce", "Total, Departmental Management", "Total, Legal Activities"):
            recs = self.records_for(label)
            self.assertTrue(recs and all(r["result"] == "pass" for r in recs), label)

    def test_offsets_nested_under_their_account_are_confirmed(self):
        confirmed = {o["account_name_as_written"] for o in self.result["observations"]
                     for r in self.result["validation_records"]
                     if r["observation_id"] == o["observation_id"] and "model-read indent" in r["expected_result"]
                     and r["result"] == "pass"}
        self.assertEqual(confirmed, {"Transfer from immigration examinations fee account",
                                     "Offsetting fee collections - current year", "Offsetting fee collections"})
        unconfirmed = {o["account_name_as_written"] for o in self.result["observations"]
                       if "hierarchy from model-read indent only" in (o["verification_reason"] or "").split("; ")}
        self.assertEqual(unconfirmed, {"Defense function", "Diversion control fund"})

    def test_observation_ids_unique(self):
        ids = Counter(o["observation_id"] for o in self.result["observations"])
        self.assertEqual([k for k, v in ids.items() if v > 1], [])


class SenateTables(unittest.TestCase):
    def test_direct_appropriation_rows_add_up(self):
        for pid, n in (("CRPT-119srpt44", 4), ("CRPT-118srpt62", 4)):
            if not (STORE / f"{pid}.pdf").exists():
                self.skipTest(f"{pid}.pdf not present")
            with tempfile.TemporaryDirectory() as out:
                r = ex.run(STORE / f"{pid}.pdf", live=True, out_dir=out, verbose=False)
            rows = {(o["source_page"], o["account_path"]) for o in r["observations"]
                    if o["account_name_as_written"] == "Direct appropriation"}
            self.assertEqual(len(rows), n, pid)
            ids = {o["observation_id"] for o in r["observations"] if o["account_name_as_written"] == "Direct appropriation"}
            recs = [x for x in r["validation_records"] if x["observation_id"] in ids and "sum of" in x["expected_result"]]
            self.assertTrue(recs and all(x["result"] == "pass" for x in recs), pid)
            self.assertEqual([k for k, v in Counter(o["observation_id"] for o in r["observations"]).items() if v > 1], [])


if __name__ == "__main__":
    unittest.main()
