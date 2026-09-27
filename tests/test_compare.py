"""
The subcommittee grid: every account of a subcommittee side by side
(approps_store.subcommittee_grid(), /api/subcommittee/<name>, the static
export's data/subcommittees/<name>.json, and the page's "Subcommittee grid"
view).

Run:  python -m unittest tests.test_compare -v

Browser tests need playwright + chromium, as tests/test_web.py; each runs
against the live server and the static export.
"""

import functools
import http.server
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import approps_store as S  # noqa: E402
import approps_web as W  # noqa: E402
import export_static as E  # noqa: E402
from test_web import FOUR, WORKBOOK, chromium_path, money, sync_playwright  # noqa: E402

YEARS = list(range(2017, 2027))


def plain(obj):
    return json.loads(json.dumps(obj, default=str))


def label(line):
    return line["amount_type"] + (" · " + line["component"] if line["component"] else "")


def expected_cell(cell):
    """What the page must show for one cell, from the Python grid: the
    whole-cell missing rule, the budget-authority headline, and every other
    line behind "more"."""
    lines = cell["lines"]
    if not lines or all(line["state"] == "missing" for line in lines):
        return {"empty": True, "outside": cell["outside_history"]}
    head = next((line for line in lines if line["amount_type"] == "budget authority" and not line["component"]),
                {"amount_type": "budget authority", "component": None, "state": "missing", "observations": []})
    return {"empty": False, "outside": cell["outside_history"],
            "head": [head["state"], [money(o["amount"]) for o in head["observations"]]],
            "more": [[label(line), line["state"], [money(o["amount"]) for o in line["observations"]]]
                     for line in lines if line is not head]}


SHOWN_JS = """() => [...document.querySelectorAll('[data-testid=compare-cell]')].map(td => {
    const acct = td.closest('tr').dataset.account;
    const amounts = (n) => [...n.querySelectorAll(':scope > .amt')].map(a => a.textContent);
    const out = {account: acct, fy: Number(td.dataset.fy), stage: td.dataset.stage,
                 empty: td.classList.contains('empty'), outside: td.dataset.outside === 'true'};
    if (!out.empty) {
      const head = td.querySelector(':scope > .line');
      out.head = [head.dataset.state, amounts(head)];
      out.more = [...td.querySelectorAll(':scope > details.more > .line')].map(l => [l.dataset.series, l.dataset.state, amounts(l)]);
    }
    return out;
})"""


class CompareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "approps.db"
        S.load(WORKBOOK, cls.db)
        conn = S.connect(cls.db, readonly=True)
        cls.grid = plain(S.subcommittee_grid(conn, "CJS"))
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, aid):
        return next(r for r in self.grid["rows"] if r["account"]["canonical_account_id"] == aid)


