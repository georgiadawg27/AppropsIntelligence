"""
Find changes and the watchlist (brief UI-6): the preset questions, the condition builder, the results (only pairs
where both cells are figures; a printed zero is one, a blank never is), the accounts left out, Save this search,
Export results, Open in Explore, the browser-only watchlist and What's new.

Run:  python -m unittest tests.test_find_changes -v

Browser tests need playwright + chromium, as tests/test_web.py; they run against the static export, and the
preset and What's new tests against the live server too.
"""

import csv
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_compare import GridBrowser  # noqa: E402

PB, HOUSE, SENATE, ENACTED = "President's Budget", "House Reported", "Senate Reported", "Enacted"
# blocks localStorage the way a browser with site data blocked does: every access throws
BLOCK_STORAGE = """Object.defineProperty(window, "localStorage", { configurable: true,
  get() { throw new DOMException("The operation is insecure.", "SecurityError"); } });"""


def headline(row, fy, stage):
    cell = row["cells"].get(f"{fy}|{stage}")
    if not cell or cell.get("outside_life") or not cell["lines"]:
        return None, None
    lines = cell["lines"]
    return cell, next((l for l in lines if l["amount_type"] == "budget authority" and not l["component"]), lines[0])


def figure(row, fy, stage):
    """The printed figure (SPEC section 1): a value or a printed zero; a confirmed absence and every other state is none."""
    _, h = headline(row, fy, stage)
    if not h or not h["observations"]:
        return None
    if h["state"] == "value" or (h["state"] == "not_funded" and not h["absence"]):
        return h["observations"][0]["amount"]
    return None


def outside(row, fy, stage):
    return headline(row, fy, stage)[1] is None


def accounts(grid):
    """The rows the page lists as accounts: every row but an agency total that sums lines below it."""
    return [r for r in grid["rows"] if not r["rollup"] or not r["rollup_members"]]


def pairs(grid, a, b, agency=None):
    """(hits as {id: (A, B)}, left-out ids) for column A = (fy, stage) against column B."""
    both, left = {}, set()
    for r in accounts(grid):
        if agency and r["account"]["agency"] != agency:
            continue
        if outside(r, *a) and outside(r, *b):
            continue
        fa, fb = figure(r, *a), figure(r, *b)
        if fa is None or fb is None:
            left.add(r["account"]["canonical_account_id"])
        else:
            both[r["account"]["canonical_account_id"]] = (fa, fb)
    return both, left


