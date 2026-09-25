"""
govinfo_ingest.py

Detect / Fetch & hash stages of the Data Pipeline (see the scoping doc's
"Data Pipeline and Ingestion Architecture" section). Polls GPO's govinfo.gov
API for new or updated BILLS packages for a set of tracked bills, follows each
one's /related links to its CRPT (committee report) and PLAW (public law)
packages, downloads them, hashes them, and stores
them locally with a manifest -- so re-running this doesn't re-download
anything that hasn't actually changed.

Stops here deliberately. Extraction into the Appropriations Observation
schema (Step 3's extraction schema) is a separate pass over whatever lands
in the document store, once there's something to point it at.

Requires a real api.data.gov key -- DEMO_KEY is rate-limited to a handful of
requests per hour and is only good for testing this script, not running it
on a schedule: https://api.data.gov/signup/

The key is read from GOVINFO_API_KEY in the .env file next to this script
(see .env.example); --api-key overrides it for a one-off run.

Usage:
    python govinfo_ingest.py --tracked-bills HR8845,S2354 --since 2026-01-01

    # Defaults to the last 7 days if --since is omitted
    python govinfo_ingest.py --tracked-bills HR8845
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

# Keys come from the project's .env, not the shell's inherited environment.
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

API_BASE = "https://api.govinfo.gov"
RELATED_COLLECTIONS = ["CRPT", "PLAW"]
STORE_DIR = Path("./document_store")
MANIFEST_PATH = STORE_DIR / "manifest.json"


def api_get(path, api_key, params=None, retries=3, backoff=5):
    """GET against the govinfo API with basic retry/backoff on 429/5xx."""
    params = dict(params or {})
    params["api_key"] = api_key
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}{path}?{query}"
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"})) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        except URLError:
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_new_packages(collection, since_iso, api_key, doc_class=None):
    """
    Detect stage: poll a govinfo.gov collection for packages modified since a
    given ISO-8601 timestamp. Follows the API's own offsetMark pagination --
    start at '*', pull the next offsetMark out of each response's nextPage
    link, stop when there isn't one.
    """
    packages = []
    offset_mark = "*"
    while True:
        params = {"offsetMark": offset_mark, "pageSize": 100}
        if doc_class:
            params["docClass"] = doc_class
        data = api_get(f"/collections/{collection}/{since_iso}", api_key, params)
        packages.extend(data.get("packages", []))
        next_page = data.get("nextPage")
        if not next_page or "offsetMark=" not in next_page:
            break
        offset_mark = next_page.split("offsetMark=")[1].split("&")[0]
    return packages


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


def pdf_link_for(package_id, summary):
    """
    Where a package's PDF lives. BILLS and PLAW summaries carry a
    package-level pdfLink. CRPT (committee report) summaries don't -- their
    PDF hangs off the report's single granule -- but /packages/{id}/pdf
    serves the same file (verified byte-identical to the granule PDF for
    CRPT-119hrpt652 and CRPT-119srpt44). The fallback is CRPT-only: any other
    collection without a pdfLink still reports no_pdf_available.
    """
    link = summary.get("download", {}).get("pdfLink")
    if link:
        return link
    if summary.get("collectionCode") == "CRPT" or package_id.startswith("CRPT-"):
        return f"{API_BASE}/packages/{package_id}/pdf"
    return None


def fetch_and_store(package_id, api_key, manifest):
    """
    Fetch & hash stage: pull a package's summary, download its PDF, hash it,
    and store both -- skipped entirely if the hash already on file matches,
    i.e. nothing actually changed since the last run.
    """
    summary = api_get(f"/packages/{package_id}/summary", api_key)
    pdf_link = pdf_link_for(package_id, summary)
    if not pdf_link:
        return {"package_id": package_id, "status": "no_pdf_available"}

    req = Request(f"{pdf_link}?api_key={api_key}", headers={"Accept": "application/pdf"})
    with urlopen(req) as resp:
        content = resp.read()
    if not content.startswith(b"%PDF"):
        return {"package_id": package_id, "status": "not_a_pdf", "url": pdf_link}
    content_hash = sha256_of(content)

    prior = manifest.get(package_id)
    out_path = STORE_DIR / f"{package_id}.pdf"
    if prior and prior.get("hash") == content_hash and out_path.exists():
        return {"package_id": package_id, "status": "unchanged", "hash": content_hash}

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)

    manifest[package_id] = {
        "hash": content_hash,
        "title": summary.get("title"),
        "doc_class": summary.get("docClass"),
        "date_issued": summary.get("dateIssued"),
        "details_link": summary.get("detailsLink"),
        "stored_path": str(out_path),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"package_id": package_id, "status": "stored", "hash": content_hash, "path": str(out_path)}


def matches_tracked_bill(title, package_id, tracked_bills):
    """
    Cheap relevance filter: does this package's id or title reference one of
    the bill numbers you're tracking (e.g. "HR8845", "S2354")? Good enough
    for a first pass -- a production version would parse the bill number out
    of packageId directly (e.g. BILLS-119hr8845rh -> hr8845) rather than
    substring-matching against the title.
    """
    if not tracked_bills:
        return True
    hay = f"{package_id} {title or ''}".lower().replace("-", "").replace(".", "").replace(" ", "")
    return any(b.lower().replace(".", "").replace(" ", "") in hay for b in tracked_bills)


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


def run(api_key, tracked_bills, since):
    """
    BILLS are detected by polling the collection and matching the bill number.
    Committee reports and public laws can't be found that way -- a report's
    packageId and title don't carry the bill number (S. 2354's report is
    CRPT-119srpt44, titled "DEPARTMENTS OF COMMERCE AND JUSTICE, SCIENCE, ...")
    -- so each matched bill is expanded through govinfo's /related service
    instead of scanning the CRPT / PLAW collections.
    """
    manifest = load_manifest()
    results = []

    def store(package_id):
        try:
            result = fetch_and_store(package_id, api_key, manifest)
            print(f"    {package_id}: {result['status']}")
            results.append(result)
        except (HTTPError, URLError) as e:
            print(f"    {package_id}: fetch failed ({e})", file=sys.stderr)

    print(f"Checking BILLS since {since}...")
    try:
        packages = fetch_new_packages("BILLS", since, api_key)
    except (HTTPError, URLError) as e:
        print(f"  Failed to poll BILLS: {e}", file=sys.stderr)
        packages = []
    bills = [p for p in packages if matches_tracked_bill(p.get("title"), p.get("packageId"), tracked_bills)]
    print(f"  {len(packages)} packages modified, {len(bills)} match tracked bills")
    for pkg in bills:
        store(pkg["packageId"])

    seen = set()   # every version of a bill relates to the same report / law
    for collection in RELATED_COLLECTIONS:
        print(f"Checking {collection} related to matched bills...")
        for pkg in bills:
            try:
                related = fetch_related(pkg["packageId"], collection, api_key)
            except (HTTPError, URLError) as e:
                print(f"  Failed to look up {collection} for {pkg['packageId']}: {e}", file=sys.stderr)
                continue
            print(f"  {pkg['packageId']}: {len(related)} related {collection} package(s)")
            for rel in related:
                if rel["packageId"] not in seen:
                    seen.add(rel["packageId"])
                    store(rel["packageId"])
    save_manifest(manifest)
    return results


def main():
    parser = argparse.ArgumentParser(description="Detect and store new govinfo.gov documents for tracked bills.")
    parser.add_argument("--api-key", default=os.environ.get("GOVINFO_API_KEY"),
                        help="api.data.gov key; defaults to GOVINFO_API_KEY from .env (https://api.data.gov/signup/)")
    parser.add_argument("--tracked-bills", default="", help="Comma-separated bill numbers, e.g. HR8845,S2354")
    parser.add_argument("--since", default=None, help="ISO date to check from, e.g. 2026-01-01 (default: 7 days ago)")
    args = parser.parse_args()
    if not args.api_key:
        parser.error(f"no govinfo key: add GOVINFO_API_KEY to {ENV_PATH} (copy .env.example) or pass --api-key")

    since = args.since
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif "T" not in since:
        since = f"{since}T00:00:00Z"

    tracked = [b.strip() for b in args.tracked_bills.split(",") if b.strip()]
    run(args.api_key, tracked, since)


if __name__ == "__main__":
    main()
