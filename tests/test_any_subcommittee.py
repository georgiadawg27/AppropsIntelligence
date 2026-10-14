"""
G-1: any subcommittee. A third subcommittee needs data and a rules file, not code edits.

  IdNamespaces          one allocator for every record type; no two subcommittees can mint the same ID
  Coverage              the cells read for components come from the data, and equal the cells read before G-1
  RegistryRules         scope, conflicts (two rules on one line stop the run), a rule in two files
  MovedRules            every rule moved out of the core: it fires on its real case and changes what it should,
                        and it does not fire outside its scope
  SyntheticSubcommittee tests/fixtures/any_subcommittee (SYNLB: 2 accounts, 1 FY, every stage, bill and law text,
                        no real figure) end to end: intake, tagger, measure_reconcile, the workbook, the export, and
                        the page with its own display name. Its quirk (Legislative Branch's custom: a chamber's
                        account is not carried in the other chamber's bill) is its own rule file's; its one unread
                        phrase lands in review.
"""

import collections
import contextlib
import functools
import http.server
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts", ROOT / "tests", ROOT / "tests" / "fixtures" / "any_subcommittee"):
    sys.path.insert(0, str(p))
import check_legislative_text as CT  # noqa: E402
import ids as IDS  # noqa: E402
import measure_reconcile as MR  # noqa: E402
import rules_registry as R  # noqa: E402
import subcommittees as SC  # noqa: E402
import tag_components as TC  # noqa: E402
import legislative_text as L  # noqa: E402

STAGED = json.loads((ROOT / "data" / "staged.json").read_text())
FIXTURE = ROOT / "tests" / "fixtures" / "any_subcommittee"
try:
    from playwright.sync_api import sync_playwright
except ImportError:                                  # pragma: no cover
    sync_playwright = None


def chromium_path():
    env = os.environ.get("PLAYWRIGHT_CHROMIUM")
    if env and Path(env).exists():
        return env
    found = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


@contextlib.contextmanager
def registry(rules):
    """Run with these rules active (the core alone with [])."""
    keep = R.active()
    reg = R.Registry(rules)
    reg.modules = keep.modules
    R.use(reg)
    try:
        yield reg
    finally:
        R.use(keep)


def without(rule_id):
    rules = [r for r in R.active().all if r.id != rule_id]
    assert len(rules) == len(R.active().all) - 1, rule_id
    return registry(rules)


def rule(rule_id):
    return next(r for r in R.active().all if r.id == rule_id)


# ------------------------------------------------------------------------------------------------ IDs

class IdNamespaces(unittest.TestCase):
    CODES = ["CJS", "LHHS", "SYNLB", "LEG", "AG", "DEF", "THUD", "L2", "LHHSX"]

    def test_no_two_subcommittees_can_mint_the_same_id(self):
        rnd = random.Random(7)
        codes = self.CODES + ["".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(4))
                              .replace(".", "") for _ in range(30)]
        codes = sorted({c for c in codes if c[0].isalpha() and c not in IDS.RESERVED})
        for typ in IDS.TYPES:
            minted = {}
            for code in codes:
                a = IDS.Allocator()
                for n in range(1, 30):
                    rid = a.next(typ, code)
                    self.assertNotIn(rid, minted, (rid, code, minted.get(rid)))
                    minted[rid] = code
                    self.assertEqual(IDS.parse(rid), (typ, code, n))
            # every ID is in exactly one namespace
            for rid, code in minted.items():
                owners = [c for c in codes if IDS.namespace(typ, c).fullmatch(rid)]
                self.assertEqual(owners, [code], rid)

    def test_codes_that_could_collide_are_refused(self):
        for bad in ("TXT", "MR", "MRV", "MRC", "MRE", "OBS", "VAL", "Legislative Branch", "lhhs", "1A", "A"):
            with self.assertRaises(IDS.IdError):
                IDS.Allocator().next("OBS", bad)
        # a derived ID (VAL-TXT-..., VAL-MR-...) is in no subcommittee's namespace
        for rid in ("VAL-TXT-0001", "VAL-TXT-LHHS-0001", "VAL-MR-LHHS-0003", "VAL-MRC-UNEXPLAINED"):
            self.assertIsNone(IDS.parse(rid))

    def test_existing_ids_keep_their_patterns_and_the_next_follows_them(self):
        used = [c["component_id"] for c in STAGED["observation_components"]] + STAGED["observation_components_retired"]
        a = IDS.Allocator(used)
        top_cjs = max(IDS.number(i) for i in used if not i.startswith("CMP-LHHS-"))
        top_lhhs = max(IDS.number(i) for i in used if i.startswith("CMP-LHHS-"))
        self.assertEqual(a.next("CMP", "CJS"), f"CMP-{top_cjs + 1:04d}")          # CJS keeps CMP-####
        self.assertEqual(a.next("CMP", "LHHS"), f"CMP-LHHS-{top_lhhs + 1:04d}")
        self.assertEqual(a.next("CMP", "LEG"), "CMP-LEG-0001")
        self.assertEqual(IDS.make("VAL", "CJS", 7), "VAL-0007")
        self.assertEqual(IDS.make("VAL", "LHHS", 7), "VAL-LHHS-00007")
        # every ID in the data parses into the namespace of its record's subcommittee
        acc = {x["canonical_account_id"]: x["subcommittee"] for x in STAGED["accounts"]}
        for o in STAGED["observations"]:
            self.assertEqual(IDS.parse(o["observation_id"])[:2], ("OBS", acc[o["canonical_account_id"]]))
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        for c in STAGED["observation_components"]:
            self.assertEqual(IDS.parse(c["component_id"])[1], acc[obs[c["observation_id"]]["canonical_account_id"]])


# ------------------------------------------------------------------------------------------------ coverage

TEXT_STAGES = ("House Reported", "Senate Reported", "Enacted")


