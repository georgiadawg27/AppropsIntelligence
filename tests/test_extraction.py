"""
Tests for extract_approps.py / validate_approps.py.

Run:  python -m unittest discover -s tests -v

Needs document_store/CRPT-119hrpt652.pdf (govinfo_ingest.py, or
curl "https://api.govinfo.gov/packages/CRPT-119hrpt652/pdf?api_key=$GOVINFO_API_KEY").
Vision results come from tests/fixtures/vision_cache (hand transcriptions of
pp. 168-171), so no API key is needed and nothing here spends money.

That checks parsing and validation, not the model's reading of the page. To
run the Title III acceptance test against fresh vision calls instead (cache
ignored, needs ANTHROPIC_API_KEY in .env, costs money):

    APPROPS_LIVE=1 python -m unittest tests.test_extraction.TitleIIIAcceptance -v
"""

import copy
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

PDF = ROOT / "document_store" / "CRPT-119hrpt652.pdf"
FIXTURES = ROOT / "tests" / "fixtures" / "vision_cache"
GROUND_TRUTH = ROOT / "tests" / "ground_truth" / "CRPT-119hrpt652_title_iii_fy2026_enacted.json"

needs_pdf = unittest.skipUnless(PDF.exists(), f"{PDF} not present -- fetch it with govinfo_ingest.py")
LIVE = os.environ.get("APPROPS_LIVE") == "1"


def run_offline(cache_dir=FIXTURES, title="TITLE III"):
    with tempfile.TemporaryDirectory() as out:
        return ex.run(PDF, title=title, cache_dir=cache_dir, offline=True, out_dir=out, verbose=False)


def copy_fixtures(tmp):
    dst = Path(tmp) / "CRPT-119hrpt652"
    dst.mkdir(parents=True)
    for f in (FIXTURES / "CRPT-119hrpt652").glob("*.json"):
        (dst / f.name).write_text(f.read_text())
    return dst


def by_path(result, column="FY 2026 Enacted"):
    return {o["account_path"]: o for o in result["observations"] if o["column_header"] == column}


