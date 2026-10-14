"""
export_static.py

Static export of the read-only UI for GitHub Pages: docs/ becomes a site
that answers the same questions as approps_web.py without a server.

    python export_static.py [--workbook X.xlsx] [--out docs] [--publish-workbook]

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
    version from the workbook's Read Me), and "features": per feature in
    web/ui_contract.json, true only if every data field it reads is in the
    export (with the contract's "contract_version").
  - Writes docs/data/subcommittees.json (the list) and, per subcommittee,
    docs/data/subcommittees/<name>.json: approps_web.subcommittee() --
    approps_store.subcommittee_grid(), the live /api/subcommittee answer,
    accounting layer included (site_accounting.py, UI-0: each figure's
    views, components, earmark and defense_portion; each row's
    attributes). A file past
    SPLIT_BYTES is also written per fiscal year:
    docs/data/subcommittees/<name>/<FY>.json + index.json (split_grid).
  - Writes docs/data/whats_new.json: approps_web.whats_new() (the live
    /api/whats-new answer), every source document with the date it was
    retrieved into the record, newest first, for the page's "What's new".
  - Copies web/index.html (marked to read data/ instead of /api),
    web/match.js (the browser port of the matching rule), web/abbreviations.js
    (the display-only abbreviation map), web/documents.js (the display-only
    document short names), web/write-excel-file.min.js and its licence (the
    Excel writer the page's Export loads on demand; not the published workbook),
    web/fonts/ (IBM Plex Sans, SIL OFL) and web/brand/ (the logo set and favicons).
  - With --publish-workbook (CI's export job on main, the only writer of
    this file), publishes the built workbook as
    docs/Approps_Pilot_Schema_Loaded.xlsx (the page's "Download workbook"
    link) as a reproducible file (publish_workbook): zip timestamps and
    document dates set to the data's commit date, fixed entry order and
    compression level, so an unchanged build is the same file. Without it,
    the export leaves docs/'s copy as committed: pull requests don't commit it.

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
import subcommittees as SC
import features as F

ROOT = Path(__file__).resolve().parent
DATA_META = '<meta name="approps-data" content="data/">'
ZIP_LEVEL = 9                                     # the Download workbook's deflate level, fixed
DOWNLOAD = "Approps_Pilot_Schema_Loaded.xlsx"       # docs/<this>: the page's "Download workbook" link
CONTRACT = F.CONTRACT                              # the data fields each page feature reads
SPLIT_BYTES = 12 * 1024 * 1024                      # a subcommittee file past this is also split per fiscal year


def reference_workbook():
    """approps_store.reference_workbook(): built from data/staged.json."""
    try:
        return S.reference_workbook()
    except S.LoadError as e:
        raise SystemExit(str(e)) from None


def publish_workbook(workbook, target, when):
    """Copy the built workbook to target as a reproducible file: the same data gives the same bytes.
    openpyxl stamps the build time, so every zip timestamp and the document dates (docProps/core.xml)
    are set to `when`, the data's commit date (an ISO time, or None for 1980-01-01), never the build
    time. The entries are written in a fixed order ([Content_Types].xml first, as Office writes it, then
    by name), each deflated at the fixed level ZIP_LEVEL with the same permissions and system marker,
    so nothing about the machine or the zip library's defaults reaches the file."""
    stamp = (when or "1980-01-01T00:00:00+00:00")[:19]
    date_time = tuple(int(x) for x in re.split(r"[-T:]", stamp))
    with zipfile.ZipFile(workbook) as src:
        entries = {i.filename: src.read(i.filename) for i in src.infolist()}
    order = sorted(entries, key=lambda n: (n != "[Content_Types].xml", n))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=ZIP_LEVEL) as out:
        for name in order:
            data = entries[name]
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", lambda m: m.group(1) + (stamp + "Z").encode(), data)
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3                        # Unix, wherever it is built
            info.external_attr = 0o644 << 16
            out.writestr(info, data, compresslevel=ZIP_LEVEL)
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


field_values = F.field_values        # (moved to features.py, shared with approps_web.py's /api/features)
held = F.held


def features(data, index):
    """index.json's "features": a flag per feature in web/ui_contract.json, true only if every field the
    contract lists for it holds a value somewhere in the exported data (data = out/data; index.json is the
    dict about to be written). Computed from the export, never set by hand (features.flags, the rule
    approps_web.py's /api/features applies to the payloads it serves)."""
    data = Path(data)
    paths = ["index.json"] + [p.relative_to(data).as_posix() for p in sorted(data.rglob("*.json"))
                              if p.relative_to(data).as_posix() != "index.json"]
    return F.flags(paths, lambda p: index if p == "index.json" else json.loads((data / p).read_text()))


