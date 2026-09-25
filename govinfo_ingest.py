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

    # Bill numbers mean the current Congress; prefix one to track another
    python govinfo_ingest.py --tracked-bills 118S2321 --since 2023-01-01

    # A document the pipeline can't fetch itself (JES, advance copy of a report)
    python govinfo_ingest.py ingest-local fy26_cjs_jes.pdf --subcommittee CJS --fiscal-year 2026 \\
        --stage Enacted --doc-type jes --source-url https://www.appropriations.senate.gov/imo/media/doc/fy26_cjs_jes.pdf
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
        "ingest_method": "govinfo_api",
        "advance_copy": False,
        "confirmation_status": "official",
    }
    return {"package_id": package_id, "status": "stored", "hash": content_hash, "path": str(out_path)}


# ---------------------------------------------------------------------------
# Manual ingest: documents the pipeline can't source itself -- a committee
# report posted before GPO processes it (advance copy), a JES (never a
# govinfo package). Same store, same manifest, same hashing as
# fetch_and_store; the extraction and validation that follow are identical.
# The person finds the file; they don't vouch for its numbers.
# ---------------------------------------------------------------------------

STAGES = ("President's Budget", "House Reported", "Senate Reported", "House Passed", "Senate Passed", "Enacted")
DOC_TYPES = ("committee_report", "jes", "bill", "public_law", "other")


def manual_document_id(subcommittee, fiscal_year, stage, doc_type, content_hash):
    stage_slug = re.sub(r"[^A-Za-z]", "", stage)
    return f"MANUAL-{subcommittee}-FY{fiscal_year}-{stage_slug}-{doc_type}-{content_hash[:8]}"


def ingest_local(pdf_path, manifest, *, subcommittee, fiscal_year, stage, doc_type, advance_copy,
                 source_url=None, source_agency=None, bill_id=None, report_id=None, ingested_by=None):
    """
    Store a local PDF into document_store/ and the manifest. An advance copy
    starts "unconfirmed" until reconciled against GPO's official version; a
    JES (or anything else ingested by hand that isn't an advance copy) has no
    official counterpart to reconcile against.
    """
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {DOC_TYPES}, got {doc_type!r}")
    if advance_copy and doc_type == "jes":
        raise ValueError("a JES is never a govinfo package, so it can't be an advance copy of one")
    content = Path(pdf_path).read_bytes()
    if not content.startswith(b"%PDF"):
        raise ValueError(f"{pdf_path} is not a PDF")
    content_hash = sha256_of(content)
    for pid, entry in manifest.items():
        if entry.get("hash") == content_hash and Path(entry.get("stored_path", "")).exists():
            return {"package_id": pid, "status": "unchanged", "hash": content_hash}
    doc_id = manual_document_id(subcommittee, fiscal_year, stage, doc_type, content_hash)
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STORE_DIR / f"{doc_id}.pdf"
    out_path.write_bytes(content)
    if bill_id:
        parse_bill_spec(bill_id)                  # reject a malformed bill number up front
    manifest[doc_id] = {
        "hash": content_hash,
        "title": Path(pdf_path).name,
        "doc_class": doc_type,
        "stored_path": str(out_path),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingest_method": "manual",
        "ingested_by": ingested_by,
        "source_url": source_url,
        "source_agency": source_agency,
        "subcommittee": subcommittee,
        "fiscal_year": int(fiscal_year),
        "stage": stage,
        "doc_type": doc_type,
        "bill_id": bill_id,
        "report_id": report_id,
        "advance_copy": bool(advance_copy),
        "confirmation_status": "unconfirmed" if advance_copy else "no_official_counterpart",
        "reconciled_with_document_id": None,
    }
    return {"package_id": doc_id, "status": "stored", "hash": content_hash, "path": str(out_path)}


BILL_TYPES = ("hconres", "sconres", "hjres", "sjres", "hres", "sres", "hr", "s")
BILL_SPEC_RE = re.compile(r"^(?:(\d{2,3}))?(" + "|".join(BILL_TYPES) + r")(\d+)$")
BILL_PACKAGE_RE = re.compile(r"^BILLS-(\d+)(" + "|".join(BILL_TYPES) + r")(\d+)([a-z]+)$")


