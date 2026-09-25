"""
Tests for the text path (text_tables.py): comparative statements typeset as
real text, confirmed for Senate committee reports.

Run:  python -m unittest tests.test_text_tables -v

Needs document_store/CRPT-119srpt44.pdf and CRPT-118srpt62.pdf (the
session-start hook fetches them through govinfo_ingest.fetch_and_store).

Every run here is live (live=True: no cache is read) and makes no API call:
the Anthropic client is patched to fail the test if anything reaches for it,
and ANTHROPIC_API_KEY is removed from the environment.

Acceptance tests A and B compare against the CJS Title III pilot figures in
tests/ground_truth/*_title_iii.json -- exact match, as for the House test.
They skip, naming what's missing, until those figures are filled in.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import extract_approps as ex  # noqa: E402

STORE = ROOT / "document_store"
GT = ROOT / "tests" / "ground_truth"


def run_text_only(package_id, title="TITLE III"):
    """Live run with the API made unreachable."""
    def no_api():
        raise AssertionError("the text path must not create an Anthropic client")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as out, mock.patch.object(ex, "_client", side_effect=no_api), \
            mock.patch.dict(os.environ, env, clear=True):
        return ex.run(STORE / f"{package_id}.pdf", title=title, live=True, out_dir=out, verbose=False)


def by_key(result):
    return {(o["account_path"], o["column_header"]): o for o in result["observations"]}


class SenateTitleIII:
    """Shared checks; subclasses set the document and what its table holds."""
    package_id = None
    table_pages = None
    header_pages = None          # pages that carry their own header box
    headers = None               # [(header, kind, stage, fiscal_year)]

    @classmethod
    def setUpClass(cls):
        pdf = STORE / f"{cls.package_id}.pdf"
        if not pdf.exists():
            raise unittest.SkipTest(f"{pdf} not present -- fetch it with govinfo_ingest.fetch_and_store")
        cls.result = run_text_only(cls.package_id)
        cls.ex = cls.result["extraction"]

    # 6. all-text document: routed entirely through text, no API key needed
    def test_all_text_no_api(self):
        routes = self.result["page_routing"]
        self.assertTrue(all(r["route"] == "text" for r in routes))
        self.assertEqual(self.ex["vision_calls_this_run"], [])
        self.assertEqual({v["source"] for v in self.ex["page_sources"].values()}, {"text_layer"})
        self.assertEqual({o["extraction_method"] for o in self.result["observations"]}, {"text-extracted"})

    # 1. table pages found by header pattern + layout (not the table of contents)
    def test_table_pages(self):
        self.assertEqual(self.ex["text_table_pages"], self.table_pages)

    # 2. column meaning read from the table's own header row
    def test_columns_from_header(self):
        got = [(c["header"], c["kind"], c["stage"], c["fiscal_year"]) for c in self.ex["columns"]]
        self.assertEqual(got, self.headers)

    # 3. header only on the left page of a spread; right pages inherit it
    def test_right_pages_inherit_header(self):
        src = {int(p): m["column_headers_from_page"] for p, m in self.ex["page_sources"].items()}
        want = {p: (p if p in self.header_pages else p - 1) for p in self.table_pages}
        self.assertEqual(src, want)

    # 4. sign glyphs: decoding checked against arithmetic on every page
    def test_sign_glyph_mapping_holds_on_every_page(self):
        check = self.ex["sign_glyph_check"]
        self.assertEqual(sorted(int(p) for p in check), self.table_pages)
        for p, v in check.items():
            self.assertEqual(v["disagree"], {}, f"p{p}: {v}")
        total = {}
        for v in check.values():
            for k, n in v["agree"].items():
                total[k] = total.get(k, 0) + n
        self.assertGreater(total.get("∂->+", 0), 20)
        self.assertGreater(total.get("¥->-", 0), 20)
        self.assertGreaterEqual(sum(1 for v in check.values() if v["agree"]), len(self.table_pages) - 2)

    # 5. dot-leader blanks: not zero, not missing
    def test_leader_blank_is_blank(self):
        path, col = self.leader_blank_cell
        self.assertNotIn((path, col), by_key(self.result))       # no observation for a blank...
        emergency_rows = [o for o in self.result["observations"] if o["account_path"] == path]
        self.assertTrue(emergency_rows)                           # ...but the row itself was read
        self.assertFalse(any(o["amount_is_dash_zero"] for o in emergency_rows))

    def test_title_iii_validation_clean(self):
        s = self.result["validation_summary"]
        self.assertEqual(s["failures"], 0, [l for l in s["rollup_lines"] if not l.startswith("PASS")])
        self.assertEqual(s["by_rule"]["table_total"], {"pass": self.table_totals})
        self.assertEqual({o["title"] for o in self.result["observations"]}, {"TITLE III—SCIENCE"})

    # Acceptance test: exact match against the pilot workbook
    def test_acceptance_pilot_ground_truth(self):
        gt = GT / f"{self.package_id}_title_iii.json"
        missing = ex.missing_ground_truth(gt)
        if missing:
            self.skipTest(f"{len(missing)} pilot figures not supplied in {gt.name} (e.g. {missing[0]})")
        ok, rows = ex.compare_ground_truth(self.result, gt)
        exact = [n for n, w, g, m in rows if m and isinstance(g, int)]
        blank = [n for n, w, g, m in rows if m and g == ex.BLANK]
        absent = [n for n, w, g, m in rows if m and g == ex.NOT_PRINTED]
        print(f"\n{self.package_id}: {len(rows)} pilot figures -- {len(exact)} exact numeric matches, "
              f"{len(blank)} pilot 0 vs printed blank, {len(absent)} pilot 0 with no row printed, "
              f"{sum(1 for r in rows if not r[3])} mismatches")
        for n, w, g, m in rows:
            if not isinstance(g, int) or not m:
                print(f"    {'ok  ' if m else 'FAIL'} {n}: pilot {w:,} / extracted {g if not isinstance(g, int) else f'{g:,}'}")
        self.assertEqual([(n, w, g) for n, w, g, m in rows if not m], [])


class TestA_SRpt119_44(SenateTitleIII, unittest.TestCase):
    """S.Rept. 119-44, FY2026 CJS Senate Reported: 3 columns, no Budget Request."""
    package_id = "CRPT-119srpt44"
    table_pages = list(range(211, 224))
    header_pages = [211, 212, 214, 216, 218, 220, 222]
    headers = [
        ("2025 appropriation", "value", "Enacted", 2025),
        ("Committee recommendation", "value", "Senate Reported", 2026),
        ("Senate Committee recommendation compared with (+ or -) 2025 appropriation", "delta", None, None),
    ]
    table_totals = 6
    leader_blank_cell = ("National Aeronautics and Space Administration / Exploration (emergency)",
                         "Committee recommendation")


class TestB_SRpt118_62(SenateTitleIII, unittest.TestCase):
    """S.Rept. 118-62, FY2024 CJS Senate Reported: 5 columns incl. Budget estimate."""
    package_id = "CRPT-118srpt62"
    table_pages = list(range(212, 226))
    header_pages = [212, 214, 216, 218, 220, 222, 224]
    headers = [
        ("2023 appropriation", "value", "Enacted", 2023),
        ("Budget estimate", "value", "President's Budget", 2024),
        ("Committee recommendation", "value", "Senate Reported", 2024),
        ("Senate Committee recommendation compared with (+ or -) 2023 appropriation", "delta", None, None),
        ("Senate Committee recommendation compared with (+ or -) Budget estimate", "delta", None, None),
    ]
    table_totals = 9
    leader_blank_cell = ("National Aeronautics and Space Administration / Deep Space Exploration Systems / "
                         "Deep Space Exploration Systems (emergency)", "2023 appropriation")

    def test_delta_columns_pair_with_the_right_value_columns(self):
        cols = self.ex["columns"]
        self.assertEqual([(c.get("minuend_index"), c.get("subtrahend_index")) for c in cols if c["kind"] == "delta"],
                         [(2, 0), (2, 1)])


class NoTableAllText(unittest.TestCase):
    """An all-text document without a comparative statement says so -- it
    never asks for ANTHROPIC_API_KEY."""

    def test_message(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "CRPT-119srpt1.pdf"
            doc = pymupdf.open()
            for _ in range(2):
                doc.new_page().insert_text((72, 72), "Committee report prose. " * 20)
            doc.save(pdf)
            with self.assertRaises(SystemExit) as cm, mock.patch.object(ex, "_client", side_effect=AssertionError):
                ex.run(pdf, live=True, out_dir=d, verbose=False)
        self.assertIn("no comparative statement found", str(cm.exception))
        self.assertNotIn("ANTHROPIC_API_KEY", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