class Coverage(unittest.TestCase):
    # the cells tag_components read before G-1 (PILOT | BACKFILL | FY2027_HOUSE | CJS_2024_2025, CR_CELLS)
    BEFORE = ({(sc, fy, st) for sc in ("LHHS", "CJS") for fy in range(2017, 2027) for st in TEXT_STAGES}
              | {("LHHS", 2027, "House Reported"), ("CJS", 2027, "House Reported")})
    CR_BEFORE = {("LHHS", 2025, "Enacted"), ("CJS", 2025, "Enacted")}

    def test_the_derived_cells_equal_the_cells_read_before(self):
        cells, cr = TC.coverage(STAGED)
        self.assertEqual(cr, self.CR_BEFORE)
        # the one cell the old list named and the data doesn't: Labor-HHS FY2020 Senate, where no bill was ever
        # published (exempt) -- it holds no figure, so reading it or not changes nothing
        self.assertEqual(self.BEFORE - cells, {("LHHS", 2020, "Senate Reported")})
        self.assertEqual(cells - self.BEFORE, set())
        acc = {a["canonical_account_id"]: a["subcommittee"] for a in STAGED["accounts"]}
        self.assertFalse([o for o in STAGED["observations"] if acc[o["canonical_account_id"]] == "LHHS"
                          and int(o["fiscal_year"]) == 2020 and o["stage"] == "Senate Reported"])

    def test_cr_cells_come_from_the_enactments(self):
        data = json.loads(json.dumps(STAGED))
        for r in data["bill_report_refs"]:
            if r["reference_id"] == "BR-CJS-FY2025-ENACTED":
                r["funding_type"] = "omnibus"
        self.assertEqual(TC.cr_cells(data), {("LHHS", 2025, "Enacted")})


# ------------------------------------------------------------------------------------------------ the registry

def amt(amount, before, after=""):
    return {"amount": amount, "block": 1, "proviso": False, "phrase": f"{before} [${amount:,}] {after}"}


class RegistryRules(unittest.TestCase):
    def test_every_rule_states_its_scope_and_hook(self):
        reg = R.active()
        self.assertEqual(reg.codes, ["CJS", "LHHS"])
        for r in reg.all:
            self.assertIn(r.hook, R.HOOKS)
            r.scope.check()
            self.assertTrue(r.moved_from, r.id)
            self.assertTrue(r.doc, r.id)

    def test_two_rules_on_one_line_stop_the_run(self):
        both = [R.Rule(f"T-{i}", "phrase", R.Scope("LHHS", accounts=R.ANY, fiscal_years=R.ANY, stages=R.ANY),
                       fn=lambda x, ctx: ("transfer_in", "", "x", "x")) for i in (1, 2)]
        with registry(both), self.assertRaises(R.RuleConflict):
            TC.classify(amt(5, "Provided, That", "shall be for grants"), TC.ctx_for("LHHS", "A", 2026, "Enacted"))

    def test_a_rule_where_a_core_rule_acts_must_name_it(self):
        mine = R.Rule("T-1", "phrase", R.Scope("LHHS", accounts=R.ANY, fiscal_years=R.ANY, stages=R.ANY),
                      fn=lambda x, ctx: ("transfer_in", "", "x", "x"))
        x = amt(5, "Provided, That", "is designated by the Congress as being for an emergency requirement")
        ctx = TC.ctx_for("LHHS", "A", 2026, "Enacted")
        with registry([mine]), self.assertRaises(R.RuleConflict):
            TC.classify(x, ctx)
        with registry([R.Rule("T-1", "phrase", mine.scope, fn=mine.fn, overrides=("emergency",))]):
            self.assertEqual(TC.classify(x, ctx)[:3], ("transfer_in", "", "x"))

    def test_a_rule_in_two_files_must_move_to_the_core(self):
        with tempfile.TemporaryDirectory() as d:
            for code in ("AAA", "BBB"):
                (Path(d) / f"{code}.py").write_text(
                    "from rules_registry import ANY, Rule, Scope\n"
                    f"CODE = '{code}'\n"
                    f"RULES = [Rule('SHARED', 'display', Scope('{code}', accounts=ANY, fiscal_years=ANY, "
                    "stages=ANY), data={'name': 'x'})]\n")
            with self.assertRaisesRegex(R.RuleError, "moves to the core"):
                R.Registry.load([d])

    def test_a_scope_must_be_stated(self):
        with self.assertRaises(R.RuleError):
            R.Registry([R.Rule("T-1", "display", R.Scope("LHHS"), data={})])

    def test_display_names_come_from_subcommittees_py_then_the_rule_file(self):
        self.assertEqual(SC.display_name("LHHS"), "Labor-HHS-Education")
        self.assertEqual(SC.display_name("CJS"), "Commerce, Justice, Science")
        self.assertEqual(SC.display_name("NONE"), "NONE")
        self.assertEqual(SC.codes(STAGED), ["CJS", "LHHS"])


# ------------------------------------------------------------------------------------------------ the moved rules

TEXTS = CT.load_texts(L.TEXT_DIR)


def paragraph(doc, pid):
    if doc.startswith("REQ:"):
        account, fy = doc[4:].rsplit(":", 1)
        return TC.request_language()[(account, int(fy))]["paragraph"]
    return next(p for p in TEXTS[doc]["paragraphs"] if p["paragraph_id"] == pid)


def read(doc, pid, sc, fy, stage):
    """tag_components.read_amounts over a real paragraph -> ({amount index: classification}, report)"""
    para = paragraph(doc, pid)
    report = collections.defaultdict(list)
    report["not_a_component"] = collections.Counter()
    main = CT.main_amount(para)[0]
    tagged = TC.read_amounts("OBS-X", doc, para, main, 0, "here", report, TC.ctx_for(sc, "ACC-X", fy, stage, para))
    by_phrase = {x["phrase"]: cl for x, cl, _, _ in tagged}
    return {i: by_phrase.get(x["phrase"]) for i, x in enumerate(para["amounts"])}, report