def current_congress(today=None):
    """The Congress in session on a date: the 1st began in 1789, each runs two
    years from January 3 of an odd year (Jan 1-2 of an odd year still belong
    to the previous one)."""
    today = today or datetime.now(timezone.utc).date()
    year = today.year - 1 if today.year % 2 == 1 and (today.month, today.day) < (1, 3) else today.year
    return str((year - 1789) // 2 + 1)


def parse_bill_spec(spec, today=None):
    """
    "HR8845", "H.R. 8845", "hjres5" -> ("119", "hr", "8845") -- pinned to the
    current Congress, so the same number from another Congress never matches;
    a congress prefix pins a different one: "118S2321" -> ("118", "s", "2321").
    """
    norm = re.sub(r"[\s.\-]", "", spec).lower()
    m = BILL_SPEC_RE.match(norm)
    if not m:
        raise ValueError(f"can't parse bill number {spec!r} (expected e.g. HR8845, S2354, 118S2321)")
    return m.group(1) or current_congress(today), m.group(2), str(int(m.group(3)))


def parse_bill_package_id(package_id):
    """BILLS-119hr8845rh -> ("119", "hr", "8845", "rh"), or None."""
    m = BILL_PACKAGE_RE.match(package_id or "")
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


def matches_tracked_bill(package_id, tracked):
    """
    Exact match on Congress + bill type + number parsed out of the packageId,
    so HR884 doesn't match hr8845, S5 doesn't match sres5, and 118th-Congress
    H.R. 8845 doesn't match 119th-Congress H.R. 8845. tracked maps each spec
    to parse_bill_spec(spec). Returns the matching tracked spec (or "*" when
    nothing is tracked), else None.
    """
    parsed = parse_bill_package_id(package_id)
    if not parsed:
        return None
    if not tracked:
        return "*"
    congress, bill_type, number, _ = parsed
    for spec, (t_congress, t_type, t_number) in tracked.items():
        if (congress, bill_type, number) == (t_congress, t_type, t_number):
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


RETRY_DELAY_SECONDS = 5


def transient(e):
    """A failure worth one retry: the network, a server error, or throttling."""
    if isinstance(e, HTTPError):
        return e.code >= 500 or e.code == 429
    return isinstance(e, (URLError, TimeoutError, ConnectionError))


def with_retry(call, sleep=None):
    """call() once, and once more after RETRY_DELAY_SECONDS if the first
    failure is transient. -> (result, attempts). Re-raises the last error."""
    try:
        return call(), 1
    except (HTTPError, URLError, TimeoutError, ConnectionError) as e:
        if not transient(e):
            e.attempts = 1
            raise
        (sleep or time.sleep)(RETRY_DELAY_SECONDS)
    try:
        return call(), 2
    except (HTTPError, URLError, TimeoutError, ConnectionError) as e:
        e.attempts = 2
        raise


def run(api_key, tracked_bills, since):
    """
    BILLS are detected by polling the collection and matching the bill number.
    Committee reports and public laws can't be found that way -- a report's
    packageId and title don't carry the bill number (S. 2354's report is
    CRPT-119srpt44, titled "DEPARTMENTS OF COMMERCE AND JUSTICE, SCIENCE, ...")
    -- so each matched bill is expanded through govinfo's /related service
    instead of scanning the CRPT / PLAW collections.
    """
    tracked = {spec: parse_bill_spec(spec) for spec in tracked_bills}
    manifest = load_manifest()
    results = []

    # A failure is retried once if transient, then recorded as a result --
    # never only printed, so a caller sees what didn't arrive.
    def store(package_id):
        try:
            result, attempts = with_retry(lambda: fetch_and_store(package_id, api_key, manifest))
            if attempts > 1:
                result["attempts"] = attempts
            print(f"    {package_id}: {result['status']}" + (" (after a retry)" if attempts > 1 else ""))
            results.append(result)
        except (HTTPError, URLError, TimeoutError, ConnectionError) as e:
            print(f"    {package_id}: fetch failed ({e})", file=sys.stderr)
            results.append({"package_id": package_id, "status": "fetch_failed", "error": str(e),
                            "attempts": getattr(e, "attempts", 1)})

    print(f"Checking BILLS since {since}...")
    try:
        packages = fetch_new_packages("BILLS", since, api_key)
    except (HTTPError, URLError) as e:
        print(f"  Failed to poll BILLS: {e}", file=sys.stderr)
        packages = []
    bills = [p for p in packages if matches_tracked_bill(p.get("packageId"), tracked)]
    print(f"  {len(packages)} packages modified, {len(bills)} match tracked bills")
    for pkg in bills:
        store(pkg["packageId"])

    seen = set()   # every version of a bill relates to the same report / law
    for collection in RELATED_COLLECTIONS:
        print(f"Checking {collection} related to matched bills...")
        for pkg in bills:
            try:
                related, _ = with_retry(lambda: fetch_related(pkg["packageId"], collection, api_key))
            except (HTTPError, URLError, TimeoutError, ConnectionError) as e:
                print(f"  Failed to look up {collection} for {pkg['packageId']}: {e}", file=sys.stderr)
                results.append({"package_id": pkg["packageId"], "status": "related_lookup_failed",
                                "collection": collection, "error": str(e), "attempts": getattr(e, "attempts", 1)})
                continue
            print(f"  {pkg['packageId']}: {len(related)} related {collection} package(s)")
            for rel in related:
                if rel["packageId"] not in seen:
                    seen.add(rel["packageId"])
                    store(rel["packageId"])
                if collection == "CRPT" and rel["packageId"] in manifest:
                    reconcile_advance_copies(manifest, pkg["packageId"], rel["packageId"])
    save_manifest(manifest)
    return results


def reconcile_advance_copies(manifest, bill_package_id, official_package_id):
    """An official committee report just arrived: reconcile any unconfirmed
    advance copy for the same bill and stage against it (reconcile.py)."""
    import reconcile                               # pulls in the extraction pipeline; only needed here
    for advance_id in reconcile.advance_copies_for(manifest, bill_package_id, official_package_id):
        save_manifest(manifest)
        report = reconcile.reconcile(advance_id, official_package_id, manifest, STORE_DIR,
                                     Path("./extractions"), manifest_path=MANIFEST_PATH)
        print(f"    reconciled advance copy {advance_id} against {official_package_id}: {report['outcome']}")


def main_ingest_local(argv):
    parser = argparse.ArgumentParser(prog="govinfo_ingest.py ingest-local",
                                     description="Store a document the pipeline can't fetch itself.")
    parser.add_argument("pdf")
    parser.add_argument("--subcommittee", required=True, help="e.g. CJS")
    parser.add_argument("--fiscal-year", type=int, required=True)
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--doc-type", required=True, choices=DOC_TYPES)
    parser.add_argument("--advance-copy", action="store_true",
                        help="posted before GPO processed it; reconciled when the official package appears")
    parser.add_argument("--source-url", help="where the file was found")
    parser.add_argument("--source-agency", help="who published it, e.g. 'Senate Committee on Appropriations'")
    parser.add_argument("--bill-id", help="e.g. S2354 -- lets reconciliation find the official report via /related")
    parser.add_argument("--report-id", help="e.g. S.Rept.119-44, if known")
    parser.add_argument("--ingested-by", help="who found the file")
    args = parser.parse_args(argv)
    manifest = load_manifest()
    try:
        res = ingest_local(args.pdf, manifest, subcommittee=args.subcommittee, fiscal_year=args.fiscal_year,
                           stage=args.stage, doc_type=args.doc_type, advance_copy=args.advance_copy,
                           source_url=args.source_url, source_agency=args.source_agency, bill_id=args.bill_id,
                           report_id=args.report_id, ingested_by=args.ingested_by)
    except ValueError as e:
        parser.error(str(e))
    save_manifest(manifest)
    print(f"{res['package_id']}: {res['status']}" + (f" -> {res['path']}" if res.get("path") else ""))
    return res


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "ingest-local":
        return main_ingest_local(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Detect and store new govinfo.gov documents for tracked bills.")
    parser.add_argument("--api-key", default=os.environ.get("GOVINFO_API_KEY"),
                        help="api.data.gov key; defaults to GOVINFO_API_KEY from .env (https://api.data.gov/signup/)")
    parser.add_argument("--tracked-bills", default="", help="Comma-separated bill numbers, e.g. HR8845,S2354 (current Congress) or 118S2321")
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
    try:
        for spec in tracked:
            parse_bill_spec(spec)
    except ValueError as e:
        parser.error(str(e))
    run(args.api_key, tracked, since)


if __name__ == "__main__":
    main()
