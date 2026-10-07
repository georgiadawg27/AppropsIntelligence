"""
Public links: where the site and the workbook send a reader for a source
document -- a PDF anyone can open at a page, never an API fetch link.

Derived automatically, first match wins, and only ever kept when the file at
the link is the file we ingested (its sha256 equals the stored hash):

  1. committee reports: Congress.gov. From the bill we hold, the Congress.gov
     API lists the bill's committee reports; only Appropriations Committee
     reports are kept, a multi-part report is matched by part, and the
     report's text formats give its PDF (www.congress.gov/.../CRPT-...pdf).
  2. govinfo, the fallback and the source for documents Congress.gov has no
     report for (bills, public laws, explanatory statements printed in the
     Congressional Record): www.govinfo.gov/content/pkg/<package>/pdf/<package
     or granule>.pdf, with the granule the download itself used.
  3. manual ingests posted only on a committee site: the --source-url given
     at ingest.

When no candidate matches, the document keeps no public link and is listed
for review ("link needs review"). The Congress.gov API takes the same
api.data.gov key as govinfo (GOVINFO_API_KEY); the key goes on the request
only, is never stored, never shown, never printed.
"""

import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONGRESS_API = "https://api.congress.gov/v3"
GOVINFO_CONTENT = "https://www.govinfo.gov/content/pkg"
APPROPRIATIONS_COMMITTEES = {"hsap00", "ssap00"}   # House / Senate Committee on Appropriations
# Congress.gov's CDN refuses Python's default user agent (Cloudflare error 1010)
USER_AGENT = "AppropsIntelligence/1.0 (+https://github.com/georgiadawg27/AppropsIntelligence)"
BILL_TYPES = {"hr": "hr", "s": "s", "hjres": "hjres", "sjres": "sjres", "hconres": "hconres", "sconres": "sconres",
              "hres": "hres", "sres": "sres"}


# ---- plumbing (tests replace these two) -------------------------------------------------

def http_get(url, accept="*/*"):
    """GET a URL -> bytes. Raises on HTTP / network errors."""
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept}), timeout=120) as r:
        return r.read()


def congress_get(path, api_key, retries=1):
    """A Congress.gov API call -> JSON (one retry on a network error or timeout). The key rides
    on the request and nowhere else."""
    q = urlencode({"format": "json", "api_key": api_key})
    for attempt in range(retries + 1):
        try:
            return json.loads(http_get(f"{CONGRESS_API}/{path.lstrip('/')}?{q}", accept="application/json"))
        except HTTPError:
            raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise


# ---- links -----------------------------------------------------------------------------

def no_api_key(url):
    """Refuse a URL that carries an api_key: one is never stored or shown."""
    if url and re.search(r"api[_-]?key", url, re.I):
        raise ValueError("a stored or shown URL must not carry an api_key")
    return url


def govinfo_pdf(package_id, granule_id=None):
    return f"{GOVINFO_CONTENT}/{package_id}/pdf/{granule_id or package_id}.pdf"


def govinfo_from_url(url):
    """A govinfo landing page or content link for a package (or a granule, e.g. a Congressional
    Record article) -> that document's content PDF; None for anything else."""
    m = re.match(r"https?://(?:www\.)?govinfo\.gov/(?:app/details|content/pkg)/([^/?#]+)(?:/(?:pdf/)?([^/?#]+?)(?:\.pdf)?)?/?$",
                 url or "", re.I)
    if not m:
        return None
    return govinfo_pdf(m.group(1), m.group(2))


def part_of(granule_id):
    """A report's part, from the granule its PDF hangs off (CRPT-118hrpt585-pt1): 1 by default."""
    m = re.search(r"-pt(\d+)$", granule_id or "")
    return int(m.group(1)) if m else 1


def parse_bill(bill_id, congress=None):
    """'H.R.5304' / 'S. 2354' / '119hr5304' / BILLS-119hr5304rh -> (congress, type, number), or None
    (a public law, a budget appendix, 'N/A')."""
    s = re.sub(r"[\s.]", "", bill_id or "").lower()
    m = re.match(r"^(?:bills-)?(\d{2,3})?(hconres|sconres|hjres|sjres|hres|sres|hr|s)(\d+)[a-z]*$", s)
    if not m or not (m.group(1) or congress):
        return None
    return int(m.group(1) or congress), BILL_TYPES[m.group(2)], int(m.group(3))


def report_number(package_id):
    """CRPT-119hrpt271 -> (119, 'hrpt', 271)."""
    m = re.match(r"CRPT-(\d+)([hs]rpt)(\d+)$", package_id or "")
    return (int(m.group(1)), m.group(2), int(m.group(3))) if m else None


