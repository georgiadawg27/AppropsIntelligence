"""
A copy of a committee report whose image-only table pages carry an invisible OCR text layer, so the extractor
reads them as text (extract_approps.py routes a page with an OCR layer to ocr_tables, and re-reads by vision
only a page that fails the table's arithmetic). The official PDF is untouched; the copy is a working file.

    python reference/review/lhhs/backfill/ocr_pdf.py document_store/CRPT-117hrpt96.pdf extraction_cache/ocr/CRPT-117hrpt96.pdf

Each image-only page (GPO's "Insert offset folio" placeholder, or a near-empty text layer over an image) is
rendered at 400 dpi in the orientation that reads best (GPO fold-outs run bottom-to-top: 90 degrees) and run
through tesseract (--psm 6), whose PDF output (the page image plus the recognized words, invisible) replaces
the page. Every other page is copied as it is, so page numbers are the official PDF's.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

DPI = 400


def image_only(page):
    t = page.get_text()
    return bool(page.get_images()) and ("Insert offset folio" in t or len(re.sub(r"\s+", "", t)) < 400)


def numbers(text):
    return len(re.findall(r"\d{1,3}(?:,\d{3})+", text))


def ocr_page(page, td):
    best = None
    for rot in (90, 270, 0):
        page.set_rotation(rot)
        png = Path(td) / f"p{page.number}-{rot}.png"
        page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY).save(png)
        base = Path(td) / f"p{page.number}-{rot}"
        subprocess.run(["tesseract", str(png), str(base), "--psm", "6", "-c", "textonly_pdf=0", "pdf", "txt"],
                       capture_output=True, check=True, timeout=600)
        n = numbers((base.with_suffix(".txt")).read_text())
        if best is None or n > best[0]:
            best = (n, base.with_suffix(".pdf"), rot)
        if rot == 90 and n >= 20:
            break
    page.set_rotation(0)
    return best


def main(src, dst):
    doc = pymupdf.open(src)
    out = pymupdf.open()
    done = []
    with tempfile.TemporaryDirectory() as td:
        for page in doc:
            if image_only(page):
                n, pdf, rot = ocr_page(page, td)
                out.insert_pdf(pymupdf.open(pdf))
                done.append((page.number + 1, rot, n))
            else:
                out.insert_pdf(doc, from_page=page.number, to_page=page.number)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, garbage=3, deflate=True)
    print(f"{len(done)} pages OCR'd ({src} -> {dst}); rotations {sorted({r for _, r, _ in done})}; "
          f"fewest numbers on a page: {min((n for *_, n in done), default=0)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
