"""
govinfo_ingest.py

Detect / Fetch & hash stages of the Data Pipeline (see the scoping doc's
"Data Pipeline and Ingestion Architecture" section). Polls GPO's govinfo.gov
API for new or updated BILLS, CRPT (committee report), and PLAW (public law)
packages for a set of tracked bills, downloads them, hashes them, and stores
them locally with a manifest -- so re-running this doesn't re-download
anything that hasn't actually changed.

Stops here deliberately. Extraction into the Appropriations Observation
schema (Step 3's extraction schema) is a separate pass over whatever lands
in the document store, once there's something to point it at.

Requires a real api.data.gov key -- DEMO_KEY is rate-limited to a handful of
requests per hour and is only good for testing this script, not running it
on a schedule: https://api.data.gov/signup/

How detection works (verified against the live API):
  - BILLS: the collection is polled for packages modified since --since and
    filtered by parsing the bill type/number out of each packageId
    (BILLS-119hr8845rh -> hr8845).
  - CRPT / PLAW: report and public-law packageIds and titles don't carry the
    bill number (e.g. H.R. 8845's report is CRPT-119hrpt652, titled "COMMERCE,
    JUSTICE, SCIENCE, ..."), and those collections are huge (CRPT includes
    re-indexed Serial Set volumes back to the 1980s). So instead of scanning
    them, each matched bill is expanded via govinfo's /related service, which
    links a bill to its committee reports and enacted public law.

Usage:
    python govinfo_ingest.py --api-key YOUR_KEY --tracked-bills HR8845,S2354 --since 2026-01-01

    # Key can come from the environment instead of the command line
    GOVINFO_API_KEY=... python govinfo_ingest.py --tracked-bills HR8845

    # Defaults to the last 7 days if --since is omitted
    python govinfo_ingest.py --api-key YOUR_KEY --tracked-bills HR8845
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.govinfo.gov"
RELATED_COLLECTIONS = ["CRPT", "PLAW"]
PAGE_SIZE = 1000  # max the collections service allows; 100 meant ~220 calls just to list BILLS
TIMEOUT = 120
STORE_DIR = Path("./document_store")
MANIFEST_PATH = STORE_DIR / "manifest.json"


def api_get(path_or_url, api_key, params=None, retries=3, backoff=5):
    """
    GET against the govinfo API with basic retry/backoff on 429/5xx. Takes
    either a path under API_BASE or a full URL (e.g. a nextPage link, whose
    offsetMark is already URL-encoded and must be passed through untouched).
    """
    url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
    params = dict(params or {})
    params["api_key"] = api_key
    url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        except URLError:
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise


def download(url, api_key):
    """Download a rendition (PDF) and make sure it actually is one."""
    sep = "&" if "?" in url else "?"
    req = Request(f"{url}{sep}{urlencode({'api_key': api_key})}", headers={"Accept": "application/pdf"})
    with urlopen(req, timeout=TIMEOUT) as resp:
        content = resp.read()
    if not content.startswith(b"%PDF"):
        raise ValueError(f"expected a PDF from {url}, got {content[:80]!r}")
    return content


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_new_packages(collection, since_iso, api_key):
    """
    Detect stage: poll a govinfo.gov collection for packages modified since a
    given ISO-8601 timestamp. Follows the API's own offsetMark pagination --
    start at '*', then follow each response's nextPage link until there isn't
    one.
    """
    packages = []
    data = api_get(f"/collections/{collection}/{since_iso}", api_key, {"offsetMark": "*", "pageSize": PAGE_SIZE})
    while True:
        packages.extend(data.get("packages", []))
        next_page = data.get("nextPage")
        if not next_page:
            return packages
        data = api_get(next_page, api_key)


def fetch_related(package_id, collection, api_key):
    """
    Use govinfo's /related service to find the packages in another collection
    (CRPT reports, PLAW public laws) that belong to the same bill.
    """
    try:
        data = api_get(f"/related/{package_id}/{collection}", api_key)
    except HTTPError as e:
        if e.code == 404:  # no relationship of that kind (yet)
            return []
        raise
    return [r for r in data.get("results", []) if r.get("packageId")]


def fetch_and_store(pkg, api_key, manifest, tracked_bill=None):
    """
    Fetch & hash stage. Two levels of skip:
      1. The listing's lastModified matches what's on file (and the file is
         still there) -> nothing to do, no download at all.
      2. lastModified moved but the PDF hashes the same -> don't rewrite it.
    """
    package_id = pkg["packageId"]
    last_modified = pkg.get("lastModified")
    prior = manifest.get(package_id)
    if (
        prior
        and last_modified
        and prior.get("last_modified") == last_modified
        and Path(prior.get("stored_path", "")).exists()
    ):
        return {"package_id": package_id, "status": "unchanged", "hash": prior["hash"]}

    summary = api_get(f"/packages/{package_id}/summary", api_key)
    # CRPT summaries don't list a package-level pdfLink (it lives on the
    # granule), but /packages/{id}/pdf serves the same file -- verified
    # byte-identical against the granule PDF for CRPT-119hrpt652.
    pdf_link = summary.get("download", {}).get("pdfLink") or f"{API_BASE}/packages/{package_id}/pdf"
    try:
        content = download(pdf_link, api_key)
    except HTTPError as e:
        if e.code == 404:
            return {"package_id": package_id, "status": "no_pdf_available"}
        raise
    content_hash = sha256_of(content)

    out_path = STORE_DIR / f"{package_id}.pdf"
    if prior and prior.get("hash") == content_hash and out_path.exists():
        prior["last_modified"] = summary.get("lastModified") or last_modified
        return {"package_id": package_id, "status": "unchanged", "hash": content_hash}

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)

    manifest[package_id] = {
        "hash": content_hash,
        "title": summary.get("title"),
        "collection": summary.get("collectionCode"),
        "doc_class": summary.get("docClass"),
        "tracked_bill": tracked_bill,
        "date_issued": summary.get("dateIssued"),
        "last_modified": summary.get("lastModified") or last_modified,
        "details_link": summary.get("detailsLink"),
        "stored_path": str(out_path),
        "size_bytes": len(content),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    status = "updated" if prior else "stored"
    return {"package_id": package_id, "status": status, "hash": content_hash, "path": str(out_path)}


BILL_TYPES = ("hconres", "sconres", "hjres", "sjres", "hres", "sres", "hr", "s")
BILL_SPEC_RE = re.compile(r"^(?:(\d{2,3}))?(" + "|".join(BILL_TYPES) + r")(\d+)$")
BILL_PACKAGE_RE = re.compile(r"^BILLS-(\d+)(" + "|".join(BILL_TYPES) + r")(\d+)([a-z]+)$")


def parse_bill_spec(spec):
    """
    "HR8845", "H.R. 8845", "hjres5" -> (None, "hr", "8845"); an optional
    congress prefix pins it to one Congress: "119HR8845" -> ("119", "hr", "8845").
    """
    norm = re.sub(r"[\s.\-]", "", spec).lower()
    m = BILL_SPEC_RE.match(norm)
    if not m:
        raise ValueError(f"can't parse bill number {spec!r} (expected e.g. HR8845, S2354, 119HR8845)")
    return m.group(1), m.group(2), str(int(m.group(3)))


def parse_bill_package_id(package_id):
    """BILLS-119hr8845rh -> ("119", "hr", "8845", "rh"), or None."""
    m = BILL_PACKAGE_RE.match(package_id or "")
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


def matches_tracked_bill(package_id, tracked):
    """
    Exact match on bill type + number parsed out of the packageId, so HR884
    doesn't match hr8845 and S5 doesn't match sres5. Returns the matching
    tracked spec (or "*" when nothing is tracked), else None.
    """
    parsed = parse_bill_package_id(package_id)
    if not parsed:
        return None
    if not tracked:
        return "*"
    congress, bill_type, number, _ = parsed
    for spec, (t_congress, t_type, t_number) in tracked.items():
        if bill_type == t_type and number == t_number and (t_congress is None or t_congress == congress):
            return spec
    return None


def check_cbo_estimate(bill_type, bill_number):
    """
    CBO cost estimates aren't on govinfo.gov and don't have an API -- they
    use predictable URLs instead (cbo.gov/cost-estimates/<type>/<number>).
    A HEAD request is enough to detect whether one exists yet. This is a
    stub, not wired into the main run() loop below, since it needs its own
    list of (bill_type, bill_number) pairs to check rather than a collection
    to poll -- build out real RSS/XML feed polling here if push-style
    detection matters more than a periodic guess-and-check.
    """
    from urllib.request import Request, urlopen
    url = f"https://www.cbo.gov/cost-estimates/{bill_type}/{bill_number}"
    try:
        req = Request(url, method="HEAD")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def run(api_key, tracked_specs, since):
    tracked = {spec: parse_bill_spec(spec) for spec in tracked_specs}
    manifest = load_manifest()
    results = []

    def process(pkg, tracked_bill):
        try:
            result = fetch_and_store(pkg, api_key, manifest, tracked_bill)
        except (HTTPError, URLError, ValueError) as e:
            print(f"    {pkg['packageId']}: fetch failed ({e})", file=sys.stderr)
            return
        print(f"    {pkg['packageId']}: {result['status']}")
        results.append(result)
        save_manifest(manifest)  # persist as we go so a crash mid-run doesn't lose work

    print(f"Checking BILLS since {since}...")
    try:
        packages = fetch_new_packages("BILLS", since, api_key)
    except (HTTPError, URLError) as e:
        print(f"  Failed to poll BILLS: {e}", file=sys.stderr)
        return results
    matched = []
    for p in packages:
        spec = matches_tracked_bill(p.get("packageId"), tracked)
        if spec:
            matched.append((p, spec))
    print(f"  {len(packages)} packages modified, {len(matched)} match tracked bills")
    for pkg, spec in matched:
        process(pkg, spec)

    # One /related lookup per bill (per Congress) is enough -- relationships
    # are bill-level, not version-level.
    seen_bills = {}
    for pkg, spec in matched:
        congress, bill_type, number, _ = parse_bill_package_id(pkg["packageId"])
        seen_bills.setdefault((congress, bill_type, number), (pkg["packageId"], spec))
    for collection in RELATED_COLLECTIONS:
        print(f"Checking {collection} related to matched bills...")
        for package_id, spec in seen_bills.values():
            try:
                related = fetch_related(package_id, collection, api_key)
            except (HTTPError, URLError) as e:
                print(f"  Failed to look up {collection} for {package_id}: {e}", file=sys.stderr)
                continue
            print(f"  {package_id}: {len(related)} related {collection} package(s)")
            for pkg in related:
                process(pkg, spec)

    save_manifest(manifest)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Summary: " + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing matched"))
    return results


def main():
    parser = argparse.ArgumentParser(description="Detect and store new govinfo.gov documents for tracked bills.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GOVINFO_API_KEY"),
        help="api.data.gov key (get one at https://api.data.gov/signup/); defaults to $GOVINFO_API_KEY",
    )
    parser.add_argument("--tracked-bills", default="", help="Comma-separated bill numbers, e.g. HR8845,S2354")
    parser.add_argument("--since", default=None, help="ISO date to check from, e.g. 2026-01-01 (default: 7 days ago)")
    args = parser.parse_args()
    if not args.api_key:
        parser.error("an API key is required: pass --api-key or set GOVINFO_API_KEY")

    since = args.since
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif "T" not in since:
        since = f"{since}T00:00:00Z"

    tracked = [b.strip() for b in args.tracked_bills.split(",") if b.strip()]
    try:
        for spec in tracked:
            parse_bill_spec(spec)
    except ValueError as e:
        parser.error(str(e))
    run(args.api_key, tracked, since)


if __name__ == "__main__":
    main()