def split_grid(g, folder):
    """A subcommittee grid as one file per fiscal year (<folder>/<FY>.json: every row and total with only that
    year's cells) and <folder>/index.json (everything else, and the years). Nothing is recomputed: each year's file
    holds the same cells the whole file does."""
    years = g["fiscal_years"]

    def year_row(row, fy):
        return dict(row, cells={k: c for k, c in row["cells"].items() if k.split("|", 1)[0] == str(fy)},
                    **({"attributes": {k: v for k, v in row["attributes"].items() if k == str(fy)}}
                       if "attributes" in row else {}))
    for fy in years:
        dump({"subcommittee": g["subcommittee"], "fiscal_year": fy, "stages": g["stages"],
              "rows": [year_row(r, fy) for r in g["rows"]],
              "titles": [dict(t, total=year_row(t["total"], fy) if t.get("total") else None) for t in g["titles"]],
              "bill_total": year_row(g["bill_total"], fy) if g.get("bill_total") else None},
             folder / f"{fy}.json", compact=True)
    head = {k: v for k, v in g.items() if k not in ("rows", "titles", "bill_total")}
    head["files"] = {str(fy): f"{fy}.json" for fy in years}
    dump(head, folder / "index.json")
    for stale in folder.glob("*.json"):
        if stale.name != "index.json" and stale.stem not in {str(y) for y in years}:
            stale.unlink()


def export(workbook, out, download=True):
    """download=False leaves out/DOWNLOAD as it is (see main: only CI's export job on main writes docs/'s copy)."""
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
        dump(W.whats_new(str(db)), out / "data" / "whats_new.json")
        split = []
        for name in names:
            g = W.subcommittee(str(db), name)
            # the whole subcommittee side by side is large; compact keeps it small
            path = out / "data" / "subcommittees" / f"{name}.json"
            dump(g, path, compact=True)
            if path.stat().st_size > SPLIT_BYTES:
                split.append(name)
                split_grid(g, out / "data" / "subcommittees" / name)
            elif (out / "data" / "subcommittees" / name).is_dir():
                shutil.rmtree(out / "data" / "subcommittees" / name)
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
        "split_subcommittees": split,
        "default_subcommittee": SC.default_subcommittee(names),
        "accounts": F.index_accounts(pool),
    }
    index.update(features(out / "data", index))
    dump(index, out / "data" / "index.json")
    page = (ROOT / "web" / "index.html").read_text()
    if "<head>" not in page:
        raise SystemExit("web/index.html has no <head> to mark as static")
    page = page.replace("<head>", "<head>\n" + DATA_META, 1)
    # CNAME: the site's custom domain, set by the owner (web/CNAME, copied byte for byte like .nojekyll is written)
    files = [("index.html", page), (".nojekyll", ""), ("CNAME", (ROOT / "web" / "CNAME").read_text())] \
        + [(n, (ROOT / "web" / n).read_text()) for n in W.ASSETS]
    for name, text in files:
        target = out / name
        if not target.exists() or target.read_text() != text:
            target.write_text(text)
    # the self-hosted fonts (IBM Plex Sans, SIL OFL) and their licence
    fonts = out / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    keep = set()
    for src in sorted(W.FONTS.iterdir()):
        if src.suffix in (".woff2", ".txt"):
            keep.add(src.name)
            if not (fonts / src.name).exists() or (fonts / src.name).read_bytes() != src.read_bytes():
                (fonts / src.name).write_bytes(src.read_bytes())
    for stale in fonts.iterdir():
        if stale.name not in keep:
            stale.unlink()
    # the logo set and favicons (web/brand/), byte for byte
    brand = out / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    keep = set()
    for src in sorted(W.BRAND.iterdir()):
        if src.suffix in W.BRAND_TYPES:
            keep.add(src.name)
            if not (brand / src.name).exists() or (brand / src.name).read_bytes() != src.read_bytes():
                (brand / src.name).write_bytes(src.read_bytes())
    for stale in brand.iterdir():
        if stale.name not in keep:
            stale.unlink()
    if built and download:
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
    p.add_argument("--publish-workbook", action="store_true",
                   help="also write docs/" + DOWNLOAD + " (only CI's export job on main passes this)")
    args = p.parse_args(argv)
    # one builder: docs/'s Download workbook is written only by .github/workflows/export-static.yml on main;
    # anywhere else the export leaves it as committed (another --out always gets its own copy)
    to_docs = Path(args.out).resolve() == (ROOT / "docs").resolve()
    download = args.publish_workbook or not to_docs
    r = export(Path(args.workbook) if args.workbook else reference_workbook(), args.out, download=download)
    print(f"exported {r['accounts']} accounts from {r['workbook']} to {r['out']}"
          + ("" if download else f" ({DOWNLOAD} left as committed: CI's export job on main writes it)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