def congress_report_pdf(bill, api_key, part=1, number=None):
    """The Congress.gov PDF of the bill's Appropriations Committee report (that part): (url, note).
    bill: (congress, type, number). number: the report number, when we know it."""
    congress, btype, bnum = bill
    reports = congress_get(f"bill/{congress}/{btype}/{bnum}", api_key)["bill"].get("committeeReports") or []
    found = []
    for rep in reports:
        m = re.search(r"/committee-report/(\d+)/(\w+)/(\d+)", rep.get("url", ""))
        if not m:
            continue
        c, rtype, num = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        if number is not None and num != number:
            continue
        for p in congress_get(f"committee-report/{c}/{rtype}/{num}", api_key).get("committeeReports", []):
            approps = {x.get("systemCode") for x in p.get("committees", [])} & APPROPRIATIONS_COMMITTEES
            if approps and int(p.get("part") or 1) == part:
                found.append((c, rtype, num, part))
    if len(found) != 1:
        return None, (f"{len(found)} Appropriations Committee reports (part {part}) for the bill"
                      if found else "no Appropriations Committee report for the bill")
    c, rtype, num, part = found[0]
    stem = f"CRPT-{c}{rtype}{num}"
    url = pdf_for_part(congress_get(f"committee-report/{c}/{rtype}/{num}/text", api_key), stem, part)
    return url, f"{stem} part {part}" + ("" if url else ": no PDF among its text formats")


def pdf_for_part(text_response, stem, part):
    """From a committee report's /text response, the PDF of one part: part 1 is CRPT-...pdf
    (or -pt1.pdf), part N is CRPT-...-ptN.pdf."""
    want = {f"{stem}.pdf", f"{stem}-pt1.pdf"} if part == 1 else {f"{stem}-pt{part}.pdf"}
    for t in text_response.get("text", []):
        for f in t.get("formats", []):
            if f.get("type") == "PDF" and f.get("url", "").rsplit("/", 1)[-1] in want:
                return f["url"]
    return None


def sha_matches(url, sha256):
    """Does the file at url download as a PDF with this sha256? (False on any failure.)"""
    try:
        body = http_get(url, accept="application/pdf")
    except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError):
        return False
    return body.startswith(b"%PDF") and hashlib.sha256(body).hexdigest() == sha256


def candidates(package_id, entry, *, bill_id=None, api_key=None):
    """[(url, source, note)] in the order they are tried."""
    out = []
    manual = entry.get("ingest_method") == "manual"
    rep = report_number(package_id)
    granule = entry.get("granule_id")
    bill = parse_bill(bill_id or entry.get("bill_id"), rep[0] if rep else None)
    if bill and api_key and (rep or entry.get("doc_type") == "committee_report"):
        try:
            url, note = congress_report_pdf(bill, api_key, part_of(granule), rep[2] if rep else None)
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError, KeyError) as e:
            url, note = None, f"Congress.gov lookup failed ({type(e).__name__})"
        out.append((url, "congress.gov", note))
    if not manual and re.match(r"^(CRPT|BILLS|PLAW|CREC|CPRT|BUDGET)-", package_id or ""):
        out.append((govinfo_pdf(package_id, granule), "govinfo", "content PDF"))
    if manual and entry.get("source_url"):
        g = govinfo_from_url(entry["source_url"])
        if g:
            out.append((g, "govinfo", "from the --source-url's govinfo package"))
        out.append((entry["source_url"], "manual", "--source-url"))
    return out


def derive(package_id, entry, *, bill_id=None, api_key=None):
    """The public link for a stored document: {public_url, public_url_source, link_needs_review, tried}.
    Only a link whose file has the stored sha256 is kept."""
    tried = []
    for url, source, note in candidates(package_id, entry, bill_id=bill_id, api_key=api_key):
        if not url:
            tried.append({"source": source, "url": None, "matched": False, "note": note})
            continue
        no_api_key(url)
        ok = sha_matches(url, entry["hash"])
        tried.append({"source": source, "url": url, "matched": ok, "note": note})
        if ok:
            return {"public_url": url, "public_url_source": source, "link_needs_review": False, "tried": tried}
    return {"public_url": None, "public_url_source": None, "link_needs_review": True, "tried": tried}


def apply(manifest_entry, result):
    """Record a derivation on a manifest entry (the public link apart from the fetch link)."""
    manifest_entry["public_url"] = no_api_key(result["public_url"])
    manifest_entry["public_url_source"] = result["public_url_source"]
    manifest_entry["link_needs_review"] = result["link_needs_review"]
    return manifest_entry


def needing_review(manifest):
    return sorted(pid for pid, e in manifest.items() if e.get("link_needs_review"))
