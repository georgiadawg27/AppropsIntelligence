"""
D-6 round 3: where does the table print each held line, relative to the total? (owner, 2026-10-14: a held line
counts in a total's sum only because the table prints it inside that total -- its position under the total, or the
table's own printed arithmetic -- never because it makes the sum match.)

    python reference/review/accounting/total_lines.py --cache document_store      # downloads what it lacks

For every total cell whose accounts as printed don't add up to the printed total (site_accounting's gaps), and every
line held beside one of its accounts' headlines: the total's own document, as text (pdftotext -layout), the row that
prints the total (its label and the printed total in thousands), and every row printing the line's amount in
thousands. Writes total_lines_found.csv beside this file: one row per (cell, line) with the page and line number of
each row, the rows between them that start with 'Total', and the row texts -- what the evidence file
(total_lines_inside.csv) is judged from. A line from another document than the total's is outside by definition. A
page with no text layer is reported (its rows are read from the page image instead).

Needs pdftotext (poppler). The PDFs are public; nothing here uses a key.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import approps_store as S  # noqa: E402
import total_gaps  # noqa: E402

OUT = HERE / "total_lines_found.csv"
COLUMNS = ["total", "fiscal_year", "stage", "total_observation_id", "document", "printed_total", "line_account",
           "line_observation_id", "line_label", "line_amount", "line_document", "total_pdf_page", "total_row",
           "total_text", "line_hits", "nearest_line_pdf_page", "nearest_line_row", "line_text", "position",
           "totals_between"]


def govinfo(url):
    m = re.search(r"/(\d{3})/crpt/([hs]rpt\d+)/", url or "")
    return f"https://www.govinfo.gov/content/pkg/CRPT-{m.group(1)}{m.group(2)}/pdf/CRPT-{m.group(1)}{m.group(2)}.pdf" \
        if m else url


def pages(doc, url, cache):
    pdf = cache / f"{doc}.pdf"
    if not pdf.exists():
        req = urllib.request.Request(govinfo(url), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            pdf.write_bytes(r.read())
    txt = cache / f"{doc}.txt"
    if not txt.exists():
        subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
    return txt.read_text(errors="replace").split("\f")


def thousands(amount):
    v = abs(amount) // 1000
    return f"{v:,}"


def rows_with(pgs, needle):
    """[(pdf page, row index, text)] of every row holding the number as a whole token."""
    pat = re.compile(r"(?<![\d,])" + re.escape(needle) + r"(?![\d,])")
    return [(i + 1, j, line) for i, p in enumerate(pgs) for j, line in enumerate(p.split("\n")) if pat.search(line)]


def flat(pgs):
    """Every row of the document in order, with its (pdf page, row)."""
    return [(i + 1, j, line) for i, p in enumerate(pgs) for j, line in enumerate(p.split("\n"))]


def printed_label(o):
    """The row label the table prints for a held line: the observation's 'printed as' text, else the last part of its
    location ('... (emergency)', '... (Rescission)', '... (defense)' -> 'Defense function')."""
    loc = o.get("source_table_or_section") or ""
    m = re.search(r"printed as '([^']+)'", loc)
    if m:
        return m.group(1)
    if o.get("component") == "defense":
        return "Defense function"
    last = loc.rsplit(", ", 1)[-1]
    if "(emergency)" in last:
        return "(emergency)"
    return last


def norm(t):
    return re.sub(r"[^a-z0-9()]+", " ", t.lower()).strip()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=str(ROOT / "document_store"))
    args = p.parse_args(argv)
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    data = json.loads(S.STAGED.read_text())
    docs = {d["document_id"]: d for d in data["source_docs"]}
    obs = {o["observation_id"]: o for o in data["observations"]}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        S.load(S.reference_workbook(), db)
        found = total_gaps.gaps(db)
    out = []
    for g in sorted(found, key=lambda g: (g["subcommittee"], g["total"], g["fiscal_year"], g["stage"])):
        doc = g["source_document_id"]
        base = {"total": g["total"], "fiscal_year": g["fiscal_year"], "stage": g["stage"],
                "total_observation_id": g["printed_observation_id"], "document": doc, "printed_total": g["printed"]}
        try:
            pgs = pages(doc, docs[doc].get("url_or_identifier"), cache)
        except Exception as e:  # noqa: BLE001 -- a document not reachable is reported, never guessed at
            for x in g["beside"]:
                out.append(dict(base, line_account=x["account"], line_observation_id=x["observation_id"],
                                line_label=x["label"], line_amount=x["amount"], line_document=x["source_document_id"],
                                position=f"document not readable here: {type(e).__name__}"))
            continue
        rows = flat(pgs)
        index = {(pg, r): k for k, (pg, r, _) in enumerate(rows)}
        tot = [h for h in rows_with(pgs, thousands(g["printed"])) if re.match(r"\s*total,", h[2], re.I)]
        for x in g["beside"]:
            rec = dict(base, line_account=x["account"], line_observation_id=x["observation_id"], line_label=x["label"],
                       line_amount=x["amount"], line_document=x["source_document_id"])
            if x["source_document_id"] != doc:
                out.append(dict(rec, position="another document than the total's"))
                continue
            if not tot:
                textless = sum(1 for pg in pgs if len(pg.strip()) < 1000)
                out.append(dict(rec, position=f"total row not found in the text layer ({textless} pages without text)"))
                continue
            tpg, trow, ttext = tot[0]
            # the line's own row: its printed label and amount, on the total's page or the two before it
            label = printed_label(obs[x["observation_id"]])
            hits = [h for h in rows_with(pgs, thousands(x["amount"])) if tpg - 2 <= h[0] <= tpg
                    and norm(label)[:24] in norm(h[2])]
            rec.update(total_pdf_page=tpg, total_row=trow, total_text=" ".join(ttext.split())[:200],
                       line_hits=len(hits))
            if not hits:
                out.append(dict(rec, position=f"no row printing '{label}' with the amount on pages {tpg - 2}-{tpg}"))
                continue
            # the nearest row printing the amount before the total row (else after it)
            k_tot = index[(tpg, trow)]
            before = [h for h in hits if index[(h[0], h[1])] < k_tot]
            h = max(before, key=lambda h: index[(h[0], h[1])]) if before else \
                min(hits, key=lambda h: index[(h[0], h[1])])
            k = index[(h[0], h[1])]
            lo, hi = sorted((k, k_tot))
            between = [" ".join(t.split())[:80] for _, _, t in rows[lo + 1:hi] if re.match(r"\s*total\b", t, re.I)]
            out.append(dict(rec, nearest_line_pdf_page=h[0], nearest_line_row=h[1],
                            line_text=" ".join(h[2].split())[:200],
                            position=("above the total row" if k < k_tot else "below the total row")
                            + f", {abs(k_tot - k)} rows apart", totals_between=" | ".join(between)))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} lines -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
