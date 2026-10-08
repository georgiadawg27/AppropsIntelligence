"""
A worksheet for the page checks a person still has to do: the observations in review_list.csv whose figure
the text search (and OCR) did not find on its source_page, less the ones the owner resolved in the triage
(page-derived: sums of printed lines, never printed themselves). Report only; no data change.

    python reference/review/page_check_sheet.py

Writes reference/review/page_check_sheet.csv and docs/review/page-checks.html (the same rows, linked);
export_static.py renders that page from the committed CSV too (write_page), so docs/ stays its output.
Each row links the PDF at #page=<source_page> (the first page of a range; page_check.py reads source_page
as a 1-based PDF page index) and, when page_check.py found the figure within 40 pages, that page too.
"""

import csv
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STAGED = ROOT / "data" / "staged.json"
REVIEW_LIST = HERE / "review_list.csv"
MISSES = HERE / "links" / "page_check_misses_v36.csv"
PROPOSAL = HERE / "triage_proposal.csv"
OUT_CSV = HERE / "page_check_sheet.csv"
OUT_HTML = ROOT / "docs" / "review" / "page-checks.html"

COLUMNS = ["observation_id", "account", "canonical_account_id", "fiscal_year", "stage", "figure_thousands",
           "source_document_id", "source_page", "pdf_link", "nearby_page", "nearby_link", "verification_status",
           "group"]


def pages(sp):
    return [int(x) for x in re.findall(r"\d+", str(sp or ""))]


def rows():
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    docs = {d["document_id"]: d for d in data["source_docs"]}
    with open(REVIEW_LIST, newline="") as f:
        items = [r for r in csv.DictReader(f) if r["reason"] == "page not confirmed by text search"]
    with open(MISSES, newline="") as f:
        misses = {r["observation_id"]: r for r in csv.DictReader(f)}
    with open(PROPOSAL, newline="") as f:
        prop = {r["id"]: r for r in csv.DictReader(f)}
    out = []
    for it in items:
        p = prop[it["id"]]
        if p["proposed_disposition"] != "B":
            continue                                   # page-derived: resolved by the owner (group approval)
        o = obs[it["id"]]
        url = docs[o["source_document_id"]]["url_or_identifier"]
        ps = pages(o["source_page"])
        off = (misses.get(it["id"]) or {}).get("nearest_offset") or ""
        near = ""
        if off:
            lo, hi = min(ps) + int(off), max(ps) + int(off)
            near = str(lo) if lo == hi else f"{lo}-{hi}"
        out.append({
            "observation_id": it["id"], "account": acc[o["canonical_account_id"]]["canonical_name"],
            "canonical_account_id": o["canonical_account_id"], "fiscal_year": o["fiscal_year"], "stage": o["stage"],
            "figure_thousands": f"{o['amount'] / 1000:,.0f}" if o["amount"] % 1000 == 0 else f"{o['amount'] / 1000:,}",
            "source_document_id": o["source_document_id"], "source_page": o["source_page"],
            "pdf_link": f"{url}#page={min(ps)}", "nearby_page": near,
            "nearby_link": f"{url}#page={pages(near)[0]}" if near else "",
            "verification_status": o["verification_status"], "group": p["group"]})
    out.sort(key=lambda r: (r["source_document_id"], pages(r["source_page"]), r["observation_id"]))
    return out


def write_page(path=OUT_HTML):
    """Render the page from the committed CSV (export_static.py calls this for docs/review/)."""
    with open(OUT_CSV, newline="") as f:
        write_html(list(csv.DictReader(f)), Path(path))


def write_html(rs, path=OUT_HTML):
    esc = html.escape
    body = []
    for r in rs:
        near = (f'<a href="{esc(r["nearby_link"])}" target="_blank" rel="noopener">p.{esc(r["nearby_page"])}</a>'
                if r["nearby_page"] else "")
        body.append(
            f'<tr data-testid="page-check-row"><td>{esc(r["observation_id"])}</td><td>{esc(r["account"])}</td>'
            f'<td>{r["fiscal_year"]}</td><td>{esc(r["stage"])}</td><td class="n">{esc(r["figure_thousands"])}</td>'
            f'<td>{esc(r["source_document_id"])}</td>'
            f'<td><a href="{esc(r["pdf_link"])}" target="_blank" rel="noopener">p.{esc(str(r["source_page"]))}</a></td>'
            f'<td>{near}</td><td>{esc(r["verification_status"])}</td></tr>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page checks</title>
<style>
  :root {{ --bg: #fff; --fg: #1a1f29; --muted: #5b6474; --line: #e3e6eb; --link: #1f4e99; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #14171c; --fg: #e6e8ec; --muted: #9aa3b2; --line: #2a2f38; --link: #8fb4ff; }} }}
  body {{ background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; margin: 0; padding: 16px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }} p {{ color: var(--muted); margin: 0 0 12px; max-width: 70em; }}
  .wrap {{ overflow-x: auto; }} table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border-bottom: 1px solid var(--line); padding: 4px 8px; text-align: left; white-space: nowrap; }}
  td.n {{ text-align: right; font-variant-numeric: tabular-nums; }} a {{ color: var(--link); }}
</style></head><body>
<h1>Page checks</h1>
<p>{len(rs)} figures the text search and OCR did not find on their cited page. Open the PDF at the page, confirm the
figure (in $ thousands) is printed there, and note the right page if it is elsewhere; "nearby" is where the search
found the same number. Generated by reference/review/page_check_sheet.py; the same rows are in
reference/review/page_check_sheet.csv.</p>
<div class="wrap"><table>
<thead><tr><th>Observation</th><th>Account</th><th>FY</th><th>Stage</th><th>Figure ($K)</th><th>Document</th>
<th>Page</th><th>Nearby</th><th>Status</th></tr></thead>
<tbody>
{chr(10).join(body)}
</tbody></table></div>
</body></html>
""")


def main():
    rs = rows()
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rs)
    write_page()
    print(f"{len(rs)} page checks ({sum(bool(r['nearby_page']) for r in rs)} with a nearby page) -> "
          f"{OUT_CSV.relative_to(ROOT)}, {OUT_HTML.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