class FindChanges(GridBrowser):
    def go(self, query, where="static", page=None):
        page = page or self.page
        page.goto(self.urls[where] + query)
        page.wait_for_selector("[data-testid=fc-count]")
        page.wait_for_function("document.querySelector('[data-testid=whats-new]').hidden || "
                               "document.querySelector('[data-testid=whats-new]').children.length > 0")

    def preset(self, key):
        self.page.click(f"[data-testid=fc-preset][data-preset={key}]")
        self.page.wait_for_function(f"location.search.includes('preset={key}')")
        self.page.wait_for_selector("[data-testid=fc-count]")

    def result_ids(self):
        return self.page.eval_on_selector_all("[data-testid=fc-row]", "rs => rs.map(r => r.dataset.account)")

    def left_ids(self):
        return self.page.eval_on_selector_all("[data-testid=fc-left-row]", "rs => rs.map(r => r.dataset.account)")

    # ---- the presets
    def test_preset_deepest_proposed_cuts(self):
        for where in ("static", "live"):
            with self.subTest(where=where):
                self.go("?view=changes&sc=LHHS&fy=2026", where)
                self.preset("cuts")
                q = self.page.evaluate("Object.fromEntries(new URLSearchParams(location.search))")
                self.assertEqual({k: q.get(k) for k in ("a", "b", "pct", "pctop")},
                                 {"a": "prior", "b": "request", "pct": "-20", "pctop": "le"})
                both, left = pairs(self.lhhs, (2025, ENACTED), (2026, PB))
                want = {i for i, (a, b) in both.items() if a != 0 and (b - a) / abs(a) * 100 <= -20}
                ids = self.result_ids()
                self.assertEqual(set(ids), want)
                self.assertGreater(len(ids), 20)
                pct = [(both[i][1] - both[i][0]) / abs(both[i][0]) for i in ids]
                self.assertEqual(pct, sorted(pct))                                  # deepest first
                self.assertIn("ACC-HHS-NIH-NHLBI", ids)                             # a printed request of 0 counts
                self.assertEqual(self.page.inner_text("[data-testid=fc-count]").split(" ")[0], str(len(want)))
                # each row: the account page, then each figure links to its source page
                row = self.page.locator("[data-testid=fc-row][data-account=ACC-HHS-NIH-NHLBI]")
                self.assertEqual(row.locator("[data-testid=fc-account]").get_attribute("href"),
                                 "?account=ACC-HHS-NIH-NHLBI&fy=2026")
                for testid, (fy, stage) in (("fc-a", (2025, ENACTED)), ("fc-b", (2026, PB))):
                    o = headline(self.lrow("ACC-HHS-NIH-NHLBI"), fy, stage)[1]["observations"][0]
                    link = row.locator(f"[data-testid={testid}] a")
                    self.assertEqual(link.get_attribute("href"),
                                     o["url_or_identifier"] + "#page=" + o["source_page"].split("-")[0])
                self.assertEqual(row.locator("[data-testid=fc-b]").inner_text(), "—")
                self.assertEqual(row.locator("[data-testid=fc-change]").inner_text(), "−3,982,345")
                self.assertEqual(row.locator("[data-testid=fc-pct]").inner_text(), "−100.0%")
                self.assertTrue(row.locator("[data-testid=watch]").is_visible())

    def test_preset_house_vs_senate_gaps(self):
        self.go("?view=changes&sc=LHHS&fy=2026")
        self.preset("gaps")
        both, _ = pairs(self.lhhs, (2026, HOUSE), (2026, SENATE))
        want = {i for i, (a, b) in both.items() if abs(b - a) >= 100_000_000}
        ids = self.result_ids()
        self.assertEqual(set(ids), want)
        self.assertGreater(len(ids), 10)
        gaps = [abs(both[i][1] - both[i][0]) for i in ids]
        self.assertEqual(gaps, sorted(gaps, reverse=True))
        # AHRQ: a printed House zero is a figure; its % from a zero base reads "new"
        self.assertIn("ACC-HHS-AHRQ-TOTAL", ids)
        self.assertEqual(self.page.inner_text("[data-testid=fc-row][data-account=ACC-HHS-AHRQ-TOTAL] [data-testid=fc-pct]"), "new")
        # an agency total that sums lines below it is never a result (its lines are)
        self.assertNotIn("ACC-HHS-NIH-TOTAL", self.page.eval_on_selector_all("[data-testid=fc-row]", "rs => rs.map(r => r.dataset.account)"))

    def test_preset_proposed_eliminations(self):
        self.go("?view=changes&sc=LHHS&fy=2026")
        self.preset("elim")
        both, _ = pairs(self.lhhs, (2025, ENACTED), (2026, PB))
        want = {i for i, (a, b) in both.items() if b == 0 and a > 0}
        self.assertEqual(set(self.result_ids()), want)
        for aid in ("ACC-HHS-NIH-NHLBI", "ACC-HHS-ACF-LIHEAP", "ACC-HHS-AHRQ-TOTAL"):
            self.assertIn(aid, want)
        self.assertTrue(self.page.is_checked("#fc-elim"))

    def test_a_blank_request_is_not_an_elimination(self):
        # a prior enacted figure above 0 and a request that is blank: never an elimination, always listed as left out.
        # FY2022: Medicare Operations' request is a confirmed absence (bill text, no printed figure); FY2023: CDC-Wide's
        # and Medicare Operations' requests are missing (a document on file, no figure entered)
        for fy, cases in ((2022, {"ACC-HHS-GP-MEDICARE-OPERATIONS": "confirmed absent"}),
                          (2023, {"ACC-HHS-CDC-PROGRAM-SUPPORT": "figure not yet entered",
                                  "ACC-HHS-GP-MEDICARE-OPERATIONS": "figure not yet entered"})):
            with self.subTest(fy=fy):
                self.go(f"?view=changes&sc=LHHS&fy={fy}")
                self.preset("elim")
                both, left = pairs(self.lhhs, (fy - 1, ENACTED), (fy, PB))
                self.assertEqual(set(self.result_ids()), {i for i, (a, b) in both.items() if b == 0 and a > 0})
                self.assertEqual(self.page.inner_text("[data-testid=fc-left]"),
                                 f"{len(left)} account{'' if len(left) == 1 else 's'} left out: no figure in one stage. See them")
                self.page.click("[data-testid=fc-left-toggle]")
                self.assertEqual(set(self.left_ids()), left)
                self.assertIn("left=1", self.page.evaluate("location.search"))
                for aid, state in cases.items():
                    prior = figure(self.lrow(aid), fy - 1, ENACTED)
                    self.assertGreater(prior, 0)
                    self.assertIsNone(figure(self.lrow(aid), fy, PB))
                    self.assertIn(aid, left)
                    self.assertNotIn(aid, self.result_ids())
                    row = self.page.locator(f"[data-testid=fc-left-row][data-account={aid}]").inner_text()
                    self.assertIn(state, row)
                    self.assertIn(f"{round(prior / 1000):,}", row)
        # a printed zero, unlike a blank, is a figure: FY2026's eliminations are results, not left out
        self.go("?view=changes&preset=elim&sc=LHHS&fy=2026&a=prior&b=request&elim=1&left=1")
        self.assertIn("ACC-HHS-NIH-NHLBI", self.result_ids())
        self.assertNotIn("ACC-HHS-NIH-NHLBI", self.left_ids())

    def test_preset_moves_and_reorganizations(self):
        self.go("?view=changes&sc=LHHS&fy=2026")
        self.preset("moves")
        rels = {x["relationship_id"]: x for r in self.lhhs["rows"] for x in r["relationships"]}
        shown = lambda: set(self.page.eval_on_selector_all("[data-testid=fc-move]", "rs => rs.map(r => r.dataset.rel)"))
        self.assertEqual(shown(), {k for k, x in rels.items() if int(x["effective_fiscal_year"]) == 2026})
        self.assertIn("REL-LHHS-0001", shown())
        row = self.page.locator("[data-testid=fc-move][data-rel=REL-LHHS-0001]")
        self.assertIn("consolidated into (proposed, not enacted)", row.inner_text())
        self.assertIn("Administration for a Healthy America", row.inner_text())
        # its documents, from the account's record
        row.locator("[data-testid=fc-move-src] a").first.wait_for()
        self.assertTrue(row.locator("[data-testid=fc-move-src] a").first.get_attribute("href").startswith("https://"))
        self.page.click("[data-testid=fc-years]")
        self.assertEqual(shown(), set(rels))

    # ---- the builder, Save this search, Open in Explore, Export
    def test_conditions_save_and_reload(self):
        self.go("?view=changes&sc=LHHS&fy=2026")
        self.page.select_option("#fc-agency", "National Institutes of Health")
        self.page.wait_for_function("location.search.includes('agency=')")
        self.page.select_option("#fc-a", "house")
        self.page.select_option("#fc-b", "senate")
        self.page.select_option("#fc-pctop", "ge")
        self.page.fill("#fc-pct", "5")
        self.page.press("#fc-pct", "Tab")
        self.page.wait_for_function("location.search.includes('pct=5')")
        self.assertEqual(self.page.get_attribute("[data-testid=fc-preset][data-preset=cuts]", "aria-pressed"), "false")
        both, left = pairs(self.lhhs, (2026, HOUSE), (2026, SENATE), "National Institutes of Health")
        want = {i for i, (a, b) in both.items() if a and (b - a) / abs(a) * 100 >= 5}
        self.assertEqual(set(self.result_ids()), want)
        self.assertTrue(want)
        url = self.page.url
        self.page.click("[data-testid=fc-save]")
        self.assertEqual(self.page.get_attribute("[data-testid=fc-save]", "data-copied"), url)
        self.page.reload()
        self.page.wait_for_selector("[data-testid=fc-row]")
        self.assertEqual(set(self.result_ids()), want)
        self.assertEqual(self.page.input_value("#fc-pct"), "5")
        self.assertEqual(self.page.input_value("#fc-agency"), "National Institutes of Health")
        # Open in Explore: the same subcommittee, year and agency in the grid
        self.page.click("[data-testid=fc-explore]")
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        q = self.page.evaluate("Object.fromEntries(new URLSearchParams(location.search))")
        self.assertEqual((q["sc"], q["fy"], q["agency"], q["grid"]), ("LHHS", "2026", "National Institutes of Health", "stages"))
        self.assertIn("Agency: National Institutes of Health", self.page.inner_text("[data-testid=chips]"))
        # Back returns to the search
        self.page.go_back()
        self.page.wait_for_selector("[data-testid=fc-row]")
        self.assertEqual(set(self.result_ids()), want)

    def test_open_in_explore_carries_the_threshold_where_explore_measures_the_same_change(self):
        explore = "Object.fromEntries(new URLSearchParams(document.querySelector('[data-testid=fc-explore]').search))"
        # FY2026 Enacted against FY2025 Enacted: Explore's Two years mode on Enacted, the threshold with it
        self.go("?view=changes&sc=LHHS&fy=2026&a=prior&b=enacted&pct=10&pctop=abs&sort=abs")
        q = self.page.evaluate(explore)
        self.assertEqual((q["grid"], q["a"], q["b"], q["minpct"]), ("years", "2025", "2026", "10"))
        self.assertNotIn("basis", q)
        # FY2027 House against FY2026 Enacted: Stages mode's own change column (House leads a year not yet enacted)
        self.go("?view=changes&sc=LHHS&fy=2027&a=prior&b=house&usd=50&usdop=abs&sort=abs")
        q = self.page.evaluate(explore)
        self.assertEqual((q["grid"], q["fy"], q["mink"]), ("stages", "2027", "50000"))
        # House against Senate: no Explore filter measures it, so none is carried, and the page says so
        self.go("?view=changes&sc=LHHS&fy=2026&a=house&b=senate&usd=100&usdop=abs&sort=abs")
        q = self.page.evaluate(explore)
        self.assertEqual((q["grid"], q["fy"]), ("stages", "2026"))
        self.assertNotIn("mink", q)
        self.assertIn("no filter for these conditions", self.page.inner_text("[data-testid=fc-summary]"))

    def test_export_results(self):
        self.go("?view=changes&preset=cuts&sc=LHHS&fy=2026&a=prior&b=request&pct=-20&pctop=le&sort=pct")
        with self.page.expect_download() as d:
            self.page.click("[data-testid=fc-export-csv]")
        rows = list(csv.reader(io.StringIO(Path(d.value.path()).read_text(encoding="utf-8"))))
        self.assertEqual(rows[0][0], "Approps Intelligence export: Find changes")
        head = {r[0]: r[1] for r in rows if len(r) >= 2 and r[0]}
        self.assertEqual(head["Question"], "Deepest proposed cuts")
        self.assertEqual((head["Column A"], head["Column B"]), ("FY2025 Enacted", "FY2026 Request"))
        start = next(i for i, r in enumerate(rows) if r and r[0] == "Account")
        self.assertEqual(rows[start][:7], ["Account", "Account ID", "Agency", "FY2025 Enacted", "FY2026 Request", "Change", "Change %"])
        body = rows[start + 1:]
        self.assertEqual([r[1] for r in body], self.result_ids())
        nhlbi = next(r for r in body if r[1] == "ACC-HHS-NIH-NHLBI")
        self.assertEqual(nhlbi[3:7], ["3982345", "0", "-3982345", "-100"])          # a printed zero exports as 0
        self.assertIn("no figure in one stage", head["Left out"])
        with self.page.expect_download() as d:
            self.page.click("[data-testid=fc-export-xlsx]")
        self.assertTrue(d.value.suggested_filename.endswith(".xlsx"))

    # ---- the watchlist
    def test_watchlist_survives_a_reload(self):
        self.go("?view=changes&preset=cuts&sc=LHHS&fy=2026&a=prior&b=request&pct=-20&pctop=le&sort=pct")
        note = self.page.inner_text("[data-testid=watch-note]")
        self.assertIn("this browser only", note)
        self.assertIn("no alerts or emails", note)
        self.assertTrue(self.page.is_visible("[data-testid=watch-empty]"))
        star = "[data-testid=fc-row][data-account=ACC-HHS-NIH-NHLBI] [data-testid=watch]"
        self.page.click(star)
        self.assertEqual(self.page.get_attribute(star, "aria-pressed"), "true")
        self.assertEqual(self.page.eval_on_selector_all("[data-testid=watch-item]", "a => a.map(x => x.textContent)"),
                         ["National Heart, Lung, and Blood Institute"])
        self.page.reload()
        self.page.wait_for_selector("[data-testid=watch-item]")
        self.assertEqual(self.page.get_attribute(star, "aria-pressed"), "true")
        self.assertEqual(self.page.eval_on_selector_all("[data-testid=watch-item]", "a => a.map(x => x.textContent)"),
                         ["National Heart, Lung, and Blood Institute"])
        # the same list everywhere on the site: Explore's account panel and the account page
        self.page.goto(self.urls["static"] + "?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL&acct=ACC-HHS-NIH-NHLBI")
        self.page.wait_for_selector("#side-panel [data-testid=watch]")
        self.assertEqual(self.page.get_attribute("#side-panel [data-testid=watch]", "aria-pressed"), "true")
        self.page.goto(self.urls["static"] + "?account=ACC-HHS-NIH-NHLBI&fy=2026")
        self.page.wait_for_selector("[data-testid=ap-actions] [data-testid=watch]")
        self.assertEqual(self.page.get_attribute("[data-testid=ap-actions] [data-testid=watch]", "aria-pressed"), "true")
        self.page.click("[data-testid=ap-actions] [data-testid=watch]")                     # unwatch it there
        self.go("?view=changes&sc=LHHS&fy=2026")
        self.assertTrue(self.page.is_visible("[data-testid=watch-empty]"))

    def test_watchlist_exports_and_imports_as_a_link(self):
        self.go("?view=changes&preset=elim&sc=LHHS&fy=2026&a=prior&b=request&elim=1&sort=usd")
        for aid in ("ACC-HHS-NIH-NHLBI", "ACC-HHS-ACF-LIHEAP"):
            self.page.click(f"[data-testid=fc-row][data-account={aid}] [data-testid=watch]")
        link = self.page.input_value("[data-testid=watch-link]")
        self.assertTrue(link.endswith("?view=changes&watch=ACC-HHS-NIH-NHLBI,ACC-HHS-ACF-LIHEAP"), link)
        # another browser: empty storage; the link offers the list, adds it only when asked
        other = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        other.on("pageerror", lambda e: errors.append(str(e)))
        try:
            other.goto(link.replace("ACC-HHS-ACF-LIHEAP", "ACC-HHS-ACF-LIHEAP,ACC-NOT-ON-FILE"))
            other.wait_for_selector("[data-testid=watch-import-banner]")
            banner = other.inner_text("[data-testid=watch-import-banner]")
            self.assertIn("2 accounts", banner)
            self.assertIn("ACC-NOT-ON-FILE", banner)
            self.assertTrue(other.is_visible("[data-testid=watch-empty]"))
            other.click("[data-testid=watch-import]")
            other.wait_for_selector("[data-testid=watch-item]")
            self.assertEqual(other.eval_on_selector_all("[data-testid=watch-item]", "a => a.map(x => x.textContent)"),
                             ["National Heart, Lung, and Blood Institute", "Low Income Home Energy Assistance"])
            self.assertNotIn("watch=", other.evaluate("location.search"))
            other.reload()
            other.wait_for_selector("[data-testid=watch-item]")
            self.assertEqual(other.locator("[data-testid=watch-item]").count(), 2)
        finally:
            other.close()
        self.assertEqual(errors, [])

    def test_page_works_with_storage_blocked(self):
        self.page.add_init_script(BLOCK_STORAGE)
        self.go("?view=changes&preset=cuts&sc=LHHS&fy=2026&a=prior&b=request&pct=-20&pctop=le&sort=pct")
        self.assertGreater(len(self.result_ids()), 20)
        self.assertIn("blocking storage", self.page.inner_text("[data-testid=watch-blocked]"))
        star = "[data-testid=fc-row][data-account=ACC-HHS-NIH-NHLBI] [data-testid=watch]"
        self.page.click(star)                               # kept for this page only
        self.assertEqual(self.page.get_attribute(star, "aria-pressed"), "true")
        self.assertEqual(self.page.locator("[data-testid=watch-item]").count(), 1)
        # every other view still works
        self.page.click("#tab-compare")
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.page.goto(self.urls["static"] + "?account=ACC-HHS-NIH-NHLBI&fy=2026")
        self.page.wait_for_selector("[data-testid=ap-actions] [data-testid=watch]")
        self.assertEqual(self.errors, [])

    # ---- What's new
    def test_whats_new_lists_first_added_dates(self):
        # D-5: every document and figure carries the date it first entered the record (added_dates.py), so the flag
        # is on (owner, 2026-10-14: not the retrieval date, which changes on every re-fetch or reparse). Each date
        # lists its new documents and the figures added from documents already on file, newest first
        data = json.loads((self.out / "data" / "whats_new.json").read_text())
        self.assertTrue(data["documents"])
        self.assertEqual((data["undated"], data["undated_figures"]), ([], 0))
        dates = [d["first_added"] for d in data["documents"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual([a["date"] for a in data["additions"]], sorted((a["date"] for a in data["additions"]), reverse=True))
        firsts = {d["document_id"]: d["first_added"] for d in data["documents"]}
        self.assertEqual({(a["document_id"], a["date"]) for a in data["additions"] if a["new_document"]}, set(firsts.items()))
        self.assertTrue([a for a in data["additions"] if not a["new_document"] and a["figures"]])  # added later
        self.assertEqual(data["baseline"], "2026-10-07")
        contract = json.loads((ROOT / "web" / "ui_contract.json").read_text())["features"]["whats_new"]
        self.assertNotIn("waits_for", contract)
        self.assertTrue(json.loads((self.out / "data" / "index.json").read_text())["features"]["whats_new"])
        by = {d["document_id"]: d for d in data["documents"]}
        want = [[a["document_id"], a["date"]] for a in data["additions"]
                if "CJS" in by[a["document_id"]]["subcommittees"] or not by[a["document_id"]]["subcommittees"]]
        for where in ("static", "live"):
            with self.subTest(where=where):
                self.go("?view=changes&sc=CJS&fy=2026", where)
                self.page.wait_for_selector("[data-testid=whats-new-item]", state="attached")
                items = self.page.eval_on_selector_all("[data-testid=whats-new-item]",
                                                       "li => li.map(x => [x.dataset.doc, x.dataset.added])")
                self.assertEqual(items, want)
                heads = self.page.eval_on_selector_all("[data-testid=whats-new] h3", "h => h.map(x => x.textContent)")
                self.assertIn("On file by October 7, 2026", heads)
                self.assertIn("no alerts or emails", self.page.inner_text("[data-testid=whats-new]"))
                self.assertEqual(self.page.locator("[data-testid=whats-new-undated]").count(), 0)

    def test_account_tab_is_named_account(self):
        # the tab opens the account page (UI-5: the One account view is the account page), so it reads "Account"
        self.open("static", "?sc=LHHS&fy=2026")
        self.assertEqual(self.page.inner_text("#tab-account"), "Account")
        self.assertEqual(self.page.eval_on_selector_all(".tabs button:not([hidden])", "b => b.map(x => x.textContent)"),
                         ["Explore", "Find changes", "Account"])
        self.page.click("#tab-account")
        self.assertEqual(self.page.title(), "Account · Approps Intelligence")
        self.assertTrue(self.page.is_visible("#account-view"))

    def test_find_changes_is_a_tab_and_fits_a_phone(self):
        self.open("static", "?sc=LHHS&fy=2026")
        self.page.click("#tab-changes")
        self.page.wait_for_selector("[data-testid=fc-count]")
        self.assertEqual(self.page.title(), "Find changes · Approps Intelligence")
        self.assertEqual(self.page.get_attribute("#tab-changes", "aria-selected"), "true")
        phone = self.browser.new_page(viewport={"width": 390, "height": 844})
        try:
            self.go("?view=changes&preset=cuts&sc=LHHS&fy=2026&a=prior&b=request&pct=-20&pctop=le&sort=pct", page=phone)
            self.assertLessEqual(phone.evaluate("document.documentElement.scrollWidth"), 390)
        finally:
            phone.close()


if __name__ == "__main__":
    unittest.main()