# real cases from the committed text: rule -> (document, paragraph, amount index, subcommittee, FY, stage, what the
# amount reads as (type, designation, transfer_from))
PHRASE_CASES = {
    "LHHS-241-LIST": ("SRC-BILL-LHHS-FY2021-SENATE", "P0081", 4, "LHHS", 2021, "Senate Reported",
                      ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)")),
    "LHHS-241-AVAILABLE": ("SRC-BILL-LHHS-FY2021-SENATE", "P0059", 1, "LHHS", 2021, "Senate Reported",
                           ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)")),
    "LHHS-241-EVAL-TAP": ("SRC-BILLS-117S3062IS", "P0042", 1, "LHHS", 2022, "Senate Reported",
                          ("transfer_in", "", "not named in the phrase")),
    "LHHS-241-PAST-WINDOW": ("REQ:ACC-HHS-CDC-INJURY-PREVENTION:2025", None, 1, "LHHS", 2025, "President's Budget",
                             ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)")),
    "LHHS-241-SOURCE": ("SRC-BILLS-117HR8295RH", "P0098", 0, "LHHS", 2023, "House Reported",
                        ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)")),
    "LHHS-CAP-ADJUSTMENT": ("SRC-BILL-LHHS-FY2021-SENATE", "P0088", 5, "LHHS", 2021, "Senate Reported",
                            ("designated", "cap_adjustment", "")),
    "LHHS-CURES": ("SRC-BILL-LHHS-FY2021-SENATE", "P0079", 0, "LHHS", 2021, "Senate Reported",
                   ("designated", "other", "")),
    "LHHS-PRIF": ("SRC-BILL-LHHS-FY2021-SENATE", "P0077", 7, "LHHS", 2021, "Senate Reported",
                  ("transfer_in", "", "10-year Pediatric Research Initiative Fund (PRIF)")),
    # vetoes: the amount is no component (the core alone would read a transfer)
    "LHHS-QUARANTINE": ("SRC-BILLS-119HR9260RH", "P0035", 1, "LHHS", 2027, "House Reported", None),
}
# not a component (counted, never tagged): rule -> (document, paragraph, amount index, ..., kind)
KIND_CASES = {
    "LHHS-NOT-COMPONENT-AVAILABILITY": ("SRC-BILLS-119HR9260RH", "P0035", 1, "LHHS", 2027, "House Reported",
                                        "availability"),
    "LHHS-NOT-COMPONENT-CAP-ADJUSTMENT-BASE": ("SRC-BILL-LHHS-FY2021-SENATE", "P0088", 4, "LHHS", 2021,
                                               "Senate Reported", "cap-adjustment base"),
    "LHHS-NOT-COMPONENT-SUB-ALLOCATION": ("SRC-BILL-LHHS-FY2021-SENATE", "P0098", 4, "LHHS", 2021, "Senate Reported",
                                          "sub-allocation"),
}


def comps_where(**kw):
    return [c for c in STAGED["observation_components"] if all(c.get(k) == v for k, v in kw.items())]


