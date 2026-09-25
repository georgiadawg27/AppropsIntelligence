"""
Tests for govinfo_ingest.py's fetch stage.

Run:  python -m unittest tests.test_ingest -v

The Live* classes call api.govinfo.gov for real (GOVINFO_API_KEY from .env)
and download into a temporary store, never document_store/. They're the
regression test for committee reports: fetch_and_store itself has to
download a CRPT package -- no hand-fetched file, no bypass.
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

import govinfo_ingest as g  # noqa: E402

KEY = os.environ.get("GOVINFO_API_KEY")
needs_key = unittest.skipUnless(KEY, "GOVINFO_API_KEY not set in .env")


class TempStore:
    """Point the module's store at a temp dir for the duration of a test."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(g, "STORE_DIR", Path(self.tmp.name)),
                        mock.patch.object(g, "MANIFEST_PATH", Path(self.tmp.name) / "manifest.json")]
        for pt in self.patches:
            pt.start()
        return Path(self.tmp.name)

    def __exit__(self, *exc):
        for pt in self.patches:
            pt.stop()
        self.tmp.cleanup()


@needs_key
class LiveCommitteeReportFetch(unittest.TestCase):
    """The regression: CRPT summaries have no pdfLink, and fetch_and_store
    used to return no_pdf_available for every committee report."""

    def test_senate_report_downloads_through_fetch_and_store(self):
        pid = "CRPT-119srpt44"                     # S.Rept. 119-44, FY2026 CJS
        with TempStore() as store:
            manifest = {}
            res = g.fetch_and_store(pid, KEY, manifest)
            self.assertEqual(res["status"], "stored", res)
            data = (store / f"{pid}.pdf").read_bytes()
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertEqual(manifest[pid]["hash"], g.sha256_of(data))
            self.assertEqual(manifest[pid]["doc_class"], "SRPT")
            import pymupdf
            self.assertEqual(len(pymupdf.open(stream=data, filetype="pdf")), 223)
            # and a second run sees nothing changed
            self.assertEqual(g.fetch_and_store(pid, KEY, manifest)["status"], "unchanged")
            # but a manifest entry whose file has gone missing is re-downloaded
            (store / f"{pid}.pdf").unlink()
            self.assertEqual(g.fetch_and_store(pid, KEY, manifest)["status"], "stored")
            self.assertEqual((store / f"{pid}.pdf").read_bytes(), data)


@needs_key
class LiveBillsAndPublicLawsStillUsePdfLink(unittest.TestCase):
    """Scope check: the CRPT fix mustn't change how BILLS / PLAW are fetched."""

    def fetch_recording_url(self, pid):
        seen = []
        real = g.urlopen

        def spy(req, *a, **k):
            url = req.full_url if hasattr(req, "full_url") else req
            seen.append(url.split("?")[0])
            return real(req, *a, **k)

        with TempStore(), mock.patch.object(g, "urlopen", side_effect=spy):
            res = g.fetch_and_store(pid, KEY, {})
        pdf_urls = [u for u in seen if "/summary" not in u]
        return res, pdf_urls

    def check(self, pid):
        advertised = g.api_get(f"/packages/{pid}/summary", KEY)["download"].get("pdfLink")
        self.assertTrue(advertised, f"{pid} summary no longer carries a pdfLink")
        res, urls = self.fetch_recording_url(pid)
        self.assertEqual(res["status"], "stored", res)
        self.assertEqual(urls, [advertised])        # fetched from the summary's own pdfLink

    def test_bill(self):
        self.check("BILLS-119s2354rs")

    def test_public_law(self):
        self.check("PLAW-119publ4")


