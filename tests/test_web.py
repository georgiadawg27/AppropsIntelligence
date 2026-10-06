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

WORKBOOK = S.reference_workbook()
FOUR = ["President's Budget", "House Reported", "Senate Reported", "Enacted"]


def chromium_path():
    env = os.environ.get("PLAYWRIGHT_CHROMIUM")
    if env and Path(env).exists():
        return env
    found = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


NONFIG = ("missing", "not_funded", "not_collected", "not_enacted")


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
        self.assertEqual({s: {l["state"] for l in fy2027["cells"][s]} for s in FOUR},
                         {"President's Budget": {"not_collected"}, "House Reported": {"missing"},
                          "Senate Reported": {"not_collected"}, "Enacted": {"not_enacted"}})
        # a blank request cell (v13 dropped the $0 rows) is missing, not zero
        g = self.search("NASA Education")["grid"]
        fy2020 = next(r for r in g["rows"] if r["fiscal_year"] == 2020)
        self.assertTrue(all(l["missing"] and not l["observations"] for l in fy2020["cells"]["President's Budget"]))

    def test_every_series_is_listed_in_every_cell(self):
        # R&RA: base and defense budget authority, plus supplemental -- the
        # four FY/stage cells without a supplemental row say so
        g = self.search("NSF Research and Related Activities")["grid"]
        self.assertEqual([(s["amount_type"], s["component"]) for s in g["series"]],
                         [("budget authority", None), ("budget authority", "defense"), ("supplemental", None),
                          ("supplemental", "supplemental_act")])


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
                  lines.push([line.dataset.series, amt ? amt.textContent : line.dataset.state,
                              amt ? line.querySelector('.cite').textContent : null]);
                }
                if (!lines.length) lines.push([null, td.dataset.state || '?', null]);
                out[tr.dataset.fy + '|' + td.dataset.stage] = lines;
              }
            }
            return out;
          }""")

    def assert_faithful(self, q):
        """Every nonzero figure the page shows is the query layer's, with its
        citation; a printed zero shows as not funded; every other year/stage
        cell shows one of the other states."""
        _, h = self.cli(q)
        shown = self.rendered()
        want, zeros = {}, set()
        for o in h["observations"]:
            key = f"{o['fiscal_year']}|{o['stage']}"
            if o["amount"]:
                want.setdefault(key, []).append(
                    (money(o["amount"]), o["source_document_id"] + (f" p.{o['source_page']}" if o["source_page"] else "")))
            else:
                zeros.add(key)
        for key, lines in shown.items():
            got = sorted((a, c) for _, a, c in lines if a not in NONFIG)
            self.assertEqual(got, sorted(want.get(key, [])), key)
            if key not in want:
                self.assertTrue(all(a in NONFIG for _, a, _ in lines), key)
            if key in zeros:
                self.assertIn("not_funded", [a for _, a, _ in lines], key)
        self.assertTrue(set(want) <= set(shown))
        return shown

    def test_nasa_science_full_history(self):
        self.search_ui("NASA Science")
        self.assertEqual(self.page.text_content("[data-testid=account-name]"), "Science")
        shown = self.assert_faithful("NASA Science")
        years = sorted({int(k.split("|")[0]) for k in shown})
        self.assertEqual(years, list(range(2017, 2028)))
        self.assertEqual(sum(1 for k, v in shown.items() if v[0][1] not in NONFIG), 40)
        # FY2027: only the House report is on file; no FY2027 request or Senate document; no enacted law yet
        self.assertEqual({k.split("|")[1]: v[0][1] for k, v in shown.items() if k.startswith("2027|")},
                         {"President's Budget": "not_collected", "House Reported": "missing",
                          "Senate Reported": "not_collected", "Enacted": "not_enacted"})
        # v16 gave Science a rescission series (FY2020); v23 confirmed its
        # absence wherever the document could be checked -- the rest stays missing
        self.assertEqual(shown["2024|Senate Reported"][1][:2], ["rescission", "not_funded"])
        self.assertEqual(shown["2026|Enacted"][1][:2], ["rescission", "not_funded"])
        self.assertEqual(shown["2024|House Reported"][1], ["rescission", "missing", None])      # host unreachable: unchecked
        self.assertEqual(shown["2020|Enacted"], [["budget authority", "$7,138,900,000", "SRC-CRPT-116HRPT455 p.187-188"],
                                                 ["rescission", "\u2212$70,000,000", "SRC-CRPT-116HRPT455 p.191"]])
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
        # a budget amendment line exists only at President's Budget, so an
        # Enacted cell doesn't list one (not even as missing)
        self.assertEqual(shown["2024|Enacted"],
                         [["budget authority", "$7,216,200,000", "SRC-CRPT-118HRPT582 p.247-248"],
                          ["supplemental", "$450,000,000", "SRC-CRPT-118HRPT582 p.247-248"]])
        self.assertEqual(shown["2020|President's Budget"][1][:2], ["other \u00b7 budget_amendment", "$1,374,700,000"])

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
        self.search_ui("NASA Education")
        shown = self.assert_faithful("NASA Education")
        self.assertEqual(shown["2020|President's Budget"], [[None, "missing", None]])
        # OBS-0996: the budget proposed ending it (v26) -- a printed $0 is "Not funded", with its citation on hover
        self.assertEqual(shown["2019|President's Budget"][0][1], "not_funded")
        chip = "tr[data-fy='2019'] td[data-stage=\"President's Budget\"] [data-testid=not-funded]"
        self.assertEqual(self.page.text_content(chip), "Not funded")
        self.assertIn("SRC-BUDGET-APP-FY2019 p.1084", self.page.get_attribute(chip, "title"))
        # no cell renders empty
        empty = self.page.eval_on_selector_all("td[data-stage]", "tds => tds.filter(t => !t.textContent.trim()).length")
        self.assertEqual(empty, 0)

    def test_page_text_is_never_html(self):
        self.search_ui('<img src=x onerror="window.pwned=1">')
        self.assertEqual(self.page.locator("#candidates img").count(), 0)
        self.assertIsNone(self.page.evaluate("window.pwned"))

    # v16's confirmed absences render as 'not applicable' with their evidence
    # and the document checked -- distinct from missing, and never $0.
    def test_not_applicable_missing_and_value(self):
        self.search_ui("NASA Exploration")
        lines = self.page.eval_on_selector_all(
            "tr[data-fy='2017'] td[data-stage='Senate Reported'] .line",
            "ls => ls.map(l => [l.dataset.series, l.dataset.state, l.textContent])")
        states = {a: b for a, b, _ in lines}
        self.assertEqual((states["budget authority"], states["supplemental"]), ("value", "not_funded"))
        text = next(t for a, _, t in lines if a == "supplemental")
        self.assertIn("Not funded", text)
        self.assertIn("SRC-CRPT-114SRPT239", text)
        self.assertNotIn("$0", text)
        conn = S.connect(self.db)
        evidence = conn.execute("SELECT evidence FROM confirmed_absence WHERE confirmed_absence_id = 'CA-0027'").fetchone()[0]
        conn.close()
        self.assertEqual(self.page.get_attribute(
            "tr[data-fy='2017'] td[data-stage='Senate Reported'] [data-testid=not-funded]", "title"), evidence)
        # FY2027, one state per cell: House Reported is covered and unrecorded; the rest have no document or no law yet
        for stage, testid in (("House Reported", "missing"), ("President's Budget", "not-collected"),
                              ("Senate Reported", "not-collected"), ("Enacted", "not-enacted")):
            self.assertEqual(self.page.locator(f"tr[data-fy='2027'] td.empty[data-stage=\"{stage}\"] [data-testid={testid}]").count(),
                             1, stage)

    def test_every_absence_renders(self):
        conn = S.connect(self.db)
        accts = [r[0] for r in conn.execute("SELECT DISTINCT canonical_account_id FROM confirmed_absence")]
        conn.close()
        n = 0
        for acct in accts:
            self.page.goto(self.base + "/")
            self.page.evaluate(f"pick({acct!r}, 'test')")
            self.page.wait_for_selector("[data-testid=result]:not([hidden])")
            # a confirmed absence's chip carries its evidence; a printed zero's says so instead
            n += self.page.eval_on_selector_all("[data-testid=not-funded]",
                                                "cs => cs.filter(c => !c.title.startsWith('Printed as a dash')).length")
            self.assertEqual(self.page.eval_on_selector_all("[data-testid=not-funded]",
                                                            "cs => cs.filter(c => !c.title).length"), 0, acct)
        self.assertEqual(n, 222)                         # v32: CJS's 197 + Labor-HHS's 25


if __name__ == "__main__":
    unittest.main()
