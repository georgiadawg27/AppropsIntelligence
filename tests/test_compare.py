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
import datetime
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


def with_sourced_totals(src, dst):
    """A copy of the store with a printed Title III total (FY2024 Enacted,
    33,944,930 as H.Rept. 118-582 p.248 prints it) and a bill-total row: the
    shape a workbook would give them -- total_scope title / bill."""
    shutil.copy(src, dst)
    c = sqlite3.connect(dst)
    for aid, name, scope in (("ACC-T3-TOTAL", "Total, Title III, Science", "title"),
                             ("ACC-CJS-TOTAL", "Grand total", "bill")):
        c.execute("INSERT INTO account (canonical_account_id, canonical_name, agency, status, fund_type, "
                  "subcommittee, notes, title, display_order, total_scope) VALUES (?, ?, 'Commerce, Justice, Science', "
                  "'active', 'general', 'CJS', NULL, ?, ?, ?)",
                  (aid, name, "Title III" if "T3" in aid else None, 99 if "T3" in aid else None, scope))
    cols = [r[1] for r in c.execute("PRAGMA table_info(appropriations_observation)")]
    sel = ", ".join({"observation_id": "'OBS-T3'", "canonical_account_id": "'ACC-T3-TOTAL'",
                     "amount": "33944930000"}.get(k, k) for k in cols)
    c.execute(f"INSERT INTO appropriations_observation ({', '.join(cols)}) SELECT {sel} FROM appropriations_observation "
              "WHERE canonical_account_id = 'ACC-NASA-TOTAL' AND fiscal_year = 2024 AND stage = 'Enacted' "
              "AND amount_type = 'budget authority'")
    c.commit()
    c.close()
    return dst


# every figure cell of the grid as rendered: account, fiscal year, stage, state, the number shown
SHOWN_JS = """() => [...document.querySelectorAll('[data-testid=compare-cell]')].map(td => {
    const amt = td.querySelector(':scope > [data-testid=amount]');
    return {account: td.closest('tr').dataset.account, fy: Number(td.dataset.fy), stage: td.dataset.stage,
            state: td.dataset.state, amount: amt ? amt.textContent : null, outside: td.dataset.outside === 'true'};
})"""


def thousands(amount):
    """GridMath.number in $ thousands: whole thousands with commas, a minus sign for a negative."""
    v = round(amount / 1000)
    return ("\u2212" if v < 0 else "") + f"{abs(v):,}"


def expected_headline(row, fy, stage):
    """What a grid cell shows: the headline line's (budget authority, no component; else the cell's
    first line) state, and its printed figure in $ thousands where it has one."""
    cell = row["cells"].get(f"{fy}|{stage}")           # none before the subcommittee's first year
    if cell is None:
        return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": "",
                "amount": None, "outside": False}
    lines = cell["lines"]
    if not lines:
        # (a cell outside the account's life is blank, without the outside-history mark)
        return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": "",
                "amount": None, "outside": cell["outside_history"] and not cell.get("outside_life")}
    h = next((l for l in lines if l["amount_type"] == "budget authority" and not l["component"]), lines[0])
    return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": h["state"],
            "amount": thousands(h["observations"][0]["amount"]) if h["state"] == "value" else None,
            "outside": cell["outside_history"]}


def in_view(row, pairs, view="stages"):
    """The page's rowShown with the default 'Accounts shown': a proposed-only account only outside Enacted history,
    and only where a column in view falls inside its life."""
    if row.get("lifecycle", {}).get("type") == "proposed_only" and view == "history":
        return False
    return any(row["cells"].get(f"{y}|{st}") and not row["cells"][f"{y}|{st}"].get("outside_life") for y, st in pairs)


def shown_rows(grid, pairs, view="stages"):
    """The rows the page shows (everything open): title totals, then rows in view; an agency with any of its
    accounts in view."""
    keep = {r["account"]["canonical_account_id"] for r in grid["rows"] if in_view(r, pairs, view)}
    keep |= {r["member_of"] for r in grid["rows"] if r["member_of"] and r["account"]["canonical_account_id"] in keep}
    return [t["total"] for t in grid["titles"] if t["total"]] + \
        [r for r in grid["rows"] if r["account"]["canonical_account_id"] in keep]


def series_text(l):
    """The page's seriesText: a line's label; a contained or view line says it is not added."""
    if l.get("adds_to_headline") is False:
        how = "inside the figure above" if l["component_kind"] == "contained" else "the figure above, scoped another way"
        return f"{l['component_label'] or l['component']} \u2014 {how}; not added"
    return l["amount_type"] + (" \u00b7 " + l["component"] if l["component"] else "")


