"""
Acceptance: the FY2026 CJS JES (fy26_cjs_jes.pdf, a scan with an OCR text
layer), ingested through the manual path and extracted by the OCR-text-first
pipeline with the arithmetic-gated vision fallback.

Run:  python -m unittest tests.test_ocr_jes -v

Ground truth: tests/ground_truth/fy26_cjs_jes_title_iii.json (pilot
workbook, keyed by canonical account; the Title III total from H.Rept.
119-652). Vision calls replay from tests/fixtures/vision_cache_recorded
(recorded live 2026-09-25), so nothing here calls the API.
"""

import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import extract_approps as ex  # noqa: E402
import govinfo_ingest as g  # noqa: E402
import validate_approps as va  # noqa: E402
from test_rollups import row, run_rows  # noqa: E402

JES = ROOT / "fy26_cjs_jes.pdf"
RECORDED = ROOT / "tests" / "fixtures" / "vision_cache_recorded"
GT = ROOT / "tests" / "ground_truth" / "fy26_cjs_jes_title_iii.json"
DOC_ID = "MANUAL-CJS-FY2026-Enacted-jes-398bd046"
GATED = [117, 118, 120, 121, 122, 123, 124, 125, 126, 127, 130, 131, 132, 135]


def ingest_jes(store):
    with mock.patch.object(g, "STORE_DIR", store), mock.patch.object(g, "MANIFEST_PATH", store / "manifest.json"):
        m = g.load_manifest()
        res = g.ingest_local(JES, m, subcommittee="CJS", fiscal_year=2026, stage="Enacted", doc_type="jes",
                             advance_copy=False, source_agency="Senate Committee on Appropriations",
                             source_url="https://www.appropriations.senate.gov/imo/media/doc/fy26_cjs_jes.pdf")
        g.save_manifest(m)
    return Path(res["path"])


def no_api():
    raise AssertionError("no API call expected: every vision read is recorded")


