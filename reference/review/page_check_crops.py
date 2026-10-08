"""
Crops for the page checks the text could not settle: for each page, one image stacking the column headings
and the band around each account's row (found by its printed label), so a page is looked at once.

    python reference/review/page_check_crops.py <out_dir> [status ...]     # default: not_found wrong_column

Reads reference/review/page_check_matches.csv (page_check_read.py match). Writes <out_dir>/<doc>-p<page>.png
and prints which rows each image carries.
"""

import csv
import io
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import page_check_read as P  # noqa: E402

DPI = 220


def render(doc, p):
    r = P.ocr_page(P.FILES[doc], p)
    d = pymupdf.open(ROOT / P.FILES[doc])
    page = d[p - 1]
    page.set_rotation(r["rotation"] or 0)
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


def lines(img):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.png"
        img.save(f)
        tsv = subprocess.run(["tesseract", str(f), "-", "--psm", "6", "tsv"], capture_output=True, text=True).stdout
    out = defaultdict(lambda: {"words": [], "box": [10 ** 9, 10 ** 9, 0, 0]})
    for row in list(csv.reader(io.StringIO(tsv), delimiter="\t"))[1:]:
        if len(row) < 12 or not row[11].strip():
            continue
        k = (row[2], row[3], row[4])
        x, y, w, h = map(int, row[6:10])
        b = out[k]["box"]
        out[k]["words"].append(row[11])
        out[k]["box"] = [min(b[0], x), min(b[1], y), max(b[2], x + w), max(b[3], y + h)]
    return [(" ".join(v["words"]), v["box"]) for v in out.values()]


def main(argv):
    out_dir = Path(argv[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    want = set(argv[1:]) or {"not_found", "wrong_column"}
    import json
    obs = {o["observation_id"]: o for o in json.loads(P.STAGED.read_text())["observations"]}
    with open(P.OUT, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["status"] in want]
    by_page = defaultdict(list)
    for r in rows:
        for p in P.pages(r["source_page"]):
            by_page[(r["source_document_id"], p)].append(r)
    for (doc, p), rs in sorted(by_page.items()):
        img = render(doc, p)
        ls = lines(img)
        lh = max(30, sorted(b[3] - b[1] for _, b in ls)[len(ls) // 2] if ls else 30)
        # the column headings: from the "(Amounts in thousands)" line to the first agency or account line
        top = next((b for t, b in ls if re.search(r"thousands", t, re.I)), None)
        heads = [b for t, b in ls if re.search(r"request|enacted|estimate|final|bill", t, re.I)
                 and (top is None or b[1] < top[3] + 6 * lh)]
        bands = []
        if top or heads:
            y0 = (top or heads[0])[1] - lh // 2
            y1 = max([b[3] for b in heads] + [(top or heads[0])[3] + 3 * lh]) + lh // 2
            bands.append(("column headings", (0, max(0, y0), img.width, y1)))
        for r in rs:
            want_label = P.expected_label(obs[r["observation_id"]])
            scored = sorted(((P.label_score(want_label, t), b, t) for t, b in ls), key=lambda x: -x[0])
            hits = [x for x in scored if x[0] >= 0.6][:3] or scored[:1]
            for sc, b, t in hits:
                bands.append((f"{r['observation_id']} {r['account'][:40]} FY{r['fiscal_year']} {r['stage']} "
                               f"{r['figure_thousands']} (label match {sc:.2f})",
                               (0, max(0, b[1] - int(1.6 * lh)), img.width, min(img.height, b[3] + int(1.6 * lh)))))
        crops = []
        for cap, box in bands:
            c = img.crop(box)
            canvas = Image.new("L", (c.width, c.height + 28), 255)
            canvas.paste(c, (0, 28))
            ImageDraw.Draw(canvas).text((6, 6), cap, fill=0)
            crops.append(canvas)
        W = max(c.width for c in crops)
        H = sum(c.height + 6 for c in crops)
        sheet = Image.new("L", (W, H), 128)
        y = 0
        for c in crops:
            sheet.paste(c, (0, y))
            y += c.height + 6
        name = out_dir / f"{doc}-p{p}.png"
        sheet.save(name)
        print(name.name, f"{sheet.width}x{sheet.height}", "; ".join(r["observation_id"] for r in rs))


if __name__ == "__main__":
    main(sys.argv[1:])
