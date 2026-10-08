"""
export_static.py

Static export of the read-only UI for GitHub Pages: docs/ becomes a site
that answers the same questions as approps_web.py without a server.

    python export_static.py [--workbook X.xlsx] [--out docs]

  - Builds the workbook from the canonical data, data/staged.json
    (approps_store.reference_workbook(): scripts/build_workbook.py, which
    stops on any failed check), and loads it into a throwaway store with
    approps_store.load().
  - Writes, for every account, exactly what the live API returns for it:
    approps_web.account() -- history() + history_grid() -- to
    docs/data/accounts/<canonical_account_id>.json.
  - Writes docs/data/index.json: the matching pool
    (approps_store.accounts_for_matching(): canonical names + reviewed
    former names), the matching constants from accounts.py, and which data
    this is (data/staged.json: its sha256, the date it was committed, its
    version from the workbook's Read Me).
  - Writes docs/data/subcommittees.json (the list) and, per subcommittee,
    docs/data/subcommittees/<name>.json: approps_web.subcommittee() --
    approps_store.subcommittee_grid(), the live /api/subcommittee answer.
  - Copies web/index.html (marked to read data/ instead of /api) and
    web/match.js, the browser port of the matching rule.
  - Publishes the built workbook as docs/Approps_Pilot_Schema_Loaded.xlsx
    (the page's "Download workbook" link), its zip timestamps and document
    dates set to the data's commit date so an unchanged build is the same file.

Output is deterministic (sorted keys, no timestamps), so re-running on an
unchanged data/staged.json changes nothing -- .github/workflows/export-static.yml
commits docs/ only when it did change. Freshness = as of the last committed
data, not real time.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import accounts as A
import approps_store as S
import approps_web as W

ROOT = Path(__file__).resolve().parent
DATA_META = '<meta name="approps-data" content="data/">'
DOWNLOAD = "Approps_Pilot_Schema_Loaded.xlsx"       # docs/<this>: the page's "Download workbook" link


def reference_workbook():
    """approps_store.reference_workbook(): built from data/staged.json."""
    try:
        return S.reference_workbook()
    except S.LoadError as e:
        raise SystemExit(str(e)) from None


def publish_workbook(workbook, target, when):
    """Copy the built workbook to target with every zip timestamp and its document dates
    (docProps/core.xml) set to `when` (an ISO time, or None for 1980-01-01): openpyxl stamps
    the build time, which would make every rebuild a different file."""
    stamp = (when or "1980-01-01T00:00:00+00:00")[:19]
    date_time = tuple(int(x) for x in re.split(r"[-T:]", stamp))
    with zipfile.ZipFile(workbook) as src:
        entries = [(i.filename, src.read(i.filename)) for i in src.infolist()]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in entries:
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", lambda m: m.group(1) + (stamp + "Z").encode(), data)
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            out.writestr(info, data)
    if target.exists() and target.read_bytes() == tmp.read_bytes():
        tmp.unlink()
    else:
        tmp.replace(target)


def committed_date(path):
    """UTC ISO time of the last commit touching the workbook, or None outside git.
    Formatted here, not by git: git versions render %cI differently (+00:00 vs Z),
    which would make the export differ between a laptop and the Actions runner."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return datetime.fromtimestamp(int(out), timezone.utc).isoformat() if out else None


def dump(obj, path, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = {"separators": (",", ":")} if compact else {"indent": 1}
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str, **fmt) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def export(workbook, out):
    out = Path(out)
    workbook = Path(workbook)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "approps.db"
        report = S.load(workbook, db)
        conn = S.connect(db, readonly=True)
        try:
            pool = S.accounts_for_matching(conn)
        finally:
            conn.close()
        ids = [a["canonical_account_id"] for a in pool]
        for aid in ids:
            payload = W.account(str(db), aid)
            dump({"history": payload["history"], "grid": payload["grid"]}, out / "data" / "accounts" / f"{aid}.json")
        listing = W.subcommittees(str(db))
        names = listing["subcommittees"]
        dump(listing, out / "data" / "subcommittees.json")
        for name in names:
            # the whole subcommittee side by side is large; compact keeps it ~1.5 MB
            dump(W.subcommittee(str(db), name), out / "data" / "subcommittees" / f"{name}.json", compact=True)
    # an account or subcommittee no longer in the workbook doesn't linger
    for folder, keep in (("accounts", ids), ("subcommittees", names)):
        for stale in (out / "data" / folder).glob("*.json"):
            if stale.stem not in keep:
                stale.unlink()
    data_committed = committed_date(S.STAGED)
    built = workbook.resolve() == S.BUILT_WORKBOOK.resolve()
    index = {
        "source": {"data": str(S.STAGED.relative_to(ROOT)) if built else None,
                   "data_sha256": hashlib.sha256(S.STAGED.read_bytes()).hexdigest() if built else None,
                   "data_committed": data_committed if built else committed_date(workbook),
                   "version": S.data_version(workbook),
                   "workbook": workbook.name,
                   "download": DOWNLOAD if built else None,
                   "rows": report["rows"], "warnings": report["warnings"]},
        "matching": {"accept_max_distance": A.ACCEPT_MAX_DISTANCE, "accept_max_fraction": A.ACCEPT_MAX_FRACTION,
                     "ambiguity_margin": A.AMBIGUITY_MARGIN},
        "accounts": [{"canonical_account_id": a["canonical_account_id"], "canonical_name": a["canonical_name"],
                      "agency": a["agency"], "historical_names": a["historical_names"]} for a in pool],
    }
    dump(index, out / "data" / "index.json")
    page = (ROOT / "web" / "index.html").read_text()
    if "<head>" not in page:
        raise SystemExit("web/index.html has no <head> to mark as static")
    page = page.replace("<head>", "<head>\n" + DATA_META, 1)
    for name, text in (("index.html", page), ("match.js", (ROOT / "web" / "match.js").read_text()), (".nojekyll", "")):
        target = out / name
        if not target.exists() or target.read_text() != text:
            target.write_text(text)
    if built:
        publish_workbook(workbook, out / DOWNLOAD, data_committed)
    # the page checks still open for a person (reference/review/page_check_sheet.csv), linked to the PDFs
    sys.path.insert(0, str(ROOT / "reference" / "review"))
    import page_check_sheet
    page_check_sheet.write_page(out / "review" / "page-checks.html")
    return {"accounts": len(ids), "workbook": workbook.name, "out": str(out)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    p.add_argument("--workbook", help="default: the workbook built from data/staged.json")
    p.add_argument("--out", default=str(ROOT / "docs"))
    args = p.parse_args(argv)
    r = export(Path(args.workbook) if args.workbook else reference_workbook(), args.out)
    print(f"exported {r['accounts']} accounts from {r['workbook']} to {r['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