class GridData(CompareTest):
    def test_rows_are_every_account_of_the_subcommittee(self):
        conn = S.connect(self.db, readonly=True)
        want = sorted(r[0] for r in conn.execute("SELECT canonical_account_id FROM account WHERE subcommittee = 'CJS'"))
        conn.close()
        got = [r["account"]["canonical_account_id"] for r in self.grid["rows"]]
        self.assertEqual(sorted(got), want)
        self.assertEqual(len(got), 31)
        self.assertEqual(self.grid["stages"], FOUR)                   # no account has a Passed stage
        self.assertEqual(self.grid["fiscal_years"], list(range(2017, 2028)))
        # former names resolve into their account, never a row of their own
        names = {r["account"]["canonical_name"] for r in self.grid["rows"]}
        self.assertNotIn("LEO and Spaceflight Operations", names)
        self.assertNotIn("Deep Space Exploration Systems", names)
        self.assertIn("LEO and Spaceflight Operations", self.row("ACC-NASA-SPACEOPS")["historical_names"])
        self.assertIn("Deep Space Exploration Systems", self.row("ACC-NASA-EXPLORATION")["historical_names"])

    def test_cells_are_each_accounts_own_history_grid(self):
        conn = S.connect(self.db, readonly=True)
        try:
            for row in self.grid["rows"]:
                aid = row["account"]["canonical_account_id"]
                own = plain(S.history_grid(S.history(conn, aid)))
                by_year = {r["fiscal_year"]: r["cells"] for r in own["rows"]}
                self.assertEqual(row["series"], own["series"], aid)
                for y in self.grid["fiscal_years"]:
                    for st in self.grid["stages"]:
                        cell = row["cells"][f"{y}|{st}"]
                        if y in by_year and st in by_year[y]:
                            self.assertEqual(cell, {"lines": by_year[y][st], "outside_history": False}, (aid, y, st))
                        else:
                            self.assertTrue(cell["outside_history"], (aid, y, st))
                            self.assertEqual([(line["amount_type"], line["component"], line["state"], line["observations"])
                                              for line in cell["lines"]],
                                             [(s["amount_type"], s["component"], "missing", []) for s in own["series"]])
        finally:
            conn.close()

    def test_outside_history_is_only_before_an_accounts_first_record(self):
        spans = {r["account"]["canonical_account_id"]: r["fiscal_year_span"] for r in self.grid["rows"]}
        outside = {aid for aid, r in ((r["account"]["canonical_account_id"], r) for r in self.grid["rows"])
                   if any(c["outside_history"] for c in r["cells"].values())}
        # FY2027-only accounts (DOJ, NOAA, USPTO) and Exploration Technology (from FY2019)
        self.assertEqual(outside, {aid for aid, s in spans.items() if s[0] > 2017})
        self.assertEqual(spans["ACC-NASA-EXPLTECH"], [2019, 2027])
        self.assertEqual(len([a for a, s in spans.items() if s == [2027, 2027]]), 11)

    def test_after_effective_end_is_outside_too(self):
        # the mirror of "before its first record": once an account has an
        # effective_end, the years after it are outside, not missing gaps
        ended = Path(self.tmp.name) / "ended.db"
        shutil.copy(self.db, ended)
        c = sqlite3.connect(ended)
        c.execute("UPDATE account SET effective_end = '2020-09-30' WHERE canonical_account_id = 'ACC-NASA-EXPLTECH'")
        c.commit()
        c.close()
        row = next(r for r in plain(W.subcommittee(str(ended), "CJS"))["rows"]
                   if r["account"]["canonical_account_id"] == "ACC-NASA-EXPLTECH")
        self.assertEqual(row["fiscal_year_span"], [2019, 2020])
        for key, cell in row["cells"].items():
            fy = int(key.split("|")[0])
            self.assertEqual(cell["outside_history"], not 2019 <= fy <= 2020, key)
            if cell["outside_history"]:
                self.assertTrue(all(line["state"] == "missing" for line in cell["lines"]), key)
        # before: FY2021-2027 were in its history as missing gaps
        before = self.row("ACC-NASA-EXPLTECH")
        self.assertFalse(before["cells"]["2023|Enacted"]["outside_history"])

    def test_rollup_rows_carry_their_notes(self):
        rollups = [r["account"]["canonical_account_id"] for r in self.grid["rows"]
                   if "derived rollup" in (r["account"]["notes"] or "").lower()]
        self.assertEqual(rollups, ["ACC-NASA-TOTAL", "ACC-NSF-TOTAL"])

    def test_a_second_subcommittee_is_its_own_grid(self):
        # nothing is CJS-specific: move two accounts to another subcommittee
        other = Path(self.tmp.name) / "two.db"
        shutil.copy(self.db, other)
        c = sqlite3.connect(other)
        c.execute("UPDATE account SET subcommittee = 'Energy and Water' "
                  "WHERE canonical_account_id IN ('ACC-OSTP', 'ACC-NSC')")
        c.commit()
        c.close()
        self.assertEqual(W.subcommittees(str(other)), {"subcommittees": ["CJS", "Energy and Water"]})
        ew = W.subcommittee(str(other), "Energy and Water")
        self.assertEqual([r["account"]["canonical_account_id"] for r in ew["rows"]], ["ACC-NSC", "ACC-OSTP"])
        self.assertEqual(len(W.subcommittee(str(other), "CJS")["rows"]), 29)
        with self.assertRaises(LookupError):
            W.subcommittee(str(other), "Defense")

    def test_api_and_static_export_serve_the_same_grid(self):
        srv = W.serve(self.db, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/subcommittees") as r:
                self.assertEqual(json.load(r), {"subcommittees": ["CJS"]})
            with urllib.request.urlopen(base + "/api/subcommittee/CJS") as r:
                self.assertEqual(json.load(r), self.grid)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/subcommittee/Defense")
            self.assertEqual(e.exception.code, 404)
        finally:
            srv.shutdown()
            srv.server_close()
        out = Path(self.tmp.name) / "docs"
        E.export(WORKBOOK, out)
        self.assertEqual(json.loads((out / "data" / "subcommittees.json").read_text()), {"subcommittees": ["CJS"]})
        self.assertEqual(json.loads((out / "data" / "subcommittees" / "CJS.json").read_text()), self.grid)


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class CompareBrowser(CompareTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.out = Path(cls.tmp.name) / "docs"
        E.export(WORKBOOK, cls.out)

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        cls.static = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(cls.out)))
        cls.live = W.serve(cls.db, port=0, verbose=False)
        for srv in (cls.static, cls.live):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.urls = {"live": f"http://127.0.0.1:{cls.live.server_address[1]}/",
                    "static": f"http://127.0.0.1:{cls.static.server_address[1]}/"}
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
        self.page = self.browser.new_page(viewport={"width": 1300, "height": 900})
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def open(self, where, query="?view=compare&sc=CJS&fy=2017-2026"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")

    def cell(self, aid, fy, stage):
        return self.page.locator(f"tr[data-account={aid}] td[data-fy='{fy}'][data-stage=\"{stage}\"]")

    def expected(self, years, stages):
        return [{"account": r["account"]["canonical_account_id"], "fy": y, "stage": st,
                 **expected_cell(r["cells"][f"{y}|{st}"])}
                for r in self.grid["rows"] for y in years for st in stages]

    def test_fy2017_2026_all_stages_every_cell(self):
        for where in self.urls:
            with self.subTest(where):
                self.open(where)
                shown = self.page.evaluate(SHOWN_JS)
                self.assertEqual(len(shown), 31 * 10 * 4)
                self.assertEqual(shown, self.expected(YEARS, FOUR))

    def test_acceptance_accounts_are_rows_resolved_through_former_names(self):
        self.open("static")
        rows = self.page.locator("[data-testid=compare-row]")
        self.assertEqual(rows.count(), 31)
        for aid in ("ACC-NASA-SCIENCE", "ACC-NASA-EXPLORATION", "ACC-NASA-SPACEOPS", "ACC-DOJ-OIG", "ACC-NASA-OIG",
                    "ACC-NSF-OIG", "ACC-NSF-RRA", "ACC-NASA-SPACETECH", "ACC-DOJ-CVF"):
            self.assertEqual(self.page.locator(f"tr[data-account={aid}]").count(), 1, aid)
        self.assertIn("“LEO and Spaceflight Operations”",
                      self.page.locator("tr[data-account=ACC-NASA-SPACEOPS] [data-testid=row-former]").inner_text())
        self.assertIn("“Deep Space Exploration Systems”",
                      self.page.locator("tr[data-account=ACC-NASA-EXPLORATION] [data-testid=row-former]").inner_text())
        self.assertEqual(self.page.locator("tr[data-account=ACC-NASA-SPACEOPS] [data-testid=row-name]").inner_text(),
                         "Space Operations")

    def test_exploration_headline_is_budget_authority_and_expands_to_the_supplemental(self):
        self.open("static")
        td = self.cell("ACC-NASA-EXPLORATION", 2024, "Enacted")
        self.assertEqual(td.locator(":scope > .line .amt").inner_text(), "$7,216,200,000")   # BA, not BA + supplemental
        more = td.locator("details.more")
        self.assertFalse(more.locator(".line[data-series=supplemental]").is_visible())       # folded by default
        more.locator("summary").click()
        self.assertEqual(more.locator(".line[data-series=supplemental] .amt").inner_text(), "$450,000,000")
        # every Exploration cell with a supplemental figure carries it behind "more"
        exp = self.row("ACC-NASA-EXPLORATION")
        n = 0
        for y in YEARS:
            for st in FOUR:
                for line in exp["cells"][f"{y}|{st}"]["lines"]:
                    if line["amount_type"] == "supplemental" and line["state"] == "value":
                        n += 1
                        self.assertEqual(self.cell("ACC-NASA-EXPLORATION", y, st)
                                         .locator("details.more .line[data-series=supplemental] .amt").all_text_contents(),
                                         [money(o["amount"]) for o in line["observations"]])
        self.assertEqual(n, 9)

    def test_rra_expands_to_the_defense_function_line(self):
        self.open("static")
        rra = self.row("ACC-NSF-RRA")
        for y in YEARS:
            for st in FOUR:
                defense = next(line for line in rra["cells"][f"{y}|{st}"]["lines"] if line["component"] == "defense")
                got = self.cell("ACC-NSF-RRA", y, st).locator("details.more .line[data-series='budget authority · defense']")
                self.assertEqual(got.get_attribute("data-state"), "value")
                self.assertEqual(got.locator(".amt").all_text_contents(), [money(o["amount"]) for o in defense["observations"]])

    def test_confirmed_absence_is_not_applicable_never_blank_or_zero(self):
        self.open("static")
        spaceops = self.row("ACC-NASA-SPACEOPS")
        n = 0
        for y in YEARS:
            for st in FOUR:
                resc = next(line for line in spaceops["cells"][f"{y}|{st}"]["lines"] if line["amount_type"] == "rescission")
                line = self.cell("ACC-NASA-SPACEOPS", y, st).locator("details.more .line[data-series=rescission]")
                self.assertEqual(line.get_attribute("data-state"), resc["state"], (y, st))
                if resc["state"] == "not_applicable":
                    n += 1
                    self.cell("ACC-NASA-SPACEOPS", y, st).locator("details.more summary").click()
                    self.assertTrue(line.locator("[data-testid=not-applicable]").is_visible())
                    self.assertEqual(line.locator("[data-testid=not-applicable]").inner_text(), "not applicable")
                    self.assertEqual(line.locator(".amt").count(), 0)
        self.assertEqual(n, 31)

    def test_narrow_filter_fy2026_enacted(self):
        for where in self.urls:
            with self.subTest(where):
                self.open(where)
                self.page.select_option("#fy-from", "2026")
                self.page.select_option("#fy-to", "2026")
                self.page.uncheck("#stage-all")
                self.page.check("#stage-boxes input[value=Enacted]")
                self.page.click("#compare-form button[type=submit]")
                self.page.wait_for_function("document.querySelectorAll('[data-testid=compare-cell]').length === 31")
                self.assertEqual(self.page.evaluate(SHOWN_JS), self.expected([2026], ["Enacted"]))
                self.assertEqual(self.page.locator("#compare-grid thead").inner_text().split(), ["Account", "FY2026", "Enacted"])
                self.assertTrue(self.page.url.endswith("?view=compare&sc=CJS&fy=2026&stage=Enacted"), self.page.url)
                html = self.page.inner_html("#compare-grid")
                self.open(where, "?view=compare&sc=CJS&fy=2026&stage=Enacted")          # the link reproduces it
                self.assertEqual(self.page.inner_html("#compare-grid"), html)

    def test_multiple_years_and_stages(self):
        self.open("static", "?view=compare&sc=CJS&fy=2019,2021&stage=President's Budget,Enacted")
        self.assertEqual(self.page.evaluate(SHOWN_JS), self.expected([2019, 2021], ["President's Budget", "Enacted"]))
        self.assertEqual([i.get_attribute("value") for i in self.page.locator("#fy-boxes input:checked").all()],
                         ["2019", "2021"])
        self.assertFalse(self.page.is_checked("#stage-all"))

    def test_static_renders_exactly_what_live_renders(self):
        for query in ("?view=compare&sc=CJS&fy=2017-2026", "?view=compare&sc=CJS", "?view=compare&sc=CJS&fy=2020&stage=Senate Reported"):
            got = {}
            for where in self.urls:
                self.open(where, query)
                got[where] = self.page.inner_html("#compare-grid")
            self.assertEqual(got["static"], got["live"], query)

    def test_rollup_rows_are_marked(self):
        self.open("static")
        marked = [r.get_attribute("data-account") for r in self.page.locator("[data-testid=compare-row].rollup").all()]
        self.assertEqual(marked, ["ACC-NASA-TOTAL", "ACC-NSF-TOTAL"])
        for aid in marked:
            badge = self.page.locator(f"tr[data-account={aid}] [data-testid=rollup]")
            self.assertEqual(badge.inner_text(), "Rollup of the accounts above — don't add")
            self.assertIn("Derived rollup", badge.get_attribute("title"))
            # the last row of its agency, below the accounts it totals
            agency = self.row(aid)["account"]["agency"]
            order = [r["account"]["canonical_account_id"] for r in self.grid["rows"] if r["account"]["agency"] == agency]
            self.assertEqual(order[-1], aid)
        self.assertEqual(self.page.locator("[data-testid=rollup]").count(), 2)

    def test_after_effective_end_tooltip(self):
        ended = Path(self.tmp.name) / "ended_browser.db"
        shutil.copy(self.db, ended)
        c = sqlite3.connect(ended)
        c.execute("UPDATE account SET effective_end = '2020-09-30' WHERE canonical_account_id = 'ACC-NASA-EXPLTECH'")
        c.commit()
        c.close()
        srv = W.serve(ended, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS&fy=2017-2026")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            after, before = self.cell("ACC-NASA-EXPLTECH", 2023, "Enacted"), self.cell("ACC-NASA-EXPLTECH", 2017, "Enacted")
            for td in (after, before):
                self.assertEqual(td.get_attribute("data-outside"), "true")
                self.assertIn("outside", td.get_attribute("class"))
            self.assertTrue(after.get_attribute("title").startswith("After this account's effective end (2020-09-30)"))
            self.assertTrue(before.get_attribute("title").startswith("Nothing on file for this account in FY2017"))
            self.assertIsNone(self.cell("ACC-NASA-EXPLTECH", 2020, "President's Budget").get_attribute("data-outside"))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_account_name_opens_the_single_account_view(self):
        self.open("live")
        self.page.click("tr[data-account=ACC-NASA-SPACEOPS] [data-testid=row-name] button")
        self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.assertTrue(self.page.is_hidden("[data-testid=compare-view]"))
        self.assertEqual(self.page.inner_text("[data-testid=account-name]"), "Space Operations")
        self.assertTrue(self.page.url.endswith("?account=ACC-NASA-SPACEOPS"))


if __name__ == "__main__":
    unittest.main()
