"""
The daily intake check (brief D-5): has govinfo published a new or changed bill, committee report or public law
for any subcommittee in the data, for the current or next fiscal year?

    python scripts/intake_watch.py                      # live govinfo (GOVINFO_API_KEY), the last 3 days
    python scripts/intake_watch.py --since-days 30
    python scripts/intake_watch.py --recorded FILE      # recorded govinfo answers instead (tests; no network)
    python scripts/intake_watch.py --plan-out plan.json --summary "$GITHUB_STEP_SUMMARY"

Read-only: it lists govinfo's BILLS, CRPT and PLAW collections modified since the cutoff (GET
/collections/<C>/<since>, the key in a header, never printed), reads each package's title for the subcommittee
(subcommittees.subcommittees_named) and fiscal year, and compares it with what the data holds (data/staged.json's
source documents and data/legislative_text/). It never changes a file in the repository.

  nothing new  -> one summary line; exit 0; no commit, no issue.
  new/changed  -> one PR plan per document (--plan-out): branch intake/<package>, the cells it covers, the steps a
                  PR runs (register the source, fetch_legislative_text.py, intake.py --write per cell) and, where a
                  committee draft we hold stands in for that stage, reconcile_draft.py against it. Opening each PR
                  needs D-4's engine; until it lands the plan is the check's output (the workflow's summary and
                  its plan artifact).

A package is "changed" when govinfo modified it after the date our text file was retrieved (the PR re-fetches it
and keeps the text only if its sha256 differs). A title naming no subcommittee (a continuing resolution, an
omnibus) is listed for every subcommittee in the data, as a law may carry any of them.
"""

import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import approps_store as S  # noqa: E402
import paths  # noqa: E402
import subcommittees as SC  # noqa: E402

API = "https://api.govinfo.gov"
COLLECTIONS = ("BILLS", "CRPT", "PLAW")
STAGE_OF_VERSION = {"rh": "House Reported", "rs": "Senate Reported", "eh": "House Passed", "es": "Senate Passed",
                    "enr": "Enacted", "publ": "Enacted"}
FY_PATTERNS = (re.compile(r"fiscal year ending September 30, (\d{4})", re.I),
               re.compile(r"\bappropriations?\b[^,]*(?:act|bill)?,\s*(\d{4})\b", re.I))


def title_fiscal_year(title):
    """The fiscal year an appropriations title names, or None (not an appropriations title)."""
    if not re.search(r"appropriation", title or "", re.I):
        return None
    for p in FY_PATTERNS:
        m = p.search(title)
        if m:
            return int(m.group(1))
    return None


def stage_of(package_id):
    """The stage a package records, or None (a bill as introduced or placed on the calendar: no stage of its own)."""
    if package_id.startswith("CRPT-"):
        return "House Reported" if "hrpt" in package_id else "Senate Reported" if "srpt" in package_id else None
    if package_id.startswith("PLAW-"):
        return "Enacted"
    m = re.match(r"BILLS-\d{3}[a-z]+?\d+([a-z]+)$", package_id)
    return STAGE_OF_VERSION.get(m.group(1)) if m else None


def held(data, root=None):
    """{package id (upper case): the date its text was retrieved, or None} for every govinfo package the data holds."""
    text_dir = Path(root) / "data" / "legislative_text" if root else paths.text_dir()
    out = {}
    for d in data["source_docs"]:
        m = re.search(r"\b((?:CRPT|BILLS|PLAW)-\d{3}[a-z]+\d+[a-z]*)\b", d.get("url_or_identifier") or "")
        pkg = m.group(1) if m else None
        if not pkg:
            continue
        text = text_dir / f"{d['document_id']}.json"
        retrieved = json.loads(text.read_text()).get("retrieved") if text.exists() else None
        out[pkg.upper()] = retrieved or (d.get("retrieval_timestamp") or "")[:10] or None
    return out


def drafts_by_cell(data):
    """{(subcommittee, fiscal year, stage): [committee draft document ids]}: the documents a draft stage's figures
    cite that are not govinfo or Congress.gov text (approps_store.committee_drafts, read from data/staged.json)."""
    acc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    docs = {d["document_id"]: d for d in data["source_docs"]}
    draft = {(r["subcommittee"], r["fiscal_year"], r["stage"]) for r in data["bill_report_refs"]
             if str(r.get("draft")).upper() == "TRUE"}
    out = {}
    for o in data["observations"]:
        cell = (acc.get(o["canonical_account_id"]), o["fiscal_year"], o["stage"])
        if cell in draft and o.get("verification_status") != "superseded" \
                and not S.is_official_text(docs[o["source_document_id"]]["url_or_identifier"]):
            out.setdefault(cell, set()).add(o["source_document_id"])
    return {k: sorted(v) for k, v in out.items()}