def other_lines(row, fy, stage):
    """A cell's non-headline lines with a figure: what its "+N" marker counts and lists."""
    cell = row["cells"].get(f"{fy}|{stage}")
    if not cell or not cell["lines"]:
        return []
    lines = cell["lines"]
    h = next((l for l in lines if l["amount_type"] == "budget authority" and not l["component"]), lines[0])
    return [l for l in lines if l is not h and l["state"] == "value" and l["observations"]]


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
                            # the accounting layer (UI-0, UI-0b; site_accounting.py) adds its own keys beside the lines
                            layer = {"views", "components", "earmark", "defense_portion", "views_excluding_earmarks",
                                     "views_nondefense"}
                            self.assertEqual({k: v for k, v in cell.items() if k not in layer},
                                             {"lines": by_year[y][st], "outside_history": False}, (aid, y, st))
                        else:
                            self.assertTrue(cell["outside_history"], (aid, y, st))
                            # each series in its computed state: no figure or absence is there
                            cov = S.history(conn, aid)["coverage"]
                            self.assertEqual([(line["amount_type"], line["component"], line["state"], line["observations"])
                                              for line in cell["lines"]],
                                             [(s["amount_type"], s["component"], S.cell_state([], None, y, st, cov), [])
                                              for s in own["series"]])
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

    def test_no_stored_end_date_cuts_a_row_off(self):
        # v33 has no effective dates: OSTP's cells run to the last fiscal year on file (FY2027), in their
        # computed states, never outside its history
        row = self.row("ACC-OSTP")
        self.assertEqual(row["fiscal_year_span"], [2017, 2027])
        self.assertFalse(row["cells"]["2027|Enacted"]["outside_history"])
        self.assertNotIn("effective_start", row["account"])
        self.assertNotIn("effective_end", row["account"])

    def test_rollup_rows_carry_their_notes(self):
        rollups = [r["account"]["canonical_account_id"] for r in self.grid["rows"]
                   if "derived rollup" in (r["account"]["notes"] or "").lower()]
        self.assertEqual(rollups, ["ACC-NASA-TOTAL", "ACC-NSF-TOTAL"])

    def test_rows_follow_the_bill(self):
        # by title, then display_order; a rollup heads the accounts it totals
        self.assertEqual([(t["title"], t["total"]) for t in self.grid["titles"]],
                         [("Title I", None), ("Title II", None), ("Title III", None), ("Title V", None), (None, None)])
        self.assertIsNone(self.grid["bill_total"])
        by = {t["title"]: t["rows"] for t in self.grid["titles"]}
        self.assertEqual(by["Title I"], ["ACC-NOAA-ORF", "ACC-USPTO-SE", "ACC-NOAA-PDF"])
        self.assertEqual(by["Title II"], ["ACC-DOJ-AFF", "ACC-DOJ-ANTITRUST-SE", "ACC-DOJ-USTSF", "ACC-DOJ-VAWA"])
        self.assertEqual(by["Title V"], ["ACC-DOJ-WCF", "ACC-DOJ-OJP-RESC", "ACC-DOJ-OIG"])
        self.assertEqual(by[None], ["ACC-DOJ-CVF"])                       # not yet placed: after every title
        self.assertEqual(by["Title III"], [
            "ACC-OSTP", "ACC-NSC",
            "ACC-NASA-TOTAL", "ACC-NASA-SCIENCE", "ACC-NASA-AERONAUTICS", "ACC-NASA-SPACETECH", "ACC-NASA-EXPLORATION",
            "ACC-NASA-SPACEOPS", "ACC-NASA-STEM-ENGAGEMENT", "ACC-NASA-SAFETY-SECURITY", "ACC-NASA-CONSTRUCTION", "ACC-NASA-OIG",
            "ACC-NSF-TOTAL", "ACC-NSF-RRA", "ACC-NSF-MREFC", "ACC-NSF-STEM-EDUCATION", "ACC-NSF-AGENCY-OPS", "ACC-NSF-NSB",
            "ACC-NSF-OIG"])
        self.assertEqual([r["account"]["canonical_account_id"] for r in self.grid["rows"]],
                         [a for t in self.grid["titles"] for a in t["rows"]])

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
        # generic: any account whose total_scope is agency gathers its own agency's accounts
        other = Path(self.tmp.name) / "rollup.db"
        shutil.copy(self.db, other)
        c = sqlite3.connect(other)
        c.execute("UPDATE account SET total_scope = 'agency' WHERE canonical_account_id = 'ACC-OSTP'")
        c.commit()
        c.close()
        g = W.subcommittee(str(other), "CJS")
        ostp = next(r for r in g["rows"] if r["account"]["canonical_account_id"] == "ACC-OSTP")
        self.assertEqual((ostp["rollup"], ostp["rollup_members"]), ("agency", ["ACC-NSC"]))

    def test_title_and_bill_totals_come_only_from_sourced_rows(self):
        # v26 has none: no total at all, never a sum of the rows
        self.assertEqual([t["total"] for t in self.grid["titles"]], [None] * 5)
        # a sourced total -- a rollup noted as a title / bill total -- is the total, not a row
        other = with_sourced_totals(self.db, Path(self.tmp.name) / "totals.db")
        g = plain(W.subcommittee(str(other), "CJS"))
        t3 = next(t for t in g["titles"] if t["title"] == "Title III")
        self.assertEqual((t3["title"], t3["total"]["account"]["canonical_account_id"], g["bill_total"]["account"]["canonical_account_id"]),
                         ("Title III", "ACC-T3-TOTAL", "ACC-CJS-TOTAL"))
        self.assertNotIn("ACC-T3-TOTAL", t3["rows"])
        self.assertNotIn("ACC-CJS-TOTAL", [r["account"]["canonical_account_id"] for r in g["rows"]])
        self.assertEqual([o["amount"] for l in t3["total"]["cells"]["2024|Enacted"]["lines"] for o in l["observations"]],
                         [33_944_930_000])

    def test_rollup_scope_and_title_rank(self):
        # the scope is Account.total_scope, and only that: notes are never read for it
        scope = lambda total_scope, notes=None: S.rollup_scope({"total_scope": total_scope, "notes": notes})
        self.assertEqual([scope(None), scope(""), scope("agency"), scope("title"), scope("bill")],
                         [None, None, "agency", "title", "bill"])
        self.assertEqual([scope(None, "Derived rollup -- bill total (Grand total)"),
                          scope("title", "Derived rollup -- ... after the grand total")], [None, "title"])
        self.assertEqual(scope("agency", "Derived rollup -- title total"), "agency")
        self.assertEqual(S.rollup_scope({"notes": "Derived rollup -- title total"}), None)   # no column: not a total
        self.assertEqual(sorted(["Title VII", None, "Title II", "Title III", "Title IV", "Title I", "Title V"], key=S.title_rank),
                         ["Title I", "Title II", "Title III", "Title IV", "Title V", "Title VII", None])

    def test_a_grand_total_note_on_a_non_total_changes_nothing(self):
        other = Path(self.tmp.name) / "grandnote.db"
        shutil.copy(self.db, other)
        c = sqlite3.connect(other)
        c.execute("UPDATE account SET notes = 'Derived rollup -- bill total; the grand total; title total' "
                  "WHERE canonical_account_id = 'ACC-OSTP'")
        c.commit()
        c.close()
        g = plain(W.subcommittee(str(other), "CJS"))
        shape = lambda grid: ([(r["account"]["canonical_account_id"], r["rollup"], r["rollup_members"], r["member_of"])
                               for r in grid["rows"]],
                              [(t["title"], t["total"]) for t in grid["titles"]], grid["bill_total"])
        self.assertEqual(shape(g), shape(self.grid))                  # every scope, total and grouping as before
        ostp = next(r for r in g["rows"] if r["account"]["canonical_account_id"] == "ACC-OSTP")
        self.assertIsNone(ostp["rollup"])

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
        # v28 placed the other ten as derived; only CVF is left, with no candidate
        self.assertEqual({r["canonical_account_id"]: (r["candidate_title"], r["basis"], r["titles_seen"]) for r in got},
                         {"ACC-DOJ-CVF": ("", "ambiguous", "Title II; Title V; Title VII")})

    def test_printed_title_iii_totals_reconcile(self):
        # every printed "Total, title III, Science" is the title's own lines
        # (budget authority + Title III emergency lines) plus exactly the
        # rescissions / budget amendments / supplemental acts that document
        # prints inside Title III -- figures kept as printed, nothing adjusted
        spec = importlib.util.spec_from_file_location("title_totals", ROOT / "reference" / "review" / "title_totals.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with open(ROOT / "reference" / "review" / "title_iii_printed.csv", newline="") as f:
            printed = list(csv.DictReader(f))
        conn = S.connect(self.db, readonly=True)
        try:
            got = mod.reconcile(conn, printed)
        finally:
            conn.close()
        with open(ROOT / "reference" / "review" / "title_iii_totals.csv", newline="") as f:
            self.assertEqual([{k: str(v) for k, v in r.items()} for r in got], list(csv.DictReader(f)))
        by = {(int(r["fiscal_year"]), r["stage"], r["source_document_id"]): r for r in got}
        self.assertEqual(len(printed), 49)
        self.assertEqual({k for k, r in by.items() if r["reconciles"] == "no store figures"},
                         {(2016, "Enacted", "SRC-CRPT-114HRPT605"), (2016, "Enacted", "SRC-CRPT-114SRPT239")})
        # all 47 FY2017-2026 columns, through the accounts and through the stored
        # rollups (v26 missed FY2025 Senate by 1,000 there: OBS-0889, restored in v27)
        self.assertEqual([k for k, r in by.items() if r["reconciles"] == "no"], [])
        self.assertEqual([k for k, r in by.items() if r["reconciles_through_rollups"] == "no"], [])
        self.assertEqual(sum(r["reconciles"] == "yes" == r["reconciles_through_rollups"] for r in got), 47)
        # the same FY2020 request, printed two ways: the Senate folds the May 2019
        # budget amendments into its estimates, the House lists them apart
        amendments = "OBS-0989 (ACC-NASA-EXPLORATION budget_amendment 1,374,700); OBS-0990 (ACC-NASA-SPACETECH " \
                     "budget_amendment 132,000); OBS-0995 (ACC-NASA-SCIENCE budget_amendment 90,000)"
        self.assertEqual(by[(2020, "President's Budget", "SRC-CRPT-116SRPT127")]["printed_total_includes"], amendments)
        self.assertEqual(by[(2020, "President's Budget", "SRC-CRPT-116HRPT101")]["printed_total_leaves_out"], amendments)
        # a separate supplemental act is never in a title's total
        self.assertFalse(any("supplemental_act" in r["printed_total_includes"] for r in got))

    def test_a_title_total_can_subtract_a_component(self):
        # HHS Title II, S.Rept. 118-207's FY2024 column as printed (thousands):
        # the ten agency totals + Medicare Operations (Sec. 227), with NIH's
        # headline "with CURES Act funding" and CURES also its own component
        # line -- the printed title total leaves CURES out, so it has to be
        # subtracted, which the additions-only search could never find
        spec = importlib.util.spec_from_file_location("title_totals", ROOT / "reference" / "review" / "title_totals.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        db = Path(self.tmp.name) / "hhs.db"
        shutil.copy(self.db, db)
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        tmpl = dict(c.execute("SELECT * FROM appropriations_observation LIMIT 1").fetchone())
        acct = dict(c.execute("SELECT * FROM account LIMIT 1").fetchone())
        # the v32 model (CURES a 'contained' component of NIH; v33 makes it an account and drops the
        # component): the mechanism under test is a component line the title total subtracts
        c.execute("INSERT OR IGNORE INTO component VALUES ('CURES', 'CURES Act', 'contained', 'inside NIH (test)')")
        lines = [("HRSA", 9_171_787, None), ("CDC", 7_992_946, None), ("NIH", 47_168_518, None), ("NIH", 407_000, "CURES"),
                 ("SAMHSA", 7_300_729, None), ("AHRQ", 369_000, None), ("CMS", 1_133_847_008, None),
                 ("ACF", 52_748_216, None), ("ACL", 2_520_342, None), ("ASPR", 3_634_606, None), ("OS", 1_761_616, None),
                 ("MEDICARE-OPS-SEC227", 455_000, None)]
        for name in dict.fromkeys(n for n, _, _ in lines):
            c.execute(f"INSERT INTO account ({','.join(acct)}) VALUES ({','.join('?' * len(acct))})",
                      list(dict(acct, canonical_account_id=f"ACC-HHS-{name}", canonical_name=name, agency=name,
                                subcommittee="TEST-HHS", title="Title II", display_order=None, notes=None).values()))
        for i, (name, amount, component) in enumerate(lines):
            c.execute(f"INSERT INTO appropriations_observation ({','.join(tmpl)}) VALUES ({','.join('?' * len(tmpl))})",
                      list(dict(tmpl, observation_id=f"OBS-HHS-{i}", canonical_account_id=f"ACC-HHS-{name}", fiscal_year=2024,
                                stage="Enacted", amount=amount * 1000, amount_type="budget authority",
                                component=component).values()))
        c.commit()
        c.close()
        conn = S.connect(db, readonly=True)
        try:
            printed = [{"fiscal_year": "2024", "stage": "Enacted", "source_document_id": "S.Rept. 118-207",
                        "printed_total_title_iii_thousands": "1266562768"}]
            # a made-up subcommittee code: v30's real LHHS accounts are not part of this sum
            got, = mod.reconcile(conn, printed, title="Title II", subcommittee="TEST-HHS")
        finally:
            conn.close()
        self.assertEqual(got["reconciles"], "yes", got)
        self.assertEqual(got["printed_total_includes"], "")
        self.assertEqual(got["printed_total_subtracts"], "OBS-HHS-3 (ACC-HHS-NIH CURES 407,000)")
        # CJS's own Title II (Justice) is another subcommittee's title: not in this sum
        self.assertEqual(got["store_base_thousands"], 1_266_969_768)

    def test_prior_year_advance_is_checked_against_last_years_advance(self):
        db = Path(self.tmp.name) / "adv.db"
        shutil.copy(self.db, db)
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        tmpl = dict(c.execute("SELECT * FROM appropriations_observation LIMIT 1").fetchone())
        for oid, fy, stage, t, amt in (("OBS-ADV-24", 2024, "Enacted", "advance", 245_580_414),
                                       ("OBS-PYA-25", 2025, "Senate Reported", "prior_year_advance", -245_580_414),
                                       ("OBS-PYA-25H", 2025, "House Reported", "prior_year_advance", -245_580_000)):
            c.execute(f"INSERT INTO appropriations_observation ({','.join(tmpl)}) VALUES ({','.join('?' * len(tmpl))})",
                      list(dict(tmpl, observation_id=oid, fiscal_year=fy, stage=stage, amount_type=t, component=None,
                                amount=amt * 1000).values()))
        c.commit()
        got = S.prior_year_advance_warnings(c)
        c.close()
        self.assertEqual(len(got), 1)
        self.assertIn("OBS-PYA-25H", got[0])
        self.assertIn("OBS-ADV-24", got[0])

    def test_contained_and_view_lines_name_their_headline(self):
        db = Path(self.tmp.name) / "headline.db"
        shutil.copy(self.db, db)
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        tmpl = dict(c.execute("SELECT * FROM appropriations_observation WHERE component IS NULL LIMIT 1").fetchone())
        head = tmpl["observation_id"]
        other_doc = c.execute("SELECT document_id FROM source_document WHERE document_id <> ?",
                              (tmpl["source_document_id"],)).fetchone()[0]
        for oid, comp, h, doc in (("OBS-C-OK", "CURES", head, tmpl["source_document_id"]),
                                  ("OBS-C-NONE", "program_level", None, tmpl["source_document_id"]),
                                  ("OBS-C-DOC", "appropriated_in_this_bill", head, other_doc),
                                  ("OBS-C-PART", "defense", head, tmpl["source_document_id"])):
            c.execute(f"INSERT INTO appropriations_observation ({','.join(tmpl)}) VALUES ({','.join('?' * len(tmpl))})",
                      list(dict(tmpl, observation_id=oid, component=comp, headline_observation_id=h,
                                source_document_id=doc).values()))
        c.execute("INSERT OR IGNORE INTO component VALUES ('CURES', 'CURES Act', 'contained', 'inside NIH (test)')")
        c.commit()
        kinds = S.component_kinds(c)
        got = S.headline_errors(c, kinds)          # load errors since v33 (were warnings)
        c.close()
        self.assertFalse(any("OBS-C-OK" in w for w in got))                      # a contained line inside its headline
        self.assertTrue(any("OBS-C-NONE" in w and "a view of" in w for w in got))
        self.assertTrue(any("OBS-C-DOC" in w and "source_document_id" in w for w in got))
        self.assertTrue(any("OBS-C-PART" in w and "'part' line" in w for w in got))

    def test_a_second_subcommittee_is_its_own_grid(self):
        # nothing is CJS-specific: move two accounts to another subcommittee
        other = Path(self.tmp.name) / "two.db"
        shutil.copy(self.db, other)
        c = sqlite3.connect(other)
        c.execute("UPDATE account SET subcommittee = 'Energy and Water' "
                  "WHERE canonical_account_id IN ('ACC-OSTP', 'ACC-NSC')")
        c.commit()
        c.close()
        self.assertEqual(W.subcommittees(str(other)), {"subcommittees": ["CJS", "Energy and Water", "LHHS"],
                                                       "names": {"CJS": "Commerce, Justice, Science",
                                                                 "Energy and Water": "Energy and Water",
                                                                 "LHHS": "Labor-HHS-Education"}})
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
                self.assertEqual(json.load(r), {"subcommittees": ["CJS", "LHHS"],
                                            "names": {"CJS": "Commerce, Justice, Science", "LHHS": "Labor-HHS-Education"}})
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
        self.assertEqual(json.loads((out / "data" / "subcommittees.json").read_text()),
                         {"subcommittees": ["CJS", "LHHS"],
                          "names": {"CJS": "Commerce, Justice, Science", "LHHS": "Labor-HHS-Education"}})
        self.assertEqual(json.loads((out / "data" / "subcommittees" / "CJS.json").read_text()), self.grid)


@unittest.skipUnless(sync_playwright and chromium_path(), "playwright / chromium not available")
class GridBrowser(CompareTest):
    """The Explore page in headless Chromium, against the live server and the static export."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        conn = S.connect(cls.db, readonly=True)
        cls.lhhs = plain(S.subcommittee_grid(conn, "LHHS"))
        conn.close()
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
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def open(self, where="static", query="?view=compare&sc=CJS&fy=2026"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.settle()

    def settle(self, page=None):
        """Wait for every font load in flight and one frame: the grid's column widths are measured in the loaded fonts
        (UI-2c), so layout is read only once they have settled."""
        (page or self.page).evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => r())))")

    def menu(self, action, selector, *args):
        """A control in the View menu (SPEC 5.7): open the menu, use the control, close it with Esc."""
        if self.page.is_hidden("#view-menu"):
            self.page.click("#view-btn")
        getattr(self.page, action)(selector, *args)
        self.page.keyboard.press("Escape")

    def advanced(self, action, selector, *args):
        """A control in Filter › Advanced (SPEC 5.6): open the panel and the disclosure, use it, close with Esc."""
        if self.page.is_hidden("#filter-panel"):
            self.page.click("#filter-btn")
        if self.page.get_attribute("#f-adv", "aria-expanded") != "true":
            self.page.click("#f-adv")
        getattr(self.page, action)(selector, *args)
        self.page.keyboard.press("Escape")

    def definitions(self):
        """Open "Definitions and data sources" below the grid (UI-2c), where the legend, counts and provenance live."""
        if self.page.get_attribute("#definitions", "open") is None:
            self.page.click("[data-testid=definitions-toggle]")

    def cell(self, aid, fy, stage):
        return self.page.locator(f"tr[data-account={aid}] td[data-fy='{fy}'][data-stage=\"{stage}\"]")

    def lrow(self, aid):
        return next(r for r in self.lhhs["rows"] if r["account"]["canonical_account_id"] == aid)

    def shown(self):
        """Every figure cell on the page, keyed by (account, fy, stage); each shown once."""
        cells = self.page.evaluate(SHOWN_JS)
        keyed = {(c["account"], c["fy"], c["stage"]): c for c in cells}
        self.assertEqual(len(keyed), len(cells))
        return keyed

    # the default view: Compare stages, FY2026 (its prior-year enacted, then the four stages)
    DEFAULT = [(2025, "Enacted")] + [(2026, st) for st in ("President's Budget", "House Reported", "Senate Reported", "Enacted")]

    def members_in_view(self, total, pairs=None):
        """An agency's accounts the default view shows: those with a column in view inside their life."""
        return [r["account"]["canonical_account_id"] for r in self.lhhs["rows"]
                if r["member_of"] == total and in_view(r, pairs or self.DEFAULT)]

    def expected(self, grid, pairs, view="stages"):
        return {(r["account"]["canonical_account_id"], y, st): expected_headline(r, y, st)
                for r in shown_rows(grid, pairs, view) for y, st in pairs}



class CompareBrowser(GridBrowser):
    """The grid page: three views over the store's own cells. Every state and figure is the
    store's (subcommittee_grid); the page picks columns, lays rows out and works out changes."""

    # ---- the cells are the store's -------------------------------------------------------

    def test_every_headline_cell_is_the_stores(self):
        # Compare stages, each fiscal year: prior-year enacted, then the four stages, every account
        for fy in YEARS:
            with self.subTest(fy=fy):
                self.open("static", f"?view=compare&sc=CJS&grid=stages&fy={fy}")
                self.menu("click", "#expand-all")
                self.assertEqual(self.shown(), self.expected(self.grid, [(fy - 1, "Enacted")] + [(fy, st) for st in FOUR]))

    def test_split_subcommittee_loads_the_same_grid(self):
        # a subcommittee file past export_static.SPLIT_BYTES is also published per fiscal year (UI-0); the static
        # page reads those files and puts back exactly the whole file (which stays published), and the live /api
        # answer is the same grid
        index = json.loads((self.out / "data" / "index.json").read_text())
        self.assertIn("LHHS", index["split_subcommittees"])
        whole = json.loads((self.out / "data" / "subcommittees" / "LHHS.json").read_text())
        self.assertEqual(whole, self.lhhs)
        self.open("static", "?view=compare&sc=LHHS")
        got = self.page.evaluate("source.subcommittee('LHHS')")
        self.assertEqual(got, whole)
        fetched = []
        self.page.on("request", lambda r: fetched.append(r.url))
        self.page.reload()
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.assertTrue(any(u.endswith("/subcommittees/LHHS/2026.json") for u in fetched), fetched)
        self.assertFalse(any(u.endswith("/subcommittees/LHHS.json") for u in fetched))

    def test_enacted_history_and_two_years_show_the_same_cells(self):
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        self.menu("click", "#expand-all")
        # every enacted year on file (the backfill adds one each PR), then FY2027's request and House
        enacted = [y for y in self.lhhs["fiscal_years"] if y < 2027]
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, "Enacted") for y in enacted]
                                                     + [(2027, "President's Budget"), (2027, "House Reported")], "history"))
        self.open("static", "?view=compare&sc=LHHS&grid=years&a=2024&b=2026")
        self.menu("click", "#expand-all")
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, st) for y in (2024, 2026) for st in FOUR]))
        # stage checkboxes narrow both years
        self.menu("uncheck", "[data-testid=stage-checks] input[data-stage='1']")
        self.menu("click", "#expand-all")
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, st) for y in (2024, 2026) for st in FOUR if st != "House Reported"]))

    def test_grid_state_counts_are_unchanged(self):
        # the published grid's counts (tests/fixtures/grid_counts.json, scripts/grid_counts.py): the page shows them
        fixture = json.loads((ROOT / "tests" / "fixtures" / "grid_counts.json").read_text())
        for sc, counts in fixture["state_counts"].items():
            for grid in ("stages", "years", "history"):
                self.open("static", f"?view=compare&sc={sc}&grid={grid}&rev=1")     # the counts show in Reviewer mode
                self.definitions()
                got = {s.get_attribute("data-state"): int(s.inner_text().split()[0].replace(",", ""))
                       for s in self.page.locator("[data-testid=state-counts] [data-state]").all()}
                # the blank cells outside an account's life, counted on their own (never missing)
                self.assertEqual(got.pop("outside_life"), fixture["outside_life_cells"][sc], (sc, grid))
                self.assertEqual(got, counts, (sc, grid))

    def test_static_renders_exactly_what_live_renders(self):
        for query in ("?view=compare&sc=CJS&fy=2026", "?view=compare&sc=LHHS&grid=years&a=2025&b=2026&units=m",
                      "?view=compare&sc=LHHS&grid=history&rev=1"):
            got = {}
            for where in self.urls:
                self.open(where, query)
                got[where] = self.page.inner_html("#side-panel") + self.page.inner_html("#compare-grid")
            self.assertEqual(got["static"], got["live"], query)

    # ---- rows ----------------------------------------------------------------------------

    def body(self):
        return self.page.eval_on_selector_all("#compare-grid tbody tr", """trs => trs.map(t => ({
            cls: t.className, account: t.dataset.account || null, testid: t.dataset.testid,
            name: (t.querySelector('.namebox') || t.querySelector('td.acct')).firstChild.textContent,
            abbr: (t.querySelector('.abbr') || {}).textContent || null}))""")

    def test_row_order(self):
        # SPEC section 4: the total band, then the three sections; agencies collapsed by default
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        rows = self.body()
        self.assertEqual((rows[0]["cls"], rows[0]["account"], rows[0]["name"]),
                         ("title", "ACC-HHS-TITLE-II-TOTAL", "Total, Title II (printed)"))
        self.assertEqual(self.page.locator("tr.title td[data-testid=compare-cell]").count(), 5)
        self.assertNotIn("99 accounts", self.page.inner_text("#compare-grid"))
        labels = [r["name"] for r in rows if r["testid"] == "section-label"]
        self.assertEqual(labels, ["Agencies · 10", "General provisions", "Proposed in the FY2026 request, not enacted"])
        # agency rows: the name and its abbreviation; no "(agency total)"; the proposed agency in its own section
        agencies = [r for r in rows if r["cls"].startswith("agency")]
        self.assertEqual(len(agencies), 11)
        self.assertEqual((agencies[0]["name"], agencies[0]["abbr"]), ("Health Resources and Services Administration", "HRSA"))
        self.assertEqual(agencies[-1]["account"], "ACC-HHS-AHA-TOTAL")
        self.assertGreater(rows.index(agencies[-1]), next(i for i, r in enumerate(rows) if r["name"].startswith("Proposed in")))
        text = self.page.inner_text("#compare-grid")
        self.assertNotIn("(agency total)", text)
        self.assertEqual(self.page.locator("tr.child").count(), 0)                       # collapsed
        # general provisions: one collapsible row; open, its lines and the note
        gp = self.page.locator("tr[data-testid=group-head]")
        self.assertEqual(gp.locator(".namebox").inner_text(), "Title II general provisions (4 lines)")
        gp.locator("[data-testid=chevron]").click()
        self.assertIn("No printed total · each line counts in the Title II total", self.page.locator("tr[data-testid=group-head]").inner_text())
        rows = self.body()
        g = next(i for i, r in enumerate(rows) if r["testid"] == "group-head")
        self.assertEqual([r["name"] for r in rows[g + 1:g + 5]], ["Medicare Operations", "Nonrecurring Expenses Fund, HHS (rescission)",
                                                                "Adoption Incentives (rescission)",
                                                                "Limitation for Title XVIII of the Social Security Act"])
        # NIH open: its accounts in view beneath it, the proposed ones last under their label
        self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=chevron]").click()
        rows = self.body()
        nih = next(i for i, r in enumerate(rows) if r["account"] == "ACC-HHS-NIH-TOTAL")
        want = self.members_in_view("ACC-HHS-NIH-TOTAL")
        self.assertNotIn("ACC-HHS-NIH-SUBSTANCE-USE", want)
        prop = [m for m in want if self.lrow(m)["lifecycle"]["type"] == "proposed_only"]
        kids = [r["account"] for r in rows[nih + 1:nih + 2 + len(want)] if r["account"]]
        self.assertEqual(kids, [m for m in want if m not in prop] + prop)
        self.assertEqual(rows[nih + 1 + len(want) - len(prop)]["testid"], "subgroup-label")

    def test_cjs_uses_the_same_design(self):
        self.open("static", "?view=compare&sc=CJS&fy=2026")
        rows = self.body()
        self.assertEqual([r["name"] for r in rows if r["cls"] == "title"],
                         ["Title I · no printed total on file", "Title II · no printed total on file",
                          "Title III · no printed total on file", "Title V · no printed total on file", "Not yet placed in a title"])
        self.assertEqual([(r["name"], r["abbr"]) for r in rows if r["cls"].startswith("agency")],
                         [("National Aeronautics and Space Administration", "NASA"), ("National Science Foundation", "NSF")])
        self.menu("click", "#expand-all")
        self.assertEqual(sorted(r["account"] for r in self.body() if r["testid"] == "compare-row"),
                         sorted(r["account"]["canonical_account_id"] for r in self.grid["rows"]))
        self.assertEqual(self.page.inner_text("#grid-title"), "Commerce, Justice, Science")
        self.assertEqual(self.page.inner_text("#eyebrow"), "COMMERCE, JUSTICE, SCIENCE · TITLES I, II, III, V · FY2026")   # condensed caps
        self.assertEqual(self.page.locator("[data-testid=tile]").count(), 0)
        # no printed total: the overview says so and never adds the accounts
        self.assertIn("never adds accounts", self.page.inner_text("[data-testid=ov-no-total]"))

    def test_chevrons_only_on_rows_that_fold(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        with_chevron = {r.get_attribute("data-account") for r in self.page.locator("tr[data-account]:has([data-testid=chevron])").all()}
        folds = {r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["rollup_members"]}
        self.assertEqual(with_chevron, folds | {"ACC-HHS-AHA-TOTAL"})
        self.assertEqual(self.page.locator("tr[data-testid=group-head] [data-testid=chevron]").count(), 1)
        for aid in ("ACC-HHS-AHRQ-TOTAL", "ACC-HHS-ACL-TOTAL"):
            self.assertEqual(self.page.locator(f"tr[data-account={aid}] .spacer").count(), 1, aid)
        self.assertEqual(self.page.locator("[data-testid=chevron][aria-expanded=true]").count(), 0)   # all collapsed

    def test_nih_folds_over_its_institutes(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        chev = lambda: self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=chevron]")
        members = self.page.locator("tr[data-member-of=ACC-HHS-NIH-TOTAL]")
        self.assertEqual((chev().get_attribute("aria-expanded"), chev().inner_text(), members.count()), ("false", "▸", 0))
        chev().focus()
        self.page.keyboard.press("Enter")                           # keyboard-operable; focus stays on it
        self.assertEqual((chev().get_attribute("aria-expanded"), chev().inner_text()), ("true", "▾"))
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.for"), "ACC-HHS-NIH-TOTAL")
        self.assertEqual(members.count(), len(self.members_in_view("ACC-HHS-NIH-TOTAL")))
        self.assertIn("open=ACC-HHS-NIH-TOTAL", self.page.url)
        self.page.keyboard.press("Enter")
        self.assertEqual(members.count(), 0)

    def test_aha_opens_to_its_seven_incoming_relationships(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        aha = self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL]")
        self.assertEqual(aha.locator("[data-testid=tag]").inner_text(), "proposed")
        notes = self.page.locator("tr[data-testid=rel-note][data-note-of=ACC-HHS-AHA-TOTAL]")
        self.assertEqual(notes.count(), 0)                                          # folded until opened
        aha.locator("[data-testid=chevron]").click()
        self.assertEqual(notes.count(), 7)
        got = [(n.locator("td.acct").inner_text(), n.locator("td.note-text").inner_text()) for n in notes.all()]
        self.assertEqual(got[0], ("Health Resources and Services Administration",
                                  "Whole agency proposed to be consolidated into AHA in the FY2026 request"
                                  " · figures stay under its current agency · not added here"))
        self.assertIn(("National Institute of Environmental Health Sciences",
                       "Proposed to move into AHA in the FY2026 request · figures stay under its current agency · not added here"), got)

    def test_a_moving_account_carries_one_move_line(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        move = lambda aid: [m.inner_text() for m in self.page.locator(f"tr[data-account={aid}] [data-testid=move-note]").all()]
        self.assertEqual(move("ACC-HHS-NIH-NIEHS"), ["FY2026 request: moved to Administration for a Healthy America (proposed)"])
        # a proposed consolidation (REL-LHHS-0005, proposed_consolidation since 2026-10-11) says so
        self.assertEqual(move("ACC-HHS-NIH-NIDA"), ["FY2027 request: consolidated into National Institute of Substance Use and Addiction Research (proposed)"])
        self.menu("click", "#expand-all")
        self.assertEqual(move("ACC-HHS-HRSA-TOTAL"), [])                # a whole agency: only AHA's notes say so
        self.assertEqual(move("ACC-HHS-ASPR-RDP"), [])                  # into an account that is not proposed
        self.assertEqual(self.page.locator("tr[data-testid=move-note]").count(), 0)   # never a row of its own

    def test_tags(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        self.menu("click", "#expand-all")
        tag = lambda aid: [t.inner_text() for t in self.page.locator(f"tr[data-account={aid}] [data-testid=tag]").all()]
        self.assertEqual(tag("ACC-HHS-NIH-NEUROSCIENCE"), ["proposed"])
        self.assertEqual(tag("ACC-HHS-HRSA-HEALTH-CENTERS"), ["inside the line above · not added"])
        self.assertEqual(tag("ACC-HHS-NIH-NCI"), [])
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-HRSA-HEALTH-CENTERS] td.acct.lvl2").count(), 1)
        ids = self.page.eval_on_selector_all("#compare-grid tr[data-account]", "trs => trs.map(t => t.dataset.account)")
        self.assertEqual(ids[ids.index("ACC-HHS-HRSA-PRIMARY-CARE") + 1], "ACC-HHS-HRSA-HEALTH-CENTERS")
        self.assertNotIn("single appropriation heading", self.page.inner_text("#compare-grid").lower())

    # ---- the account lifecycle (owner, 2026-10-09) ----------------------------------------

    def rows_shown(self):
        return [r.get_attribute("data-account") for r in self.page.locator("tr[data-testid=compare-row]").all()]

    def test_aha_only_in_its_requests_never_in_enacted_history(self):
        # proposed in the FY2026 and FY2027 requests, never enacted: its row shows where a request column is in
        # view; Enacted cells blank; not in Enacted history unless 'proposed-only accounts' is ticked
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        self.assertIn("ACC-HHS-AHA-TOTAL", self.rows_shown())
        self.assertEqual(self.cell("ACC-HHS-AHA-TOTAL", 2026, "President's Budget").get_attribute("data-state"), "value")
        enacted = self.cell("ACC-HHS-AHA-TOTAL", 2026, "Enacted")
        self.assertEqual((enacted.get_attribute("data-state"), enacted.get_attribute("data-outside-life"), enacted.inner_text()),
                         ("", "outside", ""))
        note = self.cell("ACC-HHS-AHA-TOTAL", 2026, "President's Budget").locator("[data-testid=cell-note]")
        self.assertEqual(note.get_attribute("title"), "Proposed in the FY2026 request; never enacted.")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2025&rev=1")
        self.assertNotIn("ACC-HHS-AHA-TOTAL", self.rows_shown())
        self.open("static", "?view=compare&sc=LHHS&grid=history&rev=1")
        self.assertFalse(self.page.locator("[data-testid=show-proposed]").is_checked())
        self.assertNotIn("ACC-HHS-AHA-TOTAL", self.rows_shown())
        self.advanced("check", "[data-testid=show-proposed]")
        self.assertIn("ACC-HHS-AHA-TOTAL", self.rows_shown())
        self.assertIn("show=proposed", self.page.url)
        self.assertEqual(self.cell("ACC-HHS-AHA-TOTAL", 2026, "Enacted").get_attribute("data-outside-life"), "outside")
        # the filter defaults on in the two compare views
        for grid in ("stages", "years"):
            self.open("static", f"?view=compare&sc=LHHS&grid={grid}&rev=1")
            self.assertTrue(self.page.locator("[data-testid=show-proposed]").is_checked(), grid)

    def test_arpa_h_appears_from_fy2022(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2021&open=ACC-HHS-NIH-TOTAL&rev=1")
        self.assertNotIn("ACC-HHS-NIH-ARPA-H", self.rows_shown())
        # 'new accounts before their first year': the row, blank
        self.advanced("check", "[data-testid=show-created]")
        self.assertIn("ACC-HHS-NIH-ARPA-H", self.rows_shown())
        for st in FOUR:
            c = self.cell("ACC-HHS-NIH-ARPA-H", 2021, st)
            self.assertEqual((c.get_attribute("data-outside-life"), c.inner_text()), ("before", ""), st)
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2022&open=ACC-HHS-NIH-TOTAL&rev=1")
        self.assertIn("ACC-HHS-NIH-ARPA-H", self.rows_shown())
        self.assertEqual(self.cell("ACC-HHS-NIH-ARPA-H", 2022, "Enacted").locator("[data-testid=cell-note]").get_attribute("title"),
                         "Created FY2022.")
        # in Enacted history its row shows, blank before FY2022
        self.open("static", "?view=compare&sc=LHHS&grid=history&open=ACC-HHS-NIH-TOTAL&rev=1")
        self.assertIn("ACC-HHS-NIH-ARPA-H", self.rows_shown())
        self.assertEqual(self.cell("ACC-HHS-NIH-ARPA-H", 2021, "Enacted").get_attribute("data-outside-life"), "before")
        self.assertEqual(self.cell("ACC-HHS-NIH-ARPA-H", 2022, "Enacted").get_attribute("data-state"), "value")

    def test_a_created_note_links_its_predecessor(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2024&rev=1")
        self.menu("click", "#expand-all")
        n = self.cell("ACC-HHS-ASPR-TOTAL", 2024, "Enacted").locator("a[data-testid=cell-note]")
        self.assertEqual(n.get_attribute("data-link"), "ACC-HHS-OS-PHSSEF")
        self.assertIn("Created FY2024 (from Public Health and Social Services Emergency Fund).", n.get_attribute("title"))

    def test_a_renamed_account_keeps_one_row(self):
        # HRSA's 'Program Management' (through FY2022) is ACC-HHS-HRSA-PROGRAM-SUPPORT: one row, every enacted year
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        self.menu("click", "#expand-all")
        self.assertEqual(self.rows_shown().count("ACC-HHS-HRSA-PROGRAM-SUPPORT"), 1)
        for y in (2017, 2022, 2023, 2026):
            self.assertEqual(self.cell("ACC-HHS-HRSA-PROGRAM-SUPPORT", y, "Enacted").get_attribute("data-state"), "value", y)

    def test_a_missing_cell_inside_a_life_still_shows_a_question_mark(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2025")
        self.menu("click", "#expand-all")
        c = self.cell("ACC-HHS-CDC-PROGRAM-SUPPORT", 2025, "House Reported")
        self.assertEqual((c.get_attribute("data-state"), c.locator("[data-testid=token-missing]").inner_text()), ("missing", "missing"))

    def test_no_tiles_the_overview_shows_the_printed_title_total(self):
        # SPEC section 3: no tiles above the grid; the headline figures live in the overview panel
        total = next(t for t in self.lhhs["titles"] if t["title"] == "Title II")["total"]
        for view in ("stages", "years", "history"):
            self.open("static", f"?view=compare&sc=LHHS&grid={view}&fy=2026&a=2024&b=2026")
            self.assertEqual(self.page.locator("[data-testid=tile], #tiles").count(), 0, view)
            self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .s"),
                             f"{expected_headline(total, 2026, 'Enacted')['amount']} $K · as printed", view)
        self.open("static", "?view=compare&sc=CJS&fy=2026")
        self.assertIn("No printed title or bill total", self.page.inner_text("[data-testid=side-panel]"))   # never a sum

    def test_a_sourced_total_heads_the_grid_and_the_overview(self):
        db = with_sourced_totals(self.db, Path(self.tmp.name) / "totals_browser.db")
        srv = W.serve(db, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS&grid=stages&fy=2024")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .l"), "Title III enacted, FY2024")
            self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .s"), "33,944,930 $K · as printed")
            row = self.page.locator("tr.title[data-account=ACC-T3-TOTAL]")
            self.assertEqual(row.locator("[data-testid=total]").inner_text(), "Total, Title III (printed)")
        finally:
            srv.shutdown()
            srv.server_close()

    # ---- cells ---------------------------------------------------------------------------

    def test_a_number_links_to_its_source(self):
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2024&open=ACC-NASA-TOTAL")
        a = self.cell("ACC-NASA-SCIENCE", 2024, "Enacted").locator("[data-testid=amount]")
        o = next(l for l in self.row("ACC-NASA-SCIENCE")["cells"]["2024|Enacted"]["lines"]
                 if l["amount_type"] == "budget authority")["observations"][0]
        self.assertEqual(a.inner_text(), thousands(o["amount"]))
        self.assertEqual(a.get_attribute("data-source"), f"{o['source_document_id']} p.{o['source_page']}")
        # SPEC 5.1 (UI-2): a button that opens the source in the side panel; it carries the document's page link
        self.assertEqual(a.evaluate("e => e.tagName"), "BUTTON")
        self.assertEqual(a.get_attribute("data-href"), o["url_or_identifier"] + "#page=" + str(o["source_page"]).split("-")[0])
        # SPEC 5.1: a faint dotted underline (#b9c3d3, 4px below), its hover text naming the document and page
        style = lambda: a.evaluate("e => [getComputedStyle(e).textDecorationLine, getComputedStyle(e).textDecorationStyle, "
                                   "getComputedStyle(e).textDecorationColor, getComputedStyle(e).textUnderlineOffset]")
        self.assertEqual(style(), ["underline", "dotted", "rgb(185, 195, 211)", "4px"])
        short = self.page.evaluate("id => DOCUMENT_NAMES[id]", o["source_document_id"])
        self.assertTrue(short)
        self.assertTrue(a.get_attribute("title").startswith(f"Click for its source: {short}, p. {o['source_page']}"))

    def test_tokens_carry_their_meaning(self):
        for state, text in (("not_enacted", "n/e"), ("missing", "missing"), ("not_collected", "n/c"), ("not_funded", "—"),
                            ("no_printed_total", "—")):
            # not yet collected: FY2027 Senate (the FY2024 House column now holds the subcommittee draft's figures)
            # no printed total: the AHA total's FY2026 cells (ASPR's FY2023 cells now read 'no figure')
            self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2027" if state == "not_enacted" else
                      "?view=compare&sc=LHHS&grid=years&a=2026&b=2027" if state in ("not_collected", "no_printed_total") else
                      "?view=compare&sc=LHHS&grid=years&a=2023&b=2024")
            self.menu("click", "#expand-all")
            tok = self.page.locator(f"td[data-state={state}] [data-testid=token-{state.replace('_', '-')}]").first
            self.assertEqual(tok.inner_text(), text, state)
            self.assertTrue(tok.get_attribute("aria-label") and tok.get_attribute("title"), state)
            m = tok.evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).borderTopStyle, getComputedStyle(e).color]")
            # muted, no box; the word tokens small (12px), a dash at the figures' size
            self.assertEqual(m[1:], ["none", "rgb(91, 99, 114)"], state)
            if text != "—":
                self.assertEqual(m[0], "12px", state)
        self.definitions()
        legend = self.page.inner_text("[data-testid=legend]")
        for part in ("— not funded (printed zero or confirmed absent)", "missing = document on file, figure not entered",
                     "blank = outside the account’s life"):
            self.assertIn(part, legend)

    def test_change_columns(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&cols=request")
        r = self.lrow("ACC-HHS-HRSA-TOTAL")
        fig = lambda y, st: next(l for l in r["cells"][f"{y}|{st}"]["lines"] if l["amount_type"] == "budget authority"
                                 and not l["component"])["observations"][0]["amount"]
        row = self.page.locator("tr[data-account=ACC-HHS-HRSA-TOTAL]")
        cells = row.locator("[data-testid=change-cell]")
        got = [(c.locator("[data-testid=change]").inner_text(), c.locator(".pct").inner_text()) for c in cells.all()]
        sign = lambda d: ("+" if d > 0 else "−") + f"{abs(d) // 1000:,}"
        pct = lambda d, base: ("+" if d > 0 else "−") + f"{abs(d) / base * 100:.1f}%"
        prior = fig(2026, "Enacted") - fig(2025, "Enacted")
        req = fig(2026, "Enacted") - fig(2026, "President's Budget")
        self.assertEqual(got, [(sign(prior), pct(prior, fig(2025, "Enacted"))), (sign(req), pct(req, fig(2026, "President's Budget")))])
        # $ over %: the percentage on its own smaller line
        self.assertEqual(cells.first.locator(".pct").evaluate("e => [getComputedStyle(e).display, getComputedStyle(e).fontSize]"),
                         ["block", "12px"])
        colors = row.locator("[data-testid=change-cell] > span").evaluate_all("ss => ss.map(s => getComputedStyle(s).color)")
        self.assertEqual(set(colors) - {"rgb(29, 107, 58)", "rgb(179, 71, 27)"}, set())       # up / down, figures only
        self.assertEqual(set(row.locator("[data-testid=change-cell]").evaluate_all("cs => cs.map(c => getComputedStyle(c).backgroundColor)")),
                         {"rgb(255, 255, 255)"})
        # no prior-year figure, no change value: AHA has no FY2025 Enacted
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=change-cell]").first.inner_text(), "")
        heads = self.page.eval_on_selector_all("#compare-grid thead tr", "trs => trs.map(t => [...t.children].map(c => c.innerText))")
        self.assertEqual(heads, [["Account", "FY2025", "FY2026", "Change vs"],
                                 ["Enacted (CR)", "Request", "House", "Senate", "Enacted", "FY2025 enacted", "FY2026 request"]])
        # the request column is off by default (View › Columns)
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-HRSA-TOTAL] [data-testid=change-cell]").count(), 1)

    def test_draft_stages_are_labeled_in_every_view(self):
        # a House / Senate column whose Bill Report Reference row says draft = TRUE (the full committee never
        # reported the bill) reads "House (draft)" / "Senate (draft)"; its title names the document
        want = {"LHHS": {(2021, "Senate Reported"), (2022, "Senate Reported"), (2023, "Senate Reported"), (2024, "House Reported")},
                "CJS": {(2021, "Senate Reported"), (2022, "Senate Reported"), (2023, "Senate Reported"), (2024, "House Reported")}}
        title = "Committee draft \u2014 the full committee never reported this bill \u00b7 "
        for sc, drafts in want.items():
            g = self.lhhs if sc == "LHHS" else self.grid
            self.assertEqual({(int(k.split("|")[0]), k.split("|")[1]) for k in g["drafts"]}, drafts, sc)
            seen = set()
            views = [f"grid=stages&fy={y}" for y in sorted({y for y, _ in drafts})] + ["grid=years&a=2023&b=2024",
                                                                                      "grid=years&a=2021&b=2022", "grid=history"]
            for view in views:
                self.open("static", f"?view=compare&sc={sc}&{view}")
                labels = self.page.locator("#compare-grid thead [data-testid=draft-label]")
                for i in range(labels.count()):
                    th = labels.nth(i)
                    y, st = th.get_attribute("data-draft").split("|")
                    self.assertIn((int(y), st), drafts, (sc, view))
                    # (a stage with a Bill Report Reference note keeps its "†" mark before the label)
                    self.assertEqual(th.inner_text().removeprefix("\u2020").strip(),
                                     {"House Reported": "House", "Senate Reported": "Senate"}[st] + " (draft)")
                    doc = g["drafts"][f"{y}|{st}"]["document"]
                    self.assertTrue(th.get_attribute("title").startswith(title + doc), th.get_attribute("title"))
                    seen.add((int(y), st))
                # every other House / Senate header is plain
                plain = self.page.eval_on_selector_all("#compare-grid thead th:not([data-testid=draft-label])",
                                                       "ts => ts.map(t => t.innerText).filter(t => /\\(draft\\)/.test(t))")
                self.assertEqual(plain, [], (sc, view))
                if view == "grid=history":
                    # the history view's stage columns are the next year's request and House: no draft among them
                    self.assertEqual(labels.count(), 0, sc)
            self.assertEqual(seen, drafts, sc)
        self.assertEqual(self.lhhs["drafts"]["2024|House Reported"]["document_id"], "SRC-EXPL-LHHS-FY2024-HOUSE")

    def test_two_years_and_history_columns(self):
        self.open("static", "?view=compare&sc=LHHS&grid=years&b=2026")
        heads = lambda: self.page.eval_on_selector_all("#compare-grid thead tr", "trs => trs.map(t => [...t.children].map(c => c.innerText))")
        # FY2024's House column is the subcommittee draft (Bill Report Reference draft = TRUE)
        self.assertEqual(heads(), [["Account", "FY2024", "FY2026", "Change"],
                                   ["Request", "House (draft)", "Senate", "Enacted", "Request", "House", "Senate", "Enacted",
                                    "FY2026 vs FY2024 enacted"]])
        self.assertEqual(self.page.locator("#compare-grid thead th.yb").count(), 5)
        self.menu("select_option", "#basis", "0")
        self.assertEqual(heads()[1][-1], "FY2026 vs FY2024 request")
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        enacted = [y for y in self.lhhs["fiscal_years"] if y < 2027]
        self.assertEqual(heads(), [["Account", "Enacted", "FY2027", "Change"],
                                   [f"FY{y}" + (" (CR)" if y == 2025 else "") for y in enacted]
                                   + ["Request", "House", f"FY{enacted[0] % 100} → FY26"]])
        self.assertTrue(self.page.locator("#fy-pick").is_disabled())          # every enacted year shows

    def test_reviewer_mode_dots(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        self.menu("click", "#expand-all")
        self.assertEqual(self.page.locator("[data-testid=status-dot]").count(), 0)
        self.definitions()
        self.assertTrue(self.page.is_hidden("#rev-legend"))
        self.menu("check", "#reviewer")
        self.menu("click", "#expand-all")
        self.assertIn("rev=1", self.page.url)
        self.assertTrue(self.page.is_visible("#rev-legend"))
        dots = self.page.locator("[data-testid=status-dot]")
        self.assertGreater(dots.count(), 0)
        for d in dots.all():
            status = d.get_attribute("data-status")
            if status in ("auto-validated", "human-verified"):
                # a verified cell shows a mark only for its records: pending (pend) or resolved (res)
                pending, resolved = int(d.get_attribute("data-pending")), int(d.get_attribute("data-resolved"))
                self.assertEqual(d.get_attribute("class"), "dot pend" if pending else "dot res")
                self.assertGreater(pending + resolved, 0)
            else:
                self.assertEqual(d.get_attribute("class"), "dot flag" if status == "flagged" else "dot unv")
        rows = self.lhhs["rows"] + [self.lhhs["titles"][0]["total"]]
        flagged = sum(1 for r in rows for y, st in [(2025, "Enacted")] + [(2026, s) for s in FOUR]
                      for c in [r["cells"].get(f"{y}|{st}")] if c and c["lines"]
                      for h in [next((l for l in c["lines"] if l["amount_type"] == "budget authority" and not l["component"]), c["lines"][0])]
                      if h["observations"] and h["observations"][0]["verification_status"] == "flagged")
        self.assertEqual(self.page.locator("[data-testid=status-dot][data-status=flagged]").count(), flagged)

    def test_reviewer_mode_lists_resolved_records(self):
        # a record a person resolved stays in the tooltip as "resolved: <its resolution>"
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        self.menu("click", "#expand-all")
        d = self.cell("ACC-HHS-ACF-TOTAL", 2025, "Enacted").locator("[data-testid=status-dot]")
        self.assertEqual(d.count(), 1)
        self.assertEqual(d.get_attribute("data-status"), "unverified")       # was flagged; the CR question resolved
        self.assertEqual(d.get_attribute("data-pending"), "0")
        self.assertIn("VAL-LHHS-01923 resolved: Owner decision: FY2025 was funded by a full-year CR (P.L. 119-4)",
                      d.get_attribute("title"))
        res = self.page.locator("[data-testid=status-dot].res")
        self.assertGreater(res.count(), 0)
        for x in res.all()[:20]:
            self.assertIn(" resolved: ", x.get_attribute("title"))
            self.assertIn(x.get_attribute("data-status"), ("auto-validated", "human-verified"))

    def test_reviewer_mode_lists_pending_records(self):
        # the mark stays verification_status; the open records come from human_review_status
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2027&rev=1")
        self.menu("click", "#expand-all")
        dots = self.page.locator("[data-testid=status-dot]")
        # CJS: no flagged or unconfirmed cell in view (the four House figures the bill text disagreed with were
        # re-sourced to H.Rept. 119-652 and now match it)
        self.assertEqual(dots.count(), 1)
        d = dots.first
        self.assertEqual(d.get_attribute("class"), "dot pend")
        self.assertEqual(d.get_attribute("data-status"), "human-verified")
        self.assertEqual(d.get_attribute("data-pending"), "1")
        self.assertIn("VAL-0089 semantic flag", d.get_attribute("title"))
        self.assertEqual(self.page.text_content("[data-testid=pending-count]"),
                         "· 1 cell in view with records pending review")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        self.menu("click", "#expand-all")
        for d in self.page.locator("[data-testid=status-dot][data-status=flagged]").all():
            n = int(d.get_attribute("data-pending"))
            self.assertGreater(n, 0)                         # every flagged cell has an open record ...
            self.assertIn(f"{n} pending review: ", d.get_attribute("title"))   # ... listed by ID

    # ---- views, units, URL ---------------------------------------------------------------

    def test_view_years_and_units_live_in_the_url(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        self.assertTrue(self.page.url.endswith("?sc=LHHS&grid=stages&fy=2026"), self.page.url)
        self.page.click("#view-switch button[data-grid=years]")
        self.assertTrue(self.page.url.endswith("grid=years&a=2024&b=2026"), self.page.url)
        self.page.select_option("#fy-a", "2025")
        self.menu("uncheck", "[data-testid=stage-checks] input[data-stage='0']")
        self.menu("select_option", "#basis", "1")
        self.menu("click", "#units button[data-units=m]")
        self.assertTrue(self.page.url.endswith("grid=years&a=2025&b=2026&stages=0111&basis=house&units=m"), self.page.url)
        self.assertEqual(self.page.locator("#units button.on").inner_text(), "$ millions")
        self.assertEqual(self.page.inner_text("#grid-subline"), "$ millions · as printed")
        state = lambda: (self.page.inner_html("#side-panel") + self.page.inner_html("#compare-grid"), self.page.url,
                         self.page.eval_on_selector_all("#fy-field select, #view-menu select, #view-menu input",
                                                        "es => es.map(e => e.type === 'checkbox' ? e.checked : e.value)"))
        self.page.mouse.move(2, 2)
        before = state()
        self.open("static", "?" + self.page.url.split("?", 1)[1])
        self.assertEqual(state(), before)                                            # the link reproduces it
        a = self.cell("ACC-HHS-HRSA-TOTAL", 2026, "Enacted").locator("[data-testid=amount]").inner_text()
        self.assertRegex(a, r"^[\d,]+\.\d$")                                         # millions: one decimal
        # old links still open: a fy range at its last year; grid=enacted as Enacted history
        self.open("static", "?view=compare&sc=CJS&fy=2017-2025&stage=Enacted")
        self.assertTrue(self.page.url.endswith("grid=stages&fy=2025"), self.page.url)
        self.open("static", "?view=compare&sc=CJS&grid=enacted")
        self.assertTrue(self.page.url.endswith("grid=history"), self.page.url)

    def test_expand_and_collapse_all(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026")
        self.menu("click", "#collapse-all")
        shown = [r.get_attribute("data-account") for r in self.page.locator("tr[data-testid=compare-row]").all()]
        every = shown_rows(self.lhhs, self.DEFAULT)              # the rows with a column in view inside their life
        # collapsed: the totals, and nothing inside an agency or the general-provisions group
        self.assertEqual(sorted(shown), sorted(r["account"]["canonical_account_id"] for r in every
                                               if not r.get("member_of") and r["account"]["bureau"] != "General Provisions"))
        self.menu("click", "#expand-all")
        self.assertEqual(self.page.locator("tr[data-testid=compare-row]").count(), len(every))
        self.assertEqual({c.get_attribute("aria-expanded") for c in self.page.locator("[data-testid=chevron]").all()}, {"true"})
        self.assertEqual(self.page.locator("tr[data-testid=rel-note]").count(), 7)

    # ---- layout and style ----------------------------------------------------------------

    def test_layout(self):
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        m = self.page.evaluate("""() => { const w = document.querySelector('#grid-wrap');
            const h2 = document.querySelector('#compare-grid thead tr:nth-child(2) th');
            const acct = document.querySelector('#compare-grid tbody td.acct');
            return {vscroll: w.scrollHeight > w.clientHeight, overflowY: getComputedStyle(w).overflowY,
                    bar: !document.querySelector('#grid-hbar').hidden, head2: getComputedStyle(h2).top,
                    head1: document.querySelector('#compare-grid thead th.grp').getBoundingClientRect().height + 'px',
                    nameSticky: getComputedStyle(acct).position, headSticky: getComputedStyle(h2).position,
                    font: getComputedStyle(document.querySelector('#compare-grid td')).fontFamily,
                    nums: getComputedStyle(document.querySelector('#compare-grid td.num')).fontVariantNumeric,
                    size: getComputedStyle(document.querySelector('#compare-grid')).fontSize,
                    wide: document.querySelector('#compare-grid').offsetWidth > w.clientWidth}; }""")
        # one vertical scrollbar, the page's (UI-2c): the grid is not a scroll box; history is wide, so the pinned bar shows
        self.assertFalse(m["vscroll"])
        self.assertEqual(m["overflowY"], "visible")
        self.assertTrue(m["wide"])
        self.assertTrue(m["bar"])
        self.assertEqual((m["head2"], m["nameSticky"], m["headSticky"]), (m["head1"], "sticky", "sticky"))
        self.assertTrue(m["font"].startswith('"IBM Plex Sans"'))
        self.assertEqual((m["size"], m["nums"]), ("14px", "tabular-nums"))
        # page order: header, control row, eyebrow, heading, units label, grid, side panel, legend, provenance
        order = self.page.evaluate("""() => ['.appbar', '#controls', '#eyebrow', '#grid-title', '#grid-subline', '#grid-wrap',
            '#side-panel', '#grid-legend', '#grid-counts', '#grid-provenance'].map(s => document.querySelector(s))
            .map((e, i, a) => i === 0 || (a[i - 1].compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) > 0)""")
        self.assertEqual(set(order), {True})
        self.assertEqual(self.page.inner_text("#grid-title"), "Department of Health and Human Services")
        self.assertEqual(self.page.inner_text("#eyebrow"), "LABOR-HHS-EDUCATION · TITLE II · ENACTED HISTORY")
        self.assertEqual(self.page.inner_text("#grid-subline"), "$ thousands · as printed")
        self.definitions()
        self.assertIn("sha256", self.page.inner_text("#grid-provenance"))
        self.assertIn("(v38)", self.page.inner_text("#grid-provenance"))
        self.assertTrue(self.page.is_hidden("#freshness"))
        self.assertTrue(self.page.is_visible(".tabs"))
        self.assertEqual(self.page.locator("[data-testid=tile]").count(), 0)
        self.assertNotIn("pilot", self.page.inner_text("#grid-title").lower())
        # the header bar: navy, the product name, Explore marked current
        self.assertEqual(self.page.locator(".appbar").evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(22, 33, 58)")
        self.assertEqual(self.page.inner_text(".appbar .brand"), "Approps Intelligence")
        self.assertEqual(self.page.locator(".appbar .brand .mark").count(), 1)

    def test_rules_and_bands(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        self.page.mouse.move(0, 0)
        css = lambda sel, prop: self.page.locator(sel).first.evaluate(f"e => getComputedStyle(e).{prop}")
        self.assertEqual(css("tr[data-account=ACC-HHS-CDC-TOTAL] td.num", "borderBottomColor"), "rgb(232, 234, 238)")   # rule
        self.assertEqual(css("tr.title td.num", "borderBottomColor"), "rgb(207, 211, 218)")                              # rule-strong
        self.assertEqual(css("tr.title td.num", "backgroundColor"), "rgb(239, 237, 231)")                                # total band
        self.assertEqual(css("tr[data-testid=section-label] td", "backgroundColor"), "rgb(246, 245, 241)")               # page
        self.assertEqual(css("tr[data-testid=section-label] td.acct", "fontFamily").split(",")[0], '"IBM Plex Sans Condensed"')
        # no zebra: every line white
        self.assertEqual(set(self.page.eval_on_selector_all("tr[data-member-of=ACC-HHS-NIH-TOTAL] td.num:not(.lead)",
                                                            "ts => ts.map(t => getComputedStyle(t).backgroundColor)")), {"rgb(255, 255, 255)"})
        self.assertEqual(css("tr[data-account=ACC-HHS-NIH-TOTAL] td.num", "borderBottomColor"), "rgb(207, 211, 218)")

    # ---- design reference v2: rollups, the change block set apart, notes as a corner mark ---

    def test_total_label_on_every_open_rollup_row_and_nowhere_else(self):
        # the title band names itself a printed total; an open agency says it is the sum of the lines below it
        for sc, grid in (("LHHS", self.lhhs), ("CJS", self.grid)):
            with self.subTest(sc=sc):
                self.open("static", f"?view=compare&sc={sc}&fy=2026")
                self.menu("click", "#expand-all")
                labelled = self.page.eval_on_selector_all("[data-testid=total]", "ts => ts.map(t => t.closest('tr').dataset.account)")
                # every rollup row with lines in view, except a proposed agency (its rows beneath are relationship notes)
                rollups = [r["account"]["canonical_account_id"] for r in grid["rows"]
                           if r["rollup"] and r["account"]["status"] != "proposed"
                           and any(m["member_of"] == r["account"]["canonical_account_id"] for m in grid["rows"])] + \
                          [t["total"]["account"]["canonical_account_id"] for t in grid["titles"] if t["total"]]
                self.assertEqual(sorted(labelled), sorted(rollups))
                self.assertEqual(self.page.locator("tr.child [data-testid=total], tr.group [data-testid=total]").count(), 0)
                for t in self.page.locator("tr.agency [data-testid=total]").all():
                    self.assertRegex(t.inner_text(), r"^Agency total · sum of the \d+ lines? below$")

    def test_rollup_rows_and_their_groups(self):
        self.open("static", "?view=compare&sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        self.page.mouse.move(0, 0)
        w = lambda sel: self.page.locator(sel).first.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).fontWeight]")
        self.assertEqual(w("tr[data-account=ACC-HHS-NIH-TOTAL] > td.num"), ["rgb(255, 255, 255)", "600"])
        self.assertEqual(w("tr[data-account=ACC-HHS-CDC-TOTAL] > td.num"), ["rgb(255, 255, 255)", "500"])
        self.assertEqual(w("tr.title > td.num"), ["rgb(239, 237, 231)", "700"])
        # every line inside the open total: the rail
        nih = self.members_in_view("ACC-HHS-NIH-TOTAL")
        guides = self.page.eval_on_selector_all("tr[data-member-of=ACC-HHS-NIH-TOTAL] > td.acct", "ts => ts.map(t => getComputedStyle(t).backgroundImage)")
        self.assertEqual(len(guides), len(nih))
        self.assertTrue(all("linear-gradient(rgb(185, 198, 220)" in g for g in guides), set(guides))
        # AHA's group: its relationship notes on the rail
        self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=chevron]").click()
        self.assertIn("linear-gradient(rgb(185, 198, 220)", self.page.locator("tr[data-testid=rel-note] td.acct").first.evaluate(
            "e => getComputedStyle(e).backgroundImage"))

    def test_a_full_year_cr_year_is_labeled_in_every_view(self):
        # from the data (Bill Report Reference funding_type, v37), never hard-coded: FY2025 in both subcommittees
        for sc, grid in (("LHHS", self.lhhs), ("CJS", self.grid)):
            crs = {y: e for y, e in (grid.get("enactments") or {}).items() if e.get("funding_type") == "full_year_cr"}
            self.assertEqual(sorted(crs), ["2025"], sc)
            e = crs["2025"]
            title = (f"Funded by a full-year continuing resolution ({e['bill_id'].replace('P.L.', 'P.L. ')}, "
                     f"enacted {e['enactment_date']})")
            self.assertEqual(title, "Funded by a full-year continuing resolution (P.L. 119-4, enacted 2025-03-15)")
            for q, text in (("stages&fy=2026", "Enacted (CR)"), ("stages&fy=2025", "Enacted (CR)"),
                            ("years&a=2025&b=2026", "Enacted (CR)"), ("history", "FY2025 (CR)")):
                with self.subTest(sc=sc, view=q):
                    self.open("static", f"?view=compare&sc={sc}&grid={q}")
                    labels = self.page.eval_on_selector_all("[data-testid=cr-label]", "es => es.map(e => [e.innerText, e.dataset.cr, e.title])")
                    self.assertEqual(labels, [[text, "2025", title]])
                    # no other header says CR
                    heads = self.page.eval_on_selector_all("#compare-grid thead th", "ts => ts.map(t => t.innerText)")
                    self.assertEqual([h for h in heads if "(CR)" in h], [text])
            # a view without FY2025 Enacted labels nothing
            self.open("static", f"?view=compare&sc={sc}&grid=years&a=2024&b=2026")
            self.assertEqual(self.page.locator("[data-testid=cr-label]").count(), 0)

    def test_change_block_is_set_apart_in_every_view(self):
        # SPEC section 4: the change column after a rule; figures only in green / orange, never a fill
        for q in ("stages", "years", "history"):
            with self.subTest(view=q):
                self.open("static", f"?view=compare&sc=LHHS&grid={q}&fy=2026")
                self.menu("click", "#expand-all")
                self.page.mouse.move(0, 0)
                head = self.page.locator("#compare-grid thead th.grp.delta").inner_text()
                self.assertTrue(head.startswith("Change"), head)
                self.assertEqual(self.page.locator("#compare-grid thead th.delta.first").first.evaluate(
                    "e => getComputedStyle(e).borderLeftWidth + ' ' + getComputedStyle(e).borderLeftColor"), "1px rgb(232, 234, 238)")
                bad = self.page.eval_on_selector_all("#compare-grid tbody tr[data-testid=compare-row]", """trs => trs.filter(tr => {
                    const first = tr.querySelector(':scope > [data-testid=change-cell]');
                    return !first || !first.classList.contains('first') || getComputedStyle(first).borderLeftWidth !== '1px'; }).map(tr => tr.dataset.account)""")
                self.assertEqual(bad, [])
                # no tint on change cells: the row's own fill
                same = self.page.eval_on_selector_all("#compare-grid tbody tr[data-testid=compare-row]", """trs => trs.filter(tr => {
                    const a = tr.querySelector(':scope > td.acct'), d = tr.querySelector(':scope > td.delta');
                    return d && getComputedStyle(a).backgroundColor !== getComputedStyle(d).backgroundColor; }).length""")
                self.assertEqual(same, 0)

    def test_notes_are_corner_marks_in_all(self):
        # the cells with other lines on file, both subcommittees (title totals included): 208, + 6 in the
        # FY2024 House draft's column (the ACF, ACL, CDC, NIH and OS totals and Medicaid: program-level and
        # advance lines beside the headline); + 32 with the FY2022 column and FY2023 Medicaid's views; + 15 with the
        # owner's FY2022 decisions (earmark, Kids First and Diaper Grants lines); + 35 with the FY2021 column
        total = sum(1 for g in (self.lhhs, self.grid) for r in g["rows"] + [t["total"] for t in g["titles"] if t["total"]]
                    for k in r["cells"] if other_lines(r, *k.split("|")))
        self.assertGreater(total, 0)
        for sc in ("LHHS", "CJS"):
            self.open("static", f"?view=compare&sc={sc}&grid=history&rev=1")
            self.menu("click", "#expand-all")
            marks = self.page.locator("#compare-grid [data-testid=more]")
            self.assertGreater(marks.count(), 0)
            self.assertEqual(set(marks.evaluate_all("ms => ms.map(m => m.tagName + ':' + m.className + ':' + m.innerText)")), {"BUTTON:nm:"})
            # no "+N" text anywhere in the grid
            self.assertEqual(self.page.eval_on_selector_all("#compare-grid td", "ts => ts.filter(t => /\\+\\d+$/.test(t.innerText.trim())"
                                                            " && !t.querySelector('[data-testid=change]')).length"), 0)

    def test_the_cell_note_on_liheap_fy2023_enacted(self):
        # a law_text match combined across divisions of the law carries its note (VAL-LHHS-07183, OBS-LHHS-0014)
        note = ("Includes $2,500,000,000 from Division N (Disaster Relief Supplemental Appropriations Act, 2023) "
                "of P.L. 117-328.")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2023&rev=1")
        self.menu("click", "#expand-all")
        notes = self.page.locator("#compare-grid [data-testid=cell-note]")
        # the view's other notes: Refugee FY2022 Enacted (the prior-year column), ASPR's FY2023 cells (funded within
        # PHSSEF), ONC's FY2022 Enacted (the PHS evaluation set-aside) and the FY2023 Enacted figures that follow the
        # law, S.Rept. 118-84's restatement in their corner (owner, 2026-10-09)
        titles = notes.evaluate_all("ns => ns.map(n => n.title)")
        self.assertEqual(sum(1 for x in titles if x.startswith("Includes $2,500,000,000 from Division N")), 1)
        # (and ARPA-H's first enacted year, FY2022: the lifecycle's 'Created FY2022.'; and since D-2, the report-table
        # figure noted on CDC-Wide Activities' FY2023 House and Senate headlines, read from the bill text, decision 4 A)
        self.assertTrue(all(x.startswith(("Includes $", "Funded within PHSSEF", "Funded through the PHS evaluation",
                                          "S.Rept. 118-84 restates FY2023", "Created FY2022",
                                          "H.Rept. 117-403 p.813 prints no budget-authority line",
                                          "the FY2023 Senate explanatory statement prints the account's 'Subtotal'"))
                            for x in titles), titles)
        n = self.cell("ACC-HHS-ACF-LIHEAP", 2023, "Enacted").locator("[data-testid=cell-note]")
        self.assertEqual(n.count(), 1)
        self.assertEqual((n.get_attribute("title"), n.get_attribute("aria-label")), (note, note))

    def test_the_cell_note_on_refugee_fy2022_enacted(self):
        # continuing-resolution funding printed within the FY2022 Enacted figure (H.Rept. 117-403 p.825):
        # the other-law note names the two CRs (VAL-LHHS-08860, OBS-LHHS-2113)
        note = ("Includes $1,600,000,000 ('CR Funding - P.L 117-70') and $2,500,000,000 ('CR Funding - P.L. 117-43 "
                "(emergency)'), continuing-resolution funding printed within this figure (SRC-CRPT-117HRPT403 p.825).")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2022&rev=1")
        self.menu("click", "#expand-all")
        n = self.cell("ACC-HHS-ACF-REFUGEE", 2022, "Enacted").locator("[data-testid=cell-note]")
        self.assertEqual(n.get_attribute("title"), note)

    def test_the_one_cell_note_global_health_fy2024_house(self):
        # the bill text controls over the report table (owner, 2026-10-11): the figure is H.R. 5894's, and the
        # table's figure stays as the cell's note (the FY2024 view's FY2023 Enacted column may carry LIHEAP's note)
        note = ("The House subcommittee draft's explanatory table prints 'Global Health' 370,722 thousand "
                "(SRC-EXPL-LHHS-FY2024-HOUSE p.258), $50,000 less than the bill.")
        for sc in ("CJS", "LHHS"):
            self.open("static", f"?view=compare&sc={sc}&grid=stages&fy=2024&rev=1")
            self.menu("click", "#expand-all")
            notes = self.page.locator("#compare-grid [data-testid=cell-note]")
            titles = notes.evaluate_all("ns => ns.map(n => n.title)")
            self.assertEqual(sum(1 for x in titles if x == note), 1 if sc == "LHHS" else 0, sc)
            self.assertTrue(all(x.startswith(("The House subcommittee draft's", "Includes $2,500,000,000",
                                              "Funded within PHSSEF", "S.Rept. 118-84 restates FY2023",
                                              "The table prints 'Health Surveillance")) for x in titles), titles)
        cell = self.cell("ACC-HHS-CDC-GLOBAL-HEALTH", 2024, "House Reported")
        n = cell.locator("[data-testid=cell-note]")
        self.assertEqual(n.count(), 1)
        self.assertEqual(n.get_attribute("title"), note)
        self.assertEqual(n.get_attribute("aria-label"), note)
        self.assertIn("370,772", cell.inner_text())             # the bill's figure

    def test_account_name_opens_the_single_account_view(self):
        # UI-2: the name opens the account in the side panel (SPEC 5.2); its "Open account page" opens the account page
        # with the account and the fiscal year in the URL (UI-5)
        self.open("live", "?view=compare&sc=CJS&fy=2026&open=ACC-NASA-TOTAL")
        self.page.click("tr[data-account=ACC-NASA-SPACEOPS] [data-testid=row-name]")
        self.assertEqual(self.page.inner_text("[data-testid=acct-name]"), "Space Operations")
        self.page.click("[data-testid=acct-open]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.assertTrue(self.page.is_hidden("[data-testid=compare-view]"))
        self.assertEqual(self.page.inner_text("[data-testid=account-name]"), "Space Operations")
        self.assertTrue(self.page.url.endswith("?account=ACC-NASA-SPACEOPS&fy=2026"), self.page.url)

    def test_outside_tooltip(self):
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2017")
        before = self.cell("ACC-DOJ-CVF", 2017, "Enacted")
        self.assertEqual(before.get_attribute("data-outside"), "true")
        self.assertTrue(before.get_attribute("title").startswith("Nothing on file for this account in FY2017 Enacted"))


    # ---- other lines in a cell, the pinned header, narrow screens ------------------------

    def markers(self):
        return {(m.evaluate("e => e.closest('tr').dataset.account"), int(m.evaluate("e => e.closest('td').dataset.fy")),
                 m.evaluate("e => e.closest('td').dataset.stage")): (m.get_attribute("data-lines"), m.get_attribute("title"))
                for m in self.page.locator("[data-testid=more]").all()}

    def test_other_lines_marker_on_every_cell_that_has_them(self):
        lhhs_pairs = {"stages&fy=2026": [(2025, "Enacted")] + [(2026, st) for st in FOUR],
                      "history": [(y, "Enacted") for y in self.lhhs["fiscal_years"] if y < 2027] + [(2027, "President's Budget"), (2027, "House Reported")],
                      "years&a=2024&b=2026": [(y, st) for y in (2024, 2026) for st in FOUR]}
        cases = [("LHHS", q, p, self.lhhs) for q, p in lhhs_pairs.items()]
        cases.append(("CJS", "stages&fy=2024", [(2023, "Enacted")] + [(2024, st) for st in FOUR], self.grid))
        for sc, q, pairs, grid in cases:
            with self.subTest(sc=sc, view=q):
                self.open("static", f"?view=compare&sc={sc}&grid={q}&rev=1")
                self.menu("click", "#expand-all")
                # the rows the view shows (the lifecycle: Enacted history leaves out the proposed-only accounts)
                rows = shown_rows(grid, pairs, "history" if q == "history" else "stages")
                want = {}
                for r in rows:
                    for y, st in pairs:
                        rest = other_lines(r, y, st)
                        if rest:
                            want[(r["account"]["canonical_account_id"], y, st)] = (
                                str(len(rest)), f"{len(rest)} other line{'' if len(rest) == 1 else 's'} on file, $ thousands:\n"
                                + "\n".join(f"{series_text(l)}: {thousands(l['observations'][0]['amount'])}" for l in rest))
                self.assertGreater(len(want), 0)
                self.assertEqual(self.markers(), want)

    def test_other_lines_examples(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        m = self.cell("ACC-HHS-AHA-TOTAL", 2026, "President's Budget").locator("[data-testid=more]")
        self.assertEqual((m.get_attribute("data-lines"), m.get_attribute("title")),
                         ("1", "1 other line on file, $ thousands:\n"
                               "program level \u2014 the figure above, scoped another way; not added: 579,688"))
        self.assertIn("program level", m.get_attribute("aria-label"))
        # a corner triangle in the cell's top-right, no text; darker on hover
        style = "e => { const s = getComputedStyle(e), c = e.getBoundingClientRect(), td = e.closest('td').getBoundingClientRect(); " \
                "return [e.tagName, e.innerText, s.borderTopWidth, s.borderTopColor, s.borderLeftWidth, s.borderLeftColor, " \
                "Math.round(td.right - c.right - parseFloat(getComputedStyle(e.closest('td')).borderRightWidth)), " \
                "Math.round(c.top - td.top - parseFloat(getComputedStyle(e.closest('td')).borderTopWidth))]; }"
        self.assertEqual(m.evaluate(style), ["BUTTON", "", "9px", "rgb(138, 147, 163)", "9px", "rgba(0, 0, 0, 0)", 0, 0])
        m.hover()
        self.assertEqual(m.evaluate("e => getComputedStyle(e).borderTopColor"), "rgb(31, 78, 140)")
        # the figure's own source tooltip is on the number, not the cell
        cell = self.cell("ACC-HHS-AHA-TOTAL", 2026, "President's Budget")
        self.assertIsNone(cell.get_attribute("title"))
        self.assertTrue(cell.locator("[data-testid=amount]").get_attribute("title"))
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        self.definitions()
        legend = self.page.inner_text("[data-testid=mark-legend]")          # beneath the legend, in Reviewer mode
        self.assertIn("note on this figure: hover to read, click to open the account", legend)
        self.assertNotIn("other lines on file for this cell", legend)
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2024&rev=1")
        self.menu("click", "#expand-all")
        cell = self.cell("ACC-NASA-EXPLORATION", 2024, "Enacted")
        m = cell.locator("[data-testid=more]")
        self.assertEqual((m.get_attribute("data-lines"), m.get_attribute("title")), ("1", "1 other line on file, $ thousands:\nsupplemental: 450,000"))
        # the headline number still links to its source; the marker opens the account at this year and stage
        self.assertTrue(cell.locator("[data-testid=amount]").get_attribute("data-href").startswith("http"))
        m.click()
        self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.assertEqual(self.page.inner_text("[data-testid=account-name]"), "Exploration")
        self.assertTrue(self.page.url.endswith("?account=ACC-NASA-EXPLORATION&fy=2024&stage=Enacted"), self.page.url)
        focus = self.page.locator("#grid td[data-focus=true]")
        self.assertEqual((focus.count(), focus.get_attribute("data-stage")), (1, "Enacted"))
        self.assertEqual(focus.evaluate("e => e.closest('tr').dataset.fy"), "2024")

    def test_header_rows_meet_while_scrolling(self):
        for q in ("stages", "years", "history"):
            self.open("static", f"?view=compare&sc=LHHS&grid={q}")
            self.menu("click", "#expand-all")
            self.page.evaluate("window.scrollTo(0, document.querySelector('#grid-wrap').getBoundingClientRect().top + scrollY + 700)")
            self.page.wait_for_timeout(50)
            m = self.page.evaluate("""() => {
                const r1 = document.querySelector('#compare-grid thead tr th.grp').getBoundingClientRect();
                const th2 = document.querySelector('#compare-grid thead tr + tr th');
                const r2 = th2.getBoundingClientRect();
                // what shows at the seam, just above the second row, in the middle of a column
                const at = document.elementFromPoint(r2.left + r2.width / 2, r2.top - 0.5);
                return {bottom1: r1.bottom, top2: r2.top, seam: at.closest('thead') ? 'thead' : at.tagName};
            }""")
            self.assertAlmostEqual(m["top2"], m["bottom1"], delta=0.5, msg=q)
            self.assertEqual(m["seam"], "thead", q)

    def test_no_page_scroll_at_390px(self):
        # no sideways page scroll on a phone: the phone list (UI-P, SPEC section 6) in every compare mode a link can
        # carry, and the grid "Open the full table" shows (table=1)
        self.page.set_viewport_size({"width": 390, "height": 844})
        for sc in ("LHHS", "CJS"):
            for q in ("stages", "years", "history"):
                for table in ("", "&table=1"):
                    self.page.goto(self.urls["static"] + f"?view=compare&sc={sc}&grid={q}{table}")
                    self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])" if table else "#p-list .prow")
                    self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), (sc, q, table))


class ExploreV1(GridBrowser):
    """reference/ui/explore-v1/SPEC.md section 10, the tests brief UI-1 adds (1, 6, 7, 9, 10 without the selected
    figure, 11, 12, 14), plus the brief's years without an Enacted stage."""

    def go(self, query="", where="static"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.settle()

    def total(self):
        return self.lhhs["titles"][0]["total"]

    def thousands_of(self, row, fy, stage):
        return expected_headline(row, fy, stage)["amount"]

    # 1
    def test_first_screen_defaults(self):
        today = datetime.date.today()
        fy = today.year + (1 if today.month >= 10 else 0)            # the current fiscal year, from today's date
        if fy not in self.lhhs["fiscal_years"]:
            fy = max(y for y in self.lhhs["fiscal_years"] if y <= fy)
        for where in self.urls:
            with self.subTest(where=where):
                self.go("", where)
                self.assertTrue(self.page.is_visible("#compare-view"))
                self.assertTrue(self.page.is_hidden("#account-view"))
                self.assertEqual(self.page.locator("#tab-compare").get_attribute("aria-selected"), "true")
                # the four controls plus Filter, and nothing else in the control row
                shown = self.page.eval_on_selector_all("#controls .xrow > *", "es => es.filter(e => e.offsetParent).map(e => e.id)")
                self.assertEqual(shown, ["search-field", "", "fy-field", "", "filter-btn"])
                for sel in ("#search", "#sc", "#fy-pick", "#view-switch", "#filter-btn"):
                    self.assertTrue(self.page.is_visible(sel), sel)
                self.assertEqual(self.page.eval_on_selector_all("#view-switch button", "bs => bs.map(b => b.innerText)"),
                                 ["Stages", "Two years", "Enacted history"])
                self.assertEqual(self.page.input_value("#sc"), "LHHS")
                self.assertEqual(self.page.input_value("#fy-pick"), str(fy))
                self.assertIn(f"fy={fy}", self.page.url)
                # agencies collapsed; the three section labels
                self.assertEqual(self.page.locator("#compare-grid tr.child").count(), 0)
                self.assertEqual({c.get_attribute("aria-expanded") for c in self.page.locator("[data-testid=chevron]").all()}, {"false"})
                self.assertEqual(self.page.eval_on_selector_all("tr[data-testid=section-label] td.acct", "ts => ts.map(t => t.innerText)"),
                                 ["AGENCIES · 10", "GENERAL PROVISIONS", f"PROPOSED IN THE FY{fy} REQUEST, NOT ENACTED"])
                # no tiles above the grid; the overview in the side panel
                self.assertEqual(self.page.locator("[data-testid=tile], #tiles").count(), 0)
                self.assertTrue(self.page.is_visible("[data-testid=side-panel]"))
                self.assertTrue(self.page.inner_text("[data-testid=overview-title]").startswith(
                    "Title II, Department of Health and Human Services · FY"))
                # grid and panel side by side, tops aligned (UI-2c: each as tall as its content)
                g, p = (self.page.locator(s).bounding_box() for s in ("#grid-wrap", "#side-panel"))
                self.assertLess(g["x"] + g["width"], p["x"])
                self.assertEqual(g["y"], p["y"])
                self.assertEqual(self.page.inner_text("#grid-title"), "Department of Health and Human Services")
                self.assertEqual(self.page.locator("#export").get_attribute("title"), "Coming in UI-4")
                self.assertEqual(self.page.locator("#export").get_attribute("aria-disabled"), "true")

    def test_the_panel_wraps_below_a_narrow_grid(self):
        self.page.set_viewport_size({"width": 900, "height": 900})
        self.go("?sc=LHHS&fy=2026")
        g, p = (self.page.locator(s).bounding_box() for s in ("#grid-wrap", "#side-panel"))
        self.assertGreaterEqual(p["y"], g["y"] + g["height"])        # below, never over the grid
        self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"))

    # 6
    def test_expand_agency_formatting(self):
        self.go("?sc=LHHS&fy=2026")
        self.page.click("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=chevron]")
        nih = self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL]")
        acct = nih.locator("td.acct")
        self.assertIn("rgb(31, 78, 140) 4px 0px 0px 0px inset", acct.evaluate("e => getComputedStyle(e).boxShadow"))
        self.assertEqual(nih.locator("td.num").first.evaluate("e => getComputedStyle(e).borderTopWidth"), "6px")
        self.assertEqual(nih.locator("[data-testid=total]").inner_text(), "Agency total · sum of the 32 lines below")
        # N = the rollup members inside their life in FY2026, the lines actually listed
        members = self.lrow("ACC-HHS-NIH-TOTAL")["rollup_members"]
        inside = [m for m in members if in_view(self.lrow(m), [(2026, st) for st in FOUR])]
        self.assertEqual((len(members), len(inside)), (33, 32))
        listed = self.page.locator("tr[data-member-of=ACC-HHS-NIH-TOTAL]")
        self.assertEqual(listed.count(), 32)
        self.assertEqual(sorted(r.get_attribute("data-account") for r in listed.all()), sorted(inside))
        line = lambda prop: listed.first.locator("td.num").first.evaluate(f"e => getComputedStyle(e).{prop}")
        self.assertEqual((line("fontSize"), line("fontWeight")), ("13px", "400"))
        # 13px regular; a name that opens the account panel takes the link style (navy, owner decision on #59)
        self.assertEqual(listed.first.locator("td.acct a").evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).fontWeight, getComputedStyle(e).color]"),
                         ["13px", "400", "rgb(22, 50, 95)"])
        self.assertIn("rgb(185, 198, 220)", listed.first.locator("td.acct").evaluate("e => getComputedStyle(e).backgroundImage"))
        self.assertEqual(nih.locator("td.num").first.evaluate("e => getComputedStyle(e).fontWeight"), "600")
        # a collapsed agency 500, the total band 700; the Enacted column washed except on the band
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-CDC-TOTAL] td.num").first.evaluate("e => getComputedStyle(e).fontWeight"), "500")
        self.assertEqual(self.page.locator("tr.title td.num").first.evaluate("e => getComputedStyle(e).fontWeight"), "700")
        self.page.mouse.move(0, 0)
        bg = lambda sel: self.page.locator(sel).first.evaluate("e => getComputedStyle(e).backgroundColor")
        self.assertEqual(bg("tr[data-account=ACC-HHS-CDC-TOTAL] td[data-stage=Enacted][data-fy='2026']"), "rgb(247, 245, 239)")
        self.assertEqual(bg("tr.title td[data-stage=Enacted][data-fy='2026']"), "rgb(239, 237, 231)")
        # the proposed institutes under their sub-group label on the rail
        sub = self.page.locator("tr[data-testid=subgroup-label]")
        self.assertEqual(sub.inner_text().strip(), "PROPOSED IN THE FY2026 REQUEST")
        # density: Compact halves the row padding
        pad = lambda: self.page.locator("tr[data-account=ACC-HHS-CDC-TOTAL] td.num").first.evaluate("e => getComputedStyle(e).paddingTop")
        self.assertEqual(pad(), "12px")
        self.go("?sc=LHHS&fy=2026&density=compact")
        self.assertEqual(pad(), "6px")

    # 7
    def test_hover_crosshair(self):
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        bg = lambda loc: loc.evaluate("e => getComputedStyle(e).backgroundColor")
        row = self.page.locator("tr[data-account=ACC-HHS-NIH-NHLBI]")
        target = row.locator("td[data-stage='Senate Reported']")
        col = target.get_attribute("data-col")
        target.hover()
        other_in_row = row.locator("td[data-stage='House Reported']")
        other_in_col = self.page.locator("tr[data-account=ACC-HHS-NIH-NCI] td[data-stage='Senate Reported']")
        head = self.page.locator(f"#compare-grid thead th[data-col='{col}']")
        self.assertEqual(bg(other_in_row), "rgb(236, 241, 249)")
        self.assertEqual(bg(row.locator("td.acct")), "rgb(236, 241, 249)")          # the sticky name cell too
        self.assertEqual(bg(other_in_col), "rgb(241, 245, 251)")
        self.assertEqual(bg(target), "rgb(214, 226, 245)")
        self.assertEqual(head.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).fontWeight, getComputedStyle(e).boxShadow]"),
                         ["rgb(223, 232, 246)", "600", "rgb(31, 78, 140) 0px -2px 0px 0px inset"])
        # a name tints the row only
        row.locator("td.acct").hover()
        self.assertEqual(bg(other_in_row), "rgb(236, 241, 249)")
        self.assertEqual(bg(other_in_col), "rgb(255, 255, 255)")
        self.assertNotEqual(bg(head), "rgb(223, 232, 246)")
        # section labels don't highlight
        self.page.locator("tr[data-testid=section-label]").first.hover()
        self.assertEqual(self.page.locator("tr[data-hov]").count(), 0)
        # leaving the grid clears all three
        target.hover()
        self.page.mouse.move(5, 5)
        self.assertEqual((bg(other_in_row), bg(other_in_col), bg(target)), ("rgb(255, 255, 255)",) * 3)
        self.assertNotEqual(bg(head), "rgb(223, 232, 246)")

    # 9
    def test_filter_chip_and_total_label(self):
        self.go("?sc=LHHS&fy=2026")
        figs = lambda: self.page.eval_on_selector_all("tr.title td[data-testid=compare-cell], tr.title td[data-testid=change-cell]", "ts => ts.map(t => t.innerText)")
        before = figs()
        self.assertEqual(self.page.inner_text("tr.title [data-testid=total]"), "Total, Title II (printed)")
        self.assertTrue(self.page.is_hidden("[data-testid=filter-badge]"))
        self.page.click("#filter-btn")
        self.page.select_option("#f-agency", "National Institutes of Health")
        self.assertEqual([c.inner_text().replace("×", "").strip() for c in self.page.locator("[data-testid=chip]").all()],
                         ["Agency: National Institutes of Health"])
        self.assertEqual(self.page.inner_text("[data-testid=filter-badge]"), "1")
        self.assertEqual(self.page.inner_text("tr.title [data-testid=total]"), "Total, Title II (printed, all accounts)")
        self.assertEqual(figs(), before)                                   # filtering never re-sums
        self.assertEqual(self.page.inner_text("#grid-title"), "National Institutes of Health")
        self.assertEqual(self.page.eval_on_selector_all("tr[data-testid=compare-row]:not(.title)", "ts => ts.map(t => t.dataset.account)"),
                         ["ACC-HHS-NIH-TOTAL"])
        self.assertTrue(self.page.inner_text("[data-testid=overview-title]").startswith("National Institutes of Health · FY2026"))
        nih = self.lrow("ACC-HHS-NIH-TOTAL")
        self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .s"), f"{self.thousands_of(nih, 2026, 'Enacted')} $K · as printed")
        # Status: a second chip, the badge counts both
        self.page.click("#f-status button[data-status=proposed]")
        self.assertEqual(self.page.inner_text("[data-testid=filter-badge]"), "2")
        self.assertEqual(self.page.locator("tr[data-member-of=ACC-HHS-NIH-TOTAL]").count(),
                         len([m for m in nih["rollup_members"] if self.lrow(m)["lifecycle"]["type"] == "proposed_only"
                              and in_view(self.lrow(m), [(2026, st) for st in FOUR])]))
        self.page.keyboard.press("Escape")
        self.page.click("[data-testid=clear-all]")
        self.assertTrue(self.page.is_hidden("[data-testid=chips]"))
        self.assertEqual(self.page.inner_text("tr.title [data-testid=total]"), "Total, Title II (printed)")
        # Advanced shows its ungated items, and (UI-3, flags on) Account type and the basis items; (UI-3b,
        # exclude_earmarks on) Exclude earmarks
        self.page.click("#filter-btn")
        self.page.click("#f-adv")
        text = self.page.inner_text("#filter-panel")
        for word in ("Advanced", "Change of at least (%)", "or at least ($K)", "Include committee drafts",
                     "Show ended and reorganized accounts", "Only figures pending review or flagged",
                     "Account type", "Amount basis", "Advance appropriations count", "Exclude earmarks (CDS / CPF)"):
            self.assertIn(word, text)

    def test_search_keeps_ancestors(self):
        self.go("?sc=LHHS&fy=2026")
        self.page.fill("#search", "cancer")
        self.page.wait_for_function("document.querySelectorAll('tr[data-testid=compare-row]:not(.title)').length === 2")
        self.assertEqual(self.page.eval_on_selector_all("tr[data-testid=compare-row]:not(.title)", "ts => ts.map(t => t.dataset.account)"),
                         ["ACC-HHS-NIH-TOTAL", "ACC-HHS-NIH-NCI"])
        self.page.wait_for_function("location.search.includes('search=cancer')")
        # a former name finds its account; the agency's name finds the agency
        renamed = next(r for r in self.lhhs["rows"] if r["historical_names"] and r["member_of"])
        self.page.fill("#search", renamed["historical_names"][0])
        self.page.wait_for_function(f"!!document.querySelector('tr[data-account={renamed['account']['canonical_account_id']}]')")
        self.page.fill("#search", "Substance Abuse and Mental")
        self.page.wait_for_function("!!document.querySelector('tr[data-account=ACC-HHS-SAMHSA-TOTAL]')")

    # 10 (without the selected figure: UI-2)
    def state(self):
        self.page.mouse.move(2, 2)                       # no hover tint in the snapshot
        return {"url": self.page.url.split("?", 1)[1],
                "controls": self.page.evaluate("""() => ({sc: document.querySelector('#sc').value,
                    fy: [...document.querySelectorAll('#fy-field select')].map(s => s.value),
                    grid: document.querySelector('#view-switch .on').dataset.grid, search: document.querySelector('#search').value,
                    agency: document.querySelector('#f-agency').value,
                    status: document.querySelector('#f-status .on').dataset.status,
                    units: document.querySelector('#units .on').dataset.units,
                    density: document.querySelector('#density .on').dataset.density,
                    menu: [...document.querySelectorAll('#view-menu input, #filter-panel input')].map(i => i.id + ':' + (i.type === 'checkbox' ? i.checked : i.value))})"""),
                "grid": self.page.inner_html("#compare-grid tbody"), "head": self.page.inner_html("#compare-grid thead"),
                "panel": self.page.inner_html("#side-panel")}

    def test_url_round_trip(self):
        self.go("?sc=LHHS")
        steps = []
        self.page.select_option("#fy-pick", "2025"); steps.append(self.state())
        self.page.click("#view-btn")
        self.page.click("#units button[data-units=m]")
        self.page.click("#density button[data-density=compact]")
        self.page.check("#col-req")
        self.page.check("#reviewer")
        self.page.keyboard.press("Escape"); steps.append(self.state())
        self.page.click("#filter-btn")
        self.page.select_option("#f-agency", "Centers for Disease Control and Prevention")
        self.page.click("#f-status button[data-status=enacted]")
        self.page.keyboard.press("Escape")
        self.advanced("check", "#show-ended")
        self.advanced("uncheck", "#f-drafts")
        self.advanced("fill", "#f-min-k", "0")                 # FY2025 is a full-year CR: many changes are 0
        self.page.wait_for_function("location.search.includes('mink=0')")
        self.page.click("tr[data-account=ACC-HHS-CDC-TOTAL] [data-testid=chevron]"); steps.append(self.state())
        self.page.fill("#search", "global")
        self.page.wait_for_function("location.search.includes('search=global')")
        self.page.wait_for_timeout(200)
        # the selected figure (UI-2)
        self.page.click("tr[data-account=ACC-HHS-CDC-TOTAL] td[data-fy='2025'][data-stage='Enacted'] button.fig")
        self.page.wait_for_function("location.search.includes('fig=')")
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
        before = self.state()
        self.page.click("#share")
        self.assertEqual(self.page.inner_text("#share"), "Link copied")
        copied = self.page.get_attribute("#share", "data-copied")
        self.assertEqual(copied, self.page.url)
        self.page.goto(copied)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.assertEqual(self.state(), before)
        for k in ("fy=2025", "units=m", "density=compact", "cols=request", "rev=1", "agency=Centers", "status=enacted",
                  "search=global", "open=ACC-HHS-CDC-TOTAL", "show=proposed%2Cended", "drafts=0", "mink=0",
                  "fig=ACC-HHS-CDC-TOTAL%7C2025%7CEnacted"):
            self.assertIn(k, copied)
        # the two compare views' own controls
        self.go("?sc=CJS&grid=years&a=2023&b=2025&stages=1011&basis=house&show=proposed,ended")
        b2 = self.state()
        self.page.reload(); self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.assertEqual(self.state(), b2)
        # the open account (UI-2)
        self.go("?sc=LHHS&fy=2026&acct=ACC-HHS-CDC-TOTAL")
        self.assertEqual(self.page.inner_text("[data-testid=acct-name]"), "Centers for Disease Control and Prevention")
        self.assertEqual(self.page.get_attribute("tr[data-account=ACC-HHS-CDC-TOTAL]", "data-sel"), "row")
        # Back and Forward step through the changes
        self.go("?sc=LHHS&fy=2026")
        first = self.state()
        self.page.select_option("#fy-pick", "2024")
        self.page.click("tr[data-account=ACC-HHS-CDC-TOTAL] [data-testid=chevron]")
        second = self.state()
        self.page.go_back(); self.page.wait_for_function("!location.search.includes('open=')")
        self.page.go_back(); self.page.wait_for_function("location.search.includes('fy=2026')")
        self.assertEqual(self.state(), first)
        self.page.go_forward(); self.page.go_forward()
        self.page.wait_for_function("location.search.includes('open=')")
        self.assertEqual(self.state(), second)

    # ---- UI-2: click-to-source and the account panel (SPEC 5.1-5.3) -------------------------------------------

    def select_nci(self, where="static"):
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL", where)
        cell = self.cell("ACC-HHS-NIH-NCI", 2026, "Enacted")
        cell.locator("button.fig").click()
        return cell

    # 2
    def test_figure_opens_source(self):
        for where in self.urls:
            with self.subTest(where=where):
                cell = self.select_nci(where)
                o = expected_headline(self.lrow("ACC-HHS-NIH-NCI"), 2026, "Enacted")
                obs = next(l for l in self.lrow("ACC-HHS-NIH-NCI")["cells"]["2026|Enacted"]["lines"]
                           if l["amount_type"] == "budget authority" and not l["component"])["observations"][0]
                panel = self.page.locator("#side-panel")
                self.assertEqual((panel.get_attribute("data-mode"), panel.get_attribute("aria-label")), ("source", "Source for this figure"))
                text = lambda t: self.page.inner_text(f"[data-testid={t}]")
                self.assertEqual(text("src-account"), "National Cancer Institute")
                self.assertEqual(text("src-stage"), "FY2026 enacted")
                # in full dollars, then the form the document prints it in (owner, 2026-10-13, UI-2b)
                self.assertEqual(o["amount"], "7,352,159")
                self.assertEqual(text("src-value"), "$7,352,159,000")
                self.assertEqual(text("src-short"), "printed as 7,352,159 in $ thousands")
                self.assertEqual(self.page.get_attribute("[data-testid=src-badge]", "data-status"), obs["verification_status"])
                self.assertEqual(text("src-badge"), "Auto-validated")
                # one "Source document" block: the short name (plain text), its page, "Printed as", one button to the page
                self.assertEqual(self.page.get_attribute("[data-testid=src-doc]", "data-doc"), "SRC-EXPL-LHHS-FY2026-ENACTED")
                self.assertEqual(text("src-doc"), "Explanatory statement, Cong. Rec. Jan. 22, 2026")
                self.assertEqual(self.page.locator("[data-testid=src-doc] a").count(), 0)
                self.assertIn("SOURCE DOCUMENT", text("src-docblock").upper())
                self.assertEqual(text("src-page"), "Page 285")
                self.assertEqual(text("src-printed"), obs["source_table_or_section"])
                self.assertIn("Printed as", panel.inner_text())
                bill = self.page.locator("[data-testid=src-bill]")
                self.assertEqual(bill.inner_text(), "Bill / law: P.L. 119-75, Div. B")
                self.assertEqual(bill.get_attribute("href"), obs["bill_url"])
                self.assertEqual(text("src-record"), f"Record {obs['observation_id']}")
                btn = self.page.locator("[data-testid=src-open]")
                self.assertEqual(btn.inner_text(), "Open source document at page 285")
                self.assertEqual(btn.get_attribute("href"), obs["url_or_identifier"] + "#page=285")
                self.assertEqual(text("src-account-btn"), "See every stage for this account")
                self.assertEqual(text("panel-back"), "‹ Title II overview")
                self.assertEqual(self.page.locator("[data-testid=panel-hint]").count(), 0)     # the hint is gone (UI-2b)
                # the cell is marked selected: yellow fill, 2px #d9a400 inset outline
                self.assertEqual(cell.get_attribute("data-sel"), "fig")
                self.page.mouse.move(2, 2)
                self.assertEqual(cell.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).boxShadow]"),
                                 ["rgb(255, 241, 194)", "rgb(217, 164, 0) 0px 0px 0px 2px inset"])
                self.assertIn("fig=ACC-HHS-NIH-NCI%7C2026%7CEnacted", self.page.url)
                # another figure moves the selection
                self.cell("ACC-HHS-NIH-NCI", 2026, "Senate Reported").locator("button.fig").click()
                self.assertEqual(self.page.locator("#compare-grid [data-sel]").count(), 1)
                self.assertEqual(self.cell("ACC-HHS-NIH-NCI", 2026, "Senate Reported").get_attribute("data-sel"), "fig")
                self.assertEqual(text("src-stage"), "FY2026 Senate")
                # a figure whose data has no bill_url omits the line
                self.page.evaluate("""() => { const o = headOf(ROW_OF.get('ACC-HHS-NIH-NCI'), 2026, 'Senate Reported').h.observations[0];
                                             delete o.bill_url; renderPanel(current.grid); }""")
                self.assertEqual(self.page.locator("[data-testid=src-bill]").count(), 0)
                self.assertNotIn("Bill / law", panel.inner_text())
                # "See every stage for this account" opens the account
                self.page.click("[data-testid=src-account-btn]")
                self.assertEqual(panel.get_attribute("data-mode"), "account")

    # ---- UI-2b: grid and panel polish (owner review, 2026-10-13) ---------------------------------------------

    def test_note_marks_only_in_reviewer_mode(self):
        # no corner triangles (notes, other lines) and no key for them in the default view; both in Reviewer mode
        marks = "#compare-grid [data-testid=cell-note], #compare-grid [data-testid=more]"
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        self.assertEqual(self.page.locator(marks).count(), 0)
        self.definitions()
        self.assertTrue(self.page.is_hidden("[data-testid=mark-legend]"))
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL&rev=1")
        self.assertGreater(self.page.locator("#compare-grid [data-testid=cell-note]").count(), 0)
        self.assertGreater(self.page.locator("#compare-grid [data-testid=more]").count(), 0)
        self.definitions()
        self.assertTrue(self.page.is_visible("[data-testid=mark-legend]"))
        # turning Reviewer mode off in the View menu takes them away again
        self.menu("uncheck", "[data-testid=reviewer]")
        self.assertEqual(self.page.locator(marks).count(), 0)

    def test_figure_notes_in_its_panel(self):
        # AHA's FY2026 request: its cell note and its other line show under "Notes" in the source panel
        self.go("?sc=LHHS&fy=2026&fig=ACC-HHS-AHA-TOTAL|2026|President's%20Budget")
        notes = self.page.locator("[data-testid=src-notes]")
        self.assertTrue(notes.is_visible())
        self.assertIn("NOTES", notes.inner_text().upper())
        items = self.page.eval_on_selector_all("[data-testid=src-note]", "es => es.map(e => e.innerText)")
        self.assertEqual(items[0], "Proposed in the FY2026 request; never enacted.")
        self.assertTrue(items[1].startswith("1 other line on file, $ thousands (listed, never added): "
                                            "program level \u2014 the figure above, scoped another way; not added: 579,688."), items[1])
        # a figure with no note has no Notes block
        self.go("?sc=LHHS&fy=2026&fig=ACC-HHS-NIH-NCI|2026|Enacted")
        self.assertEqual(self.page.locator("[data-testid=src-notes]").count(), 0)
        # a lifecycle note naming another account links to it (ASPR, created FY2024 from PHSSEF)
        self.go("?sc=LHHS&fy=2024&fig=ACC-HHS-ASPR-TOTAL|2024|Enacted")
        self.assertIn("Created FY2024", self.page.inner_text("[data-testid=src-notes]"))
        self.assertEqual(self.page.get_attribute("[data-testid=src-note-link]", "data-link"), "ACC-HHS-OS-PHSSEF")

    def test_source_panel_full_dollars_and_one_source_link(self):
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        self.cell("ACC-HHS-NIH-NCI", 2026, "Enacted").locator("button.fig").click()
        obs = next(l for l in self.lrow("ACC-HHS-NIH-NCI")["cells"]["2026|Enacted"]["lines"]
                   if l["amount_type"] == "budget authority" and not l["component"])["observations"][0]
        self.assertEqual(self.page.inner_text("[data-testid=src-value]"), "$7,352,159,000")
        self.assertEqual(self.page.inner_text("[data-testid=src-short]"), "printed as 7,352,159 in $ thousands")
        hrefs = self.page.eval_on_selector_all("#side-panel a[href]", "as => as.map(a => a.getAttribute('href'))")
        to_doc = [h for h in hrefs if h.split("#")[0] == obs["url_or_identifier"]]
        self.assertEqual(len(to_doc), 1, hrefs)
        self.assertTrue(to_doc[0].endswith("#page=285"))
        # the bill or law is a separate, secondary link
        bill = self.page.locator("[data-testid=src-bill]")
        self.assertEqual(bill.inner_text(), "Bill / law: P.L. 119-75, Div. B")
        self.assertEqual(self.page.locator("[data-testid=src-docblock] [data-testid=src-bill]").count(), 0)
        self.assertNotIn("or click outside", self.page.inner_text("#side-panel"))
        # Esc and a click outside still return the overview
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "overview")

    def test_account_table_one_source_link_per_stage(self):
        self.go("?sc=LHHS&fy=2026&acct=ACC-HHS-NIH-NCI")
        rows = self.page.locator("[data-testid=acct-table] tbody tr[data-testid=acct-stage]")
        self.assertEqual(rows.count(), 5)
        for r in rows.all():
            self.assertEqual(r.locator("td").count(), 2)                                  # Stage (th) | Amount | Source
            self.assertEqual(r.locator("a").count(), 1)
            self.assertIn("#page=", r.locator("a").get_attribute("href"))
        self.assertEqual(self.page.locator("[data-testid=acct-bills] a").count(), 4)
        self.assertNotIn("Budget Appendix", self.page.inner_text("[data-testid=acct-bills]"))
        self.assertEqual(self.page.locator("[data-testid=panel-hint]").count(), 0)          # no hint in the account panel
        self.assertNotIn("or click outside", self.page.inner_text("#side-panel"))
        # a request figure's secondary link is the budget document
        self.page.locator("[data-testid=acct-stage][data-stage=\"President's Budget\"] [data-testid=acct-stage-fig]").click()
        self.assertEqual(self.page.inner_text("[data-testid=src-bill]"), "Budget document: FY2026 Budget Appendix")

    # 3
    def test_every_value_cell_is_clickable(self):
        for query in ("", "?sc=LHHS&fy=2026", "?sc=CJS&fy=2026"):
            with self.subTest(query=query):
                self.go(query)
                cells = self.page.evaluate("""() => [...document.querySelectorAll('#compare-grid td[data-testid=compare-cell]')].map(td => {
                    const tr = td.closest('tr'), r = ROW_OF.get(tr.dataset.account), b = td.querySelector(':scope > button.fig');
                    const t = current.grid.titles.find(t => t.total && t.total.account.canonical_account_id === tr.dataset.account);
                    return {state: td.dataset.state, fy: Number(td.dataset.fy), stage: td.dataset.stage, outside: td.dataset.outsideLife || '',
                            name: t ? 'Total, ' + t.title : plainName(r.account.canonical_name),
                            label: b && b.getAttribute('aria-label'), tag: b && b.tagName, underline: b && getComputedStyle(b).textDecorationStyle,
                            title: b && b.title}; })""")
                values = [c for c in cells if c["state"] == "value"]
                self.assertGreater(len(values), 10)
                cr = self.page.evaluate("() => Object.entries(current.grid.enactments || {}).filter(([y, e]) => e.funding_type === 'full_year_cr').map(([y]) => Number(y))")
                word = {"President's Budget": "President’s request", "House Reported": "House", "Senate Reported": "Senate"}
                for c in values:
                    stage = (f"FY{c['fy']} enacted" + (" (full-year CR)" if c["fy"] in cr else "")) if c["stage"] == "Enacted" \
                        else f"FY{c['fy']} {word[c['stage']]}"
                    self.assertEqual((c["tag"], c["label"]), ("BUTTON", f"Source for {c['name']}, {stage}"), c)
                    self.assertEqual(c["underline"], "dotted")
                    self.assertRegex(c["title"], r"^Click for its source: .+, p\. ")
                # outside-life cells are not buttons
                self.assertTrue(all(c["tag"] is None for c in cells if c["outside"]))
                # every button is reachable: a real <button>, never tabindex -1
                self.assertEqual(self.page.locator("#compare-grid button.fig[tabindex='-1']").count(), 0)

    # 4
    def test_click_away_clears(self):
        cell = self.select_nci()
        panel = self.page.locator("#side-panel")
        # clicks inside the grid and the panel keep it
        self.page.locator("tr[data-testid=section-label]").first.click()
        self.page.locator("tr[data-account=ACC-HHS-NIH-NHLBI] td.acct").click(position={"x": 200, "y": 5})
        self.page.click("[data-testid=src-printed]")
        self.assertEqual((panel.get_attribute("data-mode"), cell.get_attribute("data-sel")), ("source", "fig"))
        # controls do their own job and don't clear
        self.page.click("#search")
        self.assertEqual(panel.get_attribute("data-mode"), "source")
        # a click that closes a menu only closes the menu
        self.page.click("#view-btn")
        self.page.click("#grid-title")
        self.assertTrue(self.page.is_hidden("#view-menu"))
        self.assertEqual(panel.get_attribute("data-mode"), "source")
        # the title area, then the page background: the overview returns
        self.page.click("#grid-title")
        self.assertEqual((panel.get_attribute("data-mode"), cell.get_attribute("data-sel")), ("overview", None))
        self.assertNotIn("fig=", self.page.url)
        self.assertTrue(self.page.inner_text("[data-testid=overview-title]").startswith("Title II, Department of Health and Human Services"))
        cell.locator("button.fig").click()
        self.page.mouse.click(1430, 170)                                       # right of the title row: page background
        self.assertEqual(panel.get_attribute("data-mode"), "overview")
        # the back link
        cell.locator("button.fig").click()
        self.page.click("[data-testid=panel-back]")
        self.assertEqual(panel.get_attribute("data-mode"), "overview")
        self.assertEqual(self.page.evaluate("document.activeElement.getAttribute('aria-label')"),
                         "Source for National Cancer Institute, FY2026 enacted")
        # with an agency filter the back link names the agency
        self.go("?sc=LHHS&fy=2026&agency=National+Institutes+of+Health&fig=ACC-HHS-NIH-NCI%7C2026%7CEnacted")
        self.assertEqual(self.page.inner_text("[data-testid=panel-back]"), "‹ NIH overview")

    # 5
    def test_escape_closes_then_clears(self):
        cell = self.select_nci()
        panel = self.page.locator("#side-panel")
        self.page.click("#filter-btn")
        self.assertTrue(self.page.is_visible("#filter-panel"))
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.is_hidden("#filter-panel"))
        self.assertEqual((panel.get_attribute("data-mode"), cell.get_attribute("data-sel")), ("source", "fig"))
        self.page.keyboard.press("Escape")
        self.assertEqual((panel.get_attribute("data-mode"), cell.get_attribute("data-sel")), ("overview", None))
        self.assertNotIn("fig=", self.page.url)
        # from the keyboard: Enter on a figure selects it, Esc clears it and focus returns to the figure
        b = self.cell("ACC-HHS-NIH-NHLBI", 2026, "House Reported").locator("button.fig")
        b.focus()
        self.page.keyboard.press("Enter")
        self.assertEqual(panel.get_attribute("data-mode"), "source")
        self.page.keyboard.press("Escape")
        self.assertEqual(panel.get_attribute("data-mode"), "overview")
        self.assertEqual(self.page.evaluate("document.activeElement.getAttribute('aria-label')"),
                         "Source for National Heart, Lung, and Blood Institute, FY2026 House")
        # and for an open account
        self.page.click("tr[data-account=ACC-HHS-NIH-NCI] [data-testid=row-name]")
        self.assertEqual(panel.get_attribute("data-mode"), "account")
        self.page.keyboard.press("Escape")
        self.assertEqual(panel.get_attribute("data-mode"), "overview")

    # 8
    def test_selection_beats_hover(self):
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL")
        bg = lambda loc: loc.evaluate("e => getComputedStyle(e).backgroundColor")
        target = self.cell("ACC-HHS-NIH-NHLBI", 2026, "Senate Reported")
        target.locator("button.fig").click()
        target.hover()
        self.assertEqual(self.page.locator("tr[data-hov]").count(), 1)                 # the hover is on
        self.assertEqual(bg(target), "rgb(255, 241, 194)")
        self.assertEqual(bg(self.cell("ACC-HHS-NIH-NHLBI", 2026, "House Reported")), "rgb(236, 241, 249)")
        # hovering its column elsewhere keeps it yellow too
        self.cell("ACC-HHS-NIH-NCI", 2026, "Senate Reported").hover()
        self.assertEqual(bg(target), "rgb(255, 241, 194)")
        # the selected account row: light yellow, over the hover and the enacted wash
        self.page.click("tr[data-account=ACC-HHS-NIH-NCI] [data-testid=row-name]")
        row = self.page.locator("tr[data-account=ACC-HHS-NIH-NCI]")
        self.cell("ACC-HHS-NIH-NCI", 2026, "Enacted").hover()
        self.assertEqual({bg(td) for td in row.locator("td").all()}, {"rgb(255, 246, 220)"})

    # the brief's 3: the account panel (SPEC 5.2)
    def test_account_panel(self):
        for where in self.urls:
            with self.subTest(where=where):
                self.go("?sc=LHHS&fy=2026&open=ACC-HHS-NIH-TOTAL,ACC-HHS-CMS-TOTAL", where)
                self.page.click("tr[data-account=ACC-HHS-NIH-NCI] [data-testid=row-name]")
                self.assertIn("acct=ACC-HHS-NIH-NCI", self.page.url)
                self.assertEqual(self.page.get_attribute("tr[data-account=ACC-HHS-NIH-NCI]", "data-sel"), "row")
                text = lambda t: self.page.inner_text(f"[data-testid={t}]")
                # every name that opens the panel has the link style, selected or not: navy, light underline 3px below
                link = "e => [getComputedStyle(e).color, getComputedStyle(e).textDecorationLine, getComputedStyle(e).textDecorationColor, getComputedStyle(e).textUnderlineOffset]"
                self.page.mouse.move(2, 2)
                for aid in ("ACC-HHS-NIH-NCI", "ACC-HHS-NIH-NHLBI", "ACC-HHS-CDC-TOTAL"):
                    self.assertEqual(self.page.locator(f"tr[data-account={aid}] [data-testid=row-name]").evaluate(link),
                                     ["rgb(22, 50, 95)", "underline", "rgb(159, 180, 214)", "3px"], aid)
                self.assertEqual(text("acct-path"), "Title II › National Institutes of Health")
                self.assertEqual(text("acct-name"), "National Cancer Institute")
                self.assertEqual(text("acct-headline"), "$7.35B")
                self.assertEqual(text("acct-exact"), "FY2026 enacted, as printed")
                self.assertEqual(text("acct-change"), "+$128.0M (+1.8%) vs FY2025 enacted (full-year CR)")
                # every stage in a table: Stage | Amount ($K) | Source, the source one link to its page (UI-2b)
                row = self.lrow("ACC-HHS-NIH-NCI")
                stages = self.page.locator("[data-testid=acct-stage]")
                self.assertEqual(stages.count(), 5)
                for k, (fy, st) in enumerate(self.DEFAULT):
                    line = stages.nth(k)
                    self.assertEqual((line.get_attribute("data-fy"), line.get_attribute("data-stage")), (str(fy), st))
                    self.assertEqual(line.locator("[data-testid=acct-stage-fig]").inner_text(), expected_headline(row, fy, st)["amount"])
                    obs = next(l for l in row["cells"][f"{fy}|{st}"]["lines"] if l["amount_type"] == "budget authority"
                               and not l["component"])["observations"][0]
                    src = line.locator("[data-testid=acct-stage-src]")
                    self.assertIn(f"p.\u00a0{obs['source_page']}", src.inner_text())
                    self.assertEqual(src.locator("a").count(), 1)
                    self.assertEqual(src.locator("a").get_attribute("href"),
                                     obs["url_or_identifier"] + "#page=" + str(obs["source_page"]).split("-")[0])
                self.assertEqual(stages.nth(0).locator(".k").inner_text(), "FY2025 enacted (full-year CR)")
                self.assertEqual(self.page.eval_on_selector_all("[data-testid=acct-table] thead th", "ts => ts.map(t => t.innerText)"),
                                 ["Stage", "Amount ($K)", "Source"])
                # the bill and law text once, on one line under the table
                self.assertEqual(text("acct-bills"), "Bill text: FY2025: P.L. 119-4 (full-year CR) · H.R. 5304 · S. 2587 · P.L. 119-75")
                # a stage's figure opens its source
                stages.nth(2).locator("[data-testid=acct-stage-fig]").click()
                self.assertEqual(self.page.inner_text("[data-testid=src-stage]"), "FY2026 House")
                self.page.click("[data-testid=src-account-btn]")
                # enacted history: one bar per year; the CR year lighter; the bars are the enacted figures held
                bars = self.page.locator("[data-testid=acct-bar]")
                self.assertEqual([b.get_attribute("data-fy") for b in bars.all()], [str(y) for y in YEARS])
                for b in bars.all():
                    y = int(b.get_attribute("data-fy"))
                    want = expected_headline(row, y, "Enacted")
                    self.assertEqual(b.get_attribute("data-value") != "", want["state"] == "value", y)
                self.assertEqual(self.page.locator("[data-testid=acct-bar][data-fy='2025'] .bar.cr").count(), 1)
                self.assertEqual(self.page.locator("[data-testid=acct-bar] .bar.cr").count(), 1)
                self.assertIn("Enacted, FY2017–FY2026", self.page.inner_text("#side-panel").replace("ENACTED", "Enacted"))
                # Open account page: the account page (UI-5); Watch hidden until UI-6
                self.assertEqual(text("acct-open"), "Open account page")
                self.assertNotIn("Watch", self.page.inner_text("#side-panel"))
                self.page.click("[data-testid=acct-open]")
                self.page.wait_for_selector("[data-testid=result]:not([hidden])")
                self.assertEqual(self.page.inner_text("[data-testid=account-name]"), "National Cancer Institute")
        # gaps for non-values, never zero bars: ARPA-H before its first year
        self.go("?sc=LHHS&fy=2026&acct=ACC-HHS-NIH-ARPA-H")
        gaps = self.page.locator("[data-testid=acct-bar][data-value='']")
        self.assertGreater(gaps.count(), 0)
        self.assertEqual(self.page.locator("[data-testid=acct-bar][data-value=''] .bar").count(), 0)

    # 11
    def test_blank_vs_zero(self):
        # SPEC section 1 rule 2: a printed zero is a figure and counts in dollar changes; a % from a zero base is
        # "new" ("—" when both are zero); a confirmed absence and an outside-life cell never feed a change or %
        ahrq = self.lrow("ACC-HHS-AHRQ-TOTAL")
        cell = ahrq["cells"]["2027|House Reported"]["lines"][0]
        self.assertEqual((cell["state"], cell["observations"][0]["amount"], cell["absence"]), ("not_funded", 0, None))
        self.go("?sc=LHHS&fy=2027&cols=request")
        td = self.cell("ACC-HHS-AHRQ-TOTAL", 2027, "House Reported")
        z = td.locator("button[data-testid=token-not-funded]")
        self.assertEqual(z.inner_text(), "—")
        self.assertEqual(z.get_attribute("aria-label"), "Source for Agency for Healthcare Research and Quality, FY2027 House")
        self.assertEqual(z.get_attribute("data-href"), cell["observations"][0]["url_or_identifier"] + "#page="
                         + str(cell["observations"][0]["source_page"]).split("-")[0])
        self.assertIn("Not funded: printed zero", z.get_attribute("title"))
        z.click()                                                              # it opens its source (UI-2)
        self.assertEqual((self.page.inner_text("[data-testid=src-value]"), self.page.inner_text("[data-testid=src-short]")),
                         ("$0", "Not funded: printed zero · printed as 0 in $ thousands"))
        self.assertEqual(td.get_attribute("data-sel"), "fig")
        self.page.keyboard.press("Escape")                                     # back to the overview
        # FY2027 AHRQ House (0) vs FY2026 enacted (345,380); Request (0) vs FY2026 enacted
        changes = self.page.locator("tr[data-account=ACC-HHS-AHRQ-TOTAL] [data-testid=change-cell]")
        self.assertEqual([c.inner_text().split("\n") for c in changes.all()], [["\u2212345,380", "\u2212100.0%"]] * 2)
        self.assertEqual(changes.first.get_attribute("data-delta"), "-345380000")
        # in the agency's own scope its lines' movers use the same figures
        self.page.click("#filter-btn"); self.page.select_option("#f-agency", "Agency for Healthcare Research and Quality")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.inner_text("[data-testid=ov-vs-prior] .s"), "\u2212345,380 $K · \u2212100.0%")
        # FY2026: AHRQ's request is a printed zero. Request vs FY2025 enacted = −369,000 (−100.0%), from the page's own
        # figure() and GridMath; Enacted vs a zero request shows "new"
        self.go("?sc=LHHS&fy=2026&cols=request")
        self.assertEqual(self.page.evaluate("""() => { const r = ROW_OF.get('ACC-HHS-AHRQ-TOTAL');
            const a = figure(r, 2025, 'Enacted'), b = figure(r, 2026, "President's Budget");
            return [GridMath.signedNumber(b - a, 'k'), GridMath.pctText(a, b)]; }"""), ["\u2212369,000", "\u2212100.0%"])
        self.assertEqual([c.inner_text().split("\n") for c in self.page.locator("tr[data-account=ACC-HHS-AHRQ-TOTAL] [data-testid=change-cell]").all()],
                         [["\u221223,620", "\u22126.4%"], ["+345,380", "new"]])
        # a printed-zero base: CCPF FY2021 enacted printed 0, FY2022 enacted 5,000 -> +5,000, "new"
        ccpf = self.lrow("ACC-HHS-HRSA-CCPF")
        self.assertEqual(ccpf["cells"]["2021|Enacted"]["lines"][0]["observations"][0]["amount"], 0)
        self.go("?sc=LHHS&fy=2022&open=ACC-HHS-HRSA-TOTAL")
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-HRSA-CCPF] [data-testid=change-cell]").inner_text().split("\n"),
                         ["+5,000", "new"])
        # a confirmed absence never counts: CCPF FY2020 enacted is confirmed absent, FY2021's printed zero has no change
        absent = ccpf["cells"]["2020|Enacted"]["lines"][0]
        self.assertEqual((absent["state"], bool(absent["absence"]), absent["observations"]), ("not_funded", True, []))
        self.go("?sc=LHHS&fy=2021&open=ACC-HHS-HRSA-TOTAL")
        prior = self.cell("ACC-HHS-HRSA-CCPF", 2020, "Enacted").locator("[data-testid=token-not-funded]")
        self.assertEqual(prior.evaluate("e => e.tagName"), "SPAN")                         # not a button: no figure printed
        self.assertTrue(prior.get_attribute("title").startswith("Not funded: confirmed absent in the bill or law text"))
        ch = self.page.locator("tr[data-account=ACC-HHS-HRSA-CCPF] [data-testid=change-cell]")
        self.assertEqual((ch.inner_text(), ch.get_attribute("data-delta")), ("", None))
        # GridMath: both zero shows "—"
        self.assertEqual(self.page.evaluate("[GridMath.pctText(0, 0), GridMath.pctText(0, 5), GridMath.pctText(null, 5)]"), ["—", "new", ""])
        # an outside-life cell: blank, not clickable, no change
        self.go("?sc=LHHS&fy=2026")
        aha = self.cell("ACC-HHS-AHA-TOTAL", 2026, "Enacted")
        self.assertEqual((aha.inner_text(), aha.get_attribute("data-outside-life"), aha.locator("a, button").count()), ("", "outside", 0))
        self.assertEqual(aha.get_attribute("title"), "Outside the account's life: proposed only, never enacted (FY2026 Enacted).")
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=change-cell]").first.inner_text(), "")
        # every value cell is a button to its source, named for its account and stage
        for a in self.page.locator("tr[data-account=ACC-HHS-CDC-TOTAL] td[data-state=value] [data-testid=amount]").all():
            self.assertTrue(a.get_attribute("data-href").startswith("http"))
            self.assertRegex(a.get_attribute("aria-label"), r"^Source for Centers for Disease Control and Prevention, FY20\d\d ")
            self.assertTrue(a.get_attribute("title").startswith("Click for its source: "))
        # the state tokens (SPEC section 4)
        self.go("?sc=LHHS&fy=2025&open=ACC-HHS-CDC-TOTAL")
        m = self.cell("ACC-HHS-CDC-PROGRAM-SUPPORT", 2025, "House Reported").locator("[data-testid=token-missing]")
        self.assertEqual((m.inner_text(), m.get_attribute("title")), ("missing", "Document on file; figure not yet entered"))
        self.assertEqual(m.evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).fontStyle, getComputedStyle(e).color]"),
                         ["12px", "italic", "rgb(91, 99, 114)"])

    # 12
    def test_feature_flags_hide_unready(self):
        # with accounting_views / budget_class / defense_split forced false (a fixture of index.json and
        # /api/features), none of their controls exist; with the flags as published (on since UI-0), UI-3's do
        def off(route):
            body = json.loads(route.fetch().text())
            for flag in ("accounting_views", "budget_class", "defense_split", "exclude_earmarks", "nondefense"):
                body["features"][flag] = False
            route.fulfill(json=body)
        words = ("amount basis", "account type", "advance appropriations count", "defense", "incl. mandatory",
                 "appropriated mandatory", "earmark", "nondefense")
        for where, pattern in (("static", "**/data/index.json"), ("live", "**/api/features")):
            for flagged_off in (True, False):
                if flagged_off:
                    self.page.route(pattern, off)
                self.go("?sc=LHHS&fy=2026", where)
                self.page.click("#filter-btn")
                self.page.click("#f-adv")
                html = self.page.inner_text("body").lower()
                self.page.keyboard.press("Escape")
                self.page.click("#view-btn")
                html += self.page.inner_text("body").lower()
                for word in words:
                    if flagged_off:
                        self.assertNotIn(word, html, (where, word))
                    elif word in ("amount basis", "account type", "advance appropriations count", "defense", "incl. mandatory",
                                  "earmark", "nondefense"):
                        self.assertIn(word, html, (where, word))
                if flagged_off:
                    self.page.unroute(pattern)
        # the page reads the flags in both modes: a flag turned off hides its feature
        def off2(route):
            body = json.loads(route.fetch().text())
            body["features"]["search_filter"] = False
            body["features"]["overview_panel"] = False
            route.fulfill(json=body)
        for where, pattern in (("static", "**/data/index.json"), ("live", "**/api/features")):
            self.page.route(pattern, off2)
            self.go("?sc=LHHS&fy=2026", where)
            self.assertTrue(self.page.is_hidden("#search") and self.page.is_hidden("#filter-btn"), where)
            self.assertTrue(self.page.is_hidden("[data-testid=side-panel]"), where)
            self.page.unroute(pattern)

    # 14
    def test_keyboard_and_focus(self):
        self.go("?sc=LHHS&fy=2026")
        self.page.locator("body").focus()
        seen = []
        for _ in range(16):
            self.page.keyboard.press("Tab")
            seen.append(self.page.evaluate("""() => { const e = document.activeElement;
                return [e.id || e.dataset.grid || e.dataset.testid || e.tagName, getComputedStyle(e).outlineStyle]; }"""))
        ids = [s[0] for s in seen]
        for want in ("tab-compare", "tab-account", "search", "sc", "fy-pick", "stages", "years", "history", "filter-btn",
                     "view-btn", "share", "export"):
            self.assertIn(want, ids)
        self.assertEqual([s for s in seen if s[1] != "solid"], [])                # a visible focus ring on each
        # into the grid: the chevrons and names are reachable
        for _ in range(4):
            self.page.keyboard.press("Tab")
        self.assertTrue(self.page.evaluate("document.activeElement.closest('#compare-grid') !== null"))
        # each dialog closes on Esc and returns focus to its button
        for button, panel in (("#filter-btn", "#filter-panel"), ("#view-btn", "#view-menu")):
            self.page.focus(button)
            self.page.keyboard.press("Enter")
            self.assertTrue(self.page.is_visible(panel))
            self.assertTrue(self.page.evaluate(f"document.querySelector('{panel}').contains(document.activeElement)"))
            self.assertEqual(self.page.get_attribute(button, "aria-expanded"), "true")
            self.page.keyboard.press("Escape")
            self.assertTrue(self.page.is_hidden(panel))
            self.assertEqual(self.page.evaluate("'#' + document.activeElement.id"), button)
        # a click outside closes the menu too
        self.page.click("#view-btn")
        self.page.mouse.click(700, 1000 if self.page.viewport_size["height"] > 1000 else 890)
        self.assertTrue(self.page.is_hidden("#view-menu"))

    # the brief's 15: a year with no Enacted stage
    # 15
    def test_default_year_and_lead_stage(self):
        t = self.total()
        self.assertEqual(expected_headline(t, 2027, "Enacted")["state"], "not_enacted")
        self.assertEqual(expected_headline(t, 2027, "Senate Reported")["state"], "not_collected")
        self.assertEqual(expected_headline(t, 2027, "House Reported")["amount"], "1,606,033,805")
        self.assertEqual(expected_headline(t, 2026, "Enacted")["amount"], "1,553,262,357")
        self.go("?sc=LHHS")                                                # no fy in the URL: the current fiscal year
        self.assertEqual(self.page.input_value("#fy-pick"), "2027")
        heads = self.page.eval_on_selector_all("#compare-grid thead tr", "trs => trs.map(t => [...t.children].map(c => c.innerText))")
        self.assertEqual(heads, [["Account", "FY2026", "FY2027", "Change"],
                                 ["Enacted", "Request", "House", "Senate", "Enacted", "House vs FY2026 enacted"]])
        self.assertEqual(self.page.locator("#compare-grid thead th.lead").inner_text(), "House")
        self.assertTrue(self.cell("ACC-HHS-NIH-TOTAL", 2027, "House Reported").evaluate("e => e.classList.contains('lead')"))
        # Senate and Enacted show their tokens, never blanks or zeros
        for st, tok in (("Senate Reported", "n/c"), ("Enacted", "n/e")):
            for aid in ("ACC-HHS-TITLE-II-TOTAL", "ACC-HHS-NIH-TOTAL"):
                self.assertEqual(self.cell(aid, 2027, st).inner_text(), tok, (aid, st))
        self.assertEqual(self.page.locator("tr.title [data-testid=change-cell]").first.inner_text().split("\n"), ["+52,771,448", "+3.4%"])
        self.assertEqual(self.page.inner_text("#grid-subline"), "$ thousands · as printed · FY2027 not yet enacted · latest: House")
        # the overview: the House total first, then its change vs FY2026 enacted; Status instead of Enacted in
        self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .l"), "Title II, House, FY2027")
        self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .s"), "1,606,033,805 $K · as printed")
        self.assertEqual(self.page.inner_text("[data-testid=ov-vs-prior] .s"), "+52,771,448 $K · +3.4%")
        self.assertEqual(self.page.inner_text("[data-testid=ov-vs-prior] .l"), "vs FY2026 enacted")
        status = [s.inner_text().split("\n") for s in self.page.locator("[data-testid=ov-status]").all()]
        self.assertEqual(status, [["President’s request", "On file"], ["House", "On file"], ["Senate", "Not yet collected"],
                                  ["Enacted", "Not yet enacted"]])
        docs = [d.inner_text() for d in self.page.locator("[data-testid=ov-status-doc]").all()]
        self.assertEqual(len(docs), 2)
        for d, st in zip(docs, ("President's Budget", "House Reported")):
            o = next(l for l in t["cells"][f"2027|{st}"]["lines"] if l["amount_type"] == "budget authority" and not l["component"])["observations"][0]
            self.assertEqual(d, f"{o['source_document_id']} p.{o['source_page']}")
        self.assertEqual(self.page.locator("[data-testid=ov-law]").count(), 0)
        # View › Columns adds Request vs FY2026 enacted
        self.go("?sc=LHHS&cols=request")
        self.assertEqual(self.page.eval_on_selector_all("#compare-grid thead tr + tr th.delta", "ts => ts.map(t => t.innerText)"),
                         ["House vs FY2026 enacted", "Request vs FY2026 enacted"])
        # switching to FY2026 restores the Enacted layout
        self.go("?sc=LHHS")
        self.page.select_option("#fy-pick", "2026")
        self.assertEqual(self.page.locator("#compare-grid thead th.lead").inner_text(), "Enacted")
        self.assertEqual(self.page.eval_on_selector_all("#compare-grid thead tr + tr th.delta", "ts => ts.map(t => t.innerText)"), ["FY2025 enacted"])
        self.assertEqual(self.page.inner_text("#grid-subline"), "$ thousands · as printed")
        self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .l"), "Title II enacted, FY2026")
        law = self.page.locator("[data-testid=ov-law] a")
        self.assertEqual(law.inner_text(), "P.L. 119-75, Division B")
        self.assertEqual(law.get_attribute("href"), "https://www.govinfo.gov/content/pkg/PLAW-119publ75/pdf/PLAW-119publ75.pdf")
        self.assertIn("Signed Feb. 3, 2026 · minibus · vehicle H.R. 7148", self.page.inner_text("[data-testid=side-panel]"))
        self.assertEqual(self.page.inner_text("[data-testid=ov-cr]"), "FY2025 was a full-year continuing resolution (P.L. 119-4).")

    def test_footer_default_and_reviewer(self):
        # one line below the grid, "Definitions and data sources", expanding on click (UI-2c)
        self.go("?sc=LHHS&fy=2026")
        for sel in ("#grid-legend", "#grid-provenance"):
            self.assertTrue(self.page.is_hidden(sel), sel)
        self.definitions()
        for sel in ("#grid-legend", "#grid-provenance"):
            self.assertTrue(self.page.is_visible(sel), sel)
        for sel in ("#grid-counts", "#mark-legend", "#rev-legend", "#grid-notes"):
            self.assertTrue(self.page.is_hidden(sel), sel)
        # Reviewer mode's counts live inside it
        self.go("?sc=LHHS&fy=2026&rev=1")
        for sel in ("#grid-counts", "#mark-legend", "#rev-legend"):
            self.assertTrue(self.page.is_hidden(sel), sel)
            self.assertEqual(self.page.locator(f"#definitions {sel}").count(), 1, sel)
        self.definitions()
        for sel in ("#grid-counts", "#mark-legend", "#rev-legend"):
            self.assertTrue(self.page.is_visible(sel), sel)

    def test_advanced_filters(self):
        self.go("?sc=LHHS&fy=2026")
        shown = lambda: [r.get_attribute("data-account") for r in self.page.locator("tr[data-testid=compare-row]:not(.title)").all()]
        # a change of at least 10%: the agencies whose change column moved that much (or with a line that did)
        self.advanced("fill", "#f-min-pct", "10")
        self.page.wait_for_function("location.search.includes('minpct=10')")
        def moved(r):
            a, b = (expected_headline(r, y, "Enacted") for y in (2025, 2026))
            if a["state"] != "value" or b["state"] != "value":
                return False
            x, y = to_dollars(a["amount"]), to_dollars(b["amount"])
            return abs(y - x) / abs(x) * 100 >= 10
        agencies = [r for r in self.lhhs["rows"] if r["rollup"] == "agency" and r["lifecycle"]["type"] != "proposed_only"]
        want = [r["account"]["canonical_account_id"] for r in agencies
                if moved(r) or any(moved(m) for m in self.lhhs["rows"] if m["member_of"] == r["account"]["canonical_account_id"]
                                   and in_view(m, self.DEFAULT))]
        self.assertEqual([a for a in shown() if a in want or self.lrow(a)["rollup"] == "agency"], want)
        self.assertIn("Change of at least 10%", self.page.inner_text("[data-testid=chips]"))
        self.assertEqual(self.page.inner_text("[data-testid=filter-badge]"), "1")
        # pending review or flagged only: every row left has such a figure in view (or a line that does)
        self.page.click("[data-testid=clear-all]")
        self.advanced("check", "#f-review")
        self.assertIn("review=1", self.page.url)
        self.assertGreater(len(shown()), 0)
        # committee drafts left out: FY2024's House draft column goes
        self.page.click("[data-testid=clear-all]")
        self.go("?sc=LHHS&fy=2024")
        self.assertEqual(self.page.locator("[data-testid=draft-label]").count(), 1)
        self.advanced("uncheck", "#f-drafts")
        self.assertEqual(self.page.locator("[data-testid=draft-label]").count(), 0)
        self.assertEqual(self.page.locator("td[data-fy='2024'][data-stage='House Reported']").count(), 0)
        self.assertIn("drafts=0", self.page.url)
        self.assertIn("Committee drafts left out", self.page.inner_text("[data-testid=chips]"))
        # the lifecycle choices live here and chip when changed
        self.advanced("check", "#show-ended")
        self.assertIn("Ended and reorganized accounts shown", self.page.inner_text("[data-testid=chips]"))
        self.assertEqual(self.page.inner_text("[data-testid=filter-badge]"), "2")
        self.page.click("#filter-btn")
        self.page.click("[data-testid=filter-reset]")
        self.assertTrue(self.page.is_hidden("[data-testid=filter-badge]"))

    def test_overview_figures_are_differences_of_printed_totals(self):
        t = self.total()
        f = lambda y, st: next(l for l in t["cells"][f"{y}|{st}"]["lines"] if l["amount_type"] == "budget authority"
                               and not l["component"])["observations"][0]["amount"]
        self.go("?sc=LHHS&fy=2026")
        self.assertEqual(self.page.inner_text("[data-testid=ov-headline] .v"), "$1.55T")
        self.assertEqual(self.page.inner_text("[data-testid=ov-vs-prior] .l"), "vs FY2025 enacted (full-year CR)")
        d = f(2026, "Enacted") - f(2025, "Enacted")
        self.assertEqual(self.page.inner_text("[data-testid=ov-vs-prior] .v"), f"+${d / 1e9:.2f}B")
        stages = [s.inner_text().split("\n")[1] for s in self.page.locator("[data-testid=ov-stage]").all()]
        self.assertEqual(stages, [thousands(f(2026, st)) for st in FOUR])
        # largest movers: the agencies with both values, three up then three down
        moves = []
        for r in self.lhhs["rows"]:
            if r["rollup"] != "agency" or r["lifecycle"]["type"] == "proposed_only":
                continue
            a, b = (printed(r, y, "Enacted") for y in (2025, 2026))
            if a is not None and b is not None and a != b:
                moves.append((b - a, r["account"]["canonical_account_id"]))
        want = [i for d_, i in sorted((m for m in moves if m[0] > 0), reverse=True)[:3]] + \
               [i for d_, i in sorted(m for m in moves if m[0] < 0)[:3]]
        self.assertEqual([m.get_attribute("data-account") for m in self.page.locator("[data-testid=ov-mover]").all()], want)


