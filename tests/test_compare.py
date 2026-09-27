"""
The subcommittee grid: every account of a subcommittee side by side
(approps_store.subcommittee_grid(), /api/subcommittee/<name>, the static
export's data/subcommittees/<name>.json, and the page's "Subcommittee grid"
view).

Run:  python -m unittest tests.test_compare -v

Browser tests need playwright + chromium, as tests/test_web.py; each runs
against the live server and the static export.
"""

import csv
import functools
import importlib.util
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


def with_sourced_totals(src, dst):
    """A copy of the store with a printed Title III total (FY2024 Enacted,
    33,944,930 as H.Rept. 118-582 p.248 prints it) and a bill-total row: the
    shape a workbook would give them -- rollups noted as title / bill totals."""
    shutil.copy(src, dst)
    c = sqlite3.connect(dst)
    for aid, name, notes in (("ACC-T3-TOTAL", "Total, Title III, Science", "Derived rollup -- title total"),
                             ("ACC-CJS-TOTAL", "Grand total", "Derived rollup -- bill total")):
        c.execute("INSERT INTO account (canonical_account_id, canonical_name, agency, status, fund_type, effective_start, "
                  "subcommittee, notes, title, display_order) VALUES (?, ?, 'Commerce, Justice, Science', 'active', "
                  "'general', '2016-10-01', 'CJS', ?, ?, ?)", (aid, name, notes, "Title III" if "T3" in aid else None,
                                                                99 if "T3" in aid else None))
    cols = [r[1] for r in c.execute("PRAGMA table_info(appropriations_observation)")]
    sel = ", ".join({"observation_id": "'OBS-T3'", "canonical_account_id": "'ACC-T3-TOTAL'",
                     "amount": "33944930000"}.get(k, k) for k in cols)
    c.execute(f"INSERT INTO appropriations_observation ({', '.join(cols)}) SELECT {sel} FROM appropriations_observation "
              "WHERE canonical_account_id = 'ACC-NASA-TOTAL' AND fiscal_year = 2024 AND stage = 'Enacted' "
              "AND amount_type = 'budget authority'")
    c.commit()
    c.close()
    return dst


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
        self.assertEqual(len(got), 30)
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
        # the FY2027-only accounts (DOJ, NOAA, USPTO): every FY2017-2026 cell
        self.assertEqual(outside, {aid for aid, s in spans.items() if s[0] > 2017})
        self.assertEqual(len(outside), 11)
        for aid in outside:
            self.assertEqual(spans[aid], [2027, 2027])
            self.assertEqual({k for k, c in self.row(aid)["cells"].items() if c["outside_history"]},
                             {f"{y}|{st}" for y in YEARS for st in FOUR}, aid)
        # Space Technology, with Exploration Research and Technology folded in
        # (v24): a figure in every FY2017-2026 cell, nothing outside
        st = self.row("ACC-NASA-SPACETECH")
        self.assertFalse(any(c["outside_history"] for c in st["cells"].values()))
        heads = [next(l for l in st["cells"][f"{y}|{s}"]["lines"] if l["amount_type"] == "budget authority" and not l["component"])
                 for y in YEARS for s in FOUR]
        self.assertEqual({l["state"] for l in heads}, {"value"})

    def test_after_effective_end_is_outside_too(self):
        # the mirror of "before its first record": once an account has an
        # effective_end, the years after it are outside, not missing gaps.
        # No v24 account has one, so this sets one: OSTP ends with FY2026.
        ended = Path(self.tmp.name) / "ended.db"
        shutil.copy(self.db, ended)
        c = sqlite3.connect(ended)
        c.execute("UPDATE account SET effective_end = '2026-09-30' WHERE canonical_account_id = 'ACC-OSTP'")
        c.commit()
        c.close()
        row = next(r for r in plain(W.subcommittee(str(ended), "CJS"))["rows"]
                   if r["account"]["canonical_account_id"] == "ACC-OSTP")
        self.assertEqual(row["fiscal_year_span"], [2017, 2026])
        for key, cell in row["cells"].items():
            fy = int(key.split("|")[0])
            self.assertEqual(cell["outside_history"], fy == 2027, key)
            if cell["outside_history"]:
                self.assertTrue(all(line["state"] == "missing" for line in cell["lines"]), key)
        # before: FY2027 was in its history as a missing gap
        before = self.row("ACC-OSTP")
        self.assertEqual(before["fiscal_year_span"], [2017, 2027])
        self.assertFalse(before["cells"]["2027|Enacted"]["outside_history"])

    def test_rollup_rows_carry_their_notes(self):
        rollups = [r["account"]["canonical_account_id"] for r in self.grid["rows"]
                   if "derived rollup" in (r["account"]["notes"] or "").lower()]
        self.assertEqual(rollups, ["ACC-NASA-TOTAL", "ACC-NSF-TOTAL"])

    def test_rows_follow_the_bill(self):
        # by title, then display_order; a rollup heads the accounts it totals
        self.assertEqual([(t["title"], t["total"]) for t in self.grid["titles"]], [("Title III", None), (None, None)])
        self.assertIsNone(self.grid["bill_total"])
        self.assertEqual(self.grid["titles"][0]["rows"], [
            "ACC-OSTP", "ACC-NSC",
            "ACC-NASA-TOTAL", "ACC-NASA-SCIENCE", "ACC-NASA-AERONAUTICS", "ACC-NASA-SPACETECH", "ACC-NASA-EXPLORATION",
            "ACC-NASA-SPACEOPS", "ACC-NASA-STEM-ENGAGEMENT", "ACC-NASA-SAFETY-SECURITY", "ACC-NASA-CONSTRUCTION", "ACC-NASA-OIG",
            "ACC-NSF-TOTAL", "ACC-NSF-RRA", "ACC-NSF-MREFC", "ACC-NSF-STEM-EDUCATION", "ACC-NSF-AGENCY-OPS", "ACC-NSF-NSB",
            "ACC-NSF-OIG"])
        self.assertEqual(len(self.grid["titles"][1]["rows"]), 11)       # not yet placed: after every title
        self.assertEqual([r["account"]["canonical_account_id"] for r in self.grid["rows"]],
                         self.grid["titles"][0]["rows"] + self.grid["titles"][1]["rows"])

    def test_rollup_members_are_its_title_and_agency(self):
        nasa, nsf = self.row("ACC-NASA-TOTAL"), self.row("ACC-NSF-TOTAL")
        self.assertEqual((nasa["rollup"], len(nasa["rollup_members"]), nsf["rollup"], len(nsf["rollup_members"])),
                         ("agency", 9, "agency", 6))                  # as their notes say: NASA's 9, NSF's 6
        for roll in (nasa, nsf):
            self.assertEqual(set(roll["rollup_members"]), {r["account"]["canonical_account_id"] for r in self.grid["rows"]
                             if r["account"]["agency"] == roll["account"]["agency"] and r["account"]["title"] == "Title III"
                             and not r["rollup"]})
            for m in roll["rollup_members"]:
                self.assertEqual(self.row(m)["member_of"], roll["account"]["canonical_account_id"])
        # generic: any account the workbook notes as a rollup gathers its own agency's accounts
        other = Path(self.tmp.name) / "rollup.db"
        shutil.copy(self.db, other)
        c = sqlite3.connect(other)
        c.execute("UPDATE account SET notes = 'Derived rollup -- test' WHERE canonical_account_id = 'ACC-OSTP'")
        c.commit()
        c.close()
        g = W.subcommittee(str(other), "CJS")
        ostp = next(r for r in g["rows"] if r["account"]["canonical_account_id"] == "ACC-OSTP")
        self.assertEqual((ostp["rollup"], ostp["rollup_members"]), ("agency", ["ACC-NSC"]))

    def test_title_and_bill_totals_come_only_from_sourced_rows(self):
        # v26 has none: no total at all, never a sum of the rows
        self.assertEqual([t["total"] for t in self.grid["titles"]], [None, None])
        # a sourced total -- a rollup noted as a title / bill total -- is the total, not a row
        other = with_sourced_totals(self.db, Path(self.tmp.name) / "totals.db")
        g = plain(W.subcommittee(str(other), "CJS"))
        t3 = g["titles"][0]
        self.assertEqual((t3["title"], t3["total"]["account"]["canonical_account_id"], g["bill_total"]["account"]["canonical_account_id"]),
                         ("Title III", "ACC-T3-TOTAL", "ACC-CJS-TOTAL"))
        self.assertNotIn("ACC-T3-TOTAL", t3["rows"])
        self.assertNotIn("ACC-CJS-TOTAL", [r["account"]["canonical_account_id"] for r in g["rows"]])
        self.assertEqual([o["amount"] for l in t3["total"]["cells"]["2024|Enacted"]["lines"] for o in l["observations"]],
                         [33_944_930_000])

    def test_rollup_scope_and_title_rank(self):
        scope = lambda notes: S.rollup_scope({"notes": notes})
        self.assertEqual([scope(None), scope("Receives a transfer"), scope("Derived rollup -- equals the sum of NASA's 9"),
                          scope("Derived rollup -- title total"), scope("Derived rollup -- bill total (Grand total)")],
                         [None, None, "agency", "title", "bill"])
        self.assertEqual(sorted(["Title VII", None, "Title II", "Title III", "Title IV", "Title I", "Title V"], key=S.title_rank),
                         ["Title I", "Title II", "Title III", "Title IV", "Title V", "Title VII", None])

    def test_mechanism_title_candidates(self):
        spec = importlib.util.spec_from_file_location("mechanism_titles", ROOT / "reference" / "review" / "mechanism_titles.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        conn = S.connect(self.db, readonly=True)
        try:
            got = mod.candidates(conn)
        finally:
            conn.close()
        with open(ROOT / "reference" / "review" / "mechanism_titles.csv", newline="") as f:
            self.assertEqual(got, list(csv.DictReader(f)))              # the committed list is what v26 gives
        self.assertEqual({r["canonical_account_id"]: (r["candidate_title"], r["basis"]) for r in got}, {
            "ACC-NOAA-ORF": ("Title I", "own line"), "ACC-NOAA-PDF": ("Title I", "transfer or rescission only"),
            "ACC-USPTO-SE": ("Title I", "own line"), "ACC-DOJ-AFF": ("Title II", "own line"),
            "ACC-DOJ-ANTITRUST-SE": ("Title II", "own line"), "ACC-DOJ-CVF": ("", "ambiguous"),
            "ACC-DOJ-OIG": ("Title V", "transfer or rescission only"),
            "ACC-DOJ-OJP-RESC": ("Title V", "transfer or rescission only"), "ACC-DOJ-USTSF": ("Title II", "own line"),
            "ACC-DOJ-VAWA": ("Title II", "transfer or rescission only"),
            "ACC-DOJ-WCF": ("Title V", "transfer or rescission only")})

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
        self.assertEqual([r["account"]["canonical_account_id"] for r in ew["rows"]], ["ACC-OSTP", "ACC-NSC"])      # bill order (display_order)
        self.assertEqual(len(W.subcommittee(str(other), "CJS")["rows"]), 28)
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
                self.assertEqual(len(shown), 30 * 10 * 4)
                self.assertEqual(shown, self.expected(YEARS, FOUR))

    def test_acceptance_accounts_are_rows_resolved_through_former_names(self):
        self.open("static")
        rows = self.page.locator("[data-testid=compare-row]")
        self.assertEqual(rows.count(), 30)
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
        more.locator(":scope > summary").click()
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
                    self.cell("ACC-NASA-SPACEOPS", y, st).locator("details.more > summary").click()
                    self.assertTrue(line.locator("[data-testid=not-applicable]").is_visible())
                    self.assertEqual(line.locator("[data-testid=not-applicable]").inner_text(), "not applicable")
                    self.assertEqual(line.locator(".amt").count(), 0)
        self.assertEqual(n, 34)                     # v23 added FY2019's three 'NASA closeouts' cells

    def test_narrow_filter_fy2026_enacted(self):
        for where in self.urls:
            with self.subTest(where):
                self.open(where)
                self.page.select_option("#fy-from", "2026")
                self.page.select_option("#fy-to", "2026")
                self.page.uncheck("#stage-all")
                self.page.check("#stage-boxes input[value=Enacted]")
                self.page.click("#compare-form button[type=submit]")
                self.page.wait_for_function("document.querySelectorAll('[data-testid=compare-cell]').length === 30")
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
            self.assertEqual(badge.inner_text(), "Rollup of the accounts below — don't add")
            self.assertIn("Derived rollup", badge.get_attribute("title"))
            # it heads its group: the next rows are the accounts it totals
            order = [r.get_attribute("data-account") for r in self.page.locator("[data-testid=compare-row]").all()]
            members = self.row(aid)["rollup_members"]
            self.assertEqual(order[order.index(aid) + 1: order.index(aid) + 1 + len(members)], members)
            self.assertEqual([r.get_attribute("data-account") for r in self.page.locator(f"tr[data-member-of={aid}]").all()], members)
        self.assertEqual(self.page.locator("[data-testid=rollup]").count(), 2)

    def test_rollup_folds_its_accounts_away(self):
        self.open("static")
        toggle = self.page.locator("tr[data-account=ACC-NASA-TOTAL] [data-testid=rollup-toggle]")
        members = self.page.locator("tr[data-member-of=ACC-NASA-TOTAL]")
        self.assertEqual((members.count(), toggle.inner_text()), (9, "▾ hide its 9 accounts"))
        toggle.click()
        self.assertEqual([m.is_hidden() for m in members.all()], [True] * 9)
        self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
        self.assertTrue(self.page.locator("tr[data-account=ACC-NSF-RRA]").is_visible())         # NSF's group untouched
        toggle.click()
        self.assertEqual([m.is_visible() for m in members.all()], [True] * 9)

    def test_citations_open_on_click(self):
        self.open("static")
        td = self.cell("ACC-NASA-SCIENCE", 2024, "Enacted")
        src = td.locator(":scope > .line details.src")
        self.assertFalse(src.locator(".cite").is_visible())
        self.assertEqual(src.locator("summary").inner_text().strip(), "source")
        src.locator("summary").click()
        self.assertTrue(src.locator(".cite").is_visible())
        o = next(l for l in self.row("ACC-NASA-SCIENCE")["cells"]["2024|Enacted"]["lines"]
                 if l["amount_type"] == "budget authority")["observations"][0]
        self.assertEqual(src.locator(".cite").inner_text(), f"{o['source_document_id']} p.{o['source_page']}")

    def test_titles_head_their_groups_and_totals_are_never_summed(self):
        self.open("static")
        heads = self.page.locator("[data-testid=title-head]")
        self.assertEqual([h.locator("th").inner_text() for h in heads.all()], ["Title III", "Not yet placed in a title"])
        notes = [n.inner_text() for n in self.page.locator("[data-testid=no-total]").all()]
        self.assertEqual(len(notes), 3)                             # the bill, Title III, the unplaced accounts
        self.assertTrue(notes[0].startswith("No printed bill total on file"))
        self.assertTrue(notes[1].startswith("No printed total on file for Title III"))
        # no dollar figure anywhere in a total row
        self.assertEqual(self.page.locator("tr.total-row [data-testid=amount]").count(), 0)

    def test_a_sourced_total_renders_as_a_row(self):
        db = with_sourced_totals(self.db, Path(self.tmp.name) / "totals_browser.db")
        srv = W.serve(db, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS&fy=2024&stage=Enacted")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            total = self.page.locator("[data-testid=title-total]").first
            self.assertEqual(total.locator("[data-testid=amount]").inner_text(), "$33,944,930,000")
            self.assertIn("printed total", total.inner_text())
            self.assertEqual(self.page.locator("[data-testid=bill-total] [data-testid=no-total]").count(), 0)
            self.assertEqual(self.page.locator("[data-testid=compare-row][data-account=ACC-T3-TOTAL]").count(), 0)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_after_effective_end_tooltip(self):
        ended = Path(self.tmp.name) / "ended_browser.db"
        shutil.copy(self.db, ended)
        c = sqlite3.connect(ended)
        c.execute("UPDATE account SET effective_end = '2026-09-30' WHERE canonical_account_id = 'ACC-OSTP'")
        c.commit()
        c.close()
        srv = W.serve(ended, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            after, before = self.cell("ACC-OSTP", 2027, "Enacted"), self.cell("ACC-DOJ-CVF", 2017, "Enacted")
            for td in (after, before):
                self.assertEqual(td.get_attribute("data-outside"), "true")
                self.assertIn("outside", td.get_attribute("class"))
            self.assertTrue(after.get_attribute("title").startswith("After this account's effective end (2026-09-30)"))
            self.assertTrue(before.get_attribute("title").startswith("Nothing on file for this account in FY2017"))
            self.assertIsNone(self.cell("ACC-OSTP", 2026, "Enacted").get_attribute("data-outside"))
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
