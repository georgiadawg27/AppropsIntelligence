"""
Report only: the public link each source document and each Bill Report Reference URL
should carry, derived as public_links.py derives it, and whether the file there is the
one we ingested (its sha256 equals the stored hash). Writes link_results_vNN.csv next
to this script; nothing in the workbook or the store changes.

    python reference/review/links/build_link_report.py

Needs document_store/ (the stored files and their hashes) and GOVINFO_API_KEY (.env)
for the Congress.gov lookups. The key goes on requests only; nothing written carries it.

Rows: one per Source Document (field url_or_identifier) and one per Bill Report
Reference URL (bill_url, report_jes_url). Columns: id, field, current_url,
derived_url, origin (congress.gov / govinfo / manual / none), sha256_matched
(yes / no / no stored file), note.
"""

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

import approps_store as S  # noqa: E402
import govinfo_ingest as g  # noqa: E402
import public_links as pl  # noqa: E402

PACKAGE = re.compile(r"(CRPT-\d+[hs]rpt\d+|BILLS-\d+[a-z]+\d+[a-z]+|PLAW-\d+publ\d+|CPRT-\d+[A-Z]+\d+|BUDGET-\d{4}-APP|CREC-\d{4}-\d{2}-\d{2})", re.I)
MANUAL_FOR = {"SRC-EXPL-LHHS-FY2026-ENACTED": "FY2026-Enacted-jes", "SRC-EXPL-LHHS-FY2023-SENATE": "FY2023-SenateReported",
              "SRC-CJ-AHA-FY2026": "congressional_budget_justification"}


def sheet(wb, name):
    rows = list(wb[name].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


class Fetcher:
    """Downloads once per URL -> (sha256 or None when unreachable / not a PDF, why not, the URL it ended at
    after redirects)."""

    def __init__(self):
        self.seen = {}

    def sha(self, url):
        from urllib.request import Request, urlopen
        if url not in self.seen:
            try:
                with urlopen(Request(url, headers={"User-Agent": pl.USER_AGENT, "Accept": "application/pdf"}), timeout=120) as r:
                    body, final = r.read(), r.geturl()
                self.seen[url] = ((hashlib.sha256(body).hexdigest(), None, final) if body.startswith(b"%PDF")
                                  else (None, "not a PDF", final))
            except Exception as e:                       # noqa: BLE001 -- recorded, never raised
                code = getattr(e, "code", None)
                self.seen[url] = (None, f"unreachable ({type(e).__name__}{' ' + str(code) if code else ''})", url)
        return self.seen[url]


def main():
    key = g.clean_key(__import__("os").environ.get("GOVINFO_API_KEY"))
    workbook = S.reference_workbook()
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    sources, brr = sheet(wb, "Source Document"), sheet(wb, "Bill Report Reference")
    manifest = json.loads((ROOT / "document_store" / "manifest.json").read_text())
    fetch = Fetcher()

    def bill_for_report(pkg):
        rep = pl.report_number(pkg)
        if not rep:
            return None
        cite = f"{'H' if rep[1] == 'hrpt' else 'S'}.Rept.{rep[0]}-{rep[2]}"
        return next((b["bill_id"] for b in brr if (b["report_id"] or "").replace(" ", "") == cite), None)

    def stored_key(url, doc_id=None):
        if doc_id in MANUAL_FOR:
            return next((k for k in manifest if MANUAL_FOR[doc_id] in k), None)
        m = PACKAGE.search(url or "")
        if m and m.group(1) in manifest:
            return m.group(1)
        return next((k for k, e in manifest.items() if e.get("source_url") and e["source_url"] == url), None)

    derived_cache = {}

    def derive(url, doc_id=None):
        """-> (derived_url, origin, sha256_matched, note)"""
        url = url or ""
        if not url or url.upper() == "N/A":
            return "", "none", "no stored file", "no URL in the workbook"
        k = stored_key(url, doc_id)
        if k:
            if k in derived_cache:
                return derived_cache[k]
            entry = dict(manifest[k])
            if k.startswith("CRPT-"):
                gr = g.api_get(f"/packages/{k}/granules", key, {"offsetMark": "*", "pageSize": 100}).get("granules", [])
                ids = [x["granuleId"] for x in gr]
                entry["granule_id"] = ids[0] if len(ids) == 1 and ids[0] != k else None
            # each candidate checked with the shared fetcher (one download per URL)
            tried = []
            for cand, origin, note in pl.candidates(k, entry, bill_id=bill_for_report(k), api_key=key):
                if not cand:
                    tried.append(f"{origin}: {note}")
                    continue
                got, why, final = fetch.sha(cand)
                if got == entry["hash"]:
                    moved = final != cand and final.lower().endswith(".pdf")
                    out = (final if moved else cand, origin, "yes", "; ".join(
                        tried + [f"{origin}: matches the stored file ({note})"
                                 + (f"; {cand} redirects to this PDF, which opens at a page" if moved else "")]))
                    break
                tried.append(f"{origin} {cand}: " + (why or "a different file (sha256 differs)"))
            else:
                out = ("", "none", "no", "needs review -- " + "; ".join(tried))
            derived_cache[k] = out
            return out
        # nothing stored for this document: the link it would get, unverified
        m = PACKAGE.search(url)
        if m and not m.group(1).upper().startswith("CREC"):
            pkg = m.group(1)
            gi = pl.govinfo_from_url(url) or (pl.govinfo_pdf(pkg) if "govinfo" in url or "congress.gov" in url else None)
            if gi:
                return gi, "govinfo", "no stored file", "not ingested into document_store: link derived, sha256 not checkable"
        return (url if url.lower().split("?")[0].endswith(".pdf") else ""), "manual" if url.lower().split("?")[0].endswith(".pdf") else "none", \
            "no stored file", "not ingested into document_store; not a govinfo package"

    rows = []
    for d in sources:
        rows.append([d["document_id"], "url_or_identifier", d["url_or_identifier"], *derive(d["url_or_identifier"], d["document_id"])])
        print(rows[-1][0], rows[-1][4], rows[-1][5], flush=True)
    for b in brr:
        for field in ("bill_url", "report_jes_url"):
            rows.append([b["reference_id"], field, b[field] or "", *derive(b[field])])
    text_check = json.dumps(rows)
    assert "api_key" not in text_check and (not key or key not in text_check)
    version = re.search(r"_(v\d+)", workbook.name).group(1)
    out = Path(__file__).with_name(f"link_results_{version}.csv")
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "field", "current_url", "derived_url", "origin", "sha256_matched", "note"])
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