class MovedRules(unittest.TestCase):
    """Every rule in reference/rules/: in scope it fires on its real case and changes what it should; outside its
    scope it doesn't fire."""

    @classmethod
    def setUpClass(cls):
        cls.heads = TC.headline_of(STAGED)

    def test_every_moved_rule_has_its_case_here(self):
        covered = set(PHRASE_CASES) | set(KIND_CASES) | {
            "LHHS-PPHF-SOURCE", "LHHS-241-SUBALLOCATION", "LHHS-LAYOUT", "CJS-LAYOUT", "LHHS-APPENDIX", "CJS-APPENDIX", "LHHS-ONC-EVAL-TAP-LINE",
            "CJS-NSF-DEFENSE", "CJS-FY2027-TRANSFER", "CJS-FY2027-OFFSETTING", "LHHS-VICTF-LINES", "LHHS-PPHF-TABLE",
            "LHHS-TOTAL-ONLY", "LHHS-ADVANCES", "LHHS-CR-ANOMALIES", "LHHS-CR-PPHF", "CJS-CR-LEVEL-ONLY",
            "LHHS-CHECKS", "LHHS-ONC-VIEW-CHECK", "LHHS-LOOKS-LIKE", "LHHS-241-NOT-MAIN", "LHHS-241-DERIVATION",
            "LHHS-241-NO-MAIN", "LHHS-ONC-CELL-NOTE"}
        self.assertEqual({r.id for r in R.active().all}, covered)

    # phrase rules ------------------------------------------------------------------------------------------
    def test_phrase_rules_fire_on_their_real_case(self):
        for rid, (doc, pid, i, sc, fy, st, want) in PHRASE_CASES.items():
            with self.subTest(rid):
                got = read(doc, pid, sc, fy, st)[0][i]
                self.assertEqual(got and got[:3], want)
                with without(rid):
                    self.assertNotEqual(read(doc, pid, sc, fy, st)[0][i], got)      # the rule changes it

    def test_phrase_rules_do_not_fire_outside_their_scope(self):
        for rid, (doc, pid, i, sc, fy, st, want) in PHRASE_CASES.items():
            with self.subTest(rid):
                r = rule(rid)
                self.assertFalse(r.scope.contains("CJS", "ACC-X", fy, st))
                with without(rid):
                    core = read(doc, pid, sc, fy, st)[0][i]
                # the same paragraph read as a CJS cell: as the core alone reads it
                self.assertEqual(read(doc, pid, "CJS", fy, st)[0][i], core)

    def test_not_component_rules(self):
        for rid, (doc, pid, i, sc, fy, st, kind) in KIND_CASES.items():
            with self.subTest(rid):
                x = paragraph(doc, pid)["amounts"][i]
                self.assertEqual(TC.not_a_component(x, TC.ctx_for(sc, "ACC-X", fy, st)), kind)
                with without(rid):
                    self.assertNotEqual(TC.not_a_component(x, TC.ctx_for(sc, "ACC-X", fy, st)), kind)
                self.assertNotEqual(TC.not_a_component(x, TC.ctx_for("CJS", "ACC-X", fy, st)), kind)

    def test_241_sub_allocation(self):
        # no committed text prints a section 241 sub-allocation beside a core transfer phrase today (each one is
        # 'available under section 241' alone, which LHHS-241-AVAILABLE leaves out the same way); on its wording
        x = amt(6800000, "Provided further, That of the funds available under section 241 of the PHS Act,",
                "shall be from amounts made available under such section for evaluations")
        lx = TC.ctx_for("LHHS", "ACC-X", 2021, "Senate Reported")
        self.assertIsNone(TC.classify(x, lx))
        with without("LHHS-241-SUBALLOCATION"):
            self.assertEqual(TC.classify(x, lx)[0], "transfer_in")
        self.assertEqual(TC.classify(x, TC.ctx_for("CJS", "ACC-X", 2021, "Senate Reported"))[0], "transfer_in")
        # the real case: OS-GDM's 'of the amounts provided under this heading from amounts available under section
        # 241' (S. FY2021 Senate draft, P0098): no transfer, and counted as a sub-allocation
        x = paragraph("SRC-BILL-LHHS-FY2021-SENATE", "P0098")["amounts"][4]
        self.assertIsNone(TC.classify(x, lx))
        self.assertEqual(read("SRC-BILL-LHHS-FY2021-SENATE", "P0098", "LHHS", 2021, "Senate Reported")[0][4], None)

    def test_pphf_source(self):
        # no committed text prints this wording today (the Fund reaches its accounts through the allocation table);
        # the owner's rule of 2026-10-11, on its wording
        x = amt(244, "Provided, That in addition to amounts provided under this heading,",
                "shall be available for surveillance from amounts made available under section 4002 of the PPACA")
        got = TC.classify(x, TC.ctx_for("LHHS", "ACC-X", 2026, "Enacted"))
        self.assertEqual(got[:3], ("transfer_in", "", R.active().modules["LHHS"].PPHF))
        with without("LHHS-PPHF-SOURCE"):
            self.assertEqual(TC.classify(x, TC.ctx_for("LHHS", "ACC-X", 2026, "Enacted"))[2], "not named in the phrase")
        self.assertEqual(TC.classify(x, TC.ctx_for("CJS", "ACC-X", 2026, "Enacted"))[2], "not named in the phrase")

    # layout, appendix -------------------------------------------------------------------------------------------
    def test_layout_rules(self):
        acc = {a["canonical_account_id"]: a for a in STAGED["accounts"]}
        nasa = acc["ACC-NASA-SCIENCE"]
        self.assertEqual(CT.agency_words(nasa), "aeronautics space administration")
        with without("CJS-LAYOUT"):
            self.assertEqual(CT.agency_words(nasa), CT.norm(nasa["agency"]))
        self.assertEqual(CT.agency_words(dict(nasa, subcommittee="LHHS")), CT.norm(nasa["agency"]))
        samhsa = next(a for a in STAGED["accounts"] if a["agency"].startswith("Substance Abuse") and not a["total_scope"])
        para = next(p for p in TEXTS["SRC-BILLS-117HR8295RH"]["paragraphs"]
                    if "SUBSTANCE USE AND MENTAL HEALTH SERVICES ADMINISTRATION" in p["heading_path"])
        self.assertTrue(CT.in_context(samhsa, para))                        # FY2023's name for SAMHSA
        with without("LHHS-LAYOUT"):
            self.assertFalse(CT.in_context(samhsa, para))
        self.assertFalse(CT.in_context(dict(samhsa, subcommittee="CJS"), para))

    def test_appendix_rules(self):
        self.assertEqual(R.active().data("appendix", "LHHS"), {"chapters": ("HHS",)})
        self.assertEqual(R.active().data("appendix", "CJS")["chapters"], ("COM", "DOJ", "NASA", "NSF", "EOP"))
        self.assertEqual(R.active().data("appendix", "OTHER"), {})

    # line, table_line, components, extract ------------------------------------------------------------------
    def retagged(self, source_observation_id):
        rep = collections.defaultdict(list)
        return [c for c in TC.retag(STAGED, self.heads, rep) if c["source_observation_id"] == source_observation_id]

    def test_line_rules(self):
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        acc = {a["canonical_account_id"]: a["subcommittee"] for a in STAGED["accounts"]}
        for rid, line, want in (("LHHS-ONC-EVAL-TAP-LINE", "OBS-LHHS-2663", "CMP-LHHS-0063"),
                                ("CJS-NSF-DEFENSE", "OBS-0739", "CMP-0023"),
                                ("CJS-NSF-DEFENSE", "OBS-0998", "CMP-0085"),
                                ("CJS-FY2027-TRANSFER", "OBS-0973", "CMP-0062"),
                                ("CJS-FY2027-OFFSETTING", "OBS-0972", "CMP-0061")):
            with self.subTest(line):
                staged = comps_where(component_id=want)[0]
                got = self.retagged(line)
                self.assertEqual([(c["component_type"], c["amount"], c["in_headline"]) for c in got],
                                 [(staged["component_type"], staged["amount"], staged["in_headline"])])
                with without(rid):
                    self.assertEqual(self.retagged(line), [])
                o = obs[line]
                r = rule(rid)
                other = "CJS" if acc[o["canonical_account_id"]] == "LHHS" else "LHHS"
                out = [TC.ctx_for(other, o["canonical_account_id"], o["fiscal_year"], o["stage"])]
                if r.scope.accounts is not R.ANY:
                    out.append(TC.ctx_for(acc[o["canonical_account_id"]], "ACC-OTHER", o["fiscal_year"], o["stage"]))
                if r.scope.fiscal_years is not R.ANY:
                    out.append(TC.ctx_for(acc[o["canonical_account_id"]], o["canonical_account_id"], 2026, o["stage"]))
                for ctx in out:
                    self.assertIsNone(R.active().first("line", line, ctx, o), (rid, ctx))

    def test_victf_lines(self):
        rep = collections.defaultdict(list)
        got = [c for c in TC.table_lines(STAGED, self.heads, rep) if c["observation_id"] == "OBS-LHHS-0309"]
        self.assertIn(("mandatory_portion", 256370000), {(c["component_type"], c["amount"]) for c in got})
        with without("LHHS-VICTF-LINES"):
            rep = collections.defaultdict(list)
            self.assertFalse([c for c in TC.table_lines(STAGED, self.heads, rep)
                              if c["observation_id"] == "OBS-LHHS-0309"])
            self.assertTrue(rep["unknown_table_line"])          # a line no rule reads goes to review
        r = rule("LHHS-VICTF-LINES")
        self.assertFalse(r.scope.contains("LHHS", "ACC-HHS-HRSA-OTHER", 2023, "Enacted"))
        self.assertFalse(r.scope.contains("CJS", "ACC-HHS-HRSA-VICTF", 2023, "Enacted"))

    def test_pphf_table(self):
        rep = collections.defaultdict(list)
        got = TC.rule_components(STAGED, self.heads, rep)
        want = comps_where(tag_method="table_line", transfer_from=R.active().modules["LHHS"].PPHF)
        self.assertEqual(len(got), len(want))
        self.assertEqual({(c["observation_id"], c["amount"]) for c in got},
                         {(c["observation_id"], c["amount"]) for c in want})
        with without("LHHS-PPHF-TABLE"):
            self.assertEqual(TC.rule_components(STAGED, self.heads, collections.defaultdict(list)), [])
        self.assertEqual(R.active().rules("components", "CJS"), [])

    def test_total_only(self):
        texts, exempt = TEXTS, CT.load_exempt(L.TEXT_DIR)
        outcomes = CT.check(STAGED, texts, exempt)
        rep = collections.defaultdict(list)
        got = TC.total_only_outcomes(STAGED, texts, outcomes, rep)
        self.assertIn("OBS-LHHS-1847", got)                             # AHRQ FY2022 House: its paragraph is read
        self.assertTrue(comps_where(observation_id="OBS-LHHS-1847", tag_method="auto_text"))
        with without("LHHS-TOTAL-ONLY"):
            self.assertEqual(TC.total_only_outcomes(STAGED, texts, outcomes, collections.defaultdict(list)), {})
        self.assertFalse(rule("LHHS-TOTAL-ONLY").scope.contains("LHHS", "ACC-HHS-CDC-TOTAL", 2022, "House Reported"))

    # advances, the CR --------------------------------------------------------------------------------------
    def test_advances(self):
        base = [c for c in STAGED["observation_components"] if c["component_type"] != "advance_available"]
        got = TC.advances(STAGED, self.heads, base, collections.defaultdict(list))
        self.assertIn(("OBS-LHHS-3646", "advance_available", 125219452000),
                      {(c["observation_id"], c["component_type"], c["amount"]) for c in got})
        with without("LHHS-ADVANCES"):
            got = TC.advances(STAGED, self.heads, base, collections.defaultdict(list))
            self.assertFalse([c for c in got if c["observation_id"] == "OBS-LHHS-3646"])
        self.assertFalse(rule("LHHS-ADVANCES").scope.contains("LHHS", "ACC-HHS-CDC-TOTAL", 2018, "Enacted"))
        self.assertFalse(rule("LHHS-ADVANCES").scope.contains("CJS", "ACC-HHS-CMS-MEDICAID", 2018, "Enacted"))

    def carried(self):
        records = [c for c in STAGED["observation_components"] if c["tag_method"] not in ("carried_forward",
                                                                                          "cr_anomaly")]
        rep = collections.defaultdict(list)
        return TC.carry_forward(STAGED, self.heads, records, rep), rep

    def test_cr_anomalies_and_the_cr_pphf(self):
        got, _ = self.carried()
        stated = {(c["observation_id"], c["component_type"], c["amount"]) for c in got if c["tag_method"] == "cr_anomaly"}
        self.assertIn(("OBS-LHHS-0626", "advance_enacted", 1600000000), stated)    # CMP-LHHS-0375, sec. 1109(b)(3)
        pphf = [c for c in got if c["observation_id"] == "OBS-LHHS-0873" and c["amount"] == 681933000]
        self.assertEqual([c["tag_rule"] for c in pphf], [R.active().modules["LHHS"].CR_PPHF])
        with without("LHHS-CR-ANOMALIES"):
            got2, _ = self.carried()
            self.assertFalse([c for c in got2 if c["tag_method"] == "cr_anomaly"])
        with without("LHHS-CR-PPHF"):
            got3, _ = self.carried()
            self.assertTrue([c for c in got3 if c["observation_id"] == "OBS-LHHS-0873" and c["amount"] == 681933000
                             and c["tag_rule"].startswith("FY2025 full-year CR (P.L. 119-4 div. A sec. 1101")])
        for rid in ("LHHS-CR-ANOMALIES", "LHHS-CR-PPHF"):
            self.assertFalse(rule(rid).scope.contains("LHHS", "ACC-HHS-CMS-MEDICAID", 2024, "Enacted"))
            self.assertFalse(rule(rid).scope.contains("LHHS", "ACC-HHS-CMS-MEDICAID", 2025, "House Reported"))
            self.assertFalse(rule(rid).scope.contains("CJS", "ACC-HHS-CMS-MEDICAID", 2025, "Enacted"))

    def test_cr_level_only(self):
        # P.L. 119-4 sec. 1301(3) names NOAA ORF; the data holds no FY2024 Enacted component of it to carry, so the
        # rule has no real case today: its effect on one
        c = {"component_id": "CMP-9999", "component_type": "transfer_in", "transfer_from": "x"}
        ctx = TC.ctx_for("CJS", "ACC-NOAA-ORF", 2025, "Enacted")
        hit = R.active().first("carry_forward", "", ctx, c)
        self.assertIn("sec. 1301(3) sets the level", hit[1]["note"])
        self.assertIsNone(R.active().first("carry_forward", "", TC.ctx_for("CJS", "ACC-NOAA-ORF", 2024, "Enacted"), c))
        self.assertIsNone(R.active().first("carry_forward", "", TC.ctx_for("CJS", "ACC-NASA-SCIENCE", 2025,
                                                                            "Enacted"), c))
        self.assertIsNone(R.active().first("carry_forward", "", ctx, dict(c, component_type="earmark")))

    # measure_reconcile --------------------------------------------------------------------------------------
    def test_checks(self):
        records, outcomes = MR.run(STAGED)
        mine = {r["validation_id"]: r for r in records}
        self.assertIn("VAL-MRC-NEF-TRANSFER-UNTAGGED", mine)
        self.assertIn("Evaluation Tap Funding", mine["VAL-MRV-LHHS-2664"]["expected_result"])
        self.assertEqual(mine["VAL-MRV-LHHS-2664"]["result"], "pass")
        with without("LHHS-CHECKS"):
            r2 = {r["validation_id"] for r in MR.run(STAGED)[0]}
            self.assertNotIn("VAL-MRC-NEF-TRANSFER-UNTAGGED", r2)
        with without("LHHS-ONC-VIEW-CHECK"):
            r3 = {r["validation_id"]: r for r in MR.run(STAGED)[0]}
            self.assertNotIn("Evaluation Tap Funding", r3.get("VAL-MRV-LHHS-2664", {}).get("expected_result", ""))
        self.assertEqual(R.active().data("checks", "CJS"), {})
        self.assertFalse(rule("LHHS-ONC-VIEW-CHECK").scope.contains("LHHS", "ACC-HHS-CDC-TOTAL", 2022, "Enacted"))