class PageScroll(GridBrowser):
    """Brief UI-2c (owner decisions, 2026-10-13): one vertical scrollbar, the page's; header rows pinned under the top
    of the window; the account column pinned sideways; a pinned horizontal bar only when needed; a 300px side panel
    that can be hidden; no figure ever clipped at laptop widths; the definitions below the last row."""

    CMS = "ACC-HHS-CMS-TOTAL"

    def go(self, query="", where="static"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.settle()

    def test_only_the_page_scrolls_vertically(self):
        for size in ((1440, 900), (1280, 720)):
            for q in ("?sc=LHHS&fy=2026", "?sc=LHHS&fy=2026&grid=history"):
                with self.subTest(size=size, q=q):
                    self.page.set_viewport_size({"width": size[0], "height": size[1]})
                    self.go(q)
                    self.menu("click", "#expand-all")
                    m = self.page.evaluate("""() => {
                        const scrollers = [...document.querySelectorAll('body *')].filter((e) => e.offsetParent
                            && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1);
                        const box = (s) => { const e = document.querySelector(s);
                            return [getComputedStyle(e).overflowY, e.scrollHeight <= e.clientHeight]; };
                        return {scrollers: scrollers.map((e) => e.id || e.className), grid: box('#grid-wrap'), panel: box('#side-panel'),
                                page: document.documentElement.scrollHeight > innerHeight};
                    }""")
                    self.assertEqual(m["scrollers"], [])
                    self.assertEqual(m["grid"], ["visible", True])
                    self.assertEqual(m["panel"], ["visible", True])
                    self.assertTrue(m["page"])
                    # every row at full length: the grid's box is as tall as its table
                    g, t = (self.page.locator(s).bounding_box() for s in ("#grid-wrap", "#compare-grid"))
                    self.assertGreaterEqual(g["height"] + 1, t["height"])

    def test_header_rows_stay_in_view_after_scrolling_the_page(self):
        self.go("?sc=LHHS&fy=2026")
        self.menu("click", "#expand-all")
        self.page.evaluate("window.scrollTo(0, 1000)")
        self.page.wait_for_timeout(50)
        m = self.page.evaluate("""() => {
            const r1 = document.querySelector('#compare-grid thead th.grp').getBoundingClientRect();
            const r2 = document.querySelector('#compare-grid thead tr + tr th').getBoundingClientRect();
            const at = document.elementFromPoint(r2.left + r2.width / 2, r2.top + r2.height / 2);
            return {y: scrollY, top1: r1.top, bottom2: r2.bottom, top2: r2.top, bottom1: r1.bottom, inHead: !!at.closest('thead')};
        }""")
        self.assertEqual(m["y"], 1000)
        self.assertAlmostEqual(m["top1"], 0, delta=0.5)              # pinned under the top of the window
        self.assertAlmostEqual(m["top2"], m["bottom1"], delta=0.5)
        self.assertTrue(m["inHead"])
        self.assertLess(m["bottom2"], 200)

    def test_account_column_stays_when_the_grid_scrolls_sideways(self):
        self.page.set_viewport_size({"width": 1280, "height": 720})
        self.go("?sc=LHHS&fy=2026&grid=history")
        name = lambda: self.page.locator(f"tr[data-account={self.CMS}] td.acct").bounding_box()["x"]
        head = lambda: self.page.locator("#compare-grid th.acct").bounding_box()["x"]
        fig = lambda: self.page.locator(f"tr[data-account={self.CMS}] td[data-fy='2020'][data-stage=Enacted]").bounding_box()["x"]
        x0, h0, f0 = name(), head(), fig()
        # the pinned bar scrolls the grid
        self.page.eval_on_selector("#grid-hbar", "b => { b.scrollLeft = 300; }")
        self.page.wait_for_timeout(50)
        self.assertEqual((name(), head()), (x0, h0))
        self.assertAlmostEqual(fig(), f0 - 300, delta=1)
        # a sideways wheel over the grid moves it too, and the bar with it
        self.page.mouse.move(700, 500)
        self.page.mouse.wheel(-200, 0)
        self.page.wait_for_timeout(50)
        self.assertAlmostEqual(fig(), f0 - 100, delta=1)
        self.assertEqual(self.page.eval_on_selector("#grid-hbar", "b => Math.round(b.scrollLeft)"), 100)
        self.assertEqual(name(), x0)

    def test_touch_swipe_moves_the_grid_with_the_bar(self):
        # a touchscreen swipe sideways on the grid itself moves it and the pinned bar together; a vertical swipe on
        # the grid still scrolls the page and leaves the grid where it is; a tap still opens a figure's source
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 720}, has_touch=True)
        page = ctx.new_page()
        try:
            page.goto(self.urls["static"] + "?sc=LHHS&fy=2026&grid=history")
            page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('#grid-wrap')).touchAction"), "pan-y pinch-zoom")
            cdp = ctx.new_cdp_session(page)

            def swipe(x, y, dx, dy, steps=12):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
                for i in range(1, steps + 1):
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + dx * i / steps, "y": y + dy * i / steps}]})
                    page.wait_for_timeout(16)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(200)

            state = lambda: page.evaluate("""() => [parseFloat(getComputedStyle(document.querySelector('#grid-wrap')).getPropertyValue('--sx')),
                Math.round(document.querySelector('#grid-hbar').scrollLeft), scrollY,
                document.querySelector('tr[data-account=ACC-HHS-CMS-TOTAL] td.acct').getBoundingClientRect().left]""")
            sx, bar, y, name = state()
            self.assertEqual((sx, bar, y), (0, 0, 0))
            # start the swipe on a figure: it moves the grid, and is not a tap on that figure
            fig = page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] td[data-fy='2020'][data-stage=Enacted] button.fig").bounding_box()
            swipe(fig["x"] + fig["width"] / 2, fig["y"] + fig["height"] / 2, -300, 0)
            self.assertEqual(state()[:3], [300, 300, 0])               # the grid and the bar, together; the page stays
            self.assertEqual(page.get_attribute("#side-panel", "data-mode"), "overview")
            self.assertEqual(state()[3], name)                         # the account column stays put
            swipe(700, 600, 0, -300)
            sx, bar, y, _ = state()
            self.assertEqual((sx, bar), (300, 300))                     # a vertical swipe doesn't move the grid ...
            self.assertGreater(y, 200)                                 # ... and scrolls the page
            swipe(700, 400, 120, 0)
            self.assertEqual(state()[:2], [180, 180])
            # the bar moved by hand moves the grid the same way (one position for both)
            page.eval_on_selector("#grid-hbar", "b => { b.scrollLeft = 40; }")
            page.wait_for_timeout(50)
            self.assertEqual(state()[:2], [40, 40])
            # a tap is not a swipe: it opens the figure's source
            page.tap("tr[data-account=ACC-HHS-NIH-TOTAL] td[data-fy='2022'][data-stage=Enacted] button.fig")
            self.assertEqual(page.get_attribute("#side-panel", "data-mode"), "source")
        finally:
            ctx.close()

    def test_pinned_bar_only_when_the_grid_is_wider_than_its_space(self):
        self.page.set_viewport_size({"width": 1280, "height": 720})
        self.go("?sc=LHHS&fy=2026")
        self.assertTrue(self.page.is_hidden("[data-testid=grid-hbar]"))
        self.assertTrue(self.page.evaluate("document.querySelector('#compare-grid').offsetWidth <= document.querySelector('#grid-wrap').clientWidth"))
        self.go("?sc=LHHS&fy=2026&grid=history")
        bar = self.page.locator("[data-testid=grid-hbar]")
        self.assertTrue(bar.is_visible())
        # pinned to the bottom of the window while the grid runs past it, and the bar's range is the grid's overflow
        self.assertAlmostEqual(bar.bounding_box()["y"] + bar.bounding_box()["height"], 720, delta=1)
        self.page.evaluate("window.scrollTo(0, 600)")
        self.assertAlmostEqual(bar.bounding_box()["y"] + bar.bounding_box()["height"], 720, delta=1)
        self.assertEqual(self.page.evaluate("""() => { const b = document.querySelector('#grid-hbar');
            return b.scrollWidth - b.clientWidth === document.querySelector('#compare-grid').offsetWidth
                   - document.querySelector('#grid-wrap').clientWidth; }"""), True)
        # gone again when the grid fits (a wide window)
        self.page.set_viewport_size({"width": 2400, "height": 900})
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.is_hidden("[data-testid=grid-hbar]"))

    def test_panel_is_300px_and_hide_show_survives_a_reload(self):
        for w in (1280, 1366, 1440, 1536, 1920):
            self.page.set_viewport_size({"width": w, "height": 900})
            self.go("?sc=LHHS&fy=2026")
            self.assertEqual(self.page.locator("#side-panel").bounding_box()["width"], 300, w)
        toggle = self.page.locator("[data-testid=panel-toggle]")
        self.assertEqual((toggle.inner_text(), toggle.get_attribute("aria-expanded")), ("Hide panel", "true"))
        before = self.page.locator("#grid-wrap").bounding_box()["width"]
        toggle.focus()
        self.page.keyboard.press("Enter")                            # keyboard-reachable
        self.assertTrue(self.page.is_hidden("#side-panel"))
        self.assertEqual((toggle.inner_text(), toggle.get_attribute("aria-expanded")), ("Show panel", "false"))
        self.assertGreater(self.page.locator("#grid-wrap").bounding_box()["width"], before + 300)
        self.assertIn("panel=hidden", self.page.url)
        self.page.reload()
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.assertTrue(self.page.is_hidden("#side-panel"))
        self.assertEqual(toggle.inner_text(), "Show panel")
        # selecting a figure while hidden shows the panel again, with the figure's source
        self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] td[data-fy='2026'][data-stage=Enacted] button.fig").click()
        self.assertTrue(self.page.is_visible("#side-panel"))
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
        self.assertNotIn("panel=hidden", self.page.url)
        # and selecting an account
        toggle.click()
        self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=row-name]").click()
        self.assertTrue(self.page.is_visible("#side-panel"))
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "account")
        self.assertEqual(toggle.inner_text(), "Hide panel")

    def test_no_figure_is_clipped_at_laptop_widths(self):
        cut = """(ids) => [...document.querySelectorAll('#compare-grid tbody tr')]
            .filter((tr) => ids.includes(tr.dataset.account) || tr.classList.contains('title'))
            .flatMap((tr) => [...tr.cells]).filter((td) => !td.classList.contains('acct') && td.colSpan === 1)
            .filter((td) => td.scrollWidth > td.clientWidth)
            .map((td) => `${td.closest('tr').dataset.account} ${td.dataset.fy || ''} ${td.dataset.stage || ''} ${td.scrollWidth}/${td.clientWidth}`)"""
        cms = [self.CMS] + [r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["member_of"] == self.CMS]
        for w, h in ((1280, 720), (1366, 768), (1440, 900), (1536, 864)):
            ctx = self.browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1.25)
            page = ctx.new_page()
            try:
                for fy in (2026, 2027):
                    for grid in ("stages", "history"):
                        with self.subTest(size=(w, h), fy=fy, grid=grid):
                            page.goto(self.urls["static"] + f"?sc=LHHS&fy={fy}&grid={grid}&open={self.CMS}")
                            page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
                            self.settle(page)
                            self.assertGreater(page.locator(f"tr[data-account={self.CMS}]").count(), 0)
                            self.assertGreater(page.locator("#compare-grid tr.title").count(), 0)
                            self.assertEqual(page.evaluate(cut, cms), [])
            finally:
                ctx.close()

    def test_columns_refit_when_a_font_loads_late(self):
        # a font that finishes loading after the first render (here a much wider face for the figures' weight)
        # refits the columns, so no figure is cut by widths measured before it arrived
        self.page.set_viewport_size({"width": 1280, "height": 720})
        self.go("?sc=LHHS&fy=2026")
        cut = """() => [...document.querySelectorAll('#compare-grid td.num')].filter((td) => td.scrollWidth > td.clientWidth).length"""
        self.assertEqual(self.page.evaluate(cut), 0)
        before = self.page.eval_on_selector_all("#compare-grid col", "cs => cs.map((c) => c.style.width)")
        self.page.evaluate("""async () => {
            const f = new FontFace('IBM Plex Sans', 'local("DejaVu Sans Mono"), local("Liberation Mono"), local("Courier New")', {weight: '500'});
            document.fonts.add(f);
            await f.load();
        }""")
        self.settle()
        self.assertNotEqual(self.page.eval_on_selector_all("#compare-grid col", "cs => cs.map((c) => c.style.width)"), before)
        self.assertEqual(self.page.evaluate(cut), 0)

    def test_definitions_below_the_last_row(self):
        self.go("?sc=LHHS&fy=2026")
        summary = self.page.locator("[data-testid=definitions-toggle]")
        self.assertEqual(summary.inner_text(), "Definitions and data sources")
        self.assertIsNone(self.page.get_attribute("#definitions", "open"))
        last = self.page.locator("#compare-grid tbody tr").last.bounding_box()
        top = self.page.locator("#definitions").bounding_box()["y"]
        self.assertGreaterEqual(top, last["y"] + last["height"])
        self.assertGreaterEqual(top, self.page.evaluate("innerHeight"))   # seen only by scrolling down to it
        summary.focus()
        self.page.keyboard.press("Enter")
        self.assertIsNotNone(self.page.get_attribute("#definitions", "open"))
        self.assertTrue(self.page.is_visible("[data-testid=legend]"))
        self.assertTrue(self.page.is_visible("[data-testid=provenance]"))


