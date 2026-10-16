"""
approps_web.py

A thin, read-only web UI over the store (approps.db, built by
`approps_store.py load`). Local only: binds 127.0.0.1.

    python approps_web.py [--db approps.db] [--port 8765]
    # then open http://127.0.0.1:8765/

Every answer comes from approps_store's own query functions -- resolve()
(the extraction matching rule, agency-scoped, former names, ambiguity) and
history() / history_grid() -- nothing about matching or missing data is
decided here. The database is opened read-only (SQLite mode=ro) per request,
and the server answers GET only.

API
  GET /api/search?q=NAME[&agency=AGENCY]
      matched   -> {"status": "matched", "resolved": {...}, "history": {...}, "grid": {...}}
      ambiguous -> {"status": "ambiguous", "resolved": {...}, "candidates": [...]}   (nothing chosen)
      unmatched -> {"status": "unmatched", "resolved": {...}, "candidates": [...]}   (nearest, nothing chosen)
  GET /api/account/<canonical_account_id>
      a candidate the user picked -> {"status": "picked", "history": {...}, "grid": {...}}
  GET /api/subcommittees
      -> {"subcommittees": ["CJS", "LHHS", ...], "names": {"LHHS": "Labor-HHS-Education", ...}}
         (the stored codes, and each one's display name)
  GET /api/subcommittee/<name>
      every account of the subcommittee side by side -> approps_store.subcommittee_grid()
      (the page picks fiscal years and stages from it)
  GET /api/features
      {"features": {name: bool}, "contract_version": n}: the page's feature flags (features.flags),
      computed from the payloads this server answers with -- the same flags the static export writes
      into docs/data/index.json from the same answers
  GET /api/whats-new
      -> approps_store.whats_new(): every source document and figure by the date it first entered the record,
         newest first (the page's "What's new"; the export writes the same answer to data/whats_new.json)
  GET /api/ops
      -> {"intake": approps_store.ops_intake()}: committee drafts and whether each awaits its official version,
         documents added in the last 30 days (brief D-5; the export writes the same answer to data/ops.json)
  GET /api/documents
      -> approps_store.documents(): every source document's id, short name (web/documents.js) and address, for
         the page's source links (the export writes the same answer to data/documents.json)
"""

import argparse
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import approps_store as S
import features as F
from subcommittees import default_subcommittee, display_name

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "web" / "index.html"
# the page's own files beside index.html (export_static.py copies the same ones): name -> content type
ASSETS = {"match.js": "text/javascript; charset=utf-8", "abbreviations.js": "text/javascript; charset=utf-8",
          "documents.js": "text/javascript; charset=utf-8",
          # the Excel writer Export loads on demand (write-excel-file 4.1.1, MIT, vendored unmodified) and its licence
          "write-excel-file.min.js": "text/javascript; charset=utf-8", "write-excel-file.LICENSE.txt": "text/plain; charset=utf-8"}
FONTS = PAGE.parent / "fonts"                               # IBM Plex Sans, self-hosted (SIL OFL)
BRAND = PAGE.parent / "brand"                               # the logo set: lockups, marks, icons, favicons
BRAND_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


def search(db, q, agency=None):
    conn = S.connect(db, readonly=True)
    try:
        res = S.resolve(conn, q, agency or None)
        resolved = {k: v for k, v in res.items() if k not in ("account", "candidates")}
        if res["account"] is None:
            return {"status": res["match"], "resolved": resolved, "candidates": res["candidates"]}
        resolved["canonical_account_id"] = res["account"]["canonical_account_id"]
        h = S.history(conn, res["account"]["canonical_account_id"])
        return {"status": "matched", "resolved": resolved, "history": h, "grid": S.history_grid(h)}
    finally:
        conn.close()


def account(db, account_id):
    conn = S.connect(db, readonly=True)
    try:
        h = S.history(conn, account_id)
        return {"status": "picked", "history": h, "grid": S.history_grid(h)}
    finally:
        conn.close()


def subcommittees(db):
    conn = S.connect(db, readonly=True)
    try:
        codes = S.subcommittees(conn)
        # the stored codes, and the name the page shows for each (a display mapping, never stored)
        return {"subcommittees": codes, "names": {c: display_name(c) for c in codes}}
    finally:
        conn.close()


def subcommittee(db, name):
    conn = S.connect(db, readonly=True)
    try:
        return S.subcommittee_grid(conn, name)
    finally:
        conn.close()


def document_names():
    """web/documents.js's short names ({source_document_id: name}): the page's display-only map, read as written."""
    text = (PAGE.parent / "documents.js").read_text()
    return {k: json.loads(f'"{v}"') for k, v in re.findall(r'^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$', text, re.M)}


def documents(db):
    conn = S.connect(db, readonly=True)
    try:
        return S.documents(conn, document_names())
    finally:
        conn.close()


def whats_new(db):
    conn = S.connect(db, readonly=True)
    try:
        return S.whats_new(conn)
    finally:
        conn.close()


def ops(db):
    """data/ops.json (brief D-5): {"intake": approps_store.ops_intake()}."""
    conn = S.connect(db, readonly=True)
    try:
        return {"intake": S.ops_intake(conn)}
    finally:
        conn.close()


