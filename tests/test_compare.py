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
    cell = row["cells"][f"{fy}|{stage}"]
    lines = cell["lines"]
    if not lines:
        return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": "",
                "amount": None, "outside": cell["outside_history"]}
    h = next((l for l in lines if l["amount_type"] == "budget authority" and not l["component"]), lines[0])
    return {"account": row["account"]["canonical_account_id"], "fy": fy, "stage": stage, "state": h["state"],
            "amount": thousands(h["observations"][0]["amount"]) if h["state"] == "value" else None,
            "outside": cell["outside_history"]}


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

    # ---- the cells are the store's -------------------------------------------------------

    def test_every_headline_cell_is_the_stores(self):
        # Compare stages, each fiscal year: every account's four stage cells, state and figure
        for fy in YEARS:
            with self.subTest(fy=fy):
                self.open("static", f"?view=compare&sc=CJS&grid=stages&fy={fy}")
                self.assertEqual(self.page.evaluate(SHOWN_JS),
                                 [expected_headline(r, fy, st) for r in self.grid["rows"] for st in FOUR])

    def test_enacted_history_and_two_years_show_the_same_cells(self):
        self.open("static", "?view=compare&sc=LHHS&grid=enacted")
        self.assertEqual(self.page.evaluate(SHOWN_JS),
                         [expected_headline(r, y, "Enacted") for r in self.lhhs["rows"] for y in self.lhhs["fiscal_years"]])
        self.open("static", "?view=compare&sc=LHHS&grid=years&a=2024&b=2026")
        self.assertEqual(self.page.evaluate(SHOWN_JS),
                         [expected_headline(r, y, st) for r in self.lhhs["rows"] for y in (2024, 2026) for st in FOUR])

    def test_grid_state_counts_are_unchanged(self):
        want = {"LHHS": {"value": 1452, "not_funded": 102, "no_printed_total": 13, "missing": 133, "not_collected": 200,
                         "not_enacted": 100},
                "CJS": {"value": 752, "not_funded": 18, "no_printed_total": 0, "missing": 460, "not_collected": 60,
                        "not_enacted": 30}}
        for sc, counts in want.items():
            for grid in ("stages", "years", "enacted"):
                self.open("static", f"?view=compare&sc={sc}&grid={grid}")
                got = {s.get_attribute("data-state"): int(s.inner_text().split()[0].replace(",", ""))
                       for s in self.page.locator("[data-testid=state-counts] [data-state]").all()}
                self.assertEqual(got, counts, (sc, grid))

    def test_static_renders_exactly_what_live_renders(self):
        for query in ("?view=compare&sc=CJS", "?view=compare&sc=LHHS&grid=years&a=2025&b=2026&units=m",
                      "?view=compare&sc=LHHS&grid=enacted&rev=1"):
            got = {}
            for where in self.urls:
                self.open(where, query)
                # the grid's height follows the page above it (the static page carries a freshness line)
                got[where] = self.page.inner_html("#tiles") + self.page.inner_html("#compare-grid")
            self.assertEqual(got["static"], got["live"], query)

    # ---- rows ----------------------------------------------------------------------------

    def test_chevrons_only_where_a_row_folds(self):
        self.open("static", "?view=compare&sc=LHHS")
        chev = lambda aid: self.page.locator(f"tr[data-account={aid}] [data-testid=chevron]")
        for aid in ("ACC-HHS-AHRQ-TOTAL", "ACC-HHS-ACL-TOTAL", "ACC-HHS-NIH-NCI"):
            self.assertEqual(chev(aid).count(), 0, aid)
            self.assertEqual(self.page.locator(f"tr[data-account={aid}]").get_attribute("data-anc").find("ACC-HHS-AHA-TOTAL"), -1)
        for aid in ("ACC-HHS-AHA-TOTAL", "ACC-HHS-NIH-TOTAL", "ACC-HHS-HRSA-PRIMARY-CARE"):
            self.assertEqual(chev(aid).count(), 1, aid)
        # every chevron is a row that folds something
        with_chevron = {r.get_attribute("data-account") for r in self.page.locator("tr:has([data-testid=chevron])").all()}
        folds = {r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if r["rollup_members"] or r.get("children")}
        self.assertEqual(with_chevron, folds | {"ACC-HHS-AHA-TOTAL"})

    def test_nih_folds_over_its_institutes(self):
        self.open("static", "?view=compare&sc=LHHS")
        chev = self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL] [data-testid=chevron]")
        members = self.page.locator("tr[data-member-of=ACC-HHS-NIH-TOTAL]")
        self.assertEqual(members.count(), len(self.lrow("ACC-HHS-NIH-TOTAL")["rollup_members"]))
        self.assertEqual(chev.get_attribute("aria-expanded"), "true")
        chev.focus()
        self.page.keyboard.press("Enter")                           # keyboard-operable
        self.assertEqual(chev.get_attribute("aria-expanded"), "false")
        self.assertEqual({m.is_hidden() for m in members.all()}, {True})
        self.assertTrue(self.page.locator("tr[data-account=ACC-HHS-CDC-NIOSH]").is_visible())   # CDC's group untouched
        chev.click()
        self.assertEqual({m.is_visible() for m in members.all()}, {True})

    def test_aha_opens_to_its_seven_incoming_relationships(self):
        self.open("static", "?view=compare&sc=LHHS")
        aha = self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL]")
        self.assertEqual(aha.locator("[data-testid=tag]").inner_text(), "proposed agency · not enacted")
        notes = self.page.locator("tr[data-testid=rel-note][data-note-of=ACC-HHS-AHA-TOTAL]")
        self.assertEqual(notes.count(), 7)
        self.assertEqual({n.is_hidden() for n in notes.all()}, {True})             # folded until opened
        aha.locator("[data-testid=chevron]").click()
        self.assertEqual({n.is_visible() for n in notes.all()}, {True})
        texts = [n.locator("td.note-text").inner_text() for n in notes.all()]
        self.assertIn("FY2026 request: moved in from National Institute of Environmental Health Sciences "
                      "(proposed, not enacted) · not yet reviewed", texts)

    def test_an_outgoing_proposed_move_is_a_note_under_the_account(self):
        self.open("static", "?view=compare&sc=LHHS")
        note = self.page.locator("tr[data-testid=move-note][data-note-of=ACC-HHS-NIH-NIEHS]")
        self.assertEqual(note.count(), 1)
        self.assertEqual(note.locator("td.note-text").inner_text(),
                         "FY2026 request: moved to Administration for a Healthy America (agency total) "
                         "(proposed, not enacted) · not yet reviewed")
        ids = self.page.eval_on_selector_all("#compare-grid tbody tr", "trs => trs.map(t => t.dataset.account || t.dataset.noteOf)")
        self.assertEqual(ids[ids.index("ACC-HHS-NIH-NIEHS") + 1], "ACC-HHS-NIH-NIEHS")    # right under it

    def test_tags(self):
        self.open("static", "?view=compare&sc=LHHS")
        tag = lambda aid: [t.inner_text() for t in self.page.locator(f"tr[data-account={aid}] [data-testid=tag]").all()]
        self.assertEqual(tag("ACC-HHS-NIH-SUBSTANCE-USE"), ["proposed in request"])
        self.assertEqual(tag("ACC-HHS-HRSA-HEALTH-CENTERS"), ["inside the line above · not added"])
        self.assertEqual(tag("ACC-HHS-NIH-NCI"), [])
        ids = self.page.eval_on_selector_all("#compare-grid tr[data-account]", "trs => trs.map(t => t.dataset.account)")
        self.assertEqual(ids[ids.index("ACC-HHS-HRSA-PRIMARY-CARE") + 1], "ACC-HHS-HRSA-HEALTH-CENTERS")
        # rows show the name only: no "single appropriation heading" or other notes
        self.assertNotIn("single appropriation heading", self.page.inner_text("#compare-grid").lower())

    def test_title_and_bill_totals_are_off_the_grid(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        self.assertEqual(self.page.locator("tr[data-account=ACC-HHS-TITLE-II-TOTAL]").count(), 0)
        total = next(t for t in self.lhhs["titles"] if t["title"] == "Title II")["total"]
        want = expected_headline(total, 2026, "Enacted")["amount"]
        tiles = [t.inner_text() for t in self.page.locator("[data-testid=tile]").all()]
        self.assertTrue(any(t.startswith("Title II total · FY2026 Enacted") and want in t for t in tiles), tiles)
        self.open("static", "?view=compare&sc=CJS")
        self.assertIn("None on file", self.page.inner_text("[data-testid=tiles]"))     # never a sum of the accounts

    def test_a_sourced_total_shows_in_the_tiles(self):
        db = with_sourced_totals(self.db, Path(self.tmp.name) / "totals_browser.db")
        srv = W.serve(db, port=0, verbose=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=CJS&grid=stages&fy=2024")
            self.page.wait_for_selector("[data-testid=compare-result]:not([hidden])")
            tiles = [t.inner_text() for t in self.page.locator("[data-testid=tile]").all()]
            self.assertTrue(any(t.startswith("Title III total · FY2024 Enacted") and "33,944,930" in t for t in tiles), tiles)
            self.assertEqual(self.page.locator("[data-testid=compare-row][data-account=ACC-T3-TOTAL]").count(), 0)
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

    def test_tokens_carry_their_meaning(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        for state, text in (("not_enacted", "n/e"), ("missing", "?"), ("not_collected", "n/c"), ("not_funded", "\u2014"),
                            ("no_printed_total", "No printed total")):
            self.open("static", "?view=compare&sc=LHHS&grid=enacted" if state == "not_enacted" else
                      "?view=compare&sc=LHHS&grid=years&a=2023&b=2024")
            tok = self.page.locator(f"td[data-state={state}] [data-testid=token-{state.replace('_', '-')}]").first
            self.assertEqual(tok.inner_text(), text, state)
            self.assertTrue(tok.get_attribute("aria-label") and tok.get_attribute("title"), state)

    def test_change_columns(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        r = self.lrow("ACC-HHS-HRSA-TOTAL")
        fig = lambda y, st: next(l for l in r["cells"][f"{y}|{st}"]["lines"] if l["amount_type"] == "budget authority"
                                 and not l["component"])["observations"][0]["amount"]
        row = self.page.locator("tr[data-account=ACC-HHS-HRSA-TOTAL]")
        ch = [c.inner_text() for c in row.locator("[data-testid=change-cell]").all()]
        req = fig(2026, "President's Budget") - fig(2025, "Enacted")
        enacted = fig(2026, "Enacted") - fig(2025, "Enacted")
        pct = lambda d: ("+" if d > 0 else "\u2212") + f"{abs(d) / fig(2025, 'Enacted') * 100:.1f}%"
        sign = lambda d: ("+" if d > 0 else "\u2212") + f"{abs(d) // 1000:,}"
        self.assertEqual(ch, [sign(req), pct(req), sign(enacted), pct(enacted)])
        colors = row.locator("[data-testid=change-cell] span").evaluate_all("ss => ss.map(s => getComputedStyle(s).color)")
        self.assertEqual(set(colors) - {"rgb(26, 115, 57)", "rgb(168, 70, 12)"}, set())
        # no prior-year figure, no change value: AHA has no FY2025 Enacted
        self.assertEqual({c.inner_text() for c in self.page.locator("tr[data-account=ACC-HHS-AHA-TOTAL] [data-testid=change-cell]").all()}, {""})

    def test_reviewer_mode_dots(self):
        self.open("static", "?view=compare&sc=LHHS&grid=stages&fy=2026")
        dot = self.cell("ACC-HHS-HRSA-TOTAL", 2026, "Enacted").locator("[data-testid=status-dot]")
        self.assertTrue(dot.is_hidden())
        self.page.check("#reviewer")
        self.assertTrue(dot.is_visible())
        self.assertIn("rev=1", self.page.url)
        status = self.lrow("ACC-HHS-HRSA-TOTAL")["cells"]["2026|Enacted"]["lines"][0]["observations"][0]["verification_status"]
        self.assertEqual(dot.get_attribute("data-status"), status)                 # the status as stored, as-is
        flagged = self.page.locator("[data-testid=status-dot][data-status=flagged]")
        self.assertEqual({d.get_attribute("class") for d in flagged.all()} - {"dot flagged"}, set())

    # ---- views, units, URL ---------------------------------------------------------------

    def test_view_years_and_units_live_in_the_url(self):
        self.open("static", "?view=compare&sc=LHHS")
        self.assertTrue(self.page.url.endswith("?view=compare&sc=LHHS&grid=stages&fy=2026"), self.page.url)
        self.page.click("#view-switch button[data-grid=years]")
        self.page.select_option("#fy-a", "2024")
        self.page.click("#units button[data-units=m]")
        self.assertTrue(self.page.url.endswith("grid=years&a=2024&b=2026&units=m"), self.page.url)
        html = self.page.inner_html("#compare-result")
        self.open("static", "?" + self.page.url.split("?", 1)[1])
        self.assertEqual(self.page.inner_html("#compare-result"), html)              # the link reproduces it
        # millions: one decimal
        a = self.cell("ACC-HHS-HRSA-TOTAL", 2026, "Enacted").locator("[data-testid=amount]").inner_text()
        self.assertRegex(a, r"^[\d,]+\.\d$")
        # an old link (fy range, stages) still opens, at its last year
        self.open("static", "?view=compare&sc=CJS&fy=2017-2025&stage=Enacted")
        self.assertTrue(self.page.url.endswith("grid=stages&fy=2025"), self.page.url)

    def test_expand_and_collapse_all(self):
        self.open("static", "?view=compare&sc=LHHS")
        self.page.click("#collapse-all")
        visible = [r.get_attribute("data-account") for r in self.page.locator("tr[data-testid=compare-row]").all() if r.is_visible()]
        self.assertEqual(visible, [r["account"]["canonical_account_id"] for r in self.lhhs["rows"] if not r["member_of"]])
        self.page.click("#expand-all")
        self.assertEqual(sum(r.is_visible() for r in self.page.locator("tr[data-testid=compare-row]").all()), len(self.lhhs["rows"]))
        self.assertEqual({c.get_attribute("aria-expanded") for c in self.page.locator("[data-testid=chevron]").all()}, {"true"})

    # ---- layout and style ----------------------------------------------------------------

    def test_layout(self):
        self.open("static", "?view=compare&sc=LHHS&grid=enacted")
        m = self.page.evaluate("""() => { const w = document.querySelector('#grid-wrap'), r = w.getBoundingClientRect();
            const h2 = document.querySelector('#compare-grid thead tr:nth-child(2) th');
            return {bottom: r.bottom, inner: innerHeight, scrollable: w.scrollHeight > w.clientHeight,
                    overflow: getComputedStyle(w).overflowX + ' ' + getComputedStyle(w).overflowY,
                    pageScrolls: document.documentElement.scrollHeight > innerHeight + 1,
                    head2: getComputedStyle(h2).top, nameSticky: getComputedStyle(document.querySelector('#compare-grid tbody th.acct')).position,
                    font: getComputedStyle(document.body).fontFamily, size: getComputedStyle(document.body).fontSize}; }""")
        self.assertLessEqual(m["bottom"], m["inner"])           # both scrollbars in view, no page scroll to reach them
        self.assertTrue(m["scrollable"])
        self.assertEqual(m["overflow"], "scroll scroll")
        self.assertFalse(m["pageScrolls"])
        self.assertEqual((m["head2"], m["nameSticky"]), ("38px", "sticky"))
        self.assertTrue(m["font"].startswith('"Public Sans"'))
        self.assertEqual(m["size"], "14px")
        self.assertEqual(self.page.inner_text("#page-title"), "Labor-HHS-Education")
        self.open("static", "?view=compare&sc=CJS")
        self.assertEqual(self.page.inner_text("#page-title"), "Commerce, Justice, Science")
        self.assertNotIn("pilot", (self.page.inner_text("#page-title") + self.page.inner_text("#page-sub")).lower())

    def test_rules_zebra_and_hover(self):
        self.open("static", "?view=compare&sc=LHHS")
        css = lambda sel, prop: self.page.locator(sel).first.evaluate(f"e => getComputedStyle(e).{prop}")
        self.assertEqual(css("#compare-grid tbody td:not(.g0)", "borderRightColor"), "rgb(230, 230, 225)")
        self.assertEqual(css("#compare-grid tbody td.g0", "borderLeftWidth"), "2px")
        self.assertEqual(css("#compare-grid tbody td.g0", "borderLeftColor"), "rgb(196, 198, 190)")
        self.assertEqual(css("#compare-grid tbody tr.z:not(.shaded) > td", "backgroundColor"), "rgb(247, 247, 244)")
        plain_row = self.page.locator("tr[data-account=ACC-HHS-NIH-NCI]")
        plain_row.hover()
        self.assertEqual({c.evaluate("e => getComputedStyle(e).backgroundColor") for c in plain_row.locator("> *").all()},
                         {"rgb(227, 236, 248)"})
        shaded = self.page.locator("tr[data-account=ACC-HHS-NIH-TOTAL]")
        shaded.hover()
        self.assertEqual({c.evaluate("e => getComputedStyle(e).backgroundColor") for c in shaded.locator("> *").all()},
                         {"rgb(214, 227, 245)"})
        title = self.page.locator("tr[data-testid=title-head]")
        title.hover()
        self.assertEqual(title.locator("th").evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(214, 227, 245)")

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