def to_dollars(thousands_text):
    return int(thousands_text.replace(",", "").replace("−", "-")) * 1000


def printed(row, fy, stage):
    """The page's figure(): a value, or a printed zero (not_funded with its observation); never a confirmed absence."""
    cell = row["cells"].get(f"{fy}|{stage}")
    if not cell or cell.get("outside_life") or not cell["lines"]:
        return None
    h = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]), cell["lines"][0])
    if not h["observations"]:
        return None
    if h["state"] == "value" or (h["state"] == "not_funded" and not h["absence"]):
        return h["observations"][0]["amount"]
    return None


def short(amount):
    """GridMath.short: a figure in dollars as $1.55T, $47.29B, $128.0M."""
    a, sign = abs(amount), "−" if amount < 0 else ""
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    return f"{sign}${a / 1e6:.1f}M"


def pct_text(a, b):
    """GridMath.pctText: the % under a change; "new" from a zero base, "—" when both are zero."""
    if a is None or b is None:
        return ""
    if a == 0:
        return "—" if b == 0 else "new"
    p = (b - a) / abs(a) * 100
    t = f"{abs(p):.1f}"
    return "0.0%" if t == "0.0" else ("+" if p > 0 else "−") + t + "%"


class PhoneV1(GridBrowser):
    """reference/ui/explore-v1/SPEC.md section 6 at 390 x 844 (brief UI-P): section 10's tests 13 and 14, and a
    desktop link with a selected account opening the sheet on a phone. Live server and static export."""

    NIH, NCI = "ACC-HHS-NIH-TOTAL", "ACC-HHS-NIH-NCI"

    def setUp(self):
        super().setUp()
        self.page.set_viewport_size({"width": 390, "height": 844})

    def go(self, query="", where="static"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("#p-list .prow")

    def lines(self, agency):
        return self.page.locator(f"#p-list .prow.line[data-member-of='{agency}']:not(.more)")

    def focus_ring(self):
        return self.page.evaluate("getComputedStyle(document.activeElement).outlineStyle")

    # 13
    def test_phone_layout(self):
        total = self.lhhs["titles"][0]["total"]
        nih = self.lrow(self.NIH)
        for where in self.urls:
            with self.subTest(where=where):
                self.go("?sc=LHHS&fy=2026", where)
                # no grid; no compare modes, View menu, Share or Export; the side panel's job is the sheet's
                for sel in ("#compare-grid", "#grid-wrap", "#side-panel", "#view-switch", "#view-btn", "#share", "#export",
                            "#search", "#sc", "#filter-btn", ".appbar .tabs"):
                    self.assertTrue(self.page.is_hidden(sel), sel)
                self.assertTrue(self.page.is_visible("#menu-btn"))
                # one row: the context picker, then Search and Filter icon buttons, 44 x 44
                self.assertEqual(self.page.inner_text("#p-context-text"), "Labor-HHS-Education · FY2026")
                for sel in ("#p-search-btn", "#p-filter-btn"):
                    box = self.page.locator(sel).bounding_box()
                    self.assertEqual((box["width"], box["height"]), (44, 44), sel)
                # the summary card: the headline printed total, its change vs FY2025 enacted and vs the request
                v, p, r = (printed(total, y, st) for y, st in ((2026, "Enacted"), (2025, "Enacted"), (2026, "President's Budget")))
                self.assertEqual(self.page.inner_text("[data-testid=p-headline]"), short(v))
                self.assertEqual(self.page.inner_text("[data-testid=p-vs-prior]"), f"+{short(v - p)} ({pct_text(p, v)}) vs FY2025 enacted")
                self.assertEqual(self.page.inner_text("[data-testid=p-vs-request]"), f"+{short(v - r)} ({pct_text(r, v)}) vs request")
                # the stage switch: four stages, Enacted by default, pinned while the list scrolls
                stages = self.page.locator("#p-stages button")
                self.assertEqual(stages.all_inner_texts(), ["Request", "House", "Senate", "Enacted"])
                self.assertEqual([b.get_attribute("aria-pressed") for b in stages.all()], ["false", "false", "false", "true"])
                self.assertEqual(self.page.inner_text("#p-stage-note"),
                                 "Showing FY2026 Enacted · % change vs FY2025 enacted · tap a line for sources")
                # each agency: a short figure and its % change vs FY2025 enacted
                row = self.page.locator(f"#p-list .prow[data-account='{self.NIH}']")
                a, b = printed(nih, 2025, "Enacted"), printed(nih, 2026, "Enacted")
                self.assertEqual(row.locator("[data-testid=p-value]").inner_text(), short(b))
                self.assertEqual(row.locator("[data-testid=p-change]").inner_text(), pct_text(a, b))
                self.assertEqual(self.lines(self.NIH).count(), 0)
                # expanding keeps the parent/line formatting: bold total and its note; lighter lines on a rail
                row.locator(".pmain").click()
                self.assertEqual(row.locator(".pmain").get_attribute("aria-expanded"), "true")
                self.assertEqual(row.locator("[data-testid=p-row-note]").inner_text(), "Agency total · sum of the 32 lines below")
                self.assertIn(f"open={self.NIH}", self.page.url)
                self.assertEqual(row.locator(".pname").evaluate("e => getComputedStyle(e).fontWeight"), "600")
                first = self.lines(self.NIH).first
                self.assertEqual(first.locator(".pname").evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).fontWeight]"),
                                 ["13px", "400"])
                self.assertEqual(first.locator(".pval").evaluate("e => getComputedStyle(e).fontWeight"), "400")
                self.assertIn("linear-gradient", first.evaluate("e => getComputedStyle(e).backgroundImage"))
                # lines largest first: the first 6, then "Show N more"
                members = [self.lrow(m) for m in self.members_in_view(self.NIH)]
                self.assertEqual(len(members), 32)
                want = sorted((printed(m, 2026, "Enacted") for m in members), key=lambda x: -1 if x is None else x, reverse=True)
                shown = lambda: self.lines(self.NIH).locator("[data-testid=p-value]").all_inner_texts()
                self.assertEqual(shown(), [short(x) for x in want[:6]])
                self.assertEqual(self.page.inner_text("[data-testid=p-more]"), "Show 26 more NIH lines")
                self.page.click("[data-testid=p-more]")
                self.assertEqual(self.lines(self.NIH).count(), 32)
                self.assertEqual(shown(), [short(x) if x else ("—" if x == 0 else "") for x in want])
                # the stage switch stays pinned while scrolling
                self.page.evaluate("window.scrollTo(0, 1200)")
                self.assertAlmostEqual(self.page.locator("#p-stage-bar").bounding_box()["y"], 0, delta=1)
                # tapping a line opens the sheet: the exact $K, five stages each with document · page · bill
                self.page.locator(f"#p-list .prow[data-account='{self.NCI}'] .pmain").click()
                sheet = self.page.locator("#p-sheet")
                self.assertTrue(sheet.is_visible())
                self.assertEqual(sheet.get_attribute("role"), "dialog")
                self.assertEqual(sheet.locator("[data-testid=acct-name]").inner_text(), "National Cancer Institute")
                self.assertEqual(sheet.locator("[data-testid=acct-path]").inner_text(), "Title II › National Institutes of Health")
                nci = self.lrow(self.NCI)
                self.assertEqual(sheet.locator("[data-testid=acct-exact]").inner_text(), "FY2026 enacted, as printed")
                st = sheet.locator("[data-testid=acct-stage]")
                self.assertEqual(st.count(), 5)
                for k, (y, s) in enumerate([(2025, "Enacted")] + [(2026, x) for x in FOUR]):
                    self.assertEqual(st.nth(k).locator("[data-testid=acct-stage-fig]").inner_text(), thousands(printed(nci, y, s)))
                    src = st.nth(k).locator("[data-testid=acct-stage-src]")
                    self.assertRegex(src.inner_text(), r",\u00a0p\.\u00a0\d+$")
                    self.assertEqual(src.locator("a").count(), 1)                 # the document at its page (UI-2b)
                enacted = st.nth(4).locator("[data-testid=acct-stage-src] a")
                self.assertIn("p.\u00a0285", enacted.inner_text())
                self.assertTrue(enacted.get_attribute("href").endswith("#page=285"))
                self.assertEqual(sheet.locator("[data-testid=acct-bills]").inner_text(), "Bill text: FY2025: P.L. 119-4 (full-year CR) · H.R. 5304 · S. 2587 · P.L. 119-75")
                self.assertEqual(sheet.locator("[data-testid=acct-bar]").count(), 10)
                self.assertTrue(sheet.locator("[data-testid=acct-open]").is_visible())
                self.assertIn(f"acct={self.NCI}", self.page.url)
                # × closes it
                sheet.locator("[data-testid=p-sheet-close]").click()
                self.assertTrue(self.page.is_hidden("#p-sheet-back"))
                self.assertNotIn("acct=", self.page.url)
                # another stage: its figures, its URL
                stages.nth(1).click()
                self.assertEqual(stages.nth(1).get_attribute("aria-pressed"), "true")
                self.assertIn("pstage=house", self.page.url)
                self.assertEqual(row.locator("[data-testid=p-value]").inner_text(), short(printed(nih, 2026, "House Reported")))
                self.assertTrue(self.page.inner_text("#p-stage-note").startswith("Showing FY2026 House"))

    def test_picker_name_is_one_line_with_an_ellipsis(self):
        # owner, #60: the data's name, never wrapped; cut with an ellipsis where it doesn't fit
        for width in (390, 320):
            self.page.set_viewport_size({"width": width, "height": 844})
            self.go("?sc=LHHS&fy=2026")
            m = self.page.eval_on_selector("#p-context-text", """e => { const s = getComputedStyle(e);
                return [e.textContent, s.whiteSpace, s.textOverflow, e.getBoundingClientRect().height <= parseFloat(s.lineHeight) + 1,
                        e.scrollWidth > e.clientWidth, document.documentElement.scrollWidth > innerWidth]; }""")
            self.assertEqual(m[:4], ["Labor-HHS-Education · FY2026", "nowrap", "ellipsis", True], width)
            self.assertFalse(m[5], width)                                    # no sideways page scroll
            if width == 320:
                self.assertTrue(m[4])                                        # cut, not wrapped

    def test_phone_year_without_enacted_and_no_total(self):
        # FY2027 (the default year): the lead stage leads the switch and the card; CJS prints no title total
        total = self.lhhs["titles"][0]["total"]
        self.go("?sc=LHHS&fy=2027")
        self.assertEqual(self.page.locator("#p-stages button[aria-pressed=true]").inner_text(), "House")
        v, p = printed(total, 2027, "House Reported"), printed(total, 2026, "Enacted")
        self.assertEqual(self.page.inner_text("[data-testid=p-headline]"), short(v))
        self.assertEqual(self.page.inner_text("[data-testid=p-vs-prior]"), f"+{short(v - p)} ({pct_text(p, v)}) vs FY2026 enacted")
        self.assertNotIn("pstage=", self.page.url)
        # a stage with nothing yet shows its state token, never a blank or a zero
        self.page.locator("#p-stages button", has_text="Senate").click()
        vals = self.page.locator(f"#p-list .prow[data-account='{self.NIH}'] [data-testid=p-value]")
        self.assertEqual(vals.inner_text(), "n/c")
        self.assertEqual(self.page.locator(f"#p-list .prow[data-account='{self.NIH}'] [data-testid=p-change]").inner_text(), "")
        self.go("?sc=CJS&fy=2026")
        self.assertEqual(self.page.inner_text("[data-testid=p-no-total]"),
                         "No printed title or bill total on file. The page never adds accounts into one.")

    # 14
    def test_phone_keyboard_and_focus(self):
        self.go(f"?sc=LHHS&fy=2026&open={self.NIH}")
        self.page.locator("body").focus()
        seen = []
        for _ in range(12):
            self.page.keyboard.press("Tab")
            seen.append(self.page.evaluate("""() => { const e = document.activeElement;
                return [e.id || e.dataset.key || e.tagName, getComputedStyle(e).outlineStyle]; }"""))
        ids = [s[0] for s in seen]
        for want in ("menu-btn", "p-context", "p-search-btn", "p-filter-btn", "pst-0", "pst-3", f"p-{self.NIH}", f"p-{self.NCI}"):
            self.assertIn(want, ids)
        self.assertEqual([s for s in seen if s[1] != "solid"], [])                # a visible focus ring on each
        # the picker and the filter: Esc closes each and focus returns to its button
        for button, panel in (("#p-context", "#p-picker"), ("#p-filter-btn", "#filter-panel")):
            self.page.focus(button)
            self.page.keyboard.press("Enter")
            self.assertTrue(self.page.is_visible(panel), panel)
            self.assertTrue(self.page.evaluate(f"document.querySelector('{panel}').contains(document.activeElement)"))
            self.assertEqual(self.page.get_attribute(button, "aria-expanded"), "true")
            self.page.keyboard.press("Escape")
            self.assertTrue(self.page.is_hidden(panel))
            self.assertEqual(self.page.evaluate("'#' + document.activeElement.id"), button)
        # the menu
        self.page.focus("#menu-btn")
        self.page.keyboard.press("Enter")
        self.assertTrue(self.page.is_visible(".appbar .tabs"))
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "tab-compare")
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.is_hidden(".appbar .tabs"))
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "menu-btn")
        # the search icon opens its box
        self.page.focus("#p-search-btn")
        self.page.keyboard.press("Enter")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "p-search")
        # the sheet: Enter on a line opens it with focus inside; Tab stays inside; Esc closes it, focus back on the line
        line = self.page.locator(f"#p-list .prow[data-account='{self.NCI}'] .pmain")
        line.focus()
        self.page.keyboard.press("Enter")
        self.assertTrue(self.page.is_visible("#p-sheet"))
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.testid"), "p-sheet-close")
        for _ in range(25):
            self.page.keyboard.press("Tab")
            self.assertTrue(self.page.evaluate("document.querySelector('#p-sheet').contains(document.activeElement)"))
            self.assertEqual(self.focus_ring(), "solid")
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.is_hidden("#p-sheet-back"))
        self.assertEqual(self.page.evaluate("document.activeElement.closest('.prow').dataset.account"), self.NCI)
        # × does the same; so does a tap on the dimmed page
        line.click()
        self.page.click("[data-testid=p-sheet-close]")
        self.assertEqual(self.page.evaluate("document.activeElement.closest('.prow').dataset.account"), self.NCI)
        line.click()
        self.page.mouse.click(200, 20)
        self.assertTrue(self.page.is_hidden("#p-sheet-back"))

    def test_a_desktop_link_opens_the_same_state_on_a_phone(self):
        agency = self.lrow(self.NIH)["account"]["agency"]
        for sel, query in (("acct", f"acct={self.NCI}"), ("fig", f"fig={self.NCI}|2026|Enacted")):
            with self.subTest(sel=sel):
                self.page.set_viewport_size({"width": 1440, "height": 900})
                self.page.goto(self.urls["static"] + f"?sc=LHHS&fy=2026&status=enacted&{query}&agency={agency}")
                self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
                self.assertNotEqual(self.page.get_attribute("#side-panel", "data-mode"), "overview")
                link = self.page.url
                # the same link on a phone: the subcommittee, year, filters and the account in its sheet
                self.page.set_viewport_size({"width": 390, "height": 844})
                self.go(link[len(self.urls["static"]):])
                self.assertEqual(self.page.inner_text("#p-context-text"), "Labor-HHS-Education · FY2026")
                self.assertEqual(self.page.inner_text("#p-filter-badge"), "2")
                self.assertEqual(self.page.locator("[data-testid=p-chip]").count(), 2)
                self.assertIn(f"Agency: {agency}", self.page.inner_text("#p-chips"))
                self.assertTrue(self.page.is_visible("#p-sheet"))
                self.assertEqual(self.page.inner_text("#p-sheet [data-testid=acct-name]"), "National Cancer Institute")
                self.assertEqual(self.page.evaluate("document.activeElement.dataset.testid"), "p-sheet-close")
                self.assertEqual(self.page.url, link)                       # opening it changes nothing
                # resizing back to the desktop keeps the same state
                self.page.set_viewport_size({"width": 1440, "height": 900})
                self.page.wait_for_selector("#compare-grid", state="visible")
                self.assertTrue(self.page.is_hidden("#p-sheet"))
                self.assertNotEqual(self.page.get_attribute("#side-panel", "data-mode"), "overview")
        # and the other way: the list's choices reach the desktop
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.go("?sc=LHHS&fy=2026")
        self.page.locator(f"#p-list .prow[data-account='{self.NIH}'] .pmain").click()
        self.page.locator(f"#p-list .prow[data-account='{self.NCI}'] .pmain").click()
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.wait_for_selector("#compare-grid", state="visible")
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "account")
        self.assertEqual(self.page.inner_text("#side-panel [data-testid=acct-name]"), "National Cancer Institute")
        self.assertEqual(self.page.locator(f"#compare-grid tr[data-account='{self.NCI}']").get_attribute("data-sel"), "row")

    def test_phone_class_tags_and_basis(self):
        # UI-3 (#61) on the phone: the agencies the grid tags "incl. mandatory", and the Amount basis a link carries
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(self.urls["static"] + "?sc=LHHS&fy=2026")
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        grid = set(self.page.eval_on_selector_all("#compare-grid tr.agency [data-testid=class-tag]",
                                                  "ts => ts.map(t => t.closest('tr').dataset.account)"))
        self.assertTrue(grid)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.go("?sc=LHHS&fy=2026&amt=discretionary")
        phone = set(self.page.eval_on_selector_all("#p-list .prow.agency [data-testid=p-class-tag]",
                                                   "ts => ts.map(t => t.closest('.prow').dataset.account)"))
        self.assertEqual(phone, grid)
        self.assertIn("Discretionary", self.page.inner_text("#p-stage-note"))
        self.assertIn("discretionary", self.page.inner_text("#p-summary").lower())
        # each agency's figure is its published view (or its state), never the printed figure in disguise
        views = self.page.eval_on_selector_all("#p-list .prow.agency [data-testid=p-value]", "vs => vs.map(v => v.dataset.view || '')")
        self.assertTrue(all(views), views)

    def test_open_the_full_table(self):
        self.go("?sc=LHHS&fy=2026")
        self.page.click("[data-testid=p-full]")
        self.page.wait_for_selector("#compare-grid", state="visible")
        self.assertIn("table=1", self.page.url)
        self.assertTrue(self.page.is_hidden("#phone"))
        self.page.go_back()
        self.page.wait_for_selector("#p-list .prow")
        self.assertTrue(self.page.is_hidden("#compare-grid"))


