"""Report only: does each observation's amount (in $ thousands, comma format) appear on its source_page,
read as a 1-based PDF page index? If not, where is the nearest page that has it (a consistent offset
would show up here)? Pages with no text layer (the folio inserts of House reports, the Congressional
Record's tables) are read with tesseract, at the rotation that yields the most comma-grouped numbers.

    python reference/review/links/page_check.py . page_check.json

Needs document_store/ (the stored PDFs) and tesseract; nothing in the workbook or the store changes.
The OCR text is cached in ~/.cache/approps_page_check."""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import openpyxl, pymupdf
ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT.resolve()))
import approps_store  # noqa: E402
wb = openpyxl.load_workbook(approps_store.reference_workbook(), read_only=True, data_only=True)
def sheet(n):
    rows = list(wb[n].iter_rows(values_only=True)); return [dict(zip(rows[0], r)) for r in rows[1:]]
src = {d["document_id"]: d for d in sheet("Source Document")}
man = json.load(open(ROOT / "document_store/manifest.json"))
def local(doc_id):
    d = src[doc_id]; url = d["url_or_identifier"] or ""
    m = re.search(r"/(CRPT-\d+[hs]rpt\d+|PLAW-\d+publ\d+)", url)
    if m and m.group(1) in man: return ROOT / man[m.group(1)]["stored_path"], m.group(1)
    for k, v in man.items():
        if v.get("source_url") and doc_id in ("SRC-EXPL-LHHS-FY2026-ENACTED", "SRC-EXPL-LHHS-FY2023-SENATE", "SRC-CJ-AHA-FY2026"):
            want = {"SRC-EXPL-LHHS-FY2026-ENACTED": "FY2026-Enacted-jes", "SRC-EXPL-LHHS-FY2023-SENATE": "FY2023-SenateReported",
                    "SRC-CJ-AHA-FY2026": "congressional_budget_justification"}[doc_id]
            if want in k: return ROOT / v["stored_path"], k
    if doc_id == "SRC-EXPL-FY2026-PB": return ROOT / "fy26_cjs_jes.pdf", "fy26_cjs_jes.pdf (mapping not hash-confirmed)"
    return None, None
def norm(t): return re.sub(r"\s+", " ", t)
import subprocess, tempfile, hashlib, os
from concurrent.futures import ProcessPoolExecutor
CACHE = Path.home() / ".cache" / "approps_page_check"; CACHE.mkdir(parents=True, exist_ok=True)
def image_page(text): return len(re.findall(r"\d{1,3},\d{3}", text)) < 3
def ocr(args):
    path, idx = args
    key = CACHE / (hashlib.sha1(f"{path}:{idx}".encode()).hexdigest() + ".txt")
    if key.exists(): return key.read_text()
    best = ""
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryDirectory() as td:
        for rot in (0, 90, 270):
            doc = pymupdf.open(path); page = doc[idx]
            page.set_rotation((page.rotation + rot) % 360)
            png = os.path.join(td, f"p{rot}.png")
            page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY).save(png)
            try:
                out = subprocess.run(["tesseract", png, "-", "--psm", "6"], capture_output=True, text=True, env=env, timeout=150).stdout
            except subprocess.TimeoutExpired:
                out = ""
            score = lambda t: len(re.findall(r"\d{1,3}(?:,\d{3})+", t))
            if score(out) > score(best): best = out
    key.write_text(best); return best
def digits_has(text, amt):
    k = abs(amt) // 1000
    t = re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)
    return re.search(r"(?<!\d)" + str(k) + r"(?!\d)", t) is not None
def has(text, amt):
    k = abs(amt) / 1000
    if k != int(k): s = f"{k:,.3f}".rstrip("0").rstrip(".")
    else: s = f"{int(k):,}"
    return re.search(r"(?<![\d,.])" + re.escape(s) + r"(?![\d]|,\d)", text) is not None, s
obs = sheet("Appropriations Observation")
by = defaultdict(list)
for o in obs: by[o["source_document_id"]].append(o)
report = {}
for doc_id in src:
    path, label = local(doc_id)
    rows = by.get(doc_id, [])
    if not path or not path.exists():
        report[doc_id] = {"file": None, "observations": len(rows)}; continue
    pdf = pymupdf.open(path); n = pdf.page_count
    texts = [norm(p.get_text()) for p in pdf]
    need = set()
    for o in rows:
        if o["amount"] in (None, 0): continue
        for p in [int(x) for x in re.findall(r"\d+", str(o["source_page"] or ""))]:
            for q in range(p - 2, p + 3):
                if 1 <= q <= n and image_page(texts[q - 1]): need.add(q)
    ocred = {}
    if need:
        with ProcessPoolExecutor(4) as ex:
            for q, t in zip(sorted(need), ex.map(ocr, [(str(path), q - 1) for q in sorted(need)])): ocred[q] = t
    r_ocr = len(need)
    r = {"file": label, "pages_in_pdf": n, "observations": len(rows), "checked": 0, "match": 0, "zero_or_dash": 0,
         "page_out_of_range": 0, "not_found_nearby": 0, "offsets": Counter(), "misses": []}
    for o in rows:
        amt = o["amount"]; sp = str(o["source_page"] or "").strip()
        if amt in (None, 0): r["zero_or_dash"] += 1; continue
        pages = [int(x) for x in re.findall(r"\d+", sp)]
        if not pages: r["misses"].append((o["observation_id"], sp, "no page")); continue
        lo, hi = min(pages), max(pages)
        r["checked"] += 1
        if hi > n or lo < 1: r["page_out_of_range"] += 1
        def on(q):
            if q in ocred: return digits_has(ocred[q], amt)
            return has(texts[q - 1], amt)[0]
        ok = any(on(p) for p in range(lo, hi + 1) if 1 <= p <= n)
        if ok: r["match"] += 1; continue
        best = None
        for d in sorted(range(-40, 41), key=abs):
            if d == 0: continue
            if any(1 <= p + d <= n and (p + d in ocred or not image_page(texts[p + d - 1])) and on(p + d) for p in range(lo, hi + 1)):
                best = d; break
        if best is None: r["not_found_nearby"] += 1
        else: r["offsets"][best] += 1
        r["misses"].append((o["observation_id"], sp, has("", amt)[1], best))
    r["ocr_pages"] = r_ocr
    report[doc_id] = r
json.dump({k: {**v, "offsets": dict(v.get("offsets", {}))} for k, v in report.items()}, open(sys.argv[2], "w"), indent=1, default=str)
print(f"{'document':34} {'file pages':>10} {'obs':>5} {'checked':>7} {'match':>6} {'zero':>5} {'off-page offsets (count)':>40}")
for k, v in report.items():
    if not v.get("file"): print(f"{k:34} {'no PDF on file':>10} {v['observations']:>5}"); continue
    off = ", ".join(f"{d:+d}:{c}" for d, c in sorted(v["offsets"].items(), key=lambda x: -x[1])[:6])
    print(f"{k:34} {v['pages_in_pdf']:>10} {v['observations']:>5} {v['checked']:>7} {v['match']:>6} {v['zero_or_dash']:>5}  {off}  notfound:{v['not_found_nearby']} oor:{v['page_out_of_range']} ocr:{v.get('ocr_pages',0)}")