class MovedRulesTextCheckAndSite(unittest.TestCase):
    """The rules moved in the second round (owner, 2026-10-14): the unread-phrase detector's Labor-HHS words, the
    text check's section 241 main-amount rules, ONC's $0 headline note."""

    @classmethod
    def setUpClass(cls):
        cls.exempt = CT.load_exempt(L.TEXT_DIR)
        cls.outcomes = CT.check(STAGED, TEXTS, cls.exempt)
        cls.val = {v["validation_id"]: v for v in STAGED["validations"]}

    def outcome_without(self, rule_id, oid):
        with without(rule_id):
            return CT.check(STAGED, TEXTS, self.exempt)[oid]

    def test_looks_like(self):
        # a section 241 sub-allocation no rule tags (S. FY2021 Senate draft, OS-GDM P0098): a review item only in a
        # Labor-HHS cell, by the Labor-HHS words
        x = paragraph("SRC-BILL-LHHS-FY2021-SENATE", "P0098")["amounts"][4]
        text = " ".join(TC.segments(x))
        lx, cx = TC.ctx_for("LHHS", "ACC-X", 2021, "Senate Reported"), TC.ctx_for("CJS", "ACC-X", 2021, "Senate Reported")
        self.assertFalse(TC.LOOKS_LIKE.search(text))
        self.assertTrue(TC.looks_like(text, lx))
        with without("LHHS-LOOKS-LIKE"):
            self.assertFalse(TC.looks_like(text, lx))
        self.assertFalse(TC.looks_like(text, cx))
        _, rep = read("SRC-BILL-LHHS-FY2021-SENATE", "P0098", "LHHS", 2021, "Senate Reported")
        self.assertEqual(rep["not_a_component"]["sub-allocation"], 1)

    def test_not_main(self):
        # GDM, S. FY2021 Senate draft: '$64,828,000 to be derived from amounts available under section 241'
        p = paragraph("SRC-BILL-LHHS-FY2021-SENATE", "P0098")
        a = p["amounts"][1]
        lx = CT.R.Ctx("LHHS", "ACC-HHS-OS-GDM", 2021, "Senate Reported")
        self.assertTrue(CT.is_main_candidate(a))                    # the core alone would read it
        self.assertFalse(CT.is_main_candidate(a, lx))
        self.assertNotIn(64828000, [x["amount"] for x in CT.appropriating(p, lx)])
        with without("LHHS-241-NOT-MAIN"):
            self.assertTrue(CT.is_main_candidate(a, lx))
        self.assertTrue(CT.is_main_candidate(a, CT.R.Ctx("CJS", "ACC-X", 2021, "Senate Reported")))

    def check_case(self, vid, rule_id, phrase):
        oid = self.val[vid]["observation_id"]
        res = self.outcomes[oid]
        self.assertEqual(res["result"], "match")
        self.assertIn(phrase, res["how"])
        self.assertIn(phrase, self.val[vid]["expected_result"])
        other = self.outcome_without(rule_id, oid)
        self.assertNotIn(phrase, other.get("how") or "")
        o = next(x for x in STAGED["observations"] if x["observation_id"] == oid)
        cjs = CT.compare(o, res["paragraph"], [], ctx=CT.R.Ctx("CJS", "ACC-X", int(o["fiscal_year"]), o["stage"]))
        self.assertNotIn(phrase, cjs[2] or "")

    def test_derivation(self):
        self.check_case("VAL-TXT-LHHS-1442", "LHHS-241-DERIVATION", "from section 241 (PHS evaluation funds, not new")

    def test_no_main(self):
        self.check_case("VAL-TXT-LHHS-1647", "LHHS-241-NO-MAIN", "no new budget authority: the paragraph")
        self.assertFalse(rule("LHHS-241-NO-MAIN").scope.contains("LHHS", "ACC-HHS-CDC-TOTAL", 2022, "Enacted"))

    def test_onc_cell_note(self):
        import approps_store as S
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "x.db"
            S.load(S.reference_workbook(), db)
            conn = S.connect(db, readonly=True)
            try:
                notes = lambda: {(o["fiscal_year"], o["stage"]): o.get("cell_note") or ""  # noqa: E731
                                 for o in S.history(conn, "ACC-HHS-OS-ONC")["observations"]
                                 if "PHS evaluation set-aside" in (o.get("cell_note") or "")}
                got = notes()
                self.assertIn((2022, "Senate Reported"), got)
                self.assertIn("Funded through the PHS evaluation set-aside ($86,614,000)", got[(2022, "Senate Reported")])
                with without("LHHS-ONC-CELL-NOTE"):
                    self.assertEqual(notes(), {})
            finally:
                conn.close()
        self.assertFalse(rule("LHHS-ONC-CELL-NOTE").scope.contains("LHHS", "ACC-HHS-CDC-TOTAL", 2022, "Enacted"))
        self.assertFalse(rule("LHHS-ONC-CELL-NOTE").scope.contains("CJS", "ACC-HHS-OS-ONC", 2022, "Enacted"))


