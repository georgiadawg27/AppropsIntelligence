"""The Labor-HHS backfill's rules that are not figures (reference/review/lhhs/backfill)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference" / "review" / "lhhs" / "backfill"))
_argv, sys.argv = sys.argv, sys.argv[:1]
import build_year as B  # noqa: E402
sys.argv = _argv


class FormerNamesByYear(unittest.TestCase):
    """HRSA's 'Program Management' is ACC-HHS-HRSA-PROGRAM-SUPPORT's headline through FY2022 and a line inside it
    from FY2023 (owner, 2026-10-08): the name resolves by year, not by name."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "staged.json").read_text())

    def test_the_name_resolves_differently_by_year(self):
        pm = ("ACC-HHS-HRSA-PROGRAM-SUPPORT", "program management")
        for fy in (2017, 2021, 2022):
            self.assertIn(pm, B.former_names(self.data, {}, fy), fy)
        for fy in (2023, 2026):
            self.assertNotIn(pm, B.former_names(self.data, {}, fy), fy)
        # an unbounded former name applies in every year
        self.assertIn(("ACC-HHS-NIH-NICHD", "national institute of child health and human development"),
                      B.former_names(self.data, {}, 2026))

    def test_the_data_follows_it(self):
        heads = [o for o in self.data["observations"] if o["canonical_account_id"] == "ACC-HHS-HRSA-PROGRAM-SUPPORT"
                 and not o["component"] and o["verification_status"] != "superseded"]
        printed_pm = {o["fiscal_year"] for o in heads if "printed as 'Program Management'" in o["source_table_or_section"]}
        self.assertEqual(printed_pm, {y for y in {o["fiscal_year"] for o in heads} if y <= 2022})
        self.assertFalse([o["observation_id"] for o in heads if o["fiscal_year"] >= 2023
                          and "'Program Management'" in o["source_table_or_section"]])


def published(sc="LHHS"):
    g = json.loads((ROOT / "docs" / "data" / "subcommittees" / f"{sc}.json").read_text())
    return {r["account"]["canonical_account_id"]: r for r in g["rows"]}