def stage_status(db):
    conn = S.connect(db, readonly=True)
    try:
        return S.stage_status(conn)
    finally:
        conn.close()


def payloads(db):
    """Every answer the page can read, keyed by the path the static export writes it to under docs/data/:
    index.json (the matching pool), subcommittees.json, subcommittees/<name>.json, accounts/<id>.json --
    each as a client receives it (through JSON)."""
    conn = S.connect(db, readonly=True)
    try:
        pool = S.accounts_for_matching(conn)
    finally:
        conn.close()
    out = {"index.json": {"accounts": F.index_accounts(pool), "stage_status": stage_status(db)}}
    listing = subcommittees(db)
    out["subcommittees.json"] = listing
    out["whats_new.json"] = whats_new(db)
    out["ops.json"] = ops(db)
    out["documents.json"] = documents(db)
    for name in listing["subcommittees"]:
        out[f"subcommittees/{name}.json"] = subcommittee(db, name)
    for a in pool:
        p = account(db, a["canonical_account_id"])
        out[f"accounts/{a['canonical_account_id']}.json"] = {"history": p["history"], "grid": p["grid"]}
    return {k: F.as_served(v) for k, v in out.items()}


_FEATURES = {}


def features(db):
    """The feature flags for this store (cached per database file and its modification time: the store is read-only)."""
    path = Path(db)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    if key not in _FEATURES:
        data = payloads(db)
        _FEATURES[key] = dict(F.flags(list(data), data.__getitem__),
                              default_subcommittee=default_subcommittee(subcommittees(db)["subcommittees"]))
    return _FEATURES[key]


def make_handler(db):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, ctype):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def json(self, status, obj):
            self.send(status, json.dumps(obj, default=str), "application/json; charset=utf-8")

        def do_GET(self):                                     # noqa: N802
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    return self.send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
                if url.path.lstrip("/") in ASSETS:            # loaded by the page (match.js: used only by the static export)
                    name = url.path.lstrip("/")
                    return self.send(HTTPStatus.OK, (PAGE.parent / name).read_bytes(), ASSETS[name])
                if url.path.startswith("/fonts/"):
                    name = url.path[len("/fonts/"):]
                    font = FONTS / name
                    if "/" not in name and name.endswith(".woff2") and font.is_file():
                        return self.send(HTTPStatus.OK, font.read_bytes(), "font/woff2")
                    return self.json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                if url.path.startswith("/brand/"):
                    name = url.path[len("/brand/"):]
                    asset = BRAND / name
                    if "/" not in name and asset.suffix in BRAND_TYPES and asset.is_file():
                        return self.send(HTTPStatus.OK, asset.read_bytes(), BRAND_TYPES[asset.suffix])
                    return self.json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                if url.path == "/api/features":
                    return self.json(HTTPStatus.OK, features(db))
                if url.path == "/api/search":
                    qs = parse_qs(url.query)
                    q = (qs.get("q") or [""])[0].strip()
                    if not q:
                        return self.json(HTTPStatus.BAD_REQUEST, {"error": "empty query"})
                    return self.json(HTTPStatus.OK, search(db, q, (qs.get("agency") or [""])[0].strip()))
                if url.path.startswith("/api/account/"):
                    try:
                        return self.json(HTTPStatus.OK, account(db, unquote(url.path[len("/api/account/"):])))
                    except LookupError as e:
                        return self.json(HTTPStatus.NOT_FOUND, {"error": str(e)})
                if url.path == "/api/whats-new":
                    return self.json(HTTPStatus.OK, whats_new(db))
                if url.path == "/api/ops":
                    return self.json(HTTPStatus.OK, ops(db))
                if url.path == "/api/documents":
                    return self.json(HTTPStatus.OK, documents(db))
                if url.path == "/api/subcommittees":
                    return self.json(HTTPStatus.OK, subcommittees(db))
                if url.path.startswith("/api/subcommittee/"):
                    try:
                        return self.json(HTTPStatus.OK, subcommittee(db, unquote(url.path[len("/api/subcommittee/"):])))
                    except LookupError as e:
                        return self.json(HTTPStatus.NOT_FOUND, {"error": str(e)})
                return self.json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except Exception as e:                            # a real bug: say so, don't hang the page
                return self.json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})

        def refuse(self):
            self.json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only"})

        do_POST = do_PUT = do_PATCH = do_DELETE = refuse

        def log_message(self, fmt, *args):                    # quiet unless asked
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

    return Handler


def serve(db, port=8765, host="127.0.0.1", verbose=True):
    """-> the server (not yet serving); call serve_forever()."""
    S.connect(db, readonly=True).close()                      # fail now if the store isn't there
    httpd = ThreadingHTTPServer((host, port), make_handler(str(db)))
    httpd.verbose = verbose
    return httpd


def main(argv=None):
    p = argparse.ArgumentParser(description="Read-only web UI over the appropriations store.")
    p.add_argument("--db", default=str(S.DEFAULT_DB))
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    try:
        httpd = serve(args.db, args.port)
    except FileNotFoundError:
        raise SystemExit(f"{args.db} not found -- run: python approps_store.py load <workbook>")
    print(f"serving {args.db} read-only at http://127.0.0.1:{httpd.server_address[1]}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
