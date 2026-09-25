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

import json
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
GATED = [117, 118, 120, 121, 122, 126, 127, 132, 135]
JES24 = ROOT / "FY24_CJS_Conference_JES_scan_3_3_24.pdf"
GT24 = ROOT / "tests" / "ground_truth" / "fy24_cjs_jes_title_iii.json"


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
        self.assertEqual(sorted(p for p, s in sources.items() if s == "ocr_text"),
                         sorted(set(range(116, 136)) - set(GATED)))
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
            spend = r1["extraction"]["vision_spend"]
            self.assertEqual((spend["calls"], spend["failed_calls"]), (2 * len(seen), 2 * len(seen)))
            self.assertEqual(r2["extraction"]["vision_spend"]["calls"], 0)
            self.assertTrue(all(f["result"].startswith("kept OCR") for f in r2["extraction"]["ocr_fallback"].values()))


@unittest.skipUnless(JES24.exists(), "FY24_CJS_Conference_JES_scan_3_3_24.pdf not present")
class Jes24Breadth(unittest.TestCase):
    """A second scanned JES (FY2024 conference, a different OCR run): the free
    path alone, no vision, against the pilot."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        with mock.patch.object(g, "STORE_DIR", d / "store"), mock.patch.object(g, "MANIFEST_PATH", d / "store" / "manifest.json"):
            m = g.load_manifest()
            res = g.ingest_local(JES24, m, subcommittee="CJS", fiscal_year=2024, stage="Enacted", doc_type="jes",
                                 advance_copy=False)
            g.save_manifest(m)
        with mock.patch.object(ex, "_client", side_effect=no_api):
            cls.result = ex.run(res["path"], title="TITLE III", cache_dir=d / "cache", out_dir=d / "out", verbose=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_free_path_only_and_clean(self):
        self.assertEqual(self.result["extraction"]["ocr_fallback"], {})
        self.assertEqual([c["header"] for c in self.result["extraction"]["columns"]],
                         ["FY 2023 Enacted", "FY 2024 Request", "Final Bill", "Final Bill vs Enacted", "Final Bill vs Request"])
        s = self.result["validation_summary"]
        self.assertEqual(s["failures"], 0)
        self.assertEqual(s["by_rule"]["table_total"], {"pass": 9})

    def test_every_figure_exact(self):
        ok, rows = ex.compare_ground_truth(self.result, GT24)
        self.assertEqual([(n, w, got) for n, w, got, m in rows if not m], [])
        self.assertEqual(len(rows), 72)

    def test_renamed_accounts_match_through_their_former_names(self):
        # FY2024 printed these under their names of the time
        for label, acct in (("Deep Space Exploration Systems", "ACC-NASA-EXPLORATION"),
                            ("Education and Human Resources", "ACC-NSF-STEM-EDUCATION")):
            hits = [o for o in self.result["observations"] if o["account_name_as_written"].startswith(label)]
            self.assertTrue(hits, label)
            for o in hits:
                self.assertEqual((o["canonical_account_id"], o["account_match"], o["account_match_via"]),
                                 (acct, "exact", "historical_name"))


class VisionSpend(unittest.TestCase):
    """Every paid call is counted, whether or not it produced a usable result."""

    class Msg:
        def __init__(self, stop, text='{"orientation_ok": false}'):
            from types import SimpleNamespace as NS
            self.stop_reason, self.model = stop, "claude-opus-5"
            self.stop_details = "cyber" if stop == "refusal" else None
            self.usage = NS(input_tokens=3000, output_tokens=400)
            self.content = [NS(type="text", text=text)]

    def client(self, *msgs):
        msgs = list(msgs)

        class Stream:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return msgs.pop(0)
        from types import SimpleNamespace as NS
        return NS(beta=NS(messages=NS(stream=lambda **kw: Stream())))

    def test_refused_call_carries_its_usage(self):
        with self.assertRaises(ex.VisionError) as cm:
            ex._call_json(self.client(self.Msg("refusal")), "claude-opus-5", None, [], {}, "low", 10, False)
        self.assertEqual((cm.exception.usage["input_tokens"], cm.exception.usage["output_tokens"]), (3000, 400))

    def test_failed_second_attempt_counts_both(self):
        import pymupdf
        page = pymupdf.open(JES)[127]
        with self.assertRaises(ex.VisionError) as cm:
            ex.transcribe_page(self.client(self.Msg("end_turn"), self.Msg("max_tokens")), "claude-opus-5", page, 0, 60, False)
        self.assertEqual((cm.exception.usage["input_tokens"], cm.exception.usage["attempts"]), (6000, 2))

    def test_spend_summary_prices_failures_too(self):
        log = [("transcribe", 1, {"input_tokens": 1_000_000, "output_tokens": 0, "model": "claude-opus-5", "outcome": "ok"}),
               ("ocr_fallback", 2, {"input_tokens": 0, "output_tokens": 1_000_000, "model": "claude-opus-5",
                                    "outcome": "failed", "attempts": 2}),
               ("classify", 3, {"input_tokens": 10, "output_tokens": 10, "model": "some-future-model", "outcome": "ok"})]
        spend = ex.vision_spend(log)
        self.assertEqual((spend["calls"], spend["failed_calls"], spend["unpriced_calls"]), (4, 2, 1))
        self.assertEqual(spend["estimated_usd"], 30.0)                          # $5 in + $25 out per Mtok
        self.assertEqual(spend["by_outcome"]["failed"], {"calls": 2, "estimated_usd": 25.0})


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
