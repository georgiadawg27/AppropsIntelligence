"""
Text first, and a hard cap on page images (extract_approps.py): a page with a usable text layer is read from it;
a page image goes to vision only where the page has no text layer or its text fails the table's arithmetic, and
every run stops at its page-image cap (MAX_PAGE_IMAGES, --max-page-images).

Run:  python -m unittest tests.test_extraction_budget -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import extract_approps as ex  # noqa: E402

STORE = ROOT / "document_store"


def page_with(text):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.splitlines():
        page.insert_text((72, y), line)
        y += 14
    return doc, page


class Routing(unittest.TestCase):
    def test_placeholder_beside_a_text_layer_reads_the_text(self):
        # GPO's offset-folio placeholder over a page that also carries the table's text (H.Rept. 116-62)
        body = "\n".join(f"Program line {i} .............. 1,234,{i:03d} 1,300,000 1,350,000" for i in range(12))
        doc, page = page_with("Insert offset folio 114 here HR62.026\n" + body)
        r = ex.route_page(page)
        self.assertEqual(r["route"], "text", r["reason"])

    def test_placeholder_alone_is_an_image_page(self):
        doc, page = page_with("Insert offset folio 114 here HR62.026")
        self.assertEqual(ex.route_page(page)["route"], "vision")


class PageImageCap(unittest.TestCase):
    def test_the_cap_stops_the_run_before_the_first_image_past_it(self):
        budget = ex.PageImageBudget(2)
        budget.spend("p1 classify")
        budget.spend("p2 classify")
        with self.assertRaises(ex.PageImageCapReached) as cm:
            budget.spend("p3 classify")
        self.assertIn("2 of 2 page images", str(cm.exception))
        self.assertEqual(budget.used, 2)

    def test_an_image_only_document_stops_at_a_zero_cap_without_a_call(self):
        # a page with no text layer must go to vision; with the cap at 0 the run stops before any API request
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "CRPT-119srpt9.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 40, 40), 0))
            doc.save(pdf)
            client = mock.MagicMock()
            with mock.patch.object(ex, "_client", return_value=client), \
                    self.assertRaises(ex.PageImageCapReached):
                ex.run(pdf, live=True, out_dir=d, cache_dir=d, verbose=False, max_page_images=0)
            client.beta.messages.stream.assert_not_called()

    def test_the_default_cap_is_80(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APPROPS_MAX_PAGE_IMAGES", None)
            import importlib
            self.assertEqual(importlib.reload(ex).MAX_PAGE_IMAGES, 80)


class TextReportNoVision(unittest.TestCase):
    """A text-layer report whose arithmetic closes sends no page image at all: run with the cap at 0 and the API
    unreachable, it completes."""

    def test_senate_text_report(self):
        pdf = STORE / "CRPT-119srpt44.pdf"
        if not pdf.exists():
            raise unittest.SkipTest(f"{pdf} not present -- fetch it with govinfo_ingest.fetch_and_store")

        def no_api():
            raise AssertionError("a text-layer report whose arithmetic closes must not reach the API")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with tempfile.TemporaryDirectory() as out, mock.patch.object(ex, "_client", side_effect=no_api), \
                mock.patch.dict(os.environ, env, clear=True):
            result = ex.run(pdf, title="TITLE III", live=True, out_dir=out, verbose=False, max_page_images=0)
        x = result["extraction"]
        self.assertEqual(x["vision_calls_this_run"], [])
        self.assertEqual(x["page_images_this_run"]["used"], 0)
        self.assertEqual(result["validation_summary"]["failures"], 0)
        self.assertTrue(result["observations"])


if __name__ == "__main__":
    unittest.main()