@unittest.skipUnless(JES.exists(), "fy26_cjs_jes.pdf not present")
class JesTitleIIIAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.pdf = ingest_jes(d / "store")
        with mock.patch.object(ex, "_client", side_effect=no_api):
            cls.result = ex.run(cls.pdf, title="TITLE III", cache_dir=RECORDED, out_dir=d / "out", verbose=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_ingested_through_the_manual_path(self):
        self.assertEqual(self.pdf.stem, DOC_ID)
        sd = self.result["source_document"]
        self.assertEqual((sd["ingest_method"], sd["document_type"], sd["stage"], sd["fiscal_year"], sd["subcommittee"],
                          sd["advance_copy"], sd["confirmation_status"]),
                         ("manual", "joint_explanatory_statement", "Enacted", 2026, "CJS", False, "no_official_counterpart"))

    def test_routed_to_the_free_ocr_path(self):
        routes = Counter(r["reason"] for r in self.result["page_routing"])
        self.assertEqual(routes["ocr_text_layer_over_scan"], 134)
        self.assertEqual(self.result["extraction"]["ocr_table_pages"], list(range(116, 136)))
        self.assertEqual(self.result["extraction"]["ocr_fallback"], {})             # Title III never needed vision
        self.assertEqual({m["source"] for m in self.result["extraction"]["page_sources"].values()}, {"ocr_text"})

    def test_columns_from_the_ocr_header(self):
        got = [(c["header"], c["kind"], c["stage"], c["fiscal_year"]) for c in self.result["extraction"]["columns"]]
        self.assertEqual(got, [("FY 2025 Enacted", "value", "Enacted", 2025),
                               ("FY 2026 Request", "value", "President's Budget", 2026),
                               ("Final Bill", "value", "Enacted", 2026),
                               ("Final Bill vs Enacted", "delta", None, None),
                               ("Final Bill vs Request", "delta", None, None)])

    def test_acceptance_ground_truth_exact(self):
        ok, rows = ex.compare_ground_truth(self.result, GT)
        self.assertEqual([(n, w, gt) for n, w, gt, m in rows if not m], [])
        cats = Counter("exact" if isinstance(gt, int) else gt for n, w, gt, m in rows)
        self.assertEqual(dict(cats), {"exact": 60, ex.BLANK: 10, ex.NOT_PRINTED: 1})

    def test_validation_clean(self):
        s = self.result["validation_summary"]
        self.assertEqual(s["failures"], 0)
        self.assertEqual(s["by_rule"]["table_total"], {"pass": 9})

    def test_ocr_garbled_labels_matched_by_edit_distance(self):
        by_label = {o["account_name_as_written"]: o for o in self.result["observations"]}
        for label, acct, kind, dist in (("Sci e nee", "ACC-NASA-SCIENCE", "ocr_corrected", 1),
                                        ("STEH Education", "ACC-NSF-STEM-EDUCATION", "ocr_corrected", 1),
                                        ("Expl oration", "ACC-NASA-EXPLORATION", "exact", 0)):
            o = by_label[label]
            self.assertEqual((o["canonical_account_id"], o["account_match"], o["account_match_distance"]), (acct, kind, dist))
        defense = [o for o in self.result["observations"] if o["account_name_as_written"] == "Defense function"]
        self.assertTrue(defense)
        for o in defense:
            self.assertEqual((o["account_match"], o["verification_status"]), ("inherited", "unverified"))
            self.assertIn("not matched to a canonical account", o["verification_reason"])


@unittest.skipUnless(JES.exists(), "fy26_cjs_jes.pdf not present")
class JesWholeTableGate(unittest.TestCase):
    """Every table page: the free path first, vision only for the pages the
    arithmetic gate sends to it (replayed from the recording)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.pdf = ingest_jes(d / "store")
        with mock.patch.object(ex, "_client", side_effect=no_api):
            cls.result = ex.run(cls.pdf, cache_dir=RECORDED, out_dir=d / "out", verbose=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_only_failing_pages_went_to_vision(self):
        fb = self.result["extraction"]["ocr_fallback"]
        self.assertEqual(sorted(int(p) for p in fb), GATED)
        self.assertTrue(all(f["result"] == "re-read by vision" for f in fb.values()))
        sources = {int(p): m["source"] for p, m in self.result["extraction"]["page_sources"].items()}
        self.assertEqual(sorted(p for p, s in sources.items() if s == "ocr_text"), [116, 119, 128, 129, 133, 134])
        self.assertEqual(self.result["extraction"]["vision_calls_this_run"], [])

    def test_title_iii_still_exact(self):
        ok, rows = ex.compare_ground_truth(self.result, GT)
        self.assertEqual([(n, w, gt) for n, w, gt, m in rows if not m], [])

    def test_unchecked_ocr_values_capped(self):
        # Every OCR value on this JES has a delta column or a total over it, so
        # none is uncheckable; the rule is still asserted wherever it applies
        # (ConfidenceLadder shows it firing).
        ocr_pages = {p for p, m in self.result["extraction"]["page_sources"].items() if m["source"] == "ocr_text"}
        capped = [o for o in self.result["observations"]
                  if o["verification_reason"] and va.OCR_UNCHECKED_REASON in o["verification_reason"]]
        self.assertEqual(capped, [o for o in capped if o["source_page"] in ocr_pages])
        for o in capped:
            self.assertLessEqual(o["extraction_confidence"], va.OCR_UNCHECKED_CONFIDENCE)
            self.assertNotEqual(o["verification_status"], "auto-validated")
            self.assertEqual(self.result["extraction"]["page_sources"][o["source_page"]]["source"], "ocr_text")

    def test_grand_totals_reconcile(self):
        lines = [l for l in self.result["validation_summary"]["rollup_lines"] if "Grand total" in l]
        self.assertTrue(lines)
        self.assertTrue(all(l.startswith("PASS") for l in lines), lines)


@unittest.skipUnless(JES.exists(), "fy26_cjs_jes.pdf not present")
class VisionFallbackMechanics(unittest.TestCase):
    def test_rotation_accounts_for_page_rotate_and_failures_are_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pdf = ingest_jes(d / "store")
            cache = d / "cache" / DOC_ID
            cache.mkdir(parents=True)
            shutil.copy(RECORDED / DOC_ID / "p0058.json", cache)       # the one image-only page's classify pass
            seen = []

            def fail(client, model, page, rotation, dpi, fallbacks):
                seen.append((page.number + 1, rotation))
                err = ex.VisionError("no upright rendering")
                err.usage = {"input_tokens": 10, "output_tokens": 5, "seconds": 0.1, "model": "m", "attempts": 2}
                raise err
            with mock.patch.object(ex, "_client", return_value=object()), \
                    mock.patch.object(ex, "transcribe_page", side_effect=fail):
                r1 = ex.run(pdf, cache_dir=d / "cache", out_dir=d / "out", verbose=False)
                r2 = ex.run(pdf, cache_dir=d / "cache", out_dir=d / "out", verbose=False)
            self.assertTrue(seen)
            self.assertEqual({rot for _, rot in seen}, {0})          # /Rotate 90 page: already upright
            self.assertEqual(sorted(p for p, _ in seen), sorted({p for p, _ in seen}))   # each page tried once
            self.assertEqual(len(r1["extraction"]["vision_calls_this_run"]), len(seen))  # paid failures are logged
            self.assertEqual(r2["extraction"]["vision_calls_this_run"], [])             # and never re-paid
            self.assertTrue(all(f["result"].startswith("kept OCR") for f in r2["extraction"]["ocr_fallback"].values()))


class ConfidenceLadder(unittest.TestCase):
    def test_ocr_value_with_nothing_to_check_is_capped(self):
        unchecked = dict(row("Office of Science and Technology Policy", "7,965", "8,000"), values=["7,965", "8,000", ""])
        nodes, obs, records, s = run_rows([
            row("TITLE III - SCIENCE"),
            unchecked,                                    # no total over it, and its delta cell is blank
            row("National Science Foundation"),
            row("Research and related activities", "7,057,700", "6,321,069"),
            row("Defense function", "118,800", "119,071"),
            row("Subtotal", "7,176,500", "6,440,140", indent=1),                      # checks both lines above
        ], source="ocr_text")
        by = {o["account_name_as_written"]: o for o in obs if o["column_header"] == "FY 2026 Enacted"}
        self.assertEqual(by["Office of Science and Technology Policy"]["extraction_confidence"], 0.80)
        self.assertIn(va.OCR_UNCHECKED_REASON, by["Office of Science and Technology Policy"]["verification_reason"])
        self.assertEqual(by["Research and related activities"]["extraction_confidence"], 0.95)
        self.assertEqual(by["Research and related activities"]["verification_status"], "auto-validated")

    def test_not_applied_to_non_ocr_sources(self):
        nodes, obs, records, s = run_rows([row("TITLE III - SCIENCE"),
                                           row("Office of Science and Technology Policy", "7,965", "7,965")])
        self.assertTrue(all(o["extraction_confidence"] == 0.95 for o in obs))


if __name__ == "__main__":
    unittest.main()
