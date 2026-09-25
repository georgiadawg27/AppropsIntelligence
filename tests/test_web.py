"""
The read-only web UI (approps_web.py, web/index.html) over the store built
from the committed pilot workbook.

Run:  python -m unittest tests.test_web -v

API tests always run. Browser tests drive the real page in headless Chromium
and need the playwright package (pip install playwright; the browser itself
is found at /opt/pw-browsers or via PLAYWRIGHT_CHROMIUM) -- skipped without.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import approps_store as S  # noqa: E402
import approps_web as W  # noqa: E402

try:
    import openpyxl  # noqa: F401
except ImportError:                                  # pragma: no cover
    openpyxl = None
try:
    from playwright.sync_api import sync_playwright
except ImportError:                                  # pragma: no cover
    sync_playwright = None

WORKBOOK = ROOT / "reference" / "CJS_Title_III_Science_Pilot_Schema_Loaded_v14.xlsx"
FOUR = ["President's Budget", "House Reported", "Senate Reported", "Enacted"]


def chromium_path():
    env = os.environ.get("PLAYWRIGHT_CHROMIUM")
    if env and Path(env).exists():
        return env
    found = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


def money(v):
    return ("−$" if v < 0 else "$") + f"{abs(v):,}"


@unittest.skipUnless(openpyxl, "openpyxl not installed")
class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "approps.db"
        S.load(WORKBOOK, cls.db)
        conn = S.connect(cls.db)
        with conn:
            cls.prepare(conn)
        conn.close()
        cls.httpd = W.serve(cls.db, port=0, verbose=False)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def prepare(cls, conn):
        """Store changes a test class needs before the server starts."""

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, method="GET"):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def search(self, q):
        return self.get("/api/search?q=" + urllib.parse.quote(q))[1]

    def cli(self, q):
        """The same question through the query layer directly."""
        conn = S.connect(self.db)
        try:
            res = S.resolve(conn, q)
            return res, (S.history(conn, res["account"]["canonical_account_id"]) if res["account"] else None)
        finally:
            conn.close()


class Api(WebTest):
    def test_answers_are_the_query_layers(self):
        for q in ("NASA Science", "Exploration", "Deep Space Exploration Systems", "Deep Spaee Exploratlon Systems",
                  "LEO and Spaceflight Operations", "NASA Education", "NSF Research and Related Activities"):
            res, h = self.cli(q)
            d = self.search(q)
            self.assertEqual(d["status"], "matched", q)
            self.assertEqual(d["resolved"]["canonical_account_id"], res["account"]["canonical_account_id"], q)
            self.assertEqual((d["resolved"]["match"], d["resolved"]["via"], d["resolved"]["matched_name"]),
                             (res["match"], res["via"], res["matched_name"]), q)
            self.assertEqual([o["observation_id"] for o in d["history"]["observations"]],
                             [o["observation_id"] for o in h["observations"]], q)

    def test_ambiguous_returns_candidates_and_chooses_nothing(self):
        d = self.search("Office of Inspector General")
        self.assertEqual(d["status"], "ambiguous")
        self.assertNotIn("history", d)
        self.assertEqual([c["canonical_account_id"] for c in d["candidates"]], ["ACC-DOJ-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"])

    def test_picked_candidate_and_unknown_id(self):
        status, d = self.get("/api/account/ACC-NSF-OIG")
        self.assertEqual((status, d["status"], d["history"]["account"]["agency"]),
                         (200, "picked", "National Science Foundation"))
        self.assertEqual(self.get("/api/account/ACC-NOPE")[0], 404)

    def test_read_only(self):
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.get("/api/search?q=x", method)[0], 405, method)
        conn = S.connect(self.db, readonly=True)
        with self.assertRaises(Exception):
            conn.execute("DELETE FROM account")
        conn.close()

    def test_missing_is_missing_in_the_grid(self):
        g = self.search("NASA Science")["grid"]
        fy2027 = next(r for r in g["rows"] if r["fiscal_year"] == 2027)
        self.assertEqual([l["missing"] for s in FOUR for l in fy2027["cells"][s]], [True] * 4)
        # a blank request cell (v13 dropped the $0 rows) is missing, not zero
        g = self.search("NASA Space Technology")["grid"]
        fy2019 = next(r for r in g["rows"] if r["fiscal_year"] == 2019)
        self.assertTrue(all(l["missing"] and not l["observations"] for l in fy2019["cells"]["President's Budget"]))

    def test_every_series_is_listed_in_every_cell(self):
        # R&RA: base and defense budget authority, plus supplemental -- the
        # four FY/stage cells without a supplemental row say so
        g = self.search("NSF Research and Related Activities")["grid"]
        self.assertEqual([(s["amount_type"], s["component"]) for s in g["series"]],
                         [("budget authority", None), ("budget authority", "defense"), ("supplemental", None)])
        missing = [(r["fiscal_year"], st) for r in g["rows"] for st in FOUR
                   if r["fiscal_year"] < 2027 and r["cells"][st][2]["missing"]]
        self.assertEqual(len(missing), 4)


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class Browser(WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=chromium_path())

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def search_ui(self, q):
        self.page.goto(self.base + "/")
        self.page.fill("#q", q)
        self.page.click("button[type=submit]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden]), [data-testid=candidates]:not([hidden])")

    def rendered(self):
        """{(fy, stage): [(series, amount text, citation)]} as shown."""
        return self.page.evaluate("""() => {
            const out = {};
            for (const tr of document.querySelectorAll('#grid tbody tr')) {
              for (const td of tr.querySelectorAll('td[data-stage]')) {
                const lines = [];
                for (const line of td.querySelectorAll('.line')) {
                  const amt = line.querySelector('[data-testid=amount]');
                  lines.push([line.dataset.series, amt ? amt.textContent : 'missing',
                              amt ? line.querySelector('.cite').textContent : null]);
                }
                if (!lines.length) lines.push([null, td.querySelector('[data-testid=missing]') ? 'missing' : '?', null]);
                out[tr.dataset.fy + '|' + td.dataset.stage] = lines;
              }
            }
            return out;
          }""")

    def assert_faithful(self, q):
        """Every figure the page shows is the query layer's, with its
        citation; every other year/stage cell says missing."""
        _, h = self.cli(q)
        shown = self.rendered()
        want = {}
        for o in h["observations"]:
            want.setdefault(f"{o['fiscal_year']}|{o['stage']}", []).append(
                (money(o["amount"]), f"{o['source_document_id']} p.{o['source_page']}"))
        for key, lines in shown.items():
            got = sorted((a, c) for _, a, c in lines if a != "missing")
            self.assertEqual(got, sorted(want.get(key, [])), key)
            if key not in want:
                self.assertTrue(all(a == "missing" for _, a, _ in lines), key)
        self.assertTrue(set(want) <= set(shown))
        return shown

    def test_nasa_science_full_history(self):
        self.search_ui("NASA Science")
        self.assertEqual(self.page.text_content("[data-testid=account-name]"), "Science")
        shown = self.assert_faithful("NASA Science")
        years = sorted({int(k.split("|")[0]) for k in shown})
        self.assertEqual(years, list(range(2017, 2028)))
        self.assertEqual(sum(1 for k, v in shown.items() if v[0][1] != "missing"), 40)
        self.assertEqual([v[0][1] for k, v in shown.items() if k.startswith("2027|")], ["missing"] * 4)
        self.assertEqual(shown["2024|Senate Reported"], [["budget authority", "$7,340,920,000", "SRC-CRPT-118SRPT62 p.219-220"]])
        self.assertEqual(shown["2026|Enacted"], [["budget authority", "$7,250,000,000", "SRC-EXPL-FY2026-PB p.128-130"]])
        href = self.page.get_attribute("td[data-stage='Enacted'] a >> nth=0", "href")
        self.assertTrue(href.endswith("#page=120"), href)        # FY2017 Enacted: H.Rept. 115-231, p.120

    def test_former_name_exact_and_misspelled(self):
        for q, typed in (("Deep Space Exploration Systems", False), ("Deep Spaee Exploratlon Systems", True)):
            self.search_ui(q)
            self.assertEqual(self.page.text_content("[data-testid=account-name]"), "Exploration")
            note = self.page.text_content("[data-testid=former-name]")
            self.assertIn("Matched through a former name: “Deep Space Exploration Systems”", note)
            self.assertEqual("2 edits away" in note, typed, q)
            self.assert_faithful(q)
        self.search_ui("Exploration")
        self.assertEqual(self.page.locator("[data-testid=former-name]").count(), 0)   # canonical: no note

    def test_base_and_supplemental_are_separate_lines(self):
        self.search_ui("NASA Exploration")
        shown = self.assert_faithful("NASA Exploration")
        self.assertEqual(shown["2024|Enacted"],
                         [["budget authority", "$7,216,200,000", "SRC-CRPT-118HRPT582 p.247-248"],
                          ["supplemental", "$450,000,000", "SRC-CRPT-118HRPT582 p.247-248"]])

    def test_leo_resolves_to_space_operations(self):
        self.search_ui("LEO and Spaceflight Operations")
        self.assertEqual(self.page.text_content("[data-testid=account-name]"), "Space Operations")
        self.assertIn("“LEO and Spaceflight Operations”", self.page.text_content("[data-testid=former-name]"))
        shown = self.assert_faithful("LEO and Spaceflight Operations")
        self.assertIn("$4,624,600,000", [a for _, a, _ in shown["2019|President's Budget"]])

    def test_ambiguous_shows_candidates_and_the_pick(self):
        self.search_ui("Office of Inspector General")
        self.assertFalse(self.page.is_visible("[data-testid=result]"))
        ids = self.page.eval_on_selector_all("[data-testid=candidate]", "bs => bs.map(b => b.dataset.id)")
        self.assertEqual(ids, ["ACC-DOJ-OIG", "ACC-NASA-OIG", "ACC-NSF-OIG"])
        self.page.click("[data-testid=candidate][data-id=ACC-NSF-OIG]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.assertIn("National Science Foundation", self.page.text_content("#account-panel .meta"))
        self.assertIn("Picked from the matches", self.page.text_content("[data-testid=picked]"))

    def test_missing_is_never_zero_or_blank(self):
        self.search_ui("NASA Space Technology")
        shown = self.assert_faithful("NASA Space Technology")
        for cell in ("2019|President's Budget", "2019|House Reported", "2020|President's Budget"):
            self.assertEqual(shown[cell], [[None, "missing", None]], cell)
        # no cell renders empty
        empty = self.page.eval_on_selector_all("td[data-stage]", "tds => tds.filter(t => !t.textContent.trim()).length")
        self.assertEqual(empty, 0)

    def test_page_text_is_never_html(self):
        self.search_ui('<img src=x onerror="window.pwned=1">')
        self.assertEqual(self.page.locator("#candidates img").count(), 0)
        self.assertIsNone(self.page.evaluate("window.pwned"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class ThreeStates(Browser):
    """A confirmed absence renders as 'not applicable' with its evidence and
    the document checked -- distinct from missing, and never $0."""

    @classmethod
    def prepare(cls, conn):
        conn.execute("DELETE FROM appropriations_observation WHERE observation_id = 'OBS-0605'")
        conn.execute("INSERT INTO confirmed_absence VALUES ('CA-1', 'ACC-NASA-EXPLORATION', 2017, 'Senate Reported', "
                     "'supplemental', NULL, 'SRC-CRPT-114SRPT239', 'No Exploration (emergency) line in the table', "
                     "'2026-09-25')")

    def test_not_applicable_missing_and_value(self):
        self.search_ui("NASA Exploration")
        lines = self.page.eval_on_selector_all(
            "tr[data-fy='2017'] td[data-stage='Senate Reported'] .line",
            "ls => ls.map(l => [l.dataset.series, l.dataset.state, l.textContent])")
        self.assertEqual([(a, b) for a, b, _ in lines], [("budget authority", "value"), ("supplemental", "not_applicable")])
        self.assertIn("not applicable", lines[1][2])
        self.assertIn("SRC-CRPT-114SRPT239", lines[1][2])
        self.assertNotIn("$0", lines[1][2])
        self.assertEqual(self.page.get_attribute("[data-testid=not-applicable]", "title"),
                         "No Exploration (emergency) line in the table")
        self.assertEqual(self.page.locator("tr[data-fy='2027'] [data-testid=missing]").count(), 4)