class PdfLinkRouting(unittest.TestCase):
    """Offline: which URL each kind of summary resolves to."""

    def test_crpt_without_pdflink_uses_package_pdf_endpoint(self):
        s = {"collectionCode": "CRPT", "download": {"zipLink": "z", "modsLink": "m"}}
        self.assertEqual(g.pdf_link_for("CRPT-119srpt44", s), f"{g.API_BASE}/packages/CRPT-119srpt44/pdf")

    def test_pdflink_wins_when_present(self):
        s = {"collectionCode": "CRPT", "download": {"pdfLink": "https://x/granule.pdf"}}
        self.assertEqual(g.pdf_link_for("CRPT-119srpt44", s), "https://x/granule.pdf")

    def test_not_a_blanket_fallback(self):
        for pid, coll in (("BILLS-119hr8845rh", "BILLS"), ("PLAW-119publ4", "PLAW")):
            self.assertIsNone(g.pdf_link_for(pid, {"collectionCode": coll, "download": {}}))

    def test_non_pdf_response_is_not_stored(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<html>error</html>"

        with TempStore() as store, \
                mock.patch.object(g, "api_get", return_value={"collectionCode": "CRPT", "download": {}}), \
                mock.patch.object(g, "urlopen", return_value=Resp()):
            res = g.fetch_and_store("CRPT-119srpt44", "k", {})
            self.assertEqual(res["status"], "not_a_pdf")
            self.assertEqual(list(store.iterdir()), [])


class RelatedLookupRouting(unittest.TestCase):
    """Offline: committee reports / public laws are found through each
    matched bill's /related links, not by scanning CRPT / PLAW titles."""

    def test_report_id_never_carries_the_bill_number(self):
        # why the old CRPT scan could never match S. 2354's report
        self.assertIsNone(g.matches_tracked_bill("CRPT-119srpt44", {"S2354": ("119", "s", "2354")}))

    def test_run_expands_matched_bills_through_related(self):
        bills = [{"packageId": "BILLS-119s2354rs", "title": "S. 2354 (RS)"},
                 {"packageId": "BILLS-119s2354is", "title": "S. 2354 (IS)"},
                 {"packageId": "BILLS-119hr9999ih", "title": "H.R. 9999 (IH)"}]
        related = {("BILLS-119s2354rs", "CRPT"): [{"packageId": "CRPT-119srpt44"}],
                   ("BILLS-119s2354is", "CRPT"): [{"packageId": "CRPT-119srpt44"}]}
        stored = []
        with TempStore(), \
                mock.patch.object(g, "fetch_new_packages", return_value=bills) as poll, \
                mock.patch.object(g, "fetch_related", side_effect=lambda pid, c, k: related.get((pid, c), [])), \
                mock.patch.object(g, "fetch_and_store",
                                  side_effect=lambda pid, k, m: stored.append(pid) or {"package_id": pid, "status": "stored"}):
            g.run("k", ["119S2354"], "2025-01-01T00:00:00Z")
        self.assertEqual([c.args[0] for c in poll.call_args_list], ["BILLS"])   # no CRPT/PLAW scan
        self.assertEqual(stored, ["BILLS-119s2354rs", "BILLS-119s2354is", "CRPT-119srpt44"])

    def test_no_relationship_yet_is_empty_not_an_error(self):
        err = g.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(g, "api_get", side_effect=err):
            self.assertEqual(g.fetch_related("BILLS-119s2354rs", "PLAW", "k"), [])


@needs_key
class LiveRelatedLookup(unittest.TestCase):
    """Tracked bill -> /related -> fetch_and_store, against the real API. Only
    the BILLS listing is stubbed (polling the whole collection takes minutes);
    the bill package itself is real."""

    def test_senate_bill_finds_and_stores_its_report(self):
        self.assertEqual([r["packageId"] for r in g.fetch_related("BILLS-119s2354rs", "CRPT", KEY)],
                         ["CRPT-119srpt44"])
        bill = {"packageId": "BILLS-119s2354rs",
                "title": g.api_get("/packages/BILLS-119s2354rs/summary", KEY)["title"]}
        with TempStore() as store, mock.patch.object(g, "fetch_new_packages", return_value=[bill]):
            results = g.run(KEY, ["119S2354"], "2025-01-01T00:00:00Z")
            self.assertEqual({r["package_id"]: r["status"] for r in results},
                             {"BILLS-119s2354rs": "stored", "CRPT-119srpt44": "stored"})
            self.assertTrue((store / "CRPT-119srpt44.pdf").read_bytes().startswith(b"%PDF"))
            self.assertIn("CRPT-119srpt44", json.loads((store / "manifest.json").read_text()))


class TrackedBillMatching(unittest.TestCase):
    """Exact Congress + type + number matching (ported from
    claude/govinfo-api-ingestion-ke9hyd, plus Congress pinning)."""

    def tracked(self, *specs, today=None):
        return {s: g.parse_bill_spec(s, today) for s in specs}

    def test_number_prefix_does_not_match(self):
        t = self.tracked("119HR884")
        self.assertIsNone(g.matches_tracked_bill("BILLS-119hr8845rh", t))
        self.assertEqual(g.matches_tracked_bill("BILLS-119hr884ih", t), "119HR884")

    def test_type_prefix_does_not_match(self):
        t = self.tracked("119S5")
        self.assertIsNone(g.matches_tracked_bill("BILLS-119sres5ats", t))
        self.assertIsNone(g.matches_tracked_bill("BILLS-119s50is", t))
        self.assertEqual(g.matches_tracked_bill("BILLS-119s5rs", t), "119S5")

    def test_other_congress_same_number_does_not_match(self):
        t = self.tracked("119HR8845")
        self.assertIsNone(g.matches_tracked_bill("BILLS-118hr8845ih", t))
        self.assertEqual(g.matches_tracked_bill("BILLS-119hr8845rh", t), "119HR8845")
        t118 = self.tracked("118S2321")
        self.assertEqual(g.matches_tracked_bill("BILLS-118s2321rs", t118), "118S2321")
        self.assertIsNone(g.matches_tracked_bill("BILLS-119s2321is", t118))

    def test_bare_spec_means_the_current_congress(self):
        from datetime import date
        self.assertEqual(g.parse_bill_spec("H.R. 8845", date(2026, 9, 25)), ("119", "hr", "8845"))
        self.assertEqual(g.parse_bill_spec("S2321", date(2023, 7, 13)), ("118", "s", "2321"))
        self.assertEqual(g.current_congress(date(2025, 1, 2)), "118")     # 119th convened Jan 3, 2025
        self.assertEqual(g.current_congress(date(2025, 1, 3)), "119")
        t = self.tracked("HR8845", today=date(2026, 9, 25))
        self.assertIsNone(g.matches_tracked_bill("BILLS-118hr8845ih", t))
        self.assertEqual(g.matches_tracked_bill("BILLS-119hr8845rh", t), "HR8845")

    def test_spellings_and_bad_specs(self):
        for spec in ("HR8845", "H.R. 8845", "hr-8845", "119hr8845"):
            self.assertEqual(g.parse_bill_spec(spec)[1:], ("hr", "8845"))
        self.assertEqual(g.parse_bill_spec("hjres05")[1:], ("hjres", "5"))
        for bad in ("8845", "HX8845", "HR"):
            with self.assertRaises(ValueError):
                g.parse_bill_spec(bad)

    def test_non_bill_packages_never_match(self):
        t = self.tracked("119S2354")
        for pid in ("CRPT-119srpt44", "PLAW-119publ4", "", None):
            self.assertIsNone(g.matches_tracked_bill(pid, t))


if __name__ == "__main__":
    unittest.main()