BASES = ["printed", "discretionary", "discretionary_designated", "all_appropriations", "program_level"]
TIMINGS = ["available", "enacted"]
CELL_TEXT_JS = """td => { const n = td.querySelector(':scope > [data-testid=amount], :scope > [data-testid=view-state], :scope > .fig, :scope > .tok');
                     return n ? n.textContent : ''; }"""


def view_entry(row, fy, stage, basis, timing):
    """The cell's published view entry the page shows (web/ui_contract.json shapes): the timing's own entry, else the
    'available' one."""
    cell = row["cells"].get(f"{fy}|{stage}") or {}
    entries = (cell.get("views") or {}).get(basis) or []
    return next((e for e in entries if e["timing"] == timing), None) or \
        next((e for e in entries if e["timing"] == "available"), None) or (entries[0] if entries else None)


def view_expected(row, fy, stage, basis, timing):
    """(text the cell shows, figure used in differences or None) under a basis, for a printed figure; None for a cell
    with no printed figure (it shows its state as before)."""
    p = printed(row, fy, stage)
    if p is None:
        return None
    if basis == "printed":
        return ("—" if p == 0 else thousands(p)), p
    e = view_entry(row, fy, stage, basis, timing)
    if e is None:
        return "n/a", None
    if e["status"] in ("checked", "computed"):
        return ("—" if e["amount"] == 0 and p == 0 else thousands(e["amount"])), e["amount"]
    if e["status"] == "excluded":
        return "excl.", None
    return ("not checked" if e.get("accounts_behind") is not None else "n/a"), None


