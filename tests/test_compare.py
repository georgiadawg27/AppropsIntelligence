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
        return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": "",
                "amount": None, "outside": cell["outside_history"]}
    h = next((l for l in lines if l["amount_type"] == "budget authority" and not l["component"]), lines[0])
    return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": h["state"],
            "amount": thousands(h["observations"][0]["amount"]) if h["state"] == "value" else None,
            "outside": cell["outside_history"]}


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
                            self.assertEqual(cell, {"lines": by_year[y][st], "outside_history": False}, (aid, y, st))
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
class CompareBrowser(CompareTest):
    """The grid page: three views over the store's own cells. Every state and figure is the
    store's (subcommittee_grid); the page picks columns, lays rows out and works out changes."""

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

    def open(self, where="static", query="?view=compare&sc=CJS"):
        self.page.goto(self.urls[where] + query)
        self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")

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

    def expected(self, grid, pairs):
        rows = grid["rows"] + [t["total"] for t in grid["titles"] if t["total"]]
        return {(r["account"]["canonical_account_id"], y, st): expected_headline(r, y, st) for r in rows for y, st in pairs}

    # ---- the cells are the store's -------------------------------------------------------

    def test_every_headline_cell_is_the_stores(self):
        # Compare stages, each fiscal year: prior-year enacted, then the four stages, every account
        for fy in YEARS:
            with self.subTest(fy=fy):
                self.open("static", f"?view=compare&sc=CJS&grid=stages&fy={fy}")
                self.page.click("#expand-all")
                self.assertEqual(self.shown(), self.expected(self.grid, [(fy - 1, "Enacted")] + [(fy, st) for st in FOUR]))

    def test_enacted_history_and_two_years_show_the_same_cells(self):
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        self.page.click("#expand-all")
        # every enacted year on file (the backfill adds one each PR), then FY2027's request and House
        enacted = [y for y in self.lhhs["fiscal_years"] if y < 2027]
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, "Enacted") for y in enacted]
                                                     + [(2027, "President's Budget"), (2027, "House Reported")]))
        self.open("static", "?view=compare&sc=LHHS&grid=years&a=2024&b=2026")
        self.page.click("#expand-all")
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, st) for y in (2024, 2026) for st in FOUR]))
        # stage checkboxes narrow both years
        self.page.uncheck("[data-testid=stage-checks] input[data-stage='1']")
        self.page.click("#expand-all")
        self.assertEqual(self.shown(), self.expected(self.lhhs, [(y, st) for y in (2024, 2026) for st in FOUR if st != "House Reported"]))

    def test_grid_state_counts_are_unchanged(self):
        # the published grid's counts (tests/fixtures/grid_counts.json, scripts/grid_counts.py): the page shows them
        want = json.loads((ROOT / "tests" / "fixtures" / "grid_counts.json").read_text())["state_counts"]
        for sc, counts in want.items():
            for grid in ("stages", "years", "history"):
                self.open("static", f"?view=compare&sc={sc}&grid={grid}")
                got = {s.get_attribute("data-state"): int(s.inner_text().split()[0].replace(",", ""))
                       for s in self.page.locator("[data-testid=state-counts] [data-state]").all()}
                self.assertEqual(got, counts, (sc, grid))

    def test_static_renders_exactly_what_live_renders(self):
        for query in ("?view=compare&sc=CJS", "?view=compare&sc=LHHS&grid=years&a=2025&b=2026&units=m",
                      "?view=compare&sc=LHHS&grid=history&rev=1"):
            got = {}
            for where in self.urls:
                self.open(where, query)
                got[where] = self.page.inner_html("#tiles") + self.page.inner_html("#compare-grid")
            self.assertEqual(got["static"], got["live"], query)

    # ---- rows ----------------------------------------------------------------------------

    def body(self):
        return self.page.eval_on_selector_all("#compare-grid tbody tr", """trs => trs.map(t => ({
            cls: t.className, account: t.dataset.account || null, testid: t.dataset.testid,
            name: (t.querySelector('.namebox') || t.querySelector('td.acct')).firstChild.textContent,
            abbr: (t.querySelector('.abbr') || {}).textContent || null}))""")

    def test_row_order(self):
        self.open("static", "?view=compare&sc=LHHS")
        rows = self.body()
        # the printed title total first, with figures; no "Title II · 99 accounts" band
        self.assertEqual((rows[0]["cls"], rows[0]["account"], rows[0]["name"]),
                         ("title", "ACC-HHS-TITLE-II-TOTAL", "Total, Title II (printed)"))
        self.assertEqual(self.page.locator("tr.title td[data-testid=compare-cell]").count(), 5)
        self.assertNotIn("99 accounts", self.page.inner_text("#compare-grid"))
        # agency rows: the name and its abbreviation; no "(agency total)" and no rollup tag
        agencies = [r for r in rows if r["cls"] == "agency"]
        self.assertEqual(len(agencies), 11)
        self.assertEqual((agencies[0]["name"], agencies[0]["abbr"]), ("Health Resources and Services Administration", "HRSA"))
        self.assertEqual(agencies[-1]["account"], "ACC-HHS-AHA-TOTAL")              # the proposed agency last
        text = self.page.inner_text("#compare-grid")
        self.assertNotIn("(agency total)", text)
        self.assertNotIn("agency total · printed, not added", text)
        # NIH open (the agency with the most accounts), its 33 accounts beneath it
        nih = next(i for i, r in enumerate(rows) if r["account"] == "ACC-HHS-NIH-TOTAL")
        kids = [r["account"] for r in rows[nih + 1:nih + 34]]
        self.assertEqual(kids, [r["account"]["canonical_account_id"] for r in self.lhhs["rows"]
                                if r["member_of"] == "ACC-HHS-NIH-TOTAL"])
        self.assertEqual({r["cls"] for r in rows[nih + 1:nih + 34]}, {"child", "child last"})
        # general provisions under their own heading, after the agencies
        gp = next(i for i, r in enumerate(rows) if r["cls"] == "group")
        self.assertEqual(rows[gp]["name"], "General provisions (Title II)")
        self.assertEqual([r["name"] for r in rows[gp + 1:]], ["Medicare Operations", "Nonrecurring Expenses Fund, HHS (rescission)",
                                                            "Adoption Incentives (rescission)",
                                                            "Limitation for Title XVIII of the Social Security Act"])
        self.assertLess(rows.index(agencies[-1]), gp)

    def test_cjs_uses_the_same_design(self):
        self.open("static", "?view=compare&sc=CJS")
        rows = self.body()
        self.assertEqual([r["name"] for r in rows if r["cls"] == "title"],
                         ["Title I · no printed total on file", "Title II · no printed total on file",
                          "Title III · no printed total on file", "Title V · no printed total on file", "Not yet placed in a title"])
        self.assertEqual([(r["name"], r["abbr"]) for r in rows if r["cls"] == "agency"],
                         [("National Aeronautics and Space Administration", "NASA"), ("National Science Foundation", "NSF")])
        self.page.click("#expand-all")
        self.assertEqual(sorted(r["account"] for r in self.body() if r["testid"] == "compare-row"),
                         sorted(r["account"]["canonical_account_id"] for r in self.grid["rows"]))
        self.assertEqual(self.page.inner_text("#grid-title"), "Commerce, Justice, Science · Titles I, II, III, V")
        self.assertEqual(self.page.locator("[data-testid=tile]").count(), 4)

    def test_chevrons_only_on_agency_rows_that_fold(self):
        self.open("static", "?view=compare&sc=LHHS")
        with_chevron = {r.get_attribute("data-account") for r in self.page.locator("tr:has([data-testid=chevron])").all()}
        folds = {r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["rollup_members"]}
        self.assertEqual(with_chevron, folds | {"ACC-HHS-AHA-TOTAL"})
        for aid in ("ACC-HHS-AHRQ-TOTAL", "ACC-HHS-ACL-TOTAL"):
            self.assertEqual(self.page.locator(f"tr[data-account={aid}] .spacer").count(), 1, aid)
        self.assertEqual([c.get_attribute("data-for") for c in self.page.locator("[data-testid=chevron][aria-expanded=true]").all()],
                         ["ACC-HHS-NIH-TOTAL"])

    def test_nih_folds_over_its_institutes(self):
        self.open("static", "?view=compare&sc=LHHS")
        chev = lambda: self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=chevron]")
        members = self.page.locator("tr[data-member-of=ACC-HHS-NIH-TOTAL]")
        self.assertEqual(members.count(), len(self.lrow("ACC-HHS-NIH-TOTAL")["rollup_members"]))
        self.assertEqual((chev().get_attribute("aria-expanded"), chev().inner_text()), ("true", "▾"))
        chev().focus()
        self.page.keyboard.press("Enter")                           # keyboard-operable; focus stays on it
        self.assertEqual((chev().get_attribute("aria-expanded"), chev().inner_text()), ("false", "▸"))
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.for"), "ACC-HHS-NIH-TOTAL")
        self.assertEqual(members.count(), 0)
        self.page.keyboard.press("Enter")
        self.assertEqual(members.count(), 33)

    def test_aha_opens_to_its_seven_incoming_relationships(self):
        self.open("static", "?view=compare&sc=LHHS")
        aha = self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL]")
        self.assertEqual(aha.locator("[data-testid=tag]").inner_text(), "proposed agency · not enacted")
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
        self.open("static", "?view=compare&sc=LHHS")
        move = lambda aid: [m.inner_text() for m in self.page.locator(f"tr[data-account={aid}] [data-testid=move-note]").all()]
        self.assertEqual(move("ACC-HHS-NIH-NIEHS"), ["FY2026 request: moved to Administration for a Healthy America (proposed)"])
        self.assertEqual(move("ACC-HHS-NIH-NIDA"), ["FY2027 request: moved to National Institute of Substance Use and Addiction Research (proposed)"])
        self.page.click("#expand-all")
        self.assertEqual(move("ACC-HHS-HRSA-TOTAL"), [])                # a whole agency: only AHA's notes say so
        self.assertEqual(move("ACC-HHS-ASPR-RDP"), [])                  # into an account that is not proposed
        self.assertEqual(self.page.locator("tr[data-testid=move-note]").count(), 0)   # never a row of its own

    def test_tags(self):
        self.open("static", "?view=compare&sc=LHHS")
        self.page.click("#expand-all")
        tag = lambda aid: [t.inner_text() for t in self.page.locator(f"tr[data-account={aid}] [data-testid=tag]").all()]
        self.assertEqual(tag("ACC-HHS-NIH-SUBSTANCE-USE"), ["proposed in request"])
        self.assertEqual(tag("ACC-HHS-HRSA-HEALTH-CENTERS"), ["inside the line above · not added"])
        self.assertEqual(tag("ACC-HHS-NIH-NCI"), [])
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-HRSA-HEALTH-CENTERS] td.acct.lvl2").count(), 1)
        ids = self.page.eval_on_selector_all("#compare-grid tr[data-account]", "trs => trs.map(t => t.dataset.account)")
        self.assertEqual(ids[ids.index("ACC-HHS-HRSA-PRIMARY-CARE") + 1], "ACC-HHS-HRSA-HEALTH-CENTERS")
        self.assertNotIn("single appropriation heading", self.page.inner_text("#compare-grid").lower())

    def test_tiles_show_the_printed_title_total(self):
        total = next(t for t in self.lhhs["titles"] if t["title"] == "Title II")["total"]
        fig = lambda y, st: "$" + expected_headline(total, y, st)["amount"]
        heads = {"stages": ["Title II enacted, FY2026", "vs FY2025 enacted", "vs FY2026 request", "Senate vs House"],
                 "years": ["Title II enacted, FY2024", "Title II enacted, FY2026", "Change", "Stages shown"],
                 "history": ["Title II enacted, FY2026", "FY2027 request", "FY2027 House", "Accounts"]}
        for view, want in heads.items():
            self.open("static", f"?view=compare&sc=LHHS&grid={view}")
            tiles = [t.inner_text().split("\n") for t in self.page.locator("[data-testid=tile]").all()]
            self.assertEqual([t[0] for t in tiles], want, view)
            if view == "stages":
                self.assertEqual(tiles[0][1], fig(2026, "Enacted"))
            if view == "history":
                self.assertEqual((tiles[1][1], tiles[2][1]), (fig(2027, "President's Budget"), fig(2027, "House Reported")))
                self.assertEqual(tiles[3][1:], ["99", "11 agencies · 4 general provisions"])
        self.open("static", "?view=compare&sc=CJS")
        self.assertIn("no title or bill total printed", self.page.inner_text("[data-testid=tiles]"))   # never a sum

    def test_a_sourced_total_heads_the_grid_and_the_tiles(self):
        db = with_sourced_totals(self.db, Path(self.tmp.name) / "totals_browser.db")
        srv = W.serve(db, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS&grid=stages&fy=2024")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            tiles = [t.inner_text() for t in self.page.locator("[data-testid=tile]").all()]
            self.assertTrue(tiles[0].startswith("Title III enacted, FY2024\n$33,944,930"), tiles)
            row = self.page.locator("tr.title[data-account=ACC-T3-TOTAL]")
            self.assertEqual(row.locator("td.acct .namebox").evaluate("e => e.firstChild.textContent"), "Total, Title III (printed)")
        finally:
            srv.shutdown()
            srv.server_close()

    # ---- cells ---------------------------------------------------------------------------

    def test_a_number_links_to_its_source(self):
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2024")
        a = self.cell("ACC-NASA-SCIENCE", 2024, "Enacted").locator("[data-testid=amount]")
        o = next(l for l in self.row("ACC-NASA-SCIENCE")["cells"]["2024|Enacted"]["lines"]
                 if l["amount_type"] == "budget authority")["observations"][0]
        self.assertEqual(a.inner_text(), thousands(o["amount"]))
        self.assertEqual(a.get_attribute("data-source"), f"{o['source_document_id']} p.{o['source_page']}")
        self.assertEqual(a.get_attribute("href"), o["url_or_identifier"] + "#page=" + str(o["source_page"]).split("-")[0])
        # no permanent underline: hover only
        style = lambda: a.evaluate("e => getComputedStyle(e).textDecorationLine + ' ' + getComputedStyle(e).borderBottomColor")
        self.assertEqual(style(), "none rgba(0, 0, 0, 0)")
        a.hover()
        self.assertEqual(style(), "none rgb(31, 78, 140)")

    def test_tokens_carry_their_meaning(self):
        for state, text in (("not_enacted", "n/e"), ("missing", "?"), ("not_collected", "n/c"), ("not_funded", "—"),
                            ("no_printed_total", "no printed total")):
            # not yet collected: FY2027 Senate (the FY2024 House column now holds the subcommittee draft's figures)
            # no printed total: the AHA total's FY2026 cells (ASPR's FY2023 cells now read 'no figure')
            self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2027" if state == "not_enacted" else
                      "?view=compare&sc=LHHS&grid=years&a=2026&b=2027" if state in ("not_collected", "no_printed_total") else
                      "?view=compare&sc=LHHS&grid=years&a=2023&b=2024")
            self.page.click("#expand-all")
            tok = self.page.locator(f"td[data-state={state}] [data-testid=token-{state.replace('_', '-')}]").first
            self.assertEqual(tok.inner_text(), text, state)
            self.assertTrue(tok.get_attribute("aria-label") and tok.get_attribute("title"), state)
            m = tok.evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).borderTopStyle, getComputedStyle(e).color]")
            self.assertEqual(m[:2], ["12px", "none"], state)                     # muted 12px, no box
            self.assertEqual(m[2], "rgb(138, 75, 8)" if state == "missing" else "rgb(93, 100, 114)", state)
        self.assertIn("no printed total", self.page.inner_text("[data-testid=legend]"))

    def test_change_columns(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
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
        self.assertEqual(set(colors) - {"rgb(26, 115, 57)", "rgb(168, 70, 12)"}, set())
        # no prior-year figure, no change value: AHA has no FY2025 Enacted
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=change-cell]").first.inner_text(), "")
        heads = self.page.eval_on_selector_all("#compare-grid thead tr", "trs => trs.map(t => [...t.children].map(c => c.innerText))")
        self.assertEqual(heads, [["Account", "FY2025 (CR)", "FY2026", "", "Change · FY2026 enacted vs"],
                                 ["Enacted", "Request", "House", "Senate", "Enacted", "FY2025 enacted", "Request"]])

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
        self.open("static", "?view=compare&sc=LHHS&grid=years")
        heads = lambda: self.page.eval_on_selector_all("#compare-grid thead tr", "trs => trs.map(t => [...t.children].map(c => c.innerText))")
        # FY2024's House column is the subcommittee draft (Bill Report Reference draft = TRUE)
        self.assertEqual(heads(), [["Account", "FY2024", "FY2026", "", "Change · FY2026 vs FY2024"],
                                   ["Request", "House (draft)", "Senate", "Enacted", "Request", "House", "Senate", "Enacted", "Enacted"]])
        self.assertEqual(self.page.locator("#compare-grid thead th.yb").count(), 5)
        self.page.select_option("#basis", "0")
        self.assertEqual(heads()[1][-1], "Request")
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        enacted = [y for y in self.lhhs["fiscal_years"] if y < 2027]
        self.assertEqual(heads(), [["Account", "Enacted", "FY2027", "", "Change"],
                                   [f"FY{y}" + (" (CR)" if y == 2025 else "") for y in enacted]
                                   + ["Request", "House", f"FY{enacted[0] % 100} → FY26"]])
        self.assertTrue(self.page.is_hidden("#sub-controls"))

    def test_reviewer_mode_dots(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        self.page.click("#expand-all")
        self.assertEqual(self.page.locator("[data-testid=status-dot]").count(), 0)
        self.assertTrue(self.page.is_hidden("#rev-legend"))
        self.page.check("#reviewer")
        self.page.click("#expand-all")
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
        self.page.click("#expand-all")
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
        self.page.click("#expand-all")
        dots = self.page.locator("[data-testid=status-dot]")
        self.assertEqual(dots.count(), 1)                    # CJS: no flagged or unconfirmed cell in view
        d = dots.first
        self.assertEqual(d.get_attribute("class"), "dot pend")
        self.assertEqual(d.get_attribute("data-status"), "human-verified")
        self.assertEqual(d.get_attribute("data-pending"), "1")
        self.assertIn("VAL-0089 semantic flag", d.get_attribute("title"))
        self.assertEqual(self.page.text_content("[data-testid=pending-count]"),
                         "· 1 cell in view with records pending review")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026&rev=1")
        self.page.click("#expand-all")
        for d in self.page.locator("[data-testid=status-dot][data-status=flagged]").all():
            n = int(d.get_attribute("data-pending"))
            self.assertGreater(n, 0)                         # every flagged cell has an open record ...
            self.assertIn(f"{n} pending review: ", d.get_attribute("title"))   # ... listed by ID

    # ---- views, units, URL ---------------------------------------------------------------

    def test_view_years_and_units_live_in_the_url(self):
        self.open("static", "?view=compare&sc=LHHS")
        self.assertTrue(self.page.url.endswith("?view=compare&sc=LHHS&grid=stages&fy=2026"), self.page.url)
        self.page.click("#view-switch button[data-grid=years]")
        self.assertTrue(self.page.url.endswith("grid=years&a=2024&b=2026"), self.page.url)
        self.page.select_option("#fy-a", "2025")
        self.page.uncheck("[data-testid=stage-checks] input[data-stage='0']")
        self.page.select_option("#basis", "1")
        self.page.click("#units button[data-units=m]")
        self.assertTrue(self.page.url.endswith("grid=years&a=2025&b=2026&stages=0111&basis=house&units=m"), self.page.url)
        self.assertEqual(self.page.locator("#units button.on").inner_text(), "$ millions")
        self.assertIn("Budget authority in $ millions", self.page.inner_text("#grid-subline"))
        state = lambda: (self.page.inner_html("#tiles") + self.page.inner_html("#compare-grid"), self.page.url,
                         self.page.eval_on_selector_all("#sub-controls select, #sub-controls input",
                                                        "es => es.map(e => e.type === 'checkbox' ? e.checked : e.value)"))
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
        self.open("static", "?view=compare&sc=LHHS")
        self.page.click("#collapse-all")
        shown = [r.get_attribute("data-account") for r in self.page.locator("tr[data-testid=compare-row]").all()]
        self.assertEqual(sorted(shown), sorted([r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if not r["member_of"]]
                                               + ["ACC-HHS-TITLE-II-TOTAL"]))
        self.page.click("#expand-all")
        self.assertEqual(self.page.locator("tr[data-testid=compare-row]").count(), len(self.lhhs["rows"]) + 1)
        self.assertEqual({c.get_attribute("aria-expanded") for c in self.page.locator("[data-testid=chevron]").all()}, {"true"})
        self.assertEqual(self.page.locator("tr[data-testid=rel-note]").count(), 7)

    # ---- layout and style ----------------------------------------------------------------

    def test_layout(self):
        self.open("static", "?view=compare&sc=LHHS&grid=history")
        m = self.page.evaluate("""() => { const w = document.querySelector('#grid-wrap');
            const h2 = document.querySelector('#compare-grid thead tr:nth-child(2) th');
            const acct = document.querySelector('#compare-grid tbody td.acct');
            return {height: w.getBoundingClientRect().height, scrollable: w.scrollHeight > w.clientHeight,
                    overflow: getComputedStyle(w).overflow, head2: getComputedStyle(h2).top,
                    head1: document.querySelector('#compare-grid thead th.grp').getBoundingClientRect().height + 'px',
                    nameSticky: getComputedStyle(acct).position, acct: acct.getBoundingClientRect().width,
                    font: getComputedStyle(document.querySelector('#compare-grid td')).fontFamily,
                    size: getComputedStyle(document.querySelector('#compare-grid')).fontSize}; }""")
        self.assertEqual(m["height"], 900 - 120)                    # calc(100vh - 120px)
        self.assertTrue(m["scrollable"])
        self.assertEqual(m["overflow"], "auto")
        self.assertEqual((m["head2"], m["nameSticky"], m["acct"]), (m["head1"], "sticky", 360))   # right under the first row
        self.assertTrue(m["font"].startswith('"Public Sans"'))
        self.assertEqual(m["size"], "14px")
        self.page.set_viewport_size({"width": 1440, "height": 500})
        self.assertEqual(self.page.evaluate("document.querySelector('#grid-wrap').getBoundingClientRect().height"), 480)
        # page order: eyebrow, H1, subline (subcommittee select beside), tiles, toolbar, grid, legend, provenance
        order = self.page.evaluate("""() => ['.eyebrow', '#grid-title', '#grid-subline', '#sc', '#tiles', '[role=toolbar]',
            '#grid-wrap', '#grid-legend', '#grid-counts', '#grid-provenance'].map(s => document.querySelector(s))
            .map((e, i, a) => i === 0 || (a[i - 1].compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) > 0)""")
        self.assertEqual(set(order), {True})
        self.assertEqual(self.page.inner_text("#grid-title"), "Labor-HHS-Education · Title II, Department of Health and Human Services")
        self.assertEqual(self.page.inner_text("#grid-subline"),
                         "Budget authority in $ thousands (as printed in the committee tables) · every figure links to the page "
                         "it was printed on · data as of v38")
        self.assertIn("sha256", self.page.inner_text("#grid-provenance"))
        self.assertTrue(self.page.is_hidden("#freshness"))
        self.assertTrue(self.page.is_visible(".tabs"))
        self.assertEqual(self.page.locator("[data-testid=tile]").count(), 4)
        self.assertNotIn("pilot", self.page.inner_text("#grid-title").lower())

    def test_rules_zebra_and_hover(self):
        self.open("static", "?view=compare&sc=LHHS")
        css = lambda sel, prop: self.page.locator(sel).first.evaluate(f"e => getComputedStyle(e).{prop}")
        self.assertEqual(css("#compare-grid tbody td.num:not(.gs)", "borderLeftColor"), "rgb(230, 230, 225)")
        self.assertEqual(css("#compare-grid tbody td.gs", "borderLeftWidth"), "2px")
        self.assertEqual(css("#compare-grid tbody td.gs", "borderLeftColor"), "rgb(196, 198, 190)")
        self.assertEqual(css("#compare-grid tbody tr.child:nth-child(even) > td.num:not(.delta)", "backgroundColor"), "rgb(247, 247, 244)")
        self.assertEqual(css("#compare-grid tbody tr.child:nth-child(odd) > td.num:not(.delta)", "backgroundColor"), "rgba(0, 0, 0, 0)")   # the white box shows through
        self.page.mouse.move(0, 0)
        plain_row = self.page.locator("tr[data-account=ACC-HHS-NIH-NCI]")
        plain_row.hover()
        self.assertEqual({c.evaluate("e => getComputedStyle(e).backgroundColor") for c in plain_row.locator("> :not(.gap)").all()},
                         {"rgb(227, 236, 248)"})
        # hover doesn't paint the gap column, and keeps the guide line in the account column
        self.assertEqual(plain_row.locator("> td.gap").evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(246, 246, 243)")
        self.assertIn("linear-gradient", plain_row.locator("> td.acct").evaluate("e => getComputedStyle(e).backgroundImage"))
        for sel in ("tr[data-account=ACC-HHS-NIH-TOTAL]", "tr.title"):
            row = self.page.locator(sel)
            row.hover()
            self.assertEqual({c.evaluate("e => getComputedStyle(e).backgroundColor") for c in row.locator("> :not(.gap)").all()},
                             {"rgb(214, 227, 245)"}, sel)

    # ---- design reference v2: rollups, the change block set apart, notes as a corner mark ---

    def test_total_label_on_every_rollup_row_and_nowhere_else(self):
        for sc, grid in (("LHHS", self.lhhs), ("CJS", self.grid)):
            with self.subTest(sc=sc):
                self.open("static", f"?view=compare&sc={sc}")
                self.page.click("#expand-all")
                labelled = self.page.eval_on_selector_all("[data-testid=total]", "ts => ts.map(t => t.closest('tr').dataset.account)")
                # every rollup row except a proposed agency (its rows beneath are relationship notes, not accounts)
                rollups = [r["account"]["canonical_account_id"] for r in grid["rows"]
                           if r["rollup"] and r["account"]["status"] != "proposed"] + \
                          [t["total"]["account"]["canonical_account_id"] for t in grid["titles"] if t["total"]]
                self.assertEqual(sorted(labelled), sorted(rollups))
                for r in grid["rows"]:
                    if r["rollup"] and r["account"]["status"] == "proposed":
                        tags = self.page.locator(f"tr[data-account={r['account']['canonical_account_id']}] .tag")
                        self.assertEqual([t.inner_text() for t in tags.all()], ["proposed agency · not enacted"])
                self.assertEqual(self.page.locator("tr.child [data-testid=total], tr.group [data-testid=total]").count(), 0)
                # pinned to the name cell's top-right, never on a line of its own
                for t in self.page.locator("[data-testid=total]").all():
                    m = t.evaluate("""e => { const r = e.getBoundingClientRect(), td = e.closest('td').getBoundingClientRect(),
                        name = e.closest('.namebox').firstChild, nr = document.createRange(); nr.selectNodeContents(name);
                        const n = nr.getBoundingClientRect();
                        const bw = parseFloat(getComputedStyle(e.closest('td')).borderRightWidth);
                        return [getComputedStyle(e).position, e.innerText, Math.round(td.right - bw - r.right), r.left >= n.right]; }""")
                    self.assertEqual(m, ["absolute", "TOTAL", 10, True])
        self.assertIn("printed total of the accounts indented below it, not added again", self.page.inner_text("[data-testid=legend]"))
        self.assertNotIn("Agency rows are the printed agency totals", self.page.inner_text("[data-testid=legend]"))

    def test_rollup_rows_and_their_groups(self):
        self.open("static", "?view=compare&sc=LHHS")
        bg = lambda sel: self.page.locator(sel).first.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).fontWeight]")
        self.page.mouse.move(0, 0)
        self.assertEqual(bg("tr.agency > td.num"), ["rgb(239, 240, 234)", "600"])
        self.assertEqual(bg("tr.title > td.num"), ["rgb(230, 231, 225)", "700"])
        self.assertEqual(self.page.locator("tr.title > td.num").first.evaluate(
            "e => getComputedStyle(e).borderBottomWidth + ' ' + getComputedStyle(e).borderBottomColor"), "2px rgb(154, 159, 148)")
        self.assertEqual(self.page.locator("tr.agency > td.num").first.evaluate(
            "e => getComputedStyle(e).borderTopWidth + ' ' + getComputedStyle(e).borderTopColor"), "1px rgb(185, 188, 178)")
        # every row inside an open total: the guide line; the group's last row: the closing rule
        nih = [r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["member_of"] == "ACC-HHS-NIH-TOTAL"]
        guides = self.page.eval_on_selector_all("tr[data-member-of=ACC-HHS-NIH-TOTAL] > td.acct", "ts => ts.map(t => getComputedStyle(t).backgroundImage)")
        self.assertEqual(len(guides), len(nih))
        self.assertTrue(all("linear-gradient(rgb(196, 198, 190)" in g for g in guides), set(guides))
        last = self.page.locator("tr.last")
        self.assertEqual(last.count(), 1)
        self.assertEqual(last.get_attribute("data-account"), nih[-1])
        self.assertEqual(last.locator("> td.num").first.evaluate(
            "e => getComputedStyle(e).borderBottomWidth + ' ' + getComputedStyle(e).borderBottomColor"), "1px rgb(185, 188, 178)")
        # AHA's group closes on its last relationship note
        self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=chevron]").click()
        self.assertEqual(self.page.locator("tr.last").count(), 2)
        self.assertEqual(self.page.locator("tr[data-testid=rel-note]").last.get_attribute("class"), "note last")

    def test_a_full_year_cr_year_is_labeled_in_every_view(self):
        # from the data (Bill Report Reference funding_type, v37), never hard-coded: FY2025 in both subcommittees
        for sc, grid in (("LHHS", self.lhhs), ("CJS", self.grid)):
            crs = {y: e for y, e in (grid.get("enactments") or {}).items() if e.get("funding_type") == "full_year_cr"}
            self.assertEqual(sorted(crs), ["2025"], sc)
            e = crs["2025"]
            title = (f"Funded by a full-year continuing resolution ({e['bill_id'].replace('P.L.', 'P.L. ')}, "
                     f"enacted {e['enactment_date']})")
            self.assertEqual(title, "Funded by a full-year continuing resolution (P.L. 119-4, enacted 2025-03-15)")
            for q, text in (("stages&fy=2026", "FY2025 (CR)"), ("stages&fy=2025", "Enacted (CR)"),
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
        for q in ("stages", "years", "history"):
            with self.subTest(view=q):
                self.open("static", f"?view=compare&sc=LHHS&grid={q}")
                self.page.click("#expand-all")
                self.page.mouse.move(0, 0)
                gap = self.page.locator("#compare-grid thead th.gap")
                self.assertEqual(gap.count(), 1)
                self.assertEqual(gap.evaluate("""e => { const s = getComputedStyle(e);
                    return [e.getBoundingClientRect().width, s.backgroundColor, s.borderLeftColor, s.borderTopWidth]; }"""),
                                 [14, "rgb(246, 246, 243)", "rgb(201, 203, 196)", "0px"])
                head = self.page.locator("#compare-grid thead th.grp.delta").inner_text()
                self.assertTrue(head.startswith("Change"), head)
                self.assertNotIn("Change · Change", self.page.inner_text("#compare-grid thead"))
                # every figure row: one gap cell, right before its first change cell, unpainted by stripes
                bad = self.page.eval_on_selector_all("#compare-grid tbody tr[data-testid=compare-row]", """trs => trs.filter(tr => {
                    const gaps = tr.querySelectorAll(':scope > td.gap'), first = tr.querySelector(':scope > [data-testid=change-cell]');
                    return gaps.length !== 1 || gaps[0].nextElementSibling !== first
                        || getComputedStyle(gaps[0]).backgroundColor !== 'rgb(246, 246, 243)'; }).map(tr => tr.dataset.account)""")
                self.assertEqual(bad, [])
                # no tint on change cells or headers: the same white / zebra / rollup grey as the figures
                allowed = {"rgba(0, 0, 0, 0)", "rgb(255, 255, 255)", "rgb(247, 247, 244)", "rgb(239, 240, 234)", "rgb(230, 231, 225)"}
                seen = set(self.page.eval_on_selector_all("#compare-grid td.delta, #compare-grid th.delta",
                                                          "es => es.map(e => getComputedStyle(e).backgroundColor)"))
                self.assertEqual(seen - allowed, set())
                same = self.page.eval_on_selector_all("#compare-grid tbody tr[data-testid=compare-row]", """trs => trs.filter(tr => {
                    const f = tr.querySelector(':scope > td.num:not(.delta):not(.yb)'), d = tr.querySelector(':scope > td.delta');
                    return f && d && getComputedStyle(f).backgroundColor !== getComputedStyle(d).backgroundColor; }).length""")
                self.assertEqual(same, 0)
                self.assertEqual(self.page.locator("#compare-grid td.delta").first.evaluate("e => getComputedStyle(e).fontSize"), "13px")

    def test_notes_are_corner_marks_in_all(self):
        # the cells with other lines on file, both subcommittees (title totals included): 208, + 6 in the
        # FY2024 House draft's column (the ACF, ACL, CDC, NIH and OS totals and Medicaid: program-level and
        # advance lines beside the headline); + 32 with the FY2022 column and FY2023 Medicaid's views; + 15 with the
        # owner's FY2022 decisions (earmark, Kids First and Diaper Grants lines); + 35 with the FY2021 column
        total = sum(1 for g in (self.lhhs, self.grid) for r in g["rows"] + [t["total"] for t in g["titles"] if t["total"]]
                    for k in r["cells"] if other_lines(r, *k.split("|")))
        self.assertGreater(total, 0)
        for sc in ("LHHS", "CJS"):
            self.open("static", f"?view=compare&sc={sc}&grid=history")
            self.page.click("#expand-all")
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
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2023")
        self.page.click("#expand-all")
        notes = self.page.locator("#compare-grid [data-testid=cell-note]")
        # the view's other notes: Refugee FY2022 Enacted (the prior-year column), ASPR's FY2023 cells (funded within
        # PHSSEF) and ONC's FY2022 Enacted (the PHS evaluation set-aside)
        titles = notes.evaluate_all("ns => ns.map(n => n.title)")
        self.assertEqual(sum(1 for x in titles if x.startswith("Includes $2,500,000,000 from Division N")), 1)
        self.assertTrue(all(x.startswith(("Includes $", "Funded within PHSSEF", "Funded through the PHS evaluation"))
                            for x in titles), titles)
        n = self.cell("ACC-HHS-ACF-LIHEAP", 2023, "Enacted").locator("[data-testid=cell-note]")
        self.assertEqual(n.count(), 1)
        self.assertEqual((n.get_attribute("title"), n.get_attribute("aria-label")), (note, note))

    def test_the_cell_note_on_refugee_fy2022_enacted(self):
        # continuing-resolution funding printed within the FY2022 Enacted figure (H.Rept. 117-403 p.825):
        # the other-law note names the two CRs (VAL-LHHS-08860, OBS-LHHS-2113)
        note = ("Includes $1,600,000,000 ('CR Funding - P.L 117-70') and $2,500,000,000 ('CR Funding - P.L. 117-43 "
                "(emergency)'), continuing-resolution funding printed within this figure (SRC-CRPT-117HRPT403 p.825).")
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2022")
        self.page.click("#expand-all")
        n = self.cell("ACC-HHS-ACF-REFUGEE", 2022, "Enacted").locator("[data-testid=cell-note]")
        self.assertEqual(n.get_attribute("title"), note)

    def test_the_one_cell_note_global_health_fy2024_house(self):
        # a pending cross_document flag against bill text shows as a note in the cell's corner:
        # VAL-LHHS-07178 on OBS-LHHS-1757 (the FY2024 view's FY2023 Enacted column may carry LIHEAP's note)
        note = ("H.R. 5894 bill text prints $370,772,000; the explanatory materials' table "
                "(and its change column) print 370,722 thousand.")
        for sc in ("CJS", "LHHS"):
            self.open("static", f"?view=compare&sc={sc}&grid=stages&fy=2024")
            self.page.click("#expand-all")
            notes = self.page.locator("#compare-grid [data-testid=cell-note]")
            titles = notes.evaluate_all("ns => ns.map(n => n.title)")
            self.assertEqual(sum(1 for x in titles if x.startswith("H.R. 5894")), 1 if sc == "LHHS" else 0, sc)
            self.assertTrue(all(x.startswith(("H.R. 5894", "Includes $2,500,000,000", "Funded within PHSSEF"))
                                for x in titles), titles)
        cell = self.cell("ACC-HHS-CDC-GLOBAL-HEALTH", 2024, "House Reported")
        n = cell.locator("[data-testid=cell-note]")
        self.assertEqual(n.count(), 1)
        self.assertEqual(n.get_attribute("title"), note)
        self.assertEqual(n.get_attribute("aria-label"), note)
        self.assertIn("370,722", cell.inner_text())             # the figure kept

    def test_account_name_opens_the_single_account_view(self):
        self.open("live")
        self.page.click("tr[data-account=ACC-NASA-SPACEOPS] [data-testid=row-name]")
        self.page.wait_for_selector("[data-testid=result]:not([hidden])")
        self.assertTrue(self.page.is_hidden("[data-testid=compare-view]"))
        self.assertEqual(self.page.inner_text("[data-testid=account-name]"), "Space Operations")
        self.assertTrue(self.page.url.endswith("?account=ACC-NASA-SPACEOPS"))

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
                      "history": [(y, "Enacted") for y in (2021, 2022, 2023, 2024, 2025, 2026)] + [(2027, "President's Budget"), (2027, "House Reported")],
                      "years&a=2024&b=2026": [(y, st) for y in (2024, 2026) for st in FOUR]}
        cases = [("LHHS", q, p, self.lhhs) for q, p in lhhs_pairs.items()]
        cases.append(("CJS", "stages&fy=2024", [(2023, "Enacted")] + [(2024, st) for st in FOUR], self.grid))
        for sc, q, pairs, grid in cases:
            with self.subTest(sc=sc, view=q):
                self.open("static", f"?view=compare&sc={sc}&grid={q}")
                self.page.click("#expand-all")
                rows = grid["rows"] + [t["total"] for t in grid["titles"] if t["total"]]
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
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
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
        legend = self.page.inner_text("[data-testid=legend]")
        self.assertIn("note on this figure: hover to read, click to open the account", legend)
        self.assertNotIn("other lines on file for this cell", legend)
        self.open("static", "?view=compare&sc=CJS&grid=stages&fy=2024")
        self.page.click("#expand-all")
        cell = self.cell("ACC-NASA-EXPLORATION", 2024, "Enacted")
        m = cell.locator("[data-testid=more]")
        self.assertEqual((m.get_attribute("data-lines"), m.get_attribute("title")), ("1", "1 other line on file, $ thousands:\nsupplemental: 450,000"))
        # the headline number still links to its source; the marker opens the account at this year and stage
        self.assertTrue(cell.locator("[data-testid=amount]").get_attribute("href").startswith("http"))
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
            self.page.click("#expand-all")
            self.page.eval_on_selector("#grid-wrap", "w => { w.scrollTop = 700; }")
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

    def test_tiles_fit_at_390px(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        for sc in ("LHHS", "CJS"):
            for q in ("stages", "years", "history"):
                self.open("static", f"?view=compare&sc={sc}&grid={q}")
                over = self.page.eval_on_selector_all("[data-testid=tile]", """ts => ts.flatMap(t => [...t.children].filter(c =>
                    c.scrollWidth > c.clientWidth + 0.5 || c.getBoundingClientRect().right > t.getBoundingClientRect().right + 0.5)
                    .map(c => t.innerText))""")
                self.assertEqual(over, [], (sc, q))
                self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"), (sc, q))


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
        # a printed zero is a figure: the change is real, the percentage isn't defined
        self.assertEqual(self.run_js("GridMath.change(0, 7000)"), {"delta": 7000, "pct": None})

    def test_units(self):
        self.assertEqual(self.run_js("[GridMath.number(47811518000, 'k'), GridMath.number(47811518000, 'm'), "
                                     "GridMath.number(-2500000, 'm'), GridMath.number(null, 'k')]"),
                         ["47,811,518", "47,811.5", "\u22122.5", ""])
        self.assertEqual(self.run_js("[GridMath.signedNumber(933878000, 'm'), GridMath.scale(1000, 'k')]"), ["+933.9", 1])


if __name__ == "__main__":
    unittest.main()