def plan(data, packages, today, root=None):
    """The check over govinfo's package listing: one PR plan per new or changed document for a subcommittee in
    the data and the current or next fiscal year, oldest-modified first."""
    codes = SC.codes(data)
    fy = S.fiscal_year_of(today)
    years = (fy, fy + 1)
    have = held(data, root)
    drafts = drafts_by_cell(data)
    out = []
    for p in sorted(packages, key=lambda p: (p.get("lastModified") or "", p["packageId"])):
        pid, title = p["packageId"], p.get("title") or ""
        year = title_fiscal_year(title)
        if year not in years:
            continue
        named = [c for c in SC.subcommittees_named(title) if c in codes]
        if SC.subcommittees_named(title) and not named:
            continue                                  # another subcommittee's document
        subs = named or codes
        modified = (p.get("lastModified") or "")[:10]
        if pid.upper() in have:
            if not (have[pid.upper()] and modified > have[pid.upper()]):
                continue
            status = "changed"
        else:
            status = "new"
        stage = stage_of(pid)
        cells = [{"subcommittee": c, "fiscal_year": year, "stage": stage} for c in subs] if stage else []
        reconcile = [{"cell": cell, "drafts": drafts[key]} for cell in cells
                     if (key := (cell["subcommittee"], cell["fiscal_year"], cell["stage"])) in drafts]
        steps = ["register the source document (data/staged.json source_docs; added_dates.py dates it)",
                 f"python scripts/fetch_legislative_text.py --documents <its id>  ({pid})"]
        steps += [f"python scripts/intake.py --subcommittee {c['subcommittee']} --fy {year} --stage {stage} --write"
                  for c in cells]
        steps += [f"python scripts/reconcile_draft.py --subcommittee {r['cell']['subcommittee']} --fy {year} "
                  f"--stage \"{stage}\" --official <its id> --rows <its figures> --write  (draft {', '.join(r['drafts'])})"
                  for r in reconcile]
        out.append({"package_id": pid, "collection": p.get("collection") or pid.split("-", 1)[0], "title": title,
                    "status": status, "last_modified": p.get("lastModified"), "date_issued": p.get("dateIssued"),
                    "fiscal_year": year, "subcommittees": subs, "stage": stage, "cells": cells,
                    "reconcile": reconcile, "branch": f"intake/{pid}", "steps": steps})
    return out


def listing(collection, since, get):
    """Every package of a govinfo collection modified since `since` (an ISO date), following nextPage."""
    url = f"{API}/collections/{collection}/{since}T00:00:00Z?pageSize=1000&offsetMark=*"
    out = []
    while url:
        page = get(url)
        for p in page.get("packages", []):
            out.append(dict(p, collection=collection))
        url = page.get("nextPage")
    return out


def live_get():
    import fetch_legislative_text as F
    key = F.api_key()
    if not key:
        raise SystemExit("GOVINFO_API_KEY is not set (environment, or .env): the check can't read govinfo")

    def get(url):
        body = F.get(url, "intake_watch", key)
        if body is None:
            raise SystemExit(f"govinfo answered 4xx for {urllib.parse.urlsplit(url).path}")
        return json.loads(body)
    return get


def recorded_get(path):
    """Answers from a recorded file: {url: response}, matched by URL path and query (the key is never in a URL)."""
    answers = json.loads(Path(path).read_text())
    answers = answers.get("answers", answers)

    def get(url):
        if url not in answers:
            raise SystemExit(f"no recorded answer for {url}")
        return answers[url]
    return get


def summary_lines(found, since, today):
    if not found:
        return [f"Intake check {today}: nothing new or changed on govinfo since {since} (BILLS, CRPT, PLAW)."]
    lines = [f"Intake check {today}: {len(found)} document(s) new or changed on govinfo since {since}.", "",
             "| package | status | FY | stage | subcommittees | reconcile a draft |", "|---|---|---|---|---|---|"]
    for f in found:
        lines.append(f"| {f['package_id']} | {f['status']} | {f['fiscal_year']} | {f['stage'] or '(no stage)'} | "
                     f"{', '.join(f['subcommittees'])} | {'yes' if f['reconcile'] else ''} |")
    lines += ["", "One draft PR per document opens once D-4's engine lands; until then this plan is the output."]
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since-days", type=int, default=3, help="look back this many days (default 3)")
    p.add_argument("--today", help="YYYY-MM-DD (default today, UTC)")
    p.add_argument("--recorded", help="recorded govinfo answers (JSON {url: response}) instead of the network")
    p.add_argument("--plan-out", help="write the PR plans here (JSON)")
    p.add_argument("--summary", help="append the summary (Markdown) to this file")
    a = p.parse_args(argv)
    today = a.today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    since = (datetime.date.fromisoformat(today) - datetime.timedelta(days=a.since_days)).isoformat()
    data = json.loads(paths.staged().read_text())
    get = recorded_get(a.recorded) if a.recorded else live_get()
    packages = [p for c in COLLECTIONS for p in listing(c, since, get)]
    found = plan(data, packages, today)
    lines = summary_lines(found, since, today)
    print("\n".join(lines))
    print(f"({len(packages)} packages modified since {since} read)")
    if a.summary:
        with open(a.summary, "a") as f:
            f.write("\n".join(lines) + "\n")
    if a.plan_out:
        Path(a.plan_out).write_text(json.dumps({"today": today, "since": since, "documents": found}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
