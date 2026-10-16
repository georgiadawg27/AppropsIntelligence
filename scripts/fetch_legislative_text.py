"""
Fetch the bill or law text for every document at a bill or law stage, parse it into appropriation paragraphs,
and save one file per document: data/legislative_text/<document_id>.json. Run by hand, or from the manually
started "Fetch bill and law text" workflow -- never on a pull request. The check that reads these files
(scripts/check_legislative_text.py) runs offline in CI.

    python scripts/fetch_legislative_text.py              # fetch every required document that has no text file yet
    python scripts/fetch_legislative_text.py --all        # fetch them all again
    python scripts/fetch_legislative_text.py --reparse    # re-parse the cached downloads (after a parser change)
    python scripts/fetch_legislative_text.py --only SRC-PLAW-117PUBL328

Sources, in order of preference (no page images, ever):
  govinfo package (BILLS / PLAW)   USLM XML, else the bill XML, else (a public law) the enrolled bill's USLM or
                                   XML, else govinfo's HTML text, else the PDF's text layer
  committee-site draft (a PDF)     the PDF's text layer
A document none of these give usable text for is listed, not saved: propose an exemption for it
(data/legislative_text/exemptions.json, with the reason).

The govinfo API key comes from GOVINFO_API_KEY (environment, or .env locally; a repository secret in CI) and
is sent as a request header, so it is never in a URL, a log line or a saved file. Raw downloads are cached in
document_store/legislative_text/ (gitignored) -- only the parsed JSON is committed.

A Source Document row (data/staged/) is added for a document the data has no row for yet (a bill or law
named only by its Bill Report Reference), with the next free id SRC-<package>; ids are never reused.
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import added_dates  # noqa: E402
import legislative_text as L  # noqa: E402
import staged_data  # noqa: E402

API = "https://api.govinfo.gov"
CACHE = ROOT / "document_store" / "legislative_text"
CALLS = collections.Counter()


def api_key():
    k = os.environ.get("GOVINFO_API_KEY")
    if not k and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if re.match(r"^\s*(export\s+)?GOVINFO_API_KEY\s*=", line):
                k = line.split("=", 1)[1]
    k = (k or "").strip().strip("'\"")
    return k or None


def get(url, counter, key=None, retries=3):
    """GET, counted under `counter`. The key goes in a header (govinfo accepts X-Api-Key), never the URL."""
    headers = {"User-Agent": "AppropsIntelligence legislative-text fetch"}
    if key:
        headers["X-Api-Key"] = key
    for attempt in range(retries):
        CALLS[counter] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(5 * (attempt + 1))


def summary(pkg, key):
    raw = get(f"{API}/packages/{pkg}/summary", "api.govinfo.gov package summary", key)
    return json.loads(raw) if raw else None


def candidates(req, key):
    """[(format, download URL, package, summary)] in order of preference."""
    out = []
    if req["package"]:
        pkgs = [req["package"]]
        if req["package"].startswith("PLAW-") and req.get("enrolled"):
            pkgs.append(req["enrolled"])
        sums = {}
        for p in pkgs:
            sums[p] = summary(p, key)
        for p in pkgs:
            dl = (sums[p] or {}).get("download") or {}
            if dl.get("uslmLink"):
                out.append(("uslm", dl["uslmLink"], p, sums[p]))
            if dl.get("xmlLink"):
                out.append(("bill_xml", dl["xmlLink"], p, sums[p]))
        for p in pkgs:
            dl = (sums[p] or {}).get("download") or {}
            if dl.get("txtLink"):
                out.append(("htm_text", dl["txtLink"], p, sums[p]))
        dl = (sums[req["package"]] or {}).get("download") or {}
        if dl.get("pdfLink"):
            out.append(("pdf_text", dl["pdfLink"], req["package"], sums[req["package"]]))
    else:
        out.append(("pdf_text", req["url"], None, None))
    return out


def divisions(req):
    d = set(req["divisions"].values())
    return sorted(d) if d and len(d) == len(req["subcommittees"]) else None


def usable(paras):
    return any(p["amounts"] for p in paras)


def cached(doc_id, fmt):
    return CACHE / f"{doc_id}.{fmt}"


def fetch_one(req, key, today):
    """-> (text record, summary) or (None, reason)."""
    tried = []
    for fmt, link, pkg, summ in candidates(req, key):
        host = re.match(r"https://([^/]+)", link).group(1)
        raw = get(link, f"{host} download ({fmt})", key if host == "api.govinfo.gov" else None)
        if not raw:
            tried.append(f"{fmt}: not available")
            continue
        CACHE.mkdir(parents=True, exist_ok=True)
        cached(req["document_id"], fmt).write_bytes(raw)
        try:
            paras = L.parse(fmt, raw, divisions(req))
        except Exception as e:                                  # noqa: BLE001 -- a parse failure means try the next format
            tried.append(f"{fmt}: {type(e).__name__}: {e}"[:200])
            continue
        if not usable(paras):
            tried.append(f"{fmt}: no appropriation paragraphs with a dollar amount")
            if fmt in ("uslm", "bill_xml"):
                break               # the structured text was read and has none: a looser reading would only guess
            continue
        return record(req, fmt, link, pkg, raw, paras, today), summ
    return None, "; ".join(tried) or "nothing to download"


def record(req, fmt, link, pkg, raw, paras, retrieved):
    return {
        "document_id": req["document_id"],
        "source_url": req["url"],
        "retrieved_from": {"package": pkg, "format": fmt, "download": link},
        "version_code": req["version"] if pkg == req["package"] else L.version_code(pkg),
        "version": L.VERSION_NAMES.get(req["version"] if pkg == req["package"] else L.version_code(pkg),
                                       req["version"]),
        "stage": req["stage"], "fiscal_year": req["fiscal_year"], "subcommittees": sorted(req["subcommittees"]),
        "references": sorted(req["references"]), "divisions": divisions(req),
        "retrieved": retrieved, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "parser_version": L.parser_version(),
        "paragraph_count": len(paras), "amount_count": sum(len(p["amounts"]) for p in paras),
        "paragraphs": paras,
    }


def reparse(req, old):
    """Re-parse the cached download with today's parser; the sha256 must be the committed one."""
    rf = old["retrieved_from"]
    path = cached(req["document_id"], rf["format"])
    if not path.exists():
        return None, f"no cached download ({path.relative_to(ROOT)}) -- fetch it again"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != old["sha256"]:
        return None, "the cached download's sha256 is not the committed one -- fetch it again"
    paras = L.parse(rf["format"], raw, divisions(req))
    return record(req, rf["format"], rf["download"], rf["package"], raw, paras, old["retrieved"]), None


