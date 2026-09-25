"""
The static export (export_static.py -> docs/): the same answers as the live
UI, from pre-generated JSON and browser-side matching.

Run:  python -m unittest tests.test_static -v

Matcher parity runs web/match.js under node (skipped without node); browser
tests need playwright + chromium, as tests/test_web.py.
"""

import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import approps_store as S  # noqa: E402
import approps_web as W  # noqa: E402
import export_static as E  # noqa: E402
from test_web import WORKBOOK, chromium_path, sync_playwright  # noqa: E402

NODE = shutil.which("node")


def fresh_export(out):
    return E.export(WORKBOOK, out)


def names_and_variants(pool):
    """Queries a user could type: every name as is, with its agency in
    front, and one- and two-edit misspellings of each; agencies alone;
    junk."""
    qs = set()
    agencies = sorted({a["agency"] for a in pool})
    for a in pool:
        for n in [a["canonical_name"]] + a["historical_names"]:
            qs.update({n, n.lower(), n.upper(), "  " + n + "  ", f"NASA {n}", f"NSF {n}", f"DOJ {n}", f"{a['agency']} {n}"})
            letters = [i for i, ch in enumerate(n) if ch.isalpha()]
            for k, i in enumerate(letters):
                qs.add(n[:i] + n[i + 1:])                                   # deletion
                qs.add(n[:i] + ("x" if n[i] != "x" else "q") + n[i + 1:])   # substitution
                if k % 2 == 0 and i + 3 < len(n):
                    qs.add(n[:i] + n[i + 1] + n[i] + n[i + 2:])             # transposition (two edits)
                    qs.add(n[:i] + "zz" + n[i + 2:])                        # two substitutions
    for ag in agencies:
        qs.update({ag, ag.lower(), "".join(w[0] for w in ag.split() if w.lower() not in S.STOPWORDS)})
    qs.update({"Office of Inspector General", "Nothing Like Any Account", "a", "NASA", "NSF", "National",
               "Science Science", "Deep Spaee Exploratlon Systems", "Scince", "Space Operation", "Educaton",
               "<img src=x>", "Exploration Technology", "Space Tech", "Defense function", "STEM"})
    return sorted(qs)


def python_answer(res):
    return {"query": res["query"], "agency": res["agency"], "name": res["name"], "match": res["match"],
            "distance": res["distance"], "matched_name": res["matched_name"], "via": res["via"],
            "account": res["account"]["canonical_account_id"] if res["account"] else None,
            "candidates": res["candidates"]}


class StaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "docs"
        fresh_export(cls.out)
        cls.index = json.loads((cls.out / "data" / "index.json").read_text())
        cls.db = Path(cls.tmp.name) / "approps.db"
        S.load(WORKBOOK, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Export(StaticTest):
    def test_every_account_file_is_the_live_api_payload(self):
        ids = [a["canonical_account_id"] for a in self.index["accounts"]]
        self.assertEqual(len(ids), 31)
        self.assertEqual(sorted(p.stem for p in (self.out / "data" / "accounts").glob("*.json")), sorted(ids))
        for aid in ids:
            live = json.loads(json.dumps(W.account(str(self.db), aid), default=str))
            got = json.loads((self.out / "data" / "accounts" / f"{aid}.json").read_text())
            self.assertEqual(got, {"history": live["history"], "grid": live["grid"]}, aid)

    def test_index_is_the_matching_pool(self):
        conn = S.connect(self.db, readonly=True)
        pool = S.accounts_for_matching(conn)
        conn.close()
        self.assertEqual([(a["canonical_account_id"], a["canonical_name"], a["agency"], a["historical_names"])
                          for a in pool],
                         [(a["canonical_account_id"], a["canonical_name"], a["agency"], a["historical_names"])
                          for a in self.index["accounts"]])
        self.assertEqual(self.index["source"]["workbook"], WORKBOOK.name)
        self.assertEqual(self.index["source"]["warnings"], [])

    def test_deterministic_and_stale_files_removed(self):
        with tempfile.TemporaryDirectory() as d:
            again = Path(d) / "docs"
            fresh_export(again)
            (again / "data" / "accounts" / "ACC-GONE.json").write_text("{}")
            fresh_export(again)
            self.assertFalse((again / "data" / "accounts" / "ACC-GONE.json").exists())
            a = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
            b = {p.relative_to(again): p.read_bytes() for p in again.rglob("*") if p.is_file()}
            self.assertEqual(a, b)

    def test_page_is_the_live_page_marked_static(self):
        live = (ROOT / "web" / "index.html").read_text()
        static = (self.out / "index.html").read_text()
        self.assertEqual(static, live.replace("<head>", "<head>\n" + E.DATA_META, 1))
        self.assertEqual((self.out / "match.js").read_text(), (ROOT / "web" / "match.js").read_text())

    def test_commit_date_does_not_depend_on_the_git_version(self):
        # git 2.55 (the Actions runner) renders %cI as ...Z, older git as
        # ...+00:00; the export must not flip between them
        def fake_git(cmd, **kw):
            fmt = next(a for a in cmd if a.startswith("--format="))
            out = {"--format=%cI": "2026-09-25T20:55:20Z", "--format=%ct": "1790369720"}[fmt]
            return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
        with mock.patch.object(E.subprocess, "run", fake_git):
            self.assertEqual(E.committed_date(WORKBOOK), "2026-09-25T20:55:20+00:00")

    def test_committed_docs_are_current(self):
        # docs/ is what the export makes from the committed workbook and web/
        # (the Actions workflow regenerates it on push; this catches a stale copy)
        committed = ROOT / "docs"
        a = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        b = {p.relative_to(committed): p.read_bytes() for p in committed.rglob("*") if p.is_file()}
        self.assertEqual(sorted(a), sorted(b))
        self.assertEqual([k for k in a if a[k] != b[k]], [])


@unittest.skipUnless(NODE, "node not installed")
class MatcherParity(StaticTest):
    """web/match.js gives approps_store.resolve()'s answer for every query."""

    def test_same_answers_as_python(self):
        queries = names_and_variants(self.index["accounts"])
        self.assertGreater(len(queries), 2000)
        conn = S.connect(self.db, readonly=True)
        want = [python_answer(S.resolve(conn, q)) for q in queries]
        conn.close()
        script = f"""
            const m = require({json.dumps(str(ROOT / "web" / "match.js"))});
            const idx = require({json.dumps(str(self.out / "data" / "index.json"))});
            const qs = JSON.parse(require("fs").readFileSync(0, "utf8"));
            m.configure(idx.matching);
            const out = qs.map(q => {{
              const r = m.resolve(idx.accounts, q);
              return {{query: r.query, agency: r.agency, name: r.name, match: r.match, distance: r.distance,
                       matched_name: r.matched_name, via: r.via,
                       account: r.account ? r.account.canonical_account_id : null, candidates: r.candidates}};
            }});
            process.stdout.write(JSON.stringify(out));
        """
        got = json.loads(subprocess.run([NODE, "-e", script], input=json.dumps(queries), capture_output=True,
                                        text=True, check=True).stdout)
        diffs = [(q, w, g) for q, w, g in zip(queries, want, got) if w != g]
        self.assertEqual(diffs[:3], [])
        kinds = {w["match"] for w in want}
        self.assertEqual(kinds, {"exact", "ocr_corrected", "ambiguous", "unmatched"})    # every outcome exercised


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class StaticVersusLive(StaticTest):
    """The static site renders exactly what the live UI renders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        handler = functools.partial(Quiet, directory=str(cls.out))
        cls.static = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.live = W.serve(cls.db, port=0, verbose=False)
        for srv in (cls.static, cls.live):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.static_url = f"http://127.0.0.1:{cls.static.server_address[1]}/"
        cls.live_url = f"http://127.0.0.1:{cls.live.server_address[1]}/"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=chromium_path())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for srv in (cls.static, cls.live):
            srv.shutdown()
            srv.server_close()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def shown(self, url, q, pick=None):
        self.page.goto(url)
        self.page.fill("#q", q)
        self.page.click("button[type=submit]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden]), [data-testid=candidates]:not([hidden])")
        if pick:
            self.page.click(f"[data-testid=candidate][data-id={pick}]")
            self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        return self.page.evaluate("""() => ({
            candidates: document.querySelector('#candidates').hidden ? null : document.querySelector('#candidates').innerText,
            result: document.querySelector('#result').hidden ? null : document.querySelector('#result').innerText,
            grid: document.querySelector('#grid').innerHTML,
            message: document.querySelector('#message').innerText })""")

    def same(self, q, pick=None):
        live, static = self.shown(self.live_url, q, pick), self.shown(self.static_url, q, pick)
        self.assertEqual(static, live, q)
        return static

    def test_nasa_science(self):
        r = self.same("NASA Science")
        self.assertIn("FY2017", r["result"])
        self.assertIn("$7,250,000,000", r["result"])

    def test_former_name_exact_and_misspelled(self):
        for q in ("Deep Space Exploration Systems", "Deep Spaee Exploratlon Systems"):
            r = self.same(q)
            self.assertIn("Matched through a former name", r["result"])
        self.assertIn("2 edits away", r["result"])

    def test_leo_resolves_to_space_operations(self):
        r = self.same("LEO and Spaceflight Operations")
        self.assertTrue(r["result"].startswith("Space Operations"))

    def test_ambiguous_picker_and_pick(self):
        r = self.same("Office of Inspector General")
        self.assertIsNone(r["result"])
        for oig in ("ACC-DOJ-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"):
            self.assertIn(oig, r["candidates"])
        r = self.same("Office of Inspector General", pick="ACC-NSF-OIG")
        self.assertIn("National Science Foundation", r["result"])

    def test_three_states_and_other_queries(self):
        for q in ("NASA Exploration", "NASA Space Technology", "NSF Research and Related Activities", "NASA",
                  "Crime Victims Fund", "Scince", "Nothing Like Any Account", "NASA Education"):
            self.same(q)

    def test_static_page_says_how_fresh_it_is(self):
        self.page.goto(self.static_url)
        self.page.wait_for_selector("[data-testid=freshness]:not([hidden])")
        text = self.page.text_content("[data-testid=freshness]")
        self.assertIn(WORKBOOK.name, text)
        self.assertIn("not live", text)
        self.page.goto(self.live_url)
        self.assertTrue(self.page.locator("[data-testid=freshness]").is_hidden())


if __name__ == "__main__":
    unittest.main()
