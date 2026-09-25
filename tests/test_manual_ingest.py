"""
Manual ingest (govinfo_ingest.ingest_local) and reconciliation (reconcile.py).

Run:  python -m unittest tests.test_manual_ingest -v

Reconciliation runs on S.Rept. 119-44 (text-native, free to extract), as
an "advance copy" of itself: the same bytes, the same values in different
bytes (re-saved), and a changed value.
"""

import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import extract_approps as ex  # noqa: E402
import govinfo_ingest as g  # noqa: E402
import reconcile as rc  # noqa: E402

SENATE = ROOT / "document_store" / "CRPT-119srpt44.pdf"
JES = ROOT / "fy26_cjs_jes.pdf"


class TempStore:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patches = [mock.patch.object(g, "STORE_DIR", self.dir / "store"),
                        mock.patch.object(g, "MANIFEST_PATH", self.dir / "store" / "manifest.json")]
        for pt in self.patches:
            pt.start()
        return self.dir

    def __exit__(self, *exc):
        for pt in self.patches:
            pt.stop()
        self.tmp.cleanup()


def ingest(pdf, **kw):
    m = g.load_manifest()
    args = dict(subcommittee="CJS", fiscal_year=2026, stage="Enacted", doc_type="jes", advance_copy=False)
    args.update(kw)
    res = g.ingest_local(pdf, m, **args)
    g.save_manifest(m)
    return res, m


@unittest.skipUnless(JES.exists(), "fy26_cjs_jes.pdf not present")
class ManualIngest(unittest.TestCase):
    def test_same_store_and_manifest_as_fetch_and_store(self):
        with TempStore() as d:
            res, m = ingest(JES, source_url="https://www.appropriations.senate.gov/imo/media/doc/fy26_cjs_jes.pdf",
                            source_agency="Senate Committee on Appropriations", ingested_by="CS")
            self.assertEqual(res["status"], "stored")
            self.assertEqual(res["package_id"], "MANUAL-CJS-FY2026-Enacted-jes-398bd046")
            stored = Path(res["path"])
            self.assertEqual(stored.parent, d / "store")
            self.assertEqual(stored.read_bytes(), JES.read_bytes())
            e = json.loads((d / "store" / "manifest.json").read_text())[res["package_id"]]
            self.assertEqual(e["hash"], g.sha256_of(JES.read_bytes()))
            self.assertEqual((e["ingest_method"], e["advance_copy"], e["confirmation_status"]),
                             ("manual", False, "no_official_counterpart"))
            self.assertEqual((e["subcommittee"], e["fiscal_year"], e["stage"], e["doc_type"], e["ingested_by"]),
                             ("CJS", 2026, "Enacted", "jes", "CS"))
            again, _ = ingest(JES)
            self.assertEqual(again["status"], "unchanged")

    def test_rejects(self):
        with TempStore() as d:
            with self.assertRaises(ValueError):
                ingest(JES, stage="Final")
            with self.assertRaises(ValueError):
                ingest(JES, doc_type="jes", advance_copy=True)       # a JES is never a govinfo package
            not_pdf = d / "x.pdf"
            not_pdf.write_text("hello")
            with self.assertRaises(ValueError):
                ingest(not_pdf)

    def test_cli(self):
        with TempStore() as d:
            res = g.main_ingest_local([str(JES), "--subcommittee", "CJS", "--fiscal-year", "2026", "--stage", "Enacted",
                                       "--doc-type", "jes", "--source-url", "https://example.invalid/jes.pdf"])
            self.assertEqual(res["status"], "stored")
            self.assertIn(res["package_id"], json.loads((d / "store" / "manifest.json").read_text()))

    def test_source_document_fields(self):
        with TempStore() as d:
            res, m = ingest(JES, source_url="https://example.invalid/jes.pdf", source_agency="Senate Committee on Appropriations")
            doc = ex.describe_package(res["package_id"], m[res["package_id"]])
            self.assertEqual((doc["document_type"], doc["stage"], doc["chamber"]), ("explanatory_statement", "Enacted", "N/A"))
            sd = ex.source_document_fields(res["package_id"], "sha", doc, m[res["package_id"]], [])
            self.assertEqual((sd["ingest_method"], sd["advance_copy"], sd["confirmation_status"], sd["subcommittee"],
                              sd["url_or_identifier"]),
                             ("manual", False, "no_official_counterpart", "CJS", "https://example.invalid/jes.pdf"))