class CellParsing(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(ex.parse_cell("7,250,000")["value"], 7250000)
        self.assertEqual(ex.parse_cell("+1,142,600")["value"], 1142600)
        self.assertEqual(ex.parse_cell("-85,000")["value"], -85000)
        dash = ex.parse_cell("---")
        self.assertEqual((dash["kind"], dash["value"]), ("dash", 0))
        memo = ex.parse_cell("(-1,750,000)")
        self.assertEqual((memo["value"], memo["paren"]), (-1750000, True))
        self.assertEqual(ex.parse_cell("")["kind"], "blank")
        self.assertEqual(ex.parse_cell("7,25O,000")["kind"], "unparsed")   # letter O, not zero
        self.assertEqual(ex.parse_cell("72,50,000")["kind"], "unparsed")   # misplaced comma

    def test_units(self):
        self.assertEqual(ex.parse_units("(Amounts in thousands)"), "thousands")
        self.assertEqual(ex.parse_units("[In millions of dollars]"), "millions")
        self.assertEqual(ex.parse_units(""), None)


@needs_pdf
class PageRouting(unittest.TestCase):
    def test_routes(self):
        import pymupdf
        doc = pymupdf.open(PDF)
        routes = {r["page"]: r for r in (ex.route_page(doc[i]) for i in range(len(doc)))}
        vision = {p for p, r in routes.items() if r["route"] == "vision"}
        # the comparative-statement fold-out (157-173) and the roll-call vote
        # scans (135-155) are the only image-only pages
        self.assertEqual(vision, set(range(135, 156)) | set(range(157, 174)))
        self.assertEqual(routes[156]["route"], "text")   # table's text intro page
        self.assertEqual(routes[94]["route"], "text")    # Title III narrative
        self.assertEqual(routes[174]["route"], "text")   # CBO table, has a text layer


@needs_pdf
class TitleIIIAcceptance(unittest.TestCase):
    """The user's acceptance test: FY 2026 Enacted column of Title III."""

    @classmethod
    def setUpClass(cls):
        if not LIVE:
            cls.result = run_offline()
            return
        # Fresh vision calls; a throwaway cache so the fixtures are never touched.
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out:
            cls.result = ex.run(PDF, title="TITLE III", cache_dir=cache, out_dir=out, live=True, verbose=False)
        ex.print_report(cls.result, ex.compare_ground_truth(cls.result, GROUND_TRUTH)[1])

    def test_mode(self):
        self.assertEqual(self.result["extraction"]["mode"], "live" if LIVE else "offline")
        if LIVE:
            sources = {v["source"] for v in self.result["extraction"]["page_sources"].values()}
            self.assertEqual(sources, {"claude_api"})

    def test_ground_truth_exact(self):
        ok, rows = ex.compare_ground_truth(self.result, GROUND_TRUTH)
        bad = [(n, want, got) for n, want, got, match in rows if not match]
        self.assertEqual(bad, [])
        self.assertEqual(len(rows), 20)

    def test_all_checks_pass(self):
        s = self.result["validation_summary"]
        self.assertEqual(s["failures"], 0)
        self.assertEqual(s["by_rule"]["table_total"], {"pass": 6})
        # everything auto-validated except Defense function: nested under
        # R&RA only by model-read indent, which no arithmetic confirms, and so
        # only inherits R&RA as its account -- a person confirms both
        unverified = {(o["account_path"], o["column_header"]): o["verification_reason"]
                      for o in self.result["observations"] if o["verification_status"] != "auto-validated"}
        defense = "National Science Foundation / Research and related activities / Defense function"
        self.assertEqual(unverified, {(defense, c): "hierarchy from model-read indent only; "
                                                    "account name not matched to a canonical account"
                                      for c in ("FY 2026 Enacted", "Bill")})

    def test_every_account_row_has_its_canonical_account(self):
        # the vision path matches accounts too, not only OCR
        rows = {o["account_path"]: (o.get("canonical_account_id"), o.get("account_match"))
                for o in self.result["observations"] if not (o["is_rollup"] or o["is_memo"])}
        self.assertEqual(rows["National Aeronautics and Space Administration / Science"], ("ACC-NASA-SCIENCE", "exact"))
        self.assertEqual(rows["National Aeronautics and Space Administration / Space Operations"],
                         ("ACC-NASA-SPACEOPS", "exact"))                  # v13: canonical name is plural, as printed
        self.assertEqual(rows["National Science Foundation / Research and related activities / Defense function"],
                         ("ACC-NSF-RRA", "inherited"))
        self.assertEqual([p for p, (acct, _) in rows.items() if acct is None], [])

    def test_title_boundaries(self):
        titles = {o["title"] for o in self.result["observations"]}
        self.assertEqual(titles, {"TITLE III - SCIENCE"})
        paths = by_path(self.result)
        self.assertNotIn("Total, title II, Department of Justice", paths)
        self.assertNotIn("Office of the U.S. Trade Representative / Salaries and expenses", paths)

    def test_dash_is_zero_in_value_column(self):
        stem = by_path(self.result, "Bill")[
            "National Aeronautics and Space Administration / "
            "Science, Technology, Engineering, and Mathematics Engagement"]
        self.assertEqual(stem["amount"], 0)
        self.assertTrue(stem["amount_is_dash_zero"])

    def test_no_delta_observations(self):
        self.assertFalse(any("vs." in o["column_header"] for o in self.result["observations"]))

    def test_memo_is_not_additive(self):
        paths = by_path(self.result)
        memo = paths["Total, Title III, Science / Appropriations"]
        self.assertTrue(memo["is_memo"])
        self.assertFalse(memo["is_rollup"])
        total = paths["Total, Title III, Science"]
        self.assertEqual(total["amount"], 33_196_301_000)   # not doubled by the memo

    def test_stage_and_fiscal_year(self):
        o = by_path(self.result, "Bill")["National Aeronautics and Space Administration / Science"]
        self.assertEqual((o["fiscal_year"], o["stage"], o["chamber"]), (2027, "House Reported", "House"))
        o = by_path(self.result)["National Aeronautics and Space Administration / Science"]
        self.assertEqual((o["fiscal_year"], o["stage"], o["chamber"]), (2026, "Enacted", "N/A"))
        self.assertEqual(o["amount_in_units"], 7_250_000)
        self.assertEqual(o["amount_unit"], "thousands")
        self.assertEqual(o["amount"], 7_250_000_000)            # dollars at the pipeline boundary


@needs_pdf
class ValidationCatchesMisreads(unittest.TestCase):
    """A single wrong digit -- the realistic vision failure -- must be caught."""

    def _mutate(self, page, label, col, new):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = copy_fixtures(tmp.name)
        f = d / f"p{page:04d}.json"
        entry = json.loads(f.read_text())
        for r in entry["transcription"]["rows"]:
            if r["label"] == label:
                r["values"][col] = new
        f.write_text(json.dumps(entry))
        return run_offline(cache_dir=tmp.name)

    def test_misread_line_item(self):
        res = self._mutate(169, "Aeronautics", 0, "985,000")          # 3 -> 8
        s = res["validation_summary"]
        self.assertGreater(s["failures"], 0)
        fails = [ln for ln in s["rollup_lines"] if ln.startswith("FAIL")]
        self.assertTrue(any("Total, National Aeronautics" in ln for ln in fails))
        aero = by_path(res)["National Aeronautics and Space Administration / Aeronautics"]
        self.assertEqual(aero["verification_status"], "flagged")
        self.assertLessEqual(aero["extraction_confidence"], 0.5)

    def test_misread_total(self):
        res = self._mutate(170, "Total, National Science Foundation", 1, "7,000,600")
        self.assertGreater(res["validation_summary"]["failures"], 0)

    def test_unparseable_cell(self):
        res = self._mutate(169, "Exploration", 0, "7,78З,000")       # Cyrillic Ze
        self.assertGreater(res["validation_summary"]["failures"], 0)


@needs_pdf
class VisionOrchestration(unittest.TestCase):
    """The live path (classify -> select -> transcribe -> cache), with the
    Claude call mocked out by the recorded fixtures."""

    def test_live_path_with_mocked_api(self):
        fixtures = {int(f.stem[1:]): json.loads(f.read_text())
                    for f in (FIXTURES / "CRPT-119hrpt652").glob("p*.json")}
        calls = []

        def fake_call(client, model, system, content, schema, effort, max_tokens, use_fallbacks):
            # which page? the fake client carries it
            page = client.current_page
            kind = "transcribe" if schema is ex.TRANSCRIBE_SCHEMA else "classify"
            calls.append((kind, page))
            if kind == "classify":
                if page in fixtures:
                    return copy.deepcopy(fixtures[page]["classify"]), {"model": model, "input_tokens": 1, "output_tokens": 1}
                return ({"orientation": "upright", "page_kind": "not_a_table", "table_title": "",
                         "title_headings": []}, {"model": model, "input_tokens": 1, "output_tokens": 1})
            return copy.deepcopy(fixtures[page]["transcription"]), {"model": model, "input_tokens": 1, "output_tokens": 1}

        class FakeClient:
            current_page = None

        fake = FakeClient()
        real_render = ex.render_png

        def render_and_track(page, *a, **k):
            fake.current_page = page.number + 1
            return real_render(page, *a, **k)

        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out, \
                mock.patch.object(ex, "_client", return_value=fake), \
                mock.patch.object(ex, "_call_json", side_effect=fake_call), \
                mock.patch.object(ex, "render_png", side_effect=render_and_track):
            res = ex.run(PDF, title="TITLE III", cache_dir=cache, out_dir=out, verbose=False)
            transcribed = sorted(p for k, p in calls if k == "transcribe")
            self.assertEqual(transcribed, [169, 170, 171])     # 168 is Title II; stop after Title IV heading
            self.assertEqual(len([c for c in calls if c[0] == "classify"]), 38)
            ok, _ = ex.compare_ground_truth(res, GROUND_TRUTH)
            self.assertTrue(ok)
            cached = json.loads((Path(cache) / "CRPT-119hrpt652" / "p0169.json").read_text())
            self.assertEqual(cached["meta"]["rotation"], 90)
            # second run is served entirely from cache
            calls.clear()
            ex.run(PDF, title="TITLE III", cache_dir=cache, out_dir=out, verbose=False)
            self.assertEqual(calls, [])
            # --live ignores that cache and calls the API for every page again
            res = ex.run(PDF, title="TITLE III", cache_dir=cache, out_dir=out, verbose=False, live=True)
            self.assertEqual(sorted(p for k, p in calls if k == "transcribe"), [169, 170, 171])
            self.assertEqual(len([c for c in calls if c[0] == "classify"]), 38)
            self.assertEqual(res["extraction"]["mode"], "live")

    def test_live_and_offline_exclusive(self):
        with self.assertRaises(SystemExit):
            ex.run(PDF, title="TITLE III", cache_dir=FIXTURES, offline=True, live=True, verbose=False)

    def test_rotation_retry(self):
        import pymupdf
        page = pymupdf.open(PDF)[168]
        answers = [({"orientation_ok": False}, {"model": "m"}), ({"orientation_ok": True, "rows": []}, {"model": "m"})]
        with mock.patch.object(ex, "_call_json", side_effect=answers) as call:
            _, _, rot, _ = ex.transcribe_page(object(), "m", page, 90, 100, False)
        self.assertEqual(rot, 270)
        self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