def view_entry_of(row, fy, stage, basis, timing, key):
    """view_entry for one of the other published view sets (views_excluding_earmarks, views_nondefense)."""
    cell = row["cells"].get(f"{fy}|{stage}") or {}
    entries = (cell.get(key) or {}).get(basis) or []
    return next((e for e in entries if e["timing"] == timing), None) or \
        next((e for e in entries if e["timing"] == "available"), None) or (entries[0] if entries else None)


def row_class(row, fy):
    """The row's budget_class for the year: the enacted-basis record, else the request basis' (a proposed account)."""
    a = (row.get("attributes") or {}).get(str(fy)) or {}
    return a.get("budget_class") or (a.get("request") or {}).get("budget_class")


class AmountBasis(GridBrowser):
    """Brief UI-3 (SPEC 5.6 Advanced, section 4 class items): the Amount basis and advance timing, Account type and
    the class tags, the defense column; each against the published accounting layer (UI-0), never a computed figure."""

    MEDICAID, CURES, HSPS, PM = ("ACC-HHS-CMS-MEDICAID", "ACC-HHS-NIH-CURES", "ACC-HHS-SAMHSA-PROGRAM-SUPPORT",
                                 "ACC-HHS-CMS-PROGRAM-MANAGEMENT")
    OPEN = "ACC-HHS-CMS-TOTAL,ACC-HHS-NIH-TOTAL,ACC-HHS-SAMHSA-TOTAL"

    def go(self, query="", where="static"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.settle()

    def text(self, aid, fy, stage):
        return self.cell(aid, fy, stage).evaluate(CELL_TEXT_JS)

    def test_basis_and_timing_cells(self):
        # every basis x timing: an account with advances (Medicaid, mandatory), one with a designated component (NIH
        # Innovation Account, CURES), an earmark account (SAMHSA HSPS), a mixed-class account (CMS Program
        # Management), the title total; FY2025 is a full-year CR year. Each cell shows its published view, "excl."
        # or its state; the change is the difference of the two shown figures, or empty
        rows = [self.lrow(a) for a in (self.MEDICAID, self.CURES, self.HSPS, self.PM)] + [self.lhhs["titles"][0]["total"]]
        for basis in BASES:
            for timing in TIMINGS:
                for where in (("static", "live") if timing == "available" else ("static",)):
                    with self.subTest(basis=basis, timing=timing, where=where):
                        self.go(f"?sc=LHHS&fy=2026&amt={basis}&timing={timing}&open={self.OPEN}", where)
                        label = self.page.inner_text("#grid-subline")
                        self.assertIn("as printed" if basis == "printed" else f"advances when {timing}", label)
                        for row in rows:
                            aid = row["account"]["canonical_account_id"]
                            figs = {}
                            for fy, st in self.DEFAULT:
                                exp = view_expected(row, fy, st, basis, timing)
                                if exp is None:
                                    continue
                                self.assertEqual(self.text(aid, fy, st), exp[0], (aid, fy, st))
                                figs[(fy, st)] = exp[1]
                                if basis != "printed":
                                    e = view_entry(row, fy, st, basis, timing)
                                    self.assertEqual(self.cell(aid, fy, st).get_attribute("data-view"), e["status"] if e else "none")
                                    if e and e.get("reason"):
                                        self.assertIn(e["reason"], self.cell(aid, fy, st).locator("button.fig").get_attribute("title"))
                            a, b = figs.get((2025, "Enacted")), figs.get((2026, "Enacted"))
                            delta = self.page.locator(f"tr[data-account={aid}] [data-testid=change-cell]").first.get_attribute("data-delta")
                            self.assertEqual(delta, None if a is None or b is None else str(b - a), (aid, basis, timing))

    def test_known_views(self):
        # the figures the data holds, read off the page: Medicaid's advances (FY2026 and the FY2025 CR year), CURES'
        # designated amount, Medicaid excluded under the discretionary bases, the mixed account's note
        self.go(f"?sc=LHHS&fy=2026&amt=all_appropriations&open={self.OPEN}")
        self.assertEqual(self.text(self.MEDICAID, 2026, "Enacted"), "769,212,611")
        self.assertEqual(self.text(self.MEDICAID, 2025, "Enacted"), "629,189,813")
        self.go(f"?sc=LHHS&fy=2026&amt=all_appropriations&timing=enacted&open={self.OPEN}")
        self.assertEqual(self.text(self.MEDICAID, 2026, "Enacted"), "824,663,516")
        self.assertEqual(self.cell(self.MEDICAID, 2026, "Enacted").get_attribute("data-view"), "checked")
        self.go(f"?sc=LHHS&fy=2026&amt=discretionary&open={self.OPEN}")
        self.assertEqual(self.text(self.MEDICAID, 2026, "Enacted"), "excl.")
        self.assertEqual(self.text(self.CURES, 2026, "Enacted"), "0")
        self.assertIn("mixed, no mandatory portion tagged", self.cell(self.PM, 2026, "Enacted").locator("button.fig").get_attribute("title"))
        self.go(f"?sc=LHHS&fy=2026&amt=discretionary_designated&open={self.OPEN}")
        self.assertEqual(self.text(self.CURES, 2026, "Enacted"), "226,000")
        # the earmark account: every view counts its earmark (ACCOUNTING_RULES default 4); no view leaves it out
        self.go(f"?sc=LHHS&fy=2026&amt=all_appropriations&open={self.OPEN}")
        self.assertEqual(self.text(self.HSPS, 2026, "Enacted"), "171,566")
        # the legend names the basis' tokens
        self.definitions()
        self.assertIn("excl. = left out by this basis", self.page.inner_text("[data-testid=basis-legend]"))

    def test_source_panel_under_basis(self):
        def panel(q):
            self.go(q)
            self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
            terms = self.page.eval_on_selector_all("[data-testid=arith-term]", "es => es.map(e => e.innerText.replace(/\\s+/g, ' '))")
            comps = self.page.eval_on_selector_all("[data-testid=src-component]", "es => es.map(e => [e.dataset.component, e.dataset.used])")
            return terms, comps
        terms, comps = panel(f"?sc=LHHS&fy=2026&amt=all_appropriations&fig={self.MEDICAID}|2026|Enacted")
        self.assertEqual(self.page.inner_text("[data-testid=src-view-value]"), "769,212,611")
        self.assertEqual(self.page.inner_text("[data-testid=src-value]"), "$508,148,791,000")  # the printed headline
        self.assertEqual(terms, ["Printed figure 508,148,791", "plus advance, available this year +261,063,820"])
        self.assertEqual(self.page.inner_text("[data-testid=arith-result]").split()[-1], "769,212,611")
        self.assertEqual(comps, [["CMP-LHHS-0009", "false"], ["CMP-LHHS-0010", "true"]])
        self.assertIn("p. 289", self.page.inner_text("[data-testid=src-component]"))
        self.assertIn("Open source document at page 289", self.page.inner_text("#side-panel"))
        terms, _ = panel(f"?sc=LHHS&fy=2026&amt=discretionary&fig={self.CURES}|2026|Enacted")
        self.assertEqual(terms, ["Printed figure 226,000", "less designated (other) −226,000"])
        self.assertEqual(self.page.inner_text("[data-testid=src-view-value]"), "0")
        # excluded: the reason, the printed headline and the components; no arithmetic
        terms, _ = panel(f"?sc=LHHS&fy=2026&amt=discretionary&fig={self.MEDICAID}|2026|Enacted")
        self.assertEqual(self.page.inner_text("[data-testid=src-view-value]"), "excl.")
        self.assertIn("A mandatory account (budget_class)", self.page.inner_text("[data-testid=src-view-reason]"))
        self.assertEqual(terms, [])
        # the earmark account: its earmark is a component the view keeps
        terms, comps = panel(f"?sc=LHHS&fy=2026&amt=all_appropriations&fig={self.HSPS}|2026|Enacted")
        self.assertIn(["CMP-LHHS-0209", "false"], comps)
        self.assertIn("Earmarks (CDS / CPF)", self.page.inner_text("#side-panel"))
        # as printed: the panel is UI-2's, with no view block
        self.go(f"?sc=LHHS&fy=2026&fig={self.MEDICAID}|2026|Enacted")
        self.assertEqual(self.page.locator("[data-testid=src-view-value], [data-testid=src-component]").count(), 0)

    def test_overview_view_totals(self):
        total = self.lhhs["titles"][0]["total"]
        for basis in BASES[1:]:
            with self.subTest(basis=basis):
                self.go(f"?sc=LHHS&fy=2026&amt={basis}")
                e = view_entry(total, 2026, "Enacted", basis, "available")
                head = self.page.inner_text("[data-testid=ov-headline]")
                if e["status"] == "checked":
                    self.assertIn(thousands(e["amount"]), head)
                else:
                    self.assertIn(f"not checked ({e['accounts_behind']} accounts)", head)
                stages = self.page.eval_on_selector_all("[data-testid=ov-stage] .val", "es => es.map(e => e.innerText)")
                for i, st in enumerate(("President's Budget", "House Reported", "Senate Reported", "Enacted")):
                    x = view_entry(total, 2026, st, basis, "available")
                    self.assertEqual(stages[i], thousands(x["amount"]) if x["status"] == "checked"
                                     else f"not checked ({x['accounts_behind']} accounts)")
        self.go("?sc=LHHS&fy=2026&amt=discretionary_designated")
        self.assertIn("114,797,239", self.page.inner_text("[data-testid=ov-headline]"))

    def test_account_type_and_class_tags(self):
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-CMS-TOTAL")
        attrs = lambda aid: self.lrow(aid)["attributes"]["2026"]
        # "incl. mandatory" (owner, #61): a line whose class is mandatory, or mixed with a tagged mandatory_portion; an
        # agency holding one. FY2026: HRSA, CDC, CMS, ACF and OS. A mixed line with none tagged (CMS Program
        # Management) counts as discretionary and has no tag
        tagged = self.page.eval_on_selector_all("tr.agency [data-testid=class-tag]", "ts => ts.map(t => t.closest('tr').dataset.account)")
        self.assertEqual(sorted(tagged), sorted(f"ACC-HHS-{a}-TOTAL" for a in ("HRSA", "CDC", "CMS", "ACF", "OS")))
        self.assertIn("Vaccine Injury Compensation Program Trust Fund: mandatory portion 272,062",
                      self.page.get_attribute("tr[data-account=ACC-HHS-HRSA-TOTAL] [data-testid=class-tag]", "title"))
        self.assertIn("Grants to States for Medicaid: mandatory 508,148,791",
                      self.page.get_attribute("tr[data-account=ACC-HHS-CMS-TOTAL] [data-testid=class-tag]", "title"))
        self.assertEqual(self.page.locator(f"tr[data-account={self.PM}] [data-testid=class-tag]").count(), 0)
        self.assertEqual(attrs(self.PM)["budget_class"], "mixed")
        # a mixed line with a portion: its tag names the amount and is marked provisional as its record says
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-HRSA-TOTAL")
        tag = self.page.locator("tr[data-account=ACC-HHS-HRSA-VICTF] [data-testid=class-tag]")
        self.assertEqual(tag.inner_text(), "incl. mandatory")
        self.assertEqual(tag.get_attribute("data-provisional"), str(attrs("ACC-HHS-HRSA-VICTF")["provisional"]).lower())
        self.assertIn("mandatory portion 272,062", tag.get_attribute("title"))
        self.assertIn("Budget class mixed", tag.get_attribute("title"))
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-CMS-TOTAL")
        # "Appropriated mandatory": CMS's mandatory lines follow the label, after the others
        order = self.page.eval_on_selector_all("#compare-grid tbody tr", "trs => trs.map(t => t.dataset.account || t.dataset.testid)")
        k = order.index("subgroup-mandatory")
        cms = [r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["member_of"] == "ACC-HHS-CMS-TOTAL"
               and (r.get("attributes", {}).get("2026") or {}).get("budget_class") == "mandatory"]
        self.assertEqual(sorted(order[k + 1:k + 1 + len(cms)]), sorted(cms))
        self.assertIn(self.MEDICAID, cms)
        self.assertLess(order.index(self.PM), k)
        # Account type: only the lines of that class; the badge and the chip count it
        for kind in ("discretionary", "mandatory", "mixed"):
            with self.subTest(kind=kind):
                self.page.click("#filter-btn")
                self.page.click(f"#f-type button[data-type={kind}]")
                self.page.keyboard.press("Escape")
                shown = self.page.eval_on_selector_all("#compare-grid tbody tr.child[data-account]", "ts => ts.map(t => t.dataset.account)")
                self.assertTrue(shown)
                for aid in shown:
                    self.assertEqual(row_class(self.lrow(aid), 2026), kind, aid)
                self.assertEqual(self.page.inner_text("#filter-badge"), "1")
                self.assertIn(f"Account type: {kind.capitalize()}", self.page.inner_text("#chips"))
                # the printed total stays as printed
                total = self.lhhs["titles"][0]["total"]
                self.assertEqual(self.text(total["account"]["canonical_account_id"], 2026, "Enacted"), thousands(printed(total, 2026, "Enacted")))

    def test_defense_column(self):
        conn = S.connect(self.db, readonly=True)
        cjs = plain(S.subcommittee_grid(conn, "CJS"))
        conn.close()
        tagged = {r["account"]["canonical_account_id"]: r["cells"]["2026|Enacted"]["defense_portion"]["amount"]
                  for r in cjs["rows"] if "defense_portion" in (r["cells"].get("2026|Enacted") or {})}
        self.assertTrue(tagged)
        parents = sorted({r["member_of"] for r in cjs["rows"] if r["account"]["canonical_account_id"] in tagged and r["member_of"]})
        self.go("?sc=CJS&fy=2026&open=" + ",".join(parents))
        self.assertEqual(self.page.locator("[data-testid=defense-cell]").count(), 0)
        self.menu("check", "#col-def")
        self.page.wait_for_function("location.search.includes('cols=defense')")
        cells = self.page.eval_on_selector_all("[data-testid=defense-cell]", "ts => ts.map(t => [t.closest('tr').dataset.account, t.innerText.trim()])")
        self.assertTrue(cells)
        for aid, text in cells:
            self.assertEqual(text, thousands(tagged[aid]) if aid in tagged else "", aid)      # untagged is empty, not 0
        aid = next(iter(tagged))
        self.page.click(f"tr[data-account={aid}] [data-testid=defense-cell] button.fig")
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
        self.assertIn("fig=" + aid, self.page.url)

    def test_url_round_trip_every_option(self):
        def snap():
            self.page.mouse.move(2, 2)
            return {"url": self.page.url.split("?", 1)[1], "grid": self.page.inner_html("#compare-grid"),
                    "panel": self.page.inner_html("#side-panel"), "chips": self.page.inner_html("#chips"),
                    "pressed": self.page.eval_on_selector_all("#filter-panel [aria-pressed=true], #view-menu input:checked",
                                                              "es => es.map(e => e.dataset.key || e.id)")}
        def round_trip(param):
            before = snap()
            self.assertIn(param, before["url"])
            self.page.click("#share")
            self.page.goto(self.page.get_attribute("#share", "data-copied"))
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            self.assertEqual(snap(), before, param)
        self.go("?sc=LHHS&fy=2026&open=ACC-HHS-CMS-TOTAL")
        for basis in BASES[1:] + ["printed"]:
            self.advanced("click", f"#f-basis button[data-basis={basis}]")
            round_trip(f"amt={basis}" if basis != "printed" else "sc=LHHS")
        self.advanced("click", "#f-basis button[data-basis=program_level]")
        for timing in ("enacted", "available"):
            self.advanced("click", f"#f-timing button[data-timing={timing}]")
            round_trip("timing=enacted" if timing == "enacted" else "amt=program_level")
        for kind in ("discretionary", "mandatory", "mixed", "all"):
            self.page.click("#filter-btn")
            self.page.click(f"#f-type button[data-type={kind}]")
            self.page.keyboard.press("Escape")
            round_trip(f"type={kind}" if kind != "all" else "amt=program_level")
        self.menu("check", "#col-def")
        round_trip("cols=defense")
        # all of them at once, with a selected figure; chips and badge count each non-default choice
        self.advanced("click", "#f-timing button[data-timing=enacted]")
        self.page.click("#filter-btn"); self.page.click("#f-type button[data-type=mandatory]"); self.page.keyboard.press("Escape")
        self.cell(self.MEDICAID, 2026, "Enacted").locator("button.fig").click()
        round_trip("fig=")
        self.assertEqual(self.page.inner_text("#filter-badge"), "3")
        self.assertEqual(self.page.eval_on_selector_all("#chips [data-testid=chip]", "cs => cs.map(c => c.firstChild.textContent)"),
                         ["Account type: Mandatory", "Basis: Program level", "Advances: when enacted"])
        self.assertTrue(self.page.evaluate("document.querySelector('[data-testid=advanced-summary]').textContent")
                        .startswith("Program level · advances when enacted · earmarks included"))
        self.page.click("[data-testid=clear-all]")
        self.assertNotIn("amt=", self.page.url)
        self.assertNotIn("type=", self.page.url)


class ExcludeEarmarks(GridBrowser):
    """Brief UI-3b (SPEC 5.6, 4): Filter › Advanced "Exclude earmarks (CDS / CPF)" and View › Columns "Nondefense",
    each read from the published views (UI-0b views_excluding_earmarks, views_nondefense), never computed."""

    HSPS, OPEN = "ACC-HHS-SAMHSA-PROGRAM-SUPPORT", "ACC-HHS-SAMHSA-TOTAL"
    RRA = "ACC-NSF-RRA"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        conn = S.connect(cls.db, readonly=True)
        cls.cjs = plain(S.subcommittee_grid(conn, "CJS"))
        conn.close()

    def go(self, query="", where="static"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.settle()

    def text(self, aid, fy, stage):
        return self.cell(aid, fy, stage).evaluate(CELL_TEXT_JS)

    def expected(self, row, fy, stage, basis, timing):
        if printed(row, fy, stage) is None:
            return None
        e = view_entry_of(row, fy, stage, basis, timing, "views_excluding_earmarks")
        if e is None:
            return "n/a"
        if e["status"] in ("checked", "computed"):
            return "—" if e["amount"] == 0 and printed(row, fy, stage) == 0 else thousands(e["amount"])
        return "excl." if e["status"] == "excluded" else ("not checked" if e.get("accounts_behind") is not None else "n/a")

    def nd_row(self, aid):
        return next(r for r in self.cjs["rows"] if r["account"]["canonical_account_id"] == aid)

    def test_exclude_earmarks_cells(self):
        # the box on: every cell of SAMHSA's lines and the title total shows views_excluding_earmarks for the basis and
        # timing in view (the printed basis included), with UI-3's tokens; the label, chip, badge and URL say so
        rows = [self.lrow(m) for m in self.members_in_view(self.OPEN)] + [self.lhhs["titles"][0]["total"]]
        self.go(f"?sc=LHHS&fy=2026&open={self.OPEN}")
        self.assertNotIn("excl. earmarks", self.page.inner_text("#grid-subline"))
        self.advanced("check", "#f-noear")
        self.page.wait_for_function("location.search.includes('earmarks=excl')")
        self.assertIn("· excl. earmarks", self.page.inner_text("#grid-subline"))
        self.assertEqual(self.page.inner_text("#filter-badge"), "1")
        self.assertIn("Earmarks excluded", self.page.inner_text("#chips"))
        for basis in BASES:
            for timing in TIMINGS:
                with self.subTest(basis=basis, timing=timing):
                    self.go(f"?sc=LHHS&fy=2026&earmarks=excl&amt={basis}&timing={timing}&open={self.OPEN}")
                    label = self.page.inner_text("#grid-subline")
                    self.assertIn("excl. earmarks", label)
                    for row in rows:
                        aid = row["account"]["canonical_account_id"]
                        for fy, st in self.DEFAULT:
                            exp = self.expected(row, fy, st, basis, timing)
                            if exp is None:
                                continue
                            self.assertEqual(self.text(aid, fy, st), exp, (aid, fy, st))
                            e = view_entry_of(row, fy, st, basis, timing, "views_excluding_earmarks")
                            self.assertEqual(self.cell(aid, fy, st).get_attribute("data-view"), e["status"] if e else "none")
                            if e and e.get("reason"):
                                self.assertIn(e["reason"], self.cell(aid, fy, st).locator("button.fig").get_attribute("title"))

    def test_known_earmark_figures(self):
        # SAMHSA HSPS FY2026 Enacted: printed 171,566 less its tagged earmark CMP-LHHS-0209 (54,311, P.L. 119-75) =
        # 117,255, the published view; the panel lists the earmark and the published result. FY2026 Senate the same
        # (below); the President's Budget: n/a, "earmarks not tagged for this stage"; the total: not checked
        hsps = self.lrow(self.HSPS)
        pub = view_entry_of(hsps, 2026, "Enacted", "printed", None, "views_excluding_earmarks")
        self.assertEqual((pub["status"], pub["amount"], pub["component_ids"]), ("computed", 117255000, ["CMP-LHHS-0209"]))
        self.go(f"?sc=LHHS&fy=2026&earmarks=excl&open={self.OPEN}")
        self.assertEqual(self.text(self.HSPS, 2026, "Enacted"), "117,255")
        # FY2026 Senate: printed 175,866 less its earmark CMP-LHHS-1326 (54,611, S.Rept. 119-55 p.436) = 121,255, the
        # published view; the panel lists that earmark
        senate = view_entry_of(hsps, 2026, "Senate Reported", "printed", None, "views_excluding_earmarks")
        self.assertEqual((senate["status"], senate["amount"], senate["component_ids"]), ("computed", 121255000, ["CMP-LHHS-1326"]))
        self.assertEqual(self.text(self.HSPS, 2026, "Senate Reported"), "121,255")
        self.cell(self.HSPS, 2026, "Senate Reported").locator("button.fig").click()
        self.assertEqual(self.page.inner_text("[data-testid=src-view-value]"), "121,255")
        ears = self.page.locator("[data-testid=src-earmark]")
        self.assertEqual(ears.count(), 1)
        self.assertEqual(ears.first.get_attribute("data-component"), "CMP-LHHS-1326")
        t = ears.first.inner_text()
        self.assertIn("54,611", t)
        self.assertIn("p. 436", t)
        self.assertIn("121,255", self.page.inner_text("[data-testid=src-earmark-result]"))
        self.assertEqual(self.page.inner_text("[data-testid=src-value]"), "$175,866,000")
        # the earmark list sits inside the "Source document" block, before its one button (UI-2b layout); its page is
        # the earmark's one link, to that page
        self.assertEqual(self.page.locator("[data-testid=src-docblock] [data-testid=src-earmark]").count(), 1)
        self.assertTrue(self.page.evaluate("""() => { const e = document.querySelector('[data-testid=src-earmark]'),
            b = document.querySelector('[data-testid=src-open]'); return !!(e.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }"""))
        self.assertTrue(ears.first.locator("a").get_attribute("href").endswith("#page=436"))
        self.page.keyboard.press("Escape")
        pb = self.cell(self.HSPS, 2026, "President's Budget")
        self.assertEqual(pb.evaluate(CELL_TEXT_JS), "n/a")
        self.assertIn("earmarks not tagged for this stage", pb.locator("button.fig").get_attribute("title"))
        total = self.lhhs["titles"][0]["total"]["account"]["canonical_account_id"]
        self.assertEqual(self.text(total, 2026, "Enacted"), "not checked")
        self.assertIn("earmark components tagged", self.cell(total, 2026, "Enacted").locator("button.fig").get_attribute("title"))
        # the panel: the earmark taken out (amount, document, location) and the published result; nothing added up
        self.cell(self.HSPS, 2026, "Enacted").locator("button.fig").click()
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
        self.assertEqual(self.page.inner_text("[data-testid=src-view-value]"), "117,255")
        ears = self.page.locator("[data-testid=src-earmark]")
        self.assertEqual(ears.count(), 1)
        self.assertEqual(ears.first.get_attribute("data-component"), "CMP-LHHS-0209")
        t = ears.first.inner_text()
        self.assertIn("54,311", t)
        self.assertIn("SRC-PLAW-119PUBL75", t)
        self.assertIn("140 STAT. 271", t)
        self.assertIn("117,255", self.page.inner_text("[data-testid=src-earmark-result]"))
        self.assertEqual(self.page.inner_text("[data-testid=src-value]"), "$171,566,000")
        # the box off: the cell is as printed again
        self.advanced("uncheck", "#f-noear")
        self.page.wait_for_function("!location.search.includes('earmarks=')")
        self.assertEqual(self.text(self.HSPS, 2026, "Enacted"), "171,566")

    def test_nondefense_column(self):
        # NSF R&RA FY2026 Enacted: the nondefense view equals the published one (its defense portion is printed
        # beside the headline); every other line's cell is its published amount or empty; NSF's total is not computable
        rra = self.nd_row(self.RRA)
        self.go("?sc=CJS&fy=2026&open=ACC-NSF-TOTAL")
        self.assertEqual(self.page.locator("[data-testid=nondefense-cell]").count(), 0)
        self.menu("check", "#col-nondef")
        self.page.wait_for_function("location.search.includes('cols=nondefense')")
        for basis in BASES:
            with self.subTest(basis=basis):
                self.go(f"?sc=CJS&fy=2026&cols=defense,nondefense&amt={basis}&open=ACC-NSF-TOTAL")
                cells = self.page.eval_on_selector_all("[data-testid=nondefense-cell]",
                                                       "ts => ts.map(t => [t.closest('tr').dataset.account, t.innerText.trim(), t.title])")
                self.assertTrue(cells)
                for aid, text, title in cells:
                    row = next((r for r in self.cjs["rows"] if r["account"]["canonical_account_id"] == aid), None) \
                        or next(t["total"] for t in self.cjs["titles"] if t["total"]["account"]["canonical_account_id"] == aid)
                    e = view_entry_of(row, 2026, "Enacted", basis, "available", "views_nondefense") \
                        if printed(row, 2026, "Enacted") is not None else None
                    if e and e["status"] in ("checked", "computed"):
                        self.assertEqual(text, thousands(e["amount"]), aid)
                    else:
                        self.assertEqual(text, "", aid)          # not computable: empty, never 0
                        if e:
                            self.assertIn(e["reason"], title, aid)
                pub = view_entry_of(rra, 2026, "Enacted", basis, "available", "views_nondefense")
                self.assertEqual(self.page.inner_text(f"tr[data-account={self.RRA}] [data-testid=nondefense-cell]"), thousands(pub["amount"]))
                # the nondefense column sits right after the defense portion
                cols = self.page.eval_on_selector(f"tr[data-account={self.RRA}]", "tr => ['defense-cell', 'nondefense-cell']"
                                                  ".map(t => Number(tr.querySelector(`[data-testid=${t}]`).dataset.col))")
                self.assertEqual(cols[1], cols[0] + 1)
        self.assertEqual(self.page.inner_text(f"tr[data-account={self.RRA}] [data-testid=nondefense-cell]"), "7,057,700")
        self.page.click(f"tr[data-account={self.RRA}] [data-testid=nondefense-cell] button.fig")
        self.assertEqual(self.page.get_attribute("#side-panel", "data-mode"), "source")
        # a not computable view (NSF's total: one of its accounts has a defense portion and no printed nondefense total
        # confirms it): empty, the reason on hover
        nsf = next(r for r in self.cjs["rows"] if r["account"]["canonical_account_id"] == "ACC-NSF-TOTAL")
        e = view_entry_of(nsf, 2026, "Enacted", "printed", None, "views_nondefense")
        self.assertEqual(e["status"], "not_computable")
        self.go("?sc=CJS&fy=2026&cols=nondefense&open=ACC-NSF-TOTAL")
        nd = self.page.locator("tr[data-account=ACC-NSF-TOTAL] [data-testid=nondefense-cell]")
        self.assertEqual(nd.inner_text().strip(), "")
        self.assertEqual(nd.get_attribute("data-view"), "not_computable")
        self.assertIn(e["reason"], nd.get_attribute("title"))

    def test_each_flag_hides_its_control(self):
        # each control is absent with its own flag forced false, the other still shown; a URL asking for it changes nothing
        for flag, gone, kept in (("exclude_earmarks", "#f-noear", "#col-nondef"), ("nondefense", "#col-nondef", "#f-noear")):
            for where, pattern in (("static", "**/data/index.json"), ("live", "**/api/features")):
                with self.subTest(flag=flag, where=where):
                    def off(route, request, flag=flag):
                        body = json.loads(route.fetch().text())
                        body["features"][flag] = False
                        route.fulfill(json=body)
                    self.page.route(pattern, off)
                    self.go("?sc=CJS&fy=2026&earmarks=excl&cols=nondefense&open=ACC-NSF-TOTAL", where)
                    self.page.click("#filter-btn"); self.page.click("#f-adv")
                    self.page.keyboard.press("Escape")
                    self.page.click("#view-btn")
                    self.assertEqual(self.page.locator(gone).count(), 0)
                    self.assertEqual(self.page.locator(kept).count(), 1)
                    self.page.keyboard.press("Escape")
                    if flag == "exclude_earmarks":
                        self.assertNotIn("excl. earmarks", self.page.inner_text("#grid-subline"))
                        self.assertEqual(self.page.locator("#chips [data-testid=chip]").count(), 0)
                    else:
                        self.assertEqual(self.page.locator("[data-testid=nondefense-cell]").count(), 0)
                    self.page.unroute(pattern)

    def test_url_round_trip(self):
        def snap():
            self.page.mouse.move(2, 2)
            return {"url": self.page.url.split("?", 1)[1], "grid": self.page.inner_html("#compare-grid"),
                    "panel": self.page.inner_html("#side-panel"), "chips": self.page.inner_html("#chips"),
                    "sub": self.page.inner_text("#grid-subline")}
        self.go(f"?sc=LHHS&fy=2026&open={self.OPEN}")
        self.advanced("check", "#f-noear")
        self.advanced("click", "#f-basis button[data-basis=program_level]")
        self.menu("check", "#col-nondef")
        self.cell(self.HSPS, 2026, "Enacted").locator("button.fig").click()
        before = snap()
        for p in ("earmarks=excl", "cols=nondefense", "amt=program_level", "fig="):
            self.assertIn(p, before["url"])
        self.page.click("#share")
        self.page.goto(self.page.get_attribute("#share", "data-copied"))
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
        self.assertEqual(snap(), before)
        self.assertEqual(self.page.locator("[data-testid=src-earmark]").count(), 1)
        self.assertTrue(self.page.evaluate("document.querySelector('[data-testid=advanced-summary]').textContent")
                        .startswith("Program level · advances when available · earmarks excluded"))
        self.page.click("[data-testid=clear-all]")
        self.assertNotIn("earmarks=", self.page.url)


class AccountPage(GridBrowser):
    """Brief UI-5: the account page (the One account view) in the SPEC.md visual language: the header, the enacted
    history, every stage of every year beside the source panel (SPEC 5.1), related accounts; the side panel's and the
    phone sheet's "Open account page" land on it with the account and fiscal year in the URL. Tests: NCI, Medicaid
    (advances), an account with a rename, a proposed-only account, an account whose start basis is "unidentified"."""

    NCI, MEDICAID, NIDA = "ACC-HHS-NIH-NCI", "ACC-HHS-CMS-MEDICAID", "ACC-HHS-NIH-NIDA"
    PROPOSED, UNIDENTIFIED = "ACC-HHS-NIH-BODY-SYSTEMS", "ACC-HHS-GP-MEDICARE-LIMITATION"
    STAGES = ["President's Budget", "House Reported", "Senate Reported", "Enacted"]

    def go(self, query, where="static", page=None):
        page = page or self.page
        page.goto(self.urls[where] + query)
        page.wait_for_selector("[data-testid=result]:not([hidden])")
        page.wait_for_selector("[data-testid=ap-table]")
        self.settle(page)

    def ap_cell(self, fy, stage):
        return self.page.locator(f"[data-testid=ap-table] td[data-fy='{fy}'][data-stage=\"{stage}\"]")

    def enacted_years(self):
        return [y for y in self.lhhs["fiscal_years"] if y >= 2017 and str(y) in self.lhhs["enactments"]]

    def test_nci_header_chart_and_every_stage(self):
        row = self.lrow(self.NCI)
        attrs = row["attributes"]["2026"]
        for where in self.urls:
            with self.subTest(where=where):
                self.go(f"?account={self.NCI}&fy=2026", where)
                self.assertEqual(self.page.text_content("[data-testid=account-name]"), "National Cancer Institute")
                self.assertEqual(self.page.text_content("[data-testid=ap-path]"),
                                 "Labor-HHS-Education · Title II · National Institutes of Health")
                # the class tag as the record gives it (budget_class), dashed when provisional; the Appendix account code
                # the record's class for the fiscal year in the URL, naming its year (owner, 2026-10-14)
                tag = self.page.locator("[data-testid=ap-class-tag]")
                self.assertEqual((tag.inner_text(), tag.get_attribute("data-provisional")),
                                 (f"{attrs['budget_class'].capitalize()} · FY2026" + (" (provisional)" if attrs["provisional"] else ""),
                                  str(bool(attrs["provisional"])).lower()))
                # the Appendix account code with its document's short name and page, never the document's ID
                code = self.page.inner_text("[data-testid=ap-appendix]")
                self.assertIn(attrs["account_id_code"], code)
                self.assertIn(f"FY2026 Budget Appendix, HHS, p. {attrs['citations']['account_id_code']['source_page']}", code)
                self.assertNotIn("SRC-", code)
                # "Every line on file" starts collapsed: one line, opening on click
                self.assertIsNone(self.page.get_attribute("[data-testid=ap-lines]", "open"))
                self.assertFalse(self.page.is_visible("#grid"))
                self.page.click("[data-testid=ap-lines-toggle]")
                self.assertTrue(self.page.is_visible("#grid"))
                self.assertEqual(self.page.text_content("[data-testid=figures-on-file]"), "Figures on file: FY2017–FY2027")
                # Watch is UI-6: hidden
                self.assertNotIn("Watch", self.page.inner_text("[data-testid=ap-actions]"))
                # the chart: FY2017 onward, one bar per enacted year, values only; the FY2025 CR bar lighter
                bars = self.page.locator("[data-testid=ap-bar]")
                years = self.enacted_years()
                self.assertEqual([int(b.get_attribute("data-fy")) for b in bars.all()], years)
                for y in years:
                    want = expected_headline(row, y, "Enacted")
                    b = self.page.locator(f"[data-testid=ap-bar][data-fy='{y}']")
                    v = b.get_attribute("data-value")
                    self.assertEqual(v != "", want["state"] == "value", y)
                    if v:
                        self.assertEqual(thousands(int(v)), want["amount"], y)
                    self.assertEqual(b.locator(".apbar").count(), 1 if v and int(v) > 0 else 0, y)
                self.assertEqual(self.page.locator("[data-testid=ap-bar] .apbar.cr").count(), 1)
                self.assertEqual(self.page.locator("[data-testid=ap-bar][data-fy='2025'] .apbar.cr").count(), 1)
                # hover: the figure and its source
                self.page.hover("[data-testid=ap-bar][data-fy='2026']")
                tip = self.page.inner_text("[data-testid=ap-tip]")
                o = row["cells"]["2026|Enacted"]["lines"][0]["observations"][0]
                self.assertIn(thousands(o["amount"]), tip)
                self.assertIn(f"p. {o['source_page']}", tip)
                # the sentence: the change since the first year on file, both ends values
                a, b2 = printed(row, years[0], "Enacted"), printed(row, years[-1], "Enacted")
                d = b2 - a
                self.assertEqual(self.page.inner_text("[data-testid=ap-sentence-change]"),
                                 f"+{thousands(d)} (+{d / a * 100:.1f}%)")
                self.assertIn(f"since FY{years[0]}, the first year on file", self.page.inner_text("[data-testid=ap-sentence]"))
                # every stage, every year: newest first; each cell its headline state or figure; enacted vs request
                rows = self.page.locator("[data-testid=ap-row]")
                self.assertEqual([int(r.get_attribute("data-fy")) for r in rows.all()], sorted(self.lhhs["fiscal_years"], reverse=True))
                for y in self.lhhs["fiscal_years"]:
                    for st in self.STAGES:
                        want = expected_headline(row, y, st)
                        td = self.ap_cell(y, st)
                        self.assertEqual(td.get_attribute("data-state"), want["state"], (y, st))
                        amt = td.locator("[data-testid=ap-amount]")
                        self.assertEqual(amt.inner_text() if amt.count() else None, want["amount"], (y, st))
                        if want["state"] == "value":
                            self.assertEqual(amt.get_attribute("aria-label"), f"Source for National Cancer Institute, {stage_words(y, st, self.lhhs)}")
                    ch = self.page.locator(f"[data-testid=ap-row][data-fy='{y}'] [data-testid=ap-change]")
                    p0, p3 = printed(row, y, self.STAGES[0]), printed(row, y, "Enacted")
                    self.assertEqual(ch.get_attribute("data-delta"), None if p0 is None or p3 is None else str(p3 - p0), y)
                self.assertEqual(self.ap_cell(2027, "Senate Reported").inner_text().strip(), "n/c")
                self.assertEqual(self.ap_cell(2027, "Enacted").inner_text().strip(), "n/e")
                # the fiscal year in the URL is marked (the selection row fill)
                self.assertEqual(self.page.get_attribute("[data-testid=ap-row][data-fy='2026']", "data-sel"), "row")

    def test_figure_opens_source_and_esc_returns_focus(self):
        for where in self.urls:
            with self.subTest(where=where):
                self.go(f"?account={self.NCI}&fy=2026", where)
                self.assertEqual(self.page.get_attribute("[data-testid=ap-panel]", "data-mode"), "hint")
                btn = self.ap_cell(2026, "Enacted").locator("button.fig")
                btn.click()
                panel = self.page.locator("[data-testid=ap-panel]")
                self.assertEqual(panel.get_attribute("data-mode"), "source")
                # the panel is SPEC 5.1's (UI-2b layout): full dollars, page, Printed as, the bill, the record, the button
                self.assertEqual(panel.locator("[data-testid=src-value]").inner_text(), "$7,352,159,000")
                self.assertEqual(panel.locator("[data-testid=src-page]").inner_text(), "Page 285")
                self.assertTrue(panel.locator("[data-testid=src-printed]").inner_text())
                self.assertIn("P.L. 119-75", panel.locator("[data-testid=src-bill]").inner_text())
                self.assertEqual(panel.locator("[data-testid=src-record]").inner_text(), "Record OBS-LHHS-0329")
                self.assertTrue(panel.locator("[data-testid=src-open]").get_attribute("href").endswith("#page=285"))
                self.assertEqual(panel.locator("[data-testid=src-account-btn]").count(), 0)      # this is the account page
                self.assertEqual(self.ap_cell(2026, "Enacted").get_attribute("data-sel"), "fig")
                self.assertEqual(self.page.get_attribute("[data-testid=ap-bar][data-fy='2026']", "data-sel"), "fig")
                self.assertTrue(self.page.url.endswith(f"?account={self.NCI}&fy=2026&stage=Enacted"), self.page.url)
                # selection beats hover: the yellow fill stays while hovered; the crosshair tints the row, column, header
                self.ap_cell(2024, "House Reported").hover()
                bg = lambda loc: loc.evaluate("e => getComputedStyle(e).backgroundColor")
                self.assertEqual(bg(self.ap_cell(2024, "Senate Reported")), "rgb(236, 241, 249)")      # row
                self.assertEqual(bg(self.ap_cell(2022, "House Reported")), "rgb(241, 245, 251)")       # column
                self.assertEqual(bg(self.ap_cell(2024, "House Reported")), "rgb(214, 226, 245)")       # crossing
                self.assertEqual(bg(self.page.locator("[data-testid=ap-table] th[data-col='1']")), "rgb(223, 232, 246)")
                self.ap_cell(2026, "Enacted").hover()
                self.assertEqual(bg(self.ap_cell(2026, "Enacted")), "rgb(255, 241, 194)")
                self.page.mouse.move(5, 5)
                self.assertEqual(self.page.locator("[data-testid=ap-table] [data-hov], [data-testid=ap-table] [data-hovcol]").count(), 0)
                # a bar opens its figure too; the class tag follows the selected figure's fiscal year
                self.page.click("[data-testid=ap-bar][data-fy='2024']")
                self.assertEqual(self.ap_cell(2024, "Enacted").get_attribute("data-sel"), "fig")
                k24 = self.lrow(self.NCI)["attributes"]["2024"]
                self.assertEqual(self.page.inner_text("[data-testid=ap-class-tag]"),
                                 f"{k24['budget_class'].capitalize()} · FY2024" + (" (provisional)" if k24["provisional"] else ""))
                # a click outside the table, the chart and the panel closes the panel (SPEC 5.3, as in Explore);
                # a click inside the table does not
                self.page.click("[data-testid=ap-stages] h2")
                self.assertEqual(panel.get_attribute("data-mode"), "source")
                self.page.mouse.click(1430, 300)
                self.assertEqual(panel.get_attribute("data-mode"), "hint")
                self.assertEqual(self.page.locator("[data-testid=ap-table] td[data-sel=fig]").count(), 0)
                self.assertTrue(self.page.url.endswith(f"?account={self.NCI}&fy=2024"), self.page.url)
                # Esc clears the selection and returns focus to the figure; the URL keeps the year
                btn.click()
                self.page.keyboard.press("Escape")
                self.assertEqual(panel.get_attribute("data-mode"), "hint")
                self.assertEqual(self.page.evaluate("document.activeElement.getAttribute('aria-label')"),
                                 "Source for National Cancer Institute, FY2026 enacted")
                self.assertTrue(self.page.url.endswith(f"?account={self.NCI}&fy=2026"), self.page.url)
                # the URL round-trips the selected figure
                self.go(f"?account={self.NCI}&fy=2023&stage=House%20Reported", where)
                self.assertEqual(self.ap_cell(2023, "House Reported").get_attribute("data-sel"), "fig")
                self.assertEqual(panel.locator("[data-testid=src-stage]").inner_text(), "FY2023 House")

    def test_copy_citation_and_export_csv(self):
        row = self.lrow(self.NCI)
        self.go(f"?account={self.NCI}")
        self.page.click("[data-testid=ap-copy-citation]")
        cite = self.page.get_attribute("[data-testid=ap-copy-citation]", "data-copied")
        self.assertTrue(cite.startswith("National Cancer Institute (ACC-HHS-NIH-NCI). Labor-HHS-Education · Title II · National Institutes of Health."), cite)
        self.assertIn("?account=ACC-HHS-NIH-NCI", cite)
        with self.page.expect_download() as dl:
            self.page.click("[data-testid=ap-export]")
        d = dl.value
        self.assertEqual(d.suggested_filename, "ACC-HHS-NIH-NCI-history.csv")
        with open(d.path(), encoding="utf-8") as f:
            got = list(csv.DictReader(f))
        # one row per line, year and stage; a figure only where printed, never a filled-in zero
        want = sum(1 if c.get("outside_life") else sum(max(1, len(l["observations"])) for l in c["lines"]) for c in row["cells"].values())
        self.assertEqual(len(got), want)
        for r in got:
            want_amt = printed(row, int(r["fiscal_year"]), r["stage"])
            if r["line"] == "budget authority":
                self.assertEqual(r["amount_dollars"], "" if want_amt is None else str(want_amt), r)
            if r["state"] not in ("value", "not_funded"):
                self.assertEqual(r["amount_dollars"], "", r)
        nci26 = next(r for r in got if r["fiscal_year"] == "2026" and r["stage"] == "Enacted")
        self.assertEqual((nci26["amount_dollars"], nci26["source_page"], nci26["observation_id"]), ("7352159000", "285", "OBS-LHHS-0329"))

    def test_medicaid_advances(self):
        row = self.lrow(self.MEDICAID)
        for where in self.urls:
            with self.subTest(where=where):
                self.go(f"?account={self.MEDICAID}&fy=2026", where)
                # the table shows the headline line; the advance lines are listed under Related, each to its source, never added
                self.assertEqual(self.ap_cell(2026, "Enacted").locator("[data-testid=ap-amount]").inner_text(),
                                 expected_headline(row, 2026, "Enacted")["amount"])
                lines = self.page.locator("[data-testid=ap-other-line]")
                series = [l.get_attribute("data-series") for l in lines.all()]
                self.assertIn("advance", series)
                self.assertIn("prior_year_advance", series)
                adv = next(l for l in row["cells"]["2026|Enacted"]["lines"] if l["amount_type"] == "advance")["observations"][0]
                link = self.page.locator("[data-testid=ap-other-line][data-series=advance] td[data-stage=Enacted] a")
                self.assertEqual(link.inner_text(), thousands(adv["amount"]))
                self.assertTrue(link.get_attribute("href").startswith("http"))
                self.assertIn("never added", self.page.inner_text("[data-testid=ap-other-lines]"))
                # the source panel lists the other lines too, without the "Open account page" button (this is that page)
                self.ap_cell(2026, "Enacted").locator("button.fig").click()
                notes = self.page.inner_text("[data-testid=ap-panel] [data-testid=src-notes]")
                self.assertIn("advance", notes)
                self.assertEqual(self.page.locator("[data-testid=ap-panel] [data-testid=src-note-more]").count(), 0)
                # Medicaid's class, as its record gives it for FY2026
                k = row["attributes"]["2026"]
                self.assertEqual(self.page.inner_text("[data-testid=ap-class-tag]"),
                                 f"{k['budget_class'].capitalize()} · FY2026" + (" (provisional)" if k["provisional"] else ""))

    def test_renamed_account_former_names(self):
        row = self.lrow(self.NIDA)
        self.go(f"?account={self.NIDA}")
        self.assertTrue(row["historical_names"])
        former = self.page.inner_text("[data-testid=ap-former-names]")
        for n in row["historical_names"]:
            self.assertIn(f"“{n}”", former)
        self.assertEqual(self.page.text_content("[data-testid=account-name]"), "National Institute on Drug Abuse")
        # no fiscal year selected: the class of the latest year on file
        latest = max(int(y) for y, a in row["attributes"].items() if a.get("budget_class") or (a.get("request") or {}).get("budget_class"))
        self.assertEqual(self.page.get_attribute("[data-testid=ap-class-tag]", "data-fy"), str(latest))
        # its parent total links to that account's page, the year kept
        parent = self.page.locator("[data-testid=ap-parent]")
        self.assertEqual(parent.get_attribute("data-account"), "ACC-HHS-NIH-TOTAL")
        parent.click()
        self.page.wait_for_function("document.querySelector('[data-testid=account-name]').textContent.includes('National Institutes of Health')")
        self.assertIn("?account=ACC-HHS-NIH-TOTAL&fy=", self.page.url)

    def test_proposed_only_account(self):
        row = self.lrow(self.PROPOSED)
        self.assertEqual(row["lifecycle"]["type"], "proposed_only")
        for where in self.urls:
            with self.subTest(where=where):
                self.go(f"?account={self.PROPOSED}", where)
                self.assertEqual(self.page.inner_text("[data-testid=ap-tag-proposed]"), "proposed")
                # never enacted: no bars, no change stated; outside its life the cells are blank and not clickable
                self.assertEqual(self.page.locator("[data-testid=ap-bar] .apbar").count(), 0)
                self.assertEqual(self.page.locator("button[data-testid=ap-bar]").count(), 0)
                self.assertNotIn("since FY", self.page.inner_text("[data-testid=ap-sentence]"))
                for td in self.page.locator("[data-testid=ap-cell][data-outside=true]").all():
                    self.assertEqual(td.inner_text().strip(), "")
                    self.assertEqual(td.locator("button").count(), 0)
                for y in self.lhhs["fiscal_years"]:
                    want = expected_headline(row, y, "President's Budget")
                    if want["state"] == "value":
                        self.assertEqual(self.ap_cell(y, "President's Budget").locator("[data-testid=ap-amount]").inner_text(), want["amount"])
                rels = self.page.inner_text("[data-testid=ap-relationships]")
                self.assertTrue(rels.strip())

    def test_class_tag_names_its_source(self):
        # owner, 2026-10-14: an enacted-basis record reads "<Class> · FY<n>", "(provisional)" where the record marks it;
        # a request-stage record is the request's own class: "<Class> · FY<n> request", never provisional
        nci = self.lrow(self.NCI)["attributes"]
        cases = [(self.NCI, 2026, nci["2026"]), (self.NCI, 2024, nci["2024"])]
        for aid, fy in (("ACC-HHS-NIH-BODY-SYSTEMS", 2026), ("ACC-HHS-NIH-SUBSTANCE-USE", 2027)):
            cases.append((aid, fy, self.lrow(aid)["attributes"][str(fy)]))
        self.assertTrue(nci["2026"]["provisional"])
        self.assertFalse(nci["2024"]["provisional"])
        for where in self.urls:
            for aid, fy, a in cases:
                with self.subTest(where=where, account=aid, fy=fy):
                    self.go(f"?account={aid}&fy={fy}", where)
                    tag = self.page.locator("[data-testid=ap-class-tag]")
                    dashed = tag.evaluate("e => getComputedStyle(e).outlineStyle")
                    if a.get("budget_class"):                  # the enacted-basis record
                        prov = bool(a["provisional"])
                        self.assertEqual(tag.inner_text(), f"{a['budget_class'].capitalize()} · FY{fy}" + (" (provisional)" if prov else ""))
                        self.assertEqual((tag.get_attribute("data-basis"), tag.get_attribute("data-provisional")), ("enacted", str(prov).lower()))
                        self.assertEqual(dashed, "dashed" if prov else "none")
                    else:                                      # only the request's own record
                        r = a["request"]
                        self.assertEqual(r["stage_basis"], "request")
                        self.assertEqual(tag.inner_text(), f"{r['budget_class'].capitalize()} · FY{fy} request")
                        self.assertNotIn("provisional", tag.inner_text())
                        self.assertEqual((tag.get_attribute("data-basis"), tag.get_attribute("data-provisional")), ("request", "false"))
                        self.assertEqual(dashed, "none")

    def test_unidentified_start(self):
        row = self.lrow(self.UNIDENTIFIED)
        self.assertEqual(row["lifecycle"].get("start_basis"), "unidentified")
        for where in self.urls:
            with self.subTest(where=where):
                self.go(f"?account={self.UNIDENTIFIED}", where)
                self.assertIn("Start not identified", self.page.inner_text("[data-testid=ap-start]"))
                # no change since a first year is stated: the record's start is not known
                sentence = self.page.inner_text("[data-testid=ap-sentence]")
                self.assertIn("not identified", sentence)
                self.assertEqual(self.page.locator("[data-testid=ap-sentence-change]").count(), 0)
                # the years before its one figure show their state, never a blank or a zero
                for y in self.enacted_years():
                    want = expected_headline(row, y, "Enacted")
                    td = self.ap_cell(y, "Enacted")
                    if want["state"] != "value":
                        self.assertEqual(td.get_attribute("data-state"), want["state"], y)
                        self.assertNotIn("0", td.inner_text())
                        self.assertNotEqual(td.inner_text().strip(), "", y)
                self.assertIn("general-provision", self.page.inner_text("[data-testid=ap-parents]"))

    def test_open_account_page_lands_with_account_and_year(self):
        # the side panel's button
        self.page.goto(self.urls["static"] + f"?sc=LHHS&fy=2026&acct={self.NCI}")
        self.page.wait_for_selector("[data-testid=acct-open]")
        self.page.click("[data-testid=acct-open]")
        self.page.wait_for_selector("[data-testid=ap-table]")
        self.assertTrue(self.page.url.endswith(f"?account={self.NCI}&fy=2026"), self.page.url)
        self.assertEqual(self.page.get_attribute("[data-testid=ap-row][data-fy='2026']", "data-sel"), "row")
        # the phone sheet's button
        phone = self.browser.new_page(viewport={"width": 390, "height": 844})
        try:
            phone.goto(self.urls["static"] + f"?sc=LHHS&fy=2025&acct={self.NCI}")
            phone.wait_for_selector("#p-sheet [data-testid=acct-open]")
            phone.click("#p-sheet [data-testid=acct-open]")
            phone.wait_for_selector("[data-testid=ap-table]")
            self.assertTrue(phone.url.endswith(f"?account={self.NCI}&fy=2025"), phone.url)
            # the page fits the phone: no sideways page scroll
            self.assertLessEqual(phone.evaluate("document.documentElement.scrollWidth"), 390)
        finally:
            phone.close()

    def test_keyboard_and_focus(self):
        self.go(f"?account={self.NCI}&fy=2026")
        # every control on the page is a real button, link or input, reachable by Tab, with a visible focus ring
        seen = set()
        for _ in range(120):
            self.page.keyboard.press("Tab")
            t = self.page.evaluate("""() => { const e = document.activeElement;
                return e ? [e.dataset.testid || '', e.getAttribute('aria-label') || '', getComputedStyle(e).outlineStyle] : null; }""")
            if t[0] or t[1]:
                seen.update(x for x in t[:2] if x)
                if t[0] in ("ap-copy-citation", "ap-export", "ap-amount", "ap-bar"):
                    self.assertNotEqual(t[2], "none", t)
        for want in ("ap-copy-citation", "ap-export", "ap-bar", "Source for National Cancer Institute, FY2026 enacted"):
            self.assertIn(want, seen)
        # Enter on a focused figure opens its source; Esc returns focus to it
        self.ap_cell(2025, "Senate Reported").locator("button.fig").focus()
        self.page.keyboard.press("Enter")
        self.assertEqual(self.page.get_attribute("[data-testid=ap-panel]", "data-mode"), "source")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.evaluate("document.activeElement.getAttribute('aria-label')"),
                         "Source for National Cancer Institute, FY2025 Senate")


def stage_words(y, st, grid):
    """The page's stageText: "FY2026 enacted", "FY2025 enacted (full-year CR)", "FY2026 President’s request"."""
    if st == "Enacted":
        e = grid["enactments"].get(str(y))
        return f"FY{y} enacted" + (" (full-year CR)" if e and e["funding_type"] == "full_year_cr" else "")
    draft = " (draft)" if f"{y}|{st}" in (grid.get("drafts") or {}) else ""
    return f"FY{y} " + {"President's Budget": "President’s request", "House Reported": "House", "Senate Reported": "Senate"}[st] + draft


class GridMathUnits(unittest.TestCase):
    """The page's change arithmetic (web/index.html, between GRID-MATH-BEGIN and -END), run under node."""

    @classmethod
    def setUpClass(cls):
        import shutil as sh
        cls.node = sh.which("node")
        if not cls.node:
            raise unittest.SkipTest("node not installed")
        html = (ROOT / "web" / "index.html").read_text()
        cls.code = html.split("// GRID-MATH-BEGIN", 1)[1].split("\n", 1)[1].split("// GRID-MATH-END", 1)[0]

    def run_js(self, expr):
        import subprocess
        src = self.code + f"\nprocess.stdout.write(JSON.stringify({expr}));"
        return json.loads(subprocess.run([self.node, "-e", src], capture_output=True, text=True, check=True).stdout)

    def test_sign_and_percent(self):
        self.assertEqual(self.run_js("[GridMath.signedNumber(1234000, 'k'), GridMath.signedNumber(-1234000, 'k'), "
                                     "GridMath.signedNumber(0, 'k')]"), ["+1,234", "\u22121,234", "0"])
        c = self.run_js("GridMath.change(200000, 150000)")
        self.assertEqual(c, {"delta": -50000, "pct": -25})
        self.assertEqual(self.run_js("[GridMath.signedPct(-25), GridMath.signedPct(12.345), GridMath.signedPct(0), "
                                     "GridMath.signedPct(null)]"), ["\u221225.0%", "+12.3%", "0.0%", ""])
        self.assertEqual(self.run_js("[GridMath.direction(5), GridMath.direction(-5), GridMath.direction(0)]"),
                         ["up", "down", ""])

    def test_a_missing_figure_has_no_change(self):
        self.assertEqual(self.run_js("[GridMath.change(null, 5), GridMath.change(5, null)]"), [None, None])
        # a printed zero is a figure: the change is real; the percentage from a zero base isn't defined ("new")
        self.assertEqual(self.run_js("GridMath.change(0, 7000)"), {"delta": 7000, "pct": None})

    def test_pct_from_a_zero_base(self):
        self.assertEqual(self.run_js("[GridMath.pctText(0, 7000), GridMath.pctText(0, 0), GridMath.pctText(369000, 0), "
                                     "GridMath.pctText(null, 0), GridMath.pctText(200, 150)]"),
                         ["new", "\u2014", "\u2212100.0%", "", "\u221225.0%"])

    def test_units(self):
        self.assertEqual(self.run_js("[GridMath.number(47811518000, 'k'), GridMath.number(47811518000, 'm'), "
                                     "GridMath.number(-2500000, 'm'), GridMath.number(null, 'k')]"),
                         ["47,811,518", "47,811.5", "\u22122.5", ""])
        self.assertEqual(self.run_js("[GridMath.signedNumber(933878000, 'm'), GridMath.scale(1000, 'k')]"), ["+933.9", 1])


if __name__ == "__main__":
    unittest.main()