@unittest.skipUnless(SENATE.exists(), "CRPT-119srpt44.pdf not present")
class AdvanceCopyReconciliation(unittest.TestCase):
    def arrive(self, d, m):
        """The official package lands the way fetch_and_store stores it."""
        shutil.copy(SENATE, d / "store" / "CRPT-119srpt44.pdf")
        m["CRPT-119srpt44"] = {"hash": g.sha256_of(SENATE.read_bytes()), "stored_path": str(d / "store" / "CRPT-119srpt44.pdf"),
                               "ingest_method": "govinfo_api", "advance_copy": False, "confirmation_status": "official"}
        g.save_manifest(m)

    def advance(self, d, content):
        src = d / "advance.pdf"
        src.write_bytes(content)
        return ingest(src, stage="Senate Reported", doc_type="committee_report", advance_copy=True, bill_id="119S2354")

    def reconcile(self, d, m, advance_id, tamper=None):
        real = ex.run

        def run(pdf, **kw):
            res = real(pdf, **kw)
            if tamper and Path(pdf).stem.startswith("MANUAL-"):
                tamper(res)
            return res
        with mock.patch.object(rc.ex, "run", side_effect=run):
            return rc.reconcile(advance_id, "CRPT-119srpt44", m, d / "store", d / "out", offline=True)

    def test_advance_copy_is_provisional_until_confirmed(self):
        with TempStore() as d:
            res, m = self.advance(d, SENATE.read_bytes())
            r = ex.run(res["path"], title="TITLE III", live=True, out_dir=d / "out", verbose=False)
            statuses = Counter(o["verification_status"] for o in r["observations"])
            self.assertNotIn("auto-validated", statuses)
            prov = [o for o in r["observations"] if o["verification_status"] == "provisional"]
            self.assertTrue(prov)
            self.assertTrue(all(o["extraction_confidence"] == 0.85 for o in prov))
            self.assertTrue(all("advance copy" in o["verification_reason"] for o in prov))
            self.assertEqual(r["source_document"]["confirmation_status"], "unconfirmed")

    def test_slot_lookup(self):
        with TempStore() as d:
            res, m = self.advance(d, SENATE.read_bytes())
            self.assertEqual(rc.advance_copies_for(m, "BILLS-119s2354rs", "CRPT-119srpt44"), [res["package_id"]])
            self.assertEqual(rc.advance_copies_for(m, "BILLS-118s2354rs", "CRPT-118srpt44"), [])   # other Congress
            self.assertEqual(rc.advance_copies_for(m, "BILLS-119s2354rs", "CRPT-119hrpt44"), [])   # House report

    def test_identical_bytes_confirm(self):
        with TempStore() as d:
            res, m = self.advance(d, SENATE.read_bytes())
            self.arrive(d, m)
            rep = self.reconcile(d, m, res["package_id"])
            self.assertEqual(rep["outcome"], "confirmed_identical_bytes")
            self.assertEqual(rep["validation_records"], [])
            e = json.loads((d / "store" / "manifest.json").read_text())[res["package_id"]]
            self.assertEqual((e["confirmation_status"], e["reconciled_with_document_id"]), ("confirmed", "CRPT-119srpt44"))
            self.assertNotIn("provisional", {o["verification_status"] for o in rep["advance_result"]["observations"]})

    def test_same_values_different_bytes_confirm(self):
        with TempStore() as d:
            resaved = pymupdf.open(SENATE).tobytes(garbage=4, deflate=True)
            self.assertNotEqual(resaved, SENATE.read_bytes())
            res, m = self.advance(d, resaved)
            self.arrive(d, m)
            rep = self.reconcile(d, m, res["package_id"])
            self.assertEqual(rep["outcome"], "confirmed_identical_values")
            self.assertEqual([r["result"] for r in rep["validation_records"]], ["pass"])
            self.assertIn("bytes differ", rep["validation_records"][0]["observed_result"])

    def test_changed_value_is_superseded_and_logged(self):
        def tamper(res):
            for o in res["observations"]:
                if o["account_name_as_written"] == "Science" and o["column_header"] == "Committee recommendation":
                    o["amount"] += 1000
        with TempStore() as d:
            res, m = self.advance(d, pymupdf.open(SENATE).tobytes(garbage=4, deflate=True))
            self.arrive(d, m)
            rep = self.reconcile(d, m, res["package_id"], tamper)
            self.assertEqual(rep["outcome"], "superseded")
            [rec] = rep["validation_records"]
            self.assertEqual((rec["rule_applied"], rec["result"], rec["human_review_status"]),
                             ("advance_copy_reconciliation", "flag", "pending"))
            self.assertIn("7300000000 per official CRPT-119srpt44", rec["expected_result"])
            self.assertIn("7300001000 per advance copy", rec["observed_result"])
            self.assertIn(rep["official_source_document_id"], rec["expected_result"])
            self.assertIn(rep["advance_source_document_id"], rec["observed_result"])
            adv = rep["advance_result"]["observations"]
            self.assertEqual({o["verification_status"] for o in adv}, {"superseded"})
            self.assertTrue(all(o["superseded_by_observation_id"] for o in adv))
            saved = json.loads(Path(rep["_path"]).read_text())
            self.assertEqual(saved["outcome"], "superseded")
            self.assertEqual(json.loads((d / "store" / "manifest.json").read_text())[res["package_id"]]["confirmation_status"],
                             "superseded")

    def test_polling_triggers_reconciliation(self):
        with TempStore() as d:
            res, m = self.advance(d, SENATE.read_bytes())
            bill = {"packageId": "BILLS-119s2354rs", "title": "S. 2354"}
            with mock.patch.object(g, "fetch_new_packages", return_value=[bill]), \
                    mock.patch.object(g, "fetch_related", side_effect=lambda pid, c, k: [{"packageId": "CRPT-119srpt44"}] if c == "CRPT" else []), \
                    mock.patch.object(g, "fetch_and_store", side_effect=lambda pid, k, man: (
                        man.update({pid: {"hash": "x", "stored_path": "x", "ingest_method": "govinfo_api"}}) or
                        {"package_id": pid, "status": "stored"})), \
                    mock.patch("reconcile.reconcile", return_value={"outcome": "confirmed_identical_bytes"}) as rec:
                g.run("k", ["119S2354"], "2025-01-01T00:00:00Z")
            rec.assert_called_once()
            self.assertEqual(rec.call_args.args[:2], (res["package_id"], "CRPT-119srpt44"))


if __name__ == "__main__":
    unittest.main()