def head(cell):
    return next(l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"])


class OldStructure(unittest.TestCase):
    """ASPR's accounts split from PHSSEF (REL-LHHS-0009/0010/0016, confirmed 2026-10-08): a document that funds the
    programs inside PHSSEF -- every one before the split's effective year, FY2024 -- has no ASPR figure. Enacted follows
    the enacting law (owner, 2026-10-09): P.L. 117-328 funds ASPR inside PHSSEF, so FY2023 Enacted reads 'no figure'
    too, S.Rept. 118-84's restated figure in its corner; the FY2023 House, request and Senate cells carry 'Funded
    within PHSSEF in this document.'; the first figure is FY2024's, 'Funded within PHSSEF before FY2024.'"""

    @classmethod
    def setUpClass(cls):
        cls.rows = published()

    def test_aspr(self):
        restated = {"ACC-HHS-ASPR-TOTAL": "3,629,677", "ACC-HHS-ASPR-RDP": "3,062,991", "ACC-HHS-ASPR-OPER": "566,686"}
        for aid in ("ACC-HHS-ASPR-TOTAL", "ACC-HHS-ASPR-RDP", "ACC-HHS-ASPR-OPER"):
            cells = self.rows[aid]["cells"]
            for key, cell in cells.items():
                y, st = int(key.split("|")[0]), key.split("|")[1]
                h = head(cell)
                if y < 2023:
                    # (a stage no committee reported -- FY2020 Senate -- reads 'not reported': no document at all)
                    self.assertIn((h["state"], h.get("note")), (("no_figure", None), ("not_reported", None)), (aid, key))
                    self.assertTrue(h["state"] == "no_figure" or (y, st) == (2020, "Senate Reported"), (aid, key))
                elif y == 2023 and st != "Enacted":
                    self.assertEqual((h["state"], h.get("note")), ("no_figure", "Funded within PHSSEF in this document."),
                                     (aid, key))
            enacted = head(cells["2023|Enacted"])
            self.assertEqual(enacted["state"], "no_figure", aid)
            self.assertEqual(enacted["note"], "Funded within PHSSEF in this document. S.Rept. 118-84 restates FY2023 in "
                                              "the FY2024 structure (ASPR separate from PHSSEF): "
                                              f"{restated[aid]} thousand (p.{389 if aid.endswith('RDP') else 390}).")
            first = head(cells["2024|Enacted"])
            self.assertEqual(first["state"], "value")
            self.assertEqual(first["observations"][0]["cell_note"], "Funded within PHSSEF before FY2024.")
        # PHSSEF's FY2023 Enacted is the law's: its four paragraphs
        o = head(self.rows["ACC-HHS-OS-PHSSEF"]["cells"]["2023|Enacted"])["observations"][0]
        self.assertEqual(o["amount"], 3_767_569_000)
        self.assertIn("S.Rept. 118-84 restates FY2023", o["cell_note"])
        self.assertEqual(head(self.rows["ACC-HHS-OS-PHSSEF"]["cells"]["2022|Enacted"])["state"], "value")


class StagesAsTheirDocumentsPrint(unittest.TestCase):
    """Each stage as its own documents print it; Enacted follows the enacting law; stages of one year with different
    structures are a proposed relationship with cell notes (owner, 2026-10-09: the FY2019 Strategic National
    Stockpile, under PHSSEF in the request and the House bill, in CDC in the Senate bill and P.L. 115-245)."""

    @classmethod
    def setUpClass(cls):
        cls.rows = published()

    def test_fy2019_enacted_follows_the_law(self):
        cdc = head(self.rows["ACC-HHS-CDC-PREPAREDNESS"]["cells"]["2019|Enacted"])["observations"][0]
        ps = head(self.rows["ACC-HHS-OS-PHSSEF"]["cells"]["2019|Enacted"])["observations"][0]
        self.assertEqual((cdc["amount"], ps["amount"]), (1_465_200_000, 2_021_458_000))
        for o, printed in ((cdc, "855,200"), (ps, "2,631,458")):
            self.assertTrue(o["cell_note"].startswith("H.Rept. 116-62 restates FY2019 in the FY2020 structure (SNS under "
                                                      f"PHSSEF): {printed} thousand"), o["cell_note"])

    def test_proposed_and_actual_moves(self):
        for aid in ("ACC-HHS-CDC-PREPAREDNESS", "ACC-HHS-OS-PHSSEF"):
            cells = self.rows[aid]["cells"]
            for st, x in (("President's Budget", "575,000,000"), ("House Reported", "710,000,000")):
                note = head(cells[f"2019|{st}"])["observations"][0]["cell_note"]
                self.assertEqual(note, f"Proposed moving the Strategic National Stockpile (${x}) to PHSSEF/ASPR; the "
                                       "Senate and the enacted law kept it in CDC.", (aid, st))
            self.assertIsNone(head(cells["2019|Senate Reported"])["observations"][0].get("cell_note"), aid)
            for st in ("President's Budget", "House Reported", "Enacted"):
                self.assertIn("moved from CDC to PHSSEF/ASPR from FY2020",
                              head(cells[f"2020|{st}"])["observations"][0]["cell_note"], (aid, st))
        rels = {r["relationship_id"]: r for r in self.rows["ACC-HHS-CDC-PREPAREDNESS"]["relationships"]}
        self.assertEqual({(r["relationship_type"], r["effective_fiscal_year"]) for r in rels.values()
                          if r["to_account_id"] == "ACC-HHS-OS-PHSSEF"}, {("moved_reclassified", 2019),
                                                                           ("moved_reclassified", 2020)})


class Fy2017(unittest.TestCase):
    """FY2017 under the standing rules (owner, 2026-10-09)."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "staged.json").read_text())
        cls.obs = [o for o in cls.data["observations"] if o["fiscal_year"] == 2017
                   and o["verification_status"] != "superseded"]

    def one(self, aid, stage, component=""):
        hits = [o for o in self.obs if (o["canonical_account_id"], o["stage"], o["component"] or "") == (aid, stage, component)]
        self.assertEqual(len(hits), 1, (aid, stage, component))
        return hits[0]

    def test_nef_prints_an_amount_and_cites_sec_226(self):
        # the table prints the line (H.Rept. 114-699 p.285), so the figure is recorded and cites the bill's provision,
        # which itself states no amount; no other FY2017 text holds an NEF provision: confirmed absences
        o = self.one("ACC-HHS-GP-NEF-RESCISSION", "House Reported")
        self.assertEqual((o["amount"], o["amount_type"], o["source_page"]), (-200_000_000, "rescission", "285"))
        self.assertIn("H.R. 5926 sec. 226 (p.99) terminates the Nonrecurring Expenses Fund", o["source_table_or_section"])
        gone = {(x["stage"], x["component"]) for x in self.data["confirmed_absences"]
                if x["canonical_account_id"] == "ACC-HHS-GP-NEF-RESCISSION" and x["fiscal_year"] == 2017}
        self.assertNotIn(("House Reported", ""), gone)
        self.assertTrue({("Senate Reported", ""), ("President's Budget", ""), ("Enacted", "")} <= gone)

    def test_cures_did_not_exist_before_the_act(self):
        gone = {x["stage"]: x["evidence"] for x in self.data["confirmed_absences"]
                if x["canonical_account_id"] == "ACC-HHS-NIH-CURES" and x["fiscal_year"] == 2017}
        self.assertEqual(set(gone), {"House Reported", "Senate Reported", "President's Budget"})
        self.assertTrue(all("did not exist yet" in e and "P.L. 114-255" in e for e in gone.values()))
        self.assertEqual(self.one("ACC-HHS-NIH-CURES", "Enacted")["amount"], 352_000_000)

    def test_senate_kids_first_is_inside_the_printed_od(self):
        # S.Rept. 114-274 prints Kids First '(non-add)': the OD line already holds it, so the headline is not derived
        od = self.one("ACC-HHS-NIH-OD", "Senate Reported")
        self.assertEqual(od["amount"], 1_443_752_000)
        self.assertNotEqual(od["extraction_method"], "derived")
        self.assertEqual(self.one("ACC-HHS-NIH-OD", "Senate Reported", "kids_first")["amount"], 12_600_000)

    def test_one_off_proposals_are_lines_with_notes(self):
        for aid, stage, comp, amount in (
                ("ACC-HHS-SAMHSA-PREVENTION", "House Reported", "chamber_proposal_opioid_response", 500_000_000),
                ("ACC-HHS-ACF-SSBG", "President's Budget", "request_proposal_ssbg_research", 18_500_000),
                ("ACC-HHS-ACF-TOTAL", "President's Budget", "request_proposal_childrens_research", 10_000_000)):
            o = self.one(aid, stage, comp)
            self.assertEqual(o["amount"], amount, comp)
            self.assertIn("(not enacted)", o["source_table_or_section"], comp)

    def test_cdc_wide_subtotals_rederived(self):
        for stage, amount in (("House Reported", 413_570_000), ("Senate Reported", 113_570_000),
                              ("Enacted", 113_570_000), ("President's Budget", 113_570_000)):
            self.assertEqual(self.one("ACC-HHS-CDC-PROGRAM-SUPPORT", stage)["amount"], amount, stage)

    def test_nothing_left_open(self):
        ids = {o["observation_id"] for o in self.obs}
        self.assertFalse([v["validation_id"] for v in self.data["validations"]
                          if v["observation_id"] in ids and v["human_review_status"] == "pending"])
        self.assertFalse([o["observation_id"] for o in self.obs if o["verification_status"] == "flagged"])


class PhsEvaluationSetAside(unittest.TestCase):
    """A 0 headline with a program-level line from the PHS evaluation set-aside carries the note (ONC FY2022)."""

    def test_onc_fy2022(self):
        cells = published()["ACC-HHS-OS-ONC"]["cells"]
        for st, amount in (("House Reported", 86_614_000), ("President's Budget", 86_614_000),
                           ("Senate Reported", 86_614_000), ("Enacted", 64_238_000)):
            h = head(cells[f"2022|{st}"])
            o = h["observations"][0]
            self.assertEqual(o["amount"], 0, st)
            self.assertEqual(o["cell_note"], f"Funded through the PHS evaluation set-aside (${amount:,}), not new "
                                             "budget authority.", st)
            pl = [l for l in cells[f"2022|{st}"]["lines"] if l["component"] == "program_level"]
            self.assertEqual([l["observations"][0]["amount"] for l in pl], [amount], st)


class RetiredIds(unittest.TestCase):
    """A retired ID (reference/review/lhhs/retired_ids.json) is gone from the data and never reused."""

    def test_never_reused(self):
        data = json.loads((ROOT / "data" / "staged.json").read_text())
        retired = json.loads((ROOT / "reference" / "review" / "lhhs" / "retired_ids.json").read_text())
        live = {x["confirmed_absence_id"] for x in data["confirmed_absences"]} | \
            {r["relationship_id"] for r in data["relationships"]}
        gone = {i for v in retired.values() if isinstance(v, dict) for i in v}
        self.assertFalse(live & gone)
        self.assertIn("CA-LHHS-0075", gone)
        # the Title XVIII limitation's absences are retired: its cells before FY2026 read missing
        self.assertFalse([x for x in data["confirmed_absences"]
                          if x["canonical_account_id"] == "ACC-HHS-GP-MEDICARE-LIMITATION"])


class NotReported(unittest.TestCase):
    """A stage no committee reported -- no bill, no report, no draft (Bill Report Reference not_reported, FY2020
    Senate) -- reads 'not reported' in every account's cell, never missing."""

    def test_fy2020_senate(self):
        rows = published()
        states = {line["state"] for r in rows.values() for line in r["cells"].get("2020|Senate Reported", {}).get("lines", [])}
        self.assertEqual(states, {"not_reported"})
        counts = json.loads((ROOT / "docs" / "data" / "subcommittees" / "LHHS.json").read_text())["state_counts"]
        self.assertGreaterEqual(counts["not_reported"], len(rows))           # (and the Title II total row)


if __name__ == "__main__":
    unittest.main()