class DefaultSubcommittee(unittest.TestCase):
    def test_the_page_opens_on_the_datas_default(self):
        idx = json.loads((ROOT / "docs" / "data" / "index.json").read_text())
        self.assertEqual(idx["default_subcommittee"], "LHHS")
        self.assertEqual(SC.default_subcommittee(["CJS", "LHHS"]), "LHHS")
        self.assertEqual(SC.default_subcommittee(["SYNLB"]), "SYNLB")       # not held: the first listed
        page = (ROOT / "web" / "index.html").read_text()
        self.assertNotIn('"LHHS"', page)


# ------------------------------------------------------------------------------------------------ the synthetic subcommittee

class SyntheticSubcommittee(unittest.TestCase):
    """SYNLB end to end, in its own data root (APPROPS_DATA_ROOT) with its own rule file (APPROPS_RULES_DIRS)."""

    @classmethod
    def setUpClass(cls):
        import fixture
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        fixture.build(cls.root, STAGED["components"])
        cls.env = dict(os.environ, APPROPS_DATA_ROOT=str(cls.root), APPROPS_RULES_DIRS=str(FIXTURE / "rules"))
        cls.intake = {}
        for st in ("PB", "HOUSE", "SENATE", "ENACTED"):
            cls.intake[st] = cls.run_(["scripts/intake.py", "--subcommittee", "SYNLB", "--fy", "2031", "--stage", st,
                                       "--write"])
            with open(cls.root / "reference" / "review" / "SYNLB" / "intake" / f"FY2031-{st}.csv") as f:
                import csv
                cls.intake[st] = (cls.intake[st], list(csv.DictReader(f)))
        cls.data = json.loads((cls.root / "data" / "staged.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_(cls, args, check=False):
        r = subprocess.run([sys.executable] + args, cwd=ROOT, env=cls.env, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AssertionError(f"{args}: {r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        return r

    def test_intake_runs_every_stage(self):
        self.assertEqual({st: r.returncode for st, (r, _) in self.intake.items()},
                         {"PB": 0, "HOUSE": 2, "SENATE": 0, "ENACTED": 0})

    def test_the_unknown_phrase_lands_in_review(self):
        _, items = self.intake["HOUSE"]
        review = [i for i in items if i["status"] == "review"]
        self.assertEqual([(i["kind"], i["found"]) for i in review], [("phrase no rule reads", "$2,000,000")])
        self.assertIn("Synthetic Member Gift Fund", review[0]["what"])

    def test_the_quirk_is_its_own_rule_not_an_absence_or_a_blank(self):
        for st, account in (("HOUSE", "ACC-SYNLB-SENATOR-OPS"), ("SENATE", "ACC-SYNLB-MEMBER-OPS")):
            _, items = self.intake[st]
            info = [i for i in items if i["kind"] == "explained by a rule"]
            self.assertEqual([(i["account"], i["rule"]) for i in info], [(account, "SYNLB-CHAMBER-CUSTOM")])
            self.assertIn("not carried by custom", info[0]["what"])
            self.assertFalse([i for i in items if i["kind"] == "no figure"])
        self.assertEqual(self.data["confirmed_absences"], [])                  # never recorded as an absence
        # outside the rule's scope (the Enacted law carries both), nothing is explained away
        self.assertFalse([i for i in self.intake["ENACTED"][1] if i["kind"] == "explained by a rule"])

    def test_without_its_rule_file_the_subcommittee_is_unknown(self):
        env = dict(self.env, APPROPS_RULES_DIRS="")
        r = subprocess.run([sys.executable, "scripts/intake.py", "--subcommittee", "SYNLB", "--fy", "2031", "--stage",
                            "SENATE"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)               # unknown subcommittee: a missing input, the run stops
        self.assertIn("missing input", r.stdout)

    def test_without_its_quirk_the_missing_line_is_a_review_item(self):
        with tempfile.TemporaryDirectory() as d:
            src = (FIXTURE / "rules" / "SYNLB.py").read_text()
            (Path(d) / "SYNLB.py").write_text(src + "\nRULES = [r for r in RULES if r.id != 'SYNLB-CHAMBER-CUSTOM']\n")
            env = dict(self.env, APPROPS_RULES_DIRS=d)
            r = subprocess.run([sys.executable, "scripts/intake.py", "--subcommittee", "SYNLB", "--fy", "2031",
                                "--stage", "SENATE"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("review   no figure: 1", r.stdout)

    def test_a_missing_input_stops_the_run(self):
        import fixture
        with tempfile.TemporaryDirectory() as d:
            fixture.build(d, STAGED["components"], request=False)
            env = dict(self.env, APPROPS_DATA_ROOT=d)
            r = subprocess.run([sys.executable, "scripts/intake.py", "--subcommittee", "SYNLB", "--fy", "2031",
                                "--stage", "PB", "--write"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("stopped: a missing input", r.stdout)
            self.assertEqual(json.loads((Path(d) / "data" / "staged.json").read_text())["validations"], [])

    def test_tagger_and_measure_reconcile(self):
        comps = [(c["component_id"], c["observation_id"], c["component_type"], c["designation"], c["amount"],
                  c["in_headline"]) for c in self.data["observation_components"]]
        self.assertEqual(comps, [("CMP-SYNLB-0001", "OBS-SYNLB-0003", "designated", "emergency", 5000000, "TRUE"),
                                 ("CMP-SYNLB-0002", "OBS-SYNLB-0005", "designated", "emergency", 5000000, "TRUE")])
        mr = {v["validation_id"]: v["result"] for v in self.data["validations"] if v["validation_id"].startswith("VAL-MR")}
        self.assertEqual(mr, {"VAL-MR-SYNLB-0003": "pass", "VAL-MR-SYNLB-0005": "pass"})
        for script in ("check_legislative_text.py", "tag_components.py", "measure_reconcile.py"):
            self.run_([f"scripts/{script}", "--ci"], check=True)

    def test_workbook_and_export(self):
        wb = self.root / "build" / "wb.xlsx"
        self.run_(["-c", f"import approps_store as S; S.build_workbook({str(wb)!r})"], check=True)
        out = self.root / "docs"
        self.run_(["export_static.py", "--workbook", str(wb), "--out", str(out)], check=True)
        listing = json.loads((out / "data" / "subcommittees.json").read_text())
        self.assertEqual(listing, {"subcommittees": ["SYNLB"],
                                   "names": {"SYNLB": "Synthetic Legislative Branch (test fixture)"}})
        grid = json.loads((out / "data" / "subcommittees" / "SYNLB.json").read_text())
        self.assertEqual(grid["subcommittee"], "SYNLB")
        self.assertEqual(sorted(r["account"]["canonical_name"] for r in grid["rows"]),
                         ["Member Operations", "Senator Operations"])
        import openpyxl
        readme = [r[0] for r in openpyxl.load_workbook(wb)["Read Me"].iter_rows(max_col=1, values_only=True)]
        self.assertIn("\u2022 Synthetic Legislative Branch (test fixture) (SYNLB): Title I; 2 accounts (totals "
                      "included); FY2031\u2013FY2031", readme)

    def test_a_third_subcommittee_beside_the_two(self):
        """SYNLB's records added to the repository's data (IDs can't collide): the export holds a third
        subcommittee, CJS's and Labor-HHS's files are byte for byte the committed ones, and the page shows SYNLB's
        grid under its own display name."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data" / "legislative_text").mkdir(parents=True)
            (root / "reference" / "review").mkdir(parents=True)
            (root / "reference" / "review" / "accounting").symlink_to(ROOT / "reference" / "review" / "accounting")
            for p in list((ROOT / "data" / "legislative_text").glob("*.json")):
                (root / "data" / "legislative_text" / p.name).symlink_to(p)
            for p in (self.root / "data" / "legislative_text").glob("SRC-*.json"):
                (root / "data" / "legislative_text" / p.name).write_bytes(p.read_bytes())
            merged = json.loads(json.dumps(STAGED))
            for table, rows in self.data.items():
                if table != "components":
                    merged[table] = merged.get(table, []) + rows
            for table in ("observations", "validations", "observation_components", "source_docs"):
                ids_ = [next(iter(r.values())) for r in merged[table]]
                self.assertEqual(len(ids_), len(set(ids_)), table)
            (root / "data" / "staged.json").write_text(json.dumps(merged, indent=1, ensure_ascii=False))
            env = dict(self.env, APPROPS_DATA_ROOT=str(root))
            wb, out = root / "build" / "wb.xlsx", root / "docs"
            for args in (["-c", f"import approps_store as S; S.build_workbook({str(wb)!r})"],
                         ["export_static.py", "--workbook", str(wb), "--out", str(out)]):
                r = subprocess.run([sys.executable] + args, cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            listing = json.loads((out / "data" / "subcommittees.json").read_text())
            self.assertEqual(listing["subcommittees"], ["CJS", "LHHS", "SYNLB"])
            self.assertEqual(listing["names"]["SYNLB"], "Synthetic Legislative Branch (test fixture)")
            committed = ROOT / "docs" / "data"
            # the site-wide files list every subcommittee (index.json, subcommittees.json, whats_new.json's
            # documents); every other file -- CJS's and Labor-HHS's grids, every account -- is byte for byte the same
            same = [p.relative_to(committed) for p in committed.rglob("*.json")
                    if p.name not in ("index.json", "subcommittees.json", "whats_new.json")]
            self.assertTrue(same)
            for rel in same:
                self.assertEqual((out / "data" / rel).read_bytes(), (committed / rel).read_bytes(), rel)
            mine = {d["document_id"] for d in self.data["source_docs"]}
            new, old = (json.loads((x / "whats_new.json").read_text()) for x in (out / "data", committed))
            for k in ("documents", "undated"):
                new[k] = [x for x in new[k] if not (set(json.dumps(x).split('"')) & mine)]
            self.assertEqual(new, old)
            if not (sync_playwright and chromium_path()):
                self.skipTest("playwright / chromium not available: the page check runs in the browser job")
            self.page_shows(out)

    def page_shows(self, out):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(out)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium_path())
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{srv.server_address[1]}/?view=compare&sc=SYNLB")
                page.wait_for_selector("#sc option", state="attached")
                options = page.eval_on_selector_all("#sc option", "os => os.map(o => [o.value, o.text])")
                self.assertIn(["SYNLB", "Synthetic Legislative Branch (test fixture)"], options)
                self.assertEqual(page.eval_on_selector("#sc", "s => s.value"), "SYNLB")
                page.wait_for_function("document.body.innerText.includes('Member Operations')")
                self.assertIn("Senator Operations", page.inner_text("body"))
                self.assertEqual(errors, [])
                browser.close()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