AGENCY = {"BILLS-": "U.S. {chamber} (bill; govinfo BILLS collection)",
          "PLAW-": "U.S. Congress (public law; govinfo PLAW collection)"}


def source_doc_row(req, summ, today):
    pkg = req["package"]
    chamber = "Senate" if re.match(r"BILLS-\d{3}s", pkg or "") else "House of Representatives"
    agency = (AGENCY["PLAW-"] if (pkg or "").startswith("PLAW-") else AGENCY["BILLS-"].format(chamber=chamber)) \
        if pkg else "Committee on Appropriations (committee draft bill)"
    congress = (summ or {}).get("congress") or ""
    session = (summ or {}).get("session") or ""
    title = (summ or {}).get("title") or ""
    what = f"{L.VERSION_NAMES.get(req['version'], req['version'])} ({pkg})" if pkg else "committee draft"
    return {"document_id": req["document_id"], "source_agency": agency, "url_or_identifier": req["url"],
            "document_type": req["kind"], "congress_session": f"{congress}-{session}" if congress else "",
            "fiscal_year": req["fiscal_year"], "publication_date": (summ or {}).get("dateIssued") or "",
            "stage": req["stage"], "retrieval_timestamp": today, "source_page": "", "also_covers": "",
            "notes": (f"{title} -- {what}. " if title else f"{what}. ")
            + f"The bill or law text for {', '.join(sorted(req['references']))}; parsed into "
              f"data/legislative_text/{req['document_id']}.json (scripts/fetch_legislative_text.py)."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="fetch every required document again")
    ap.add_argument("--reparse", action="store_true", help="re-parse cached downloads with the current parser")
    ap.add_argument("--only", nargs="*", default=None, help="document ids")
    a = ap.parse_args(argv)
    data = staged_data.load()
    reqs, nodoc = L.required_documents(data)
    exempt = L.load_exemptions()
    key = api_key()
    if not key and not a.reparse:
        print("GOVINFO_API_KEY is not set (environment, or .env) -- govinfo packages can't be fetched", file=sys.stderr)
    today = datetime.date.today().isoformat()
    L.TEXT_DIR.mkdir(parents=True, exist_ok=True)
    written, unusable, new_rows = [], [], []
    for doc_id, req in sorted(reqs.items()):
        if a.only is not None and doc_id not in a.only:
            continue
        if doc_id in exempt and not a.only:
            continue
        old = L.load_text(doc_id)
        if a.reparse:
            if not old:
                continue
            rec, why = reparse(req, old)
            if rec is None:
                unusable.append((doc_id, why))
                continue
            summ = None
        else:
            if old and not a.all and a.only is None:
                continue
            rec, summ = fetch_one(req, key, today)
            if rec is None:
                unusable.append((doc_id, summ))
                continue
        L.text_path(doc_id).write_text(L.dump(rec))
        written.append((doc_id, rec["retrieved_from"]["format"], rec["paragraph_count"], rec["amount_count"]))
        if not req["source_doc"] and not any(d["document_id"] == doc_id for d in data["source_docs"]):
            if summ is None and req["package"]:
                summ = summary(req["package"], key)
            new_rows.append(source_doc_row(req, summ, today))
    if new_rows:
        data["source_docs"].extend(new_rows)
        added_dates.stamp(data)                              # D-5: each new document's first-added date, today
        staged_data.save(data)
    for w in written:
        print("wrote %-34s %-9s %4d paragraphs %5d amounts" % w)
    for doc_id, why in unusable:
        print(f"NO USABLE TEXT {doc_id}: {why}")
    for ref_id, why in nodoc:
        print(f"NO DOCUMENT    {ref_id}: {why}" + ("  (exempt)" if ref_id in exempt else ""))
    if new_rows:
        print(f"{len(new_rows)} Source Document rows added: {', '.join(r['document_id'] for r in new_rows)}")
    print("calls: " + (", ".join(f"{k} {v}" for k, v in sorted(CALLS.items())) or "none"))
    print("model cost: $0 (no vision; text layers and XML only)")
    return 1 if unusable else 0


if __name__ == "__main__":
    sys.exit(main())
