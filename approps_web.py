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
"""

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import approps_store as S

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "web" / "index.html"


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
