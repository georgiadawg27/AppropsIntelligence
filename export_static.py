"""
export_static.py

Static export of the read-only UI for GitHub Pages: docs/ becomes a site
that answers the same questions as approps_web.py without a server.

    python export_static.py [--workbook reference/X.xlsx] [--out docs]

  - Loads the committed reference workbook (the one .xlsx in reference/)
    into a throwaway store with approps_store.load().
  - Writes, for every account, exactly what the live API returns for it:
    approps_web.account() -- history() + history_grid() -- to
    docs/data/accounts/<canonical_account_id>.json.
  - Writes docs/data/index.json: the matching pool
    (approps_store.accounts_for_matching(): canonical names + reviewed
    former names), the matching constants from accounts.py, and which
    workbook this is (name, sha256, date it was committed).
  - Copies web/index.html (marked to read data/ instead of /api) and
    web/match.js, the browser port of the matching rule.

Output is deterministic (sorted keys, no timestamps), so re-running on an
unchanged workbook changes nothing -- .github/workflows/export-static.yml
commits docs/ only when it did change. Freshness = as of the last committed
workbook, not real time.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import accounts as A
import approps_store as S
import approps_web as W

ROOT = Path(__file__).resolve().parent
DATA_META = '<meta name="approps-data" content="data/">'


def reference_workbook():
    found = sorted((ROOT / "reference").glob("*.xlsx"))
    if len(found) != 1:
        raise SystemExit(f"expected exactly one reference workbook in reference/, found {[f.name for f in found]}")
    return found[0]


def committed_date(path):
    """ISO date of the last commit touching the workbook, or None outside git."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        return out or None
    except (OSError, subprocess.CalledProcessError):
        return None


def dump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n"
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
    # an account no longer in the workbook doesn't linger
    for stale in (out / "data" / "accounts").glob("*.json"):
        if stale.stem not in ids:
            stale.unlink()
    index = {
        "source": {"workbook": workbook.name,
                   "workbook_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
                   "workbook_committed": committed_date(workbook),
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
    return {"accounts": len(ids), "workbook": workbook.name, "out": str(out)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    p.add_argument("--workbook", help="default: the one .xlsx in reference/")
    p.add_argument("--out", default=str(ROOT / "docs"))
    args = p.parse_args(argv)
    r = export(Path(args.workbook) if args.workbook else reference_workbook(), args.out)
    print(f"exported {r['accounts']} accounts from {r['workbook']} to {r['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
