"""
Accounting views, phases 1 and 2: the component and attribute schema (accounting.py), the build and the loader
refusing bad records, the tagging rules (scripts/tag_components.py), the measure_reconcile check
(scripts/measure_reconcile.py) and the text check's like-with-like step. Offline: small inline fixtures and
the committed data/staged.json.
"""

import collections
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import accounting as AC  # noqa: E402
import approps_store as S  # noqa: E402
import check_legislative_text as C  # noqa: E402
import measure_reconcile as MR  # noqa: E402
import tag_components as TC  # noqa: E402

STAGED = json.loads((ROOT / "data" / "staged.json").read_text())


def headline(oid="OBS-LHHS-9001", amount=1000, acc="ACC-X", fy=2024, stage="Enacted"):
    return {"observation_id": oid, "canonical_account_id": acc, "fiscal_year": fy, "stage": stage,
            "amount": amount, "amount_type": "budget authority", "component": "", "verification_status": "unverified"}


def comp(cid="CMP-LHHS-9001", oid="OBS-LHHS-9001", t="earmark", amount=100, in_headline="TRUE", **kw):
    c = {"component_id": cid, "observation_id": oid, "component_type": t, "designation": "", "transfer_from": "",
         "amount": amount, "in_headline": in_headline, "source_document_id": "SRC-A", "source_location": "p.1",
         "quoted_text": "projects specified in the table [$100]", "tag_method": "auto_text", "tag_rule": "earmark",
         "source_observation_id": "", "carried_from_component_id": "", "notes": ""}
    c.update(kw)
    return c


def errors(*components, observations=None):
    obs = observations or {"OBS-LHHS-9001": headline()}
    return AC.component_errors(list(components), obs, {"SRC-A"})


def attr(aid="AAT-LHHS-0001", attribute="budget_class", value="discretionary", fy=2024, **kw):
    r = {"account_attribute_id": aid, "canonical_account_id": "ACC-X", "fiscal_year": fy, "attribute": attribute,
         "value": value, "source_document_id": "SRC-A", "source_location": "p.12",
         "evidence": "Budget Appendix, HHS chapter: the account's schedule", "stage_basis": "enacted"}
    r.update(kw)
    return r


class ComponentSchema(unittest.TestCase):
    def test_a_sound_record_passes(self):
        self.assertEqual(errors(comp()), [])

    def test_signs(self):
        self.assertTrue(any("negative" in e for e in errors(comp(t="rescission", amount=50))))
        self.assertEqual(errors(comp(t="rescission", amount=-50)), [])
        self.assertTrue(any("positive" in e for e in errors(comp(t="earmark", amount=-50))))

    def test_designation_only_on_designated(self):
        self.assertTrue(errors(comp(t="designated")))
        self.assertEqual(errors(comp(t="designated", designation="emergency")), [])
        self.assertTrue(errors(comp(t="earmark", designation="emergency")))

    def test_transfer_in_names_its_source(self):
        self.assertTrue(errors(comp(t="transfer_in")))
        self.assertEqual(errors(comp(t="transfer_in", transfer_from="section 241")), [])
        self.assertTrue(errors(comp(t="earmark", transfer_from="section 241")))

    def test_sits_on_a_headline_that_is_not_superseded(self):
        part = dict(headline(), component="defense")
        self.assertTrue(errors(comp(), observations={"OBS-LHHS-9001": part}))
        gone = dict(headline(), verification_status="superseded")
        self.assertTrue(errors(comp(), observations={"OBS-LHHS-9001": gone}))
        self.assertTrue(errors(comp(oid="OBS-LHHS-0000")))

    def test_ids_type_and_duplicates(self):
        self.assertTrue(errors(comp(cid="CMP-1")))
        self.assertTrue(errors(comp(t="earmarks")))
        self.assertTrue(errors(comp(), comp()))
        self.assertTrue(errors(comp(in_headline="yes")))
        self.assertTrue(errors(comp(source_document_id="SRC-NONE")))

    def test_retagged_and_carried_forward(self):
        self.assertTrue(errors(comp(tag_method="retagged")))
        obs = {"OBS-LHHS-9001": headline(), "OBS-LHHS-9002": headline("OBS-LHHS-9002", fy=2025),
               "OBS-LHHS-9003": dict(headline("OBS-LHHS-9003"), component="part")}
        self.assertEqual(errors(comp(tag_method="retagged", source_observation_id="OBS-LHHS-9003"),
                                observations=obs), [])
        self.assertTrue(errors(comp(tag_method="retagged", source_observation_id="OBS-LHHS-9002"), observations=obs))
        a = comp()
        b = comp(cid="CMP-LHHS-9002", oid="OBS-LHHS-9002", tag_method="carried_forward",
                 carried_from_component_id="CMP-LHHS-9001")
        self.assertEqual(errors(a, b, observations=obs), [])
        self.assertTrue(errors(a, dict(b, amount=99), observations=obs))
        self.assertTrue(errors(a, dict(b, carried_from_component_id=""), observations=obs))

    def test_a_component_within_another(self):
        outer = comp(t="trust_fund_limitation", amount=900)
        inner = comp(cid="CMP-LHHS-9002", t="designated", designation="cap_adjustment", amount=600,
                     within_component_id="CMP-LHHS-9001")
        self.assertEqual(errors(outer, inner), [])
        self.assertEqual(AC.split(900, [outer, inner])[0], 0)
        self.assertTrue(errors(outer, dict(inner, amount=901)))
        self.assertTrue(errors(outer, dict(inner, in_headline="FALSE")))
        self.assertTrue(errors(outer, dict(inner, within_component_id="CMP-LHHS-9999")))

    def test_no_view_counts_a_nested_component_with_its_parent(self):
        outer = comp(t="trust_fund_limitation", amount=900)
        inner = comp(cid="CMP-LHHS-9002", t="designated", designation="cap_adjustment", amount=600,
                     within_component_id="CMP-LHHS-9001")
        beside = [dict(outer, in_headline="FALSE"), dict(inner, in_headline="FALSE")]
        views = dict(AC.VIEWS, split_cap={"label": "a view that splits its parent", "add": (),
                                           "remove": (("designated", "cap_adjustment"),)},
                     both={"label": "a view that picks both", "add": ("trust_fund_limitation", "designated"),
                           "remove": ("trust_fund_limitation", "designated")})
        by_obs = AC.components_by_observation(STAGED)
        cases = [[outer, inner], beside] + list(by_obs.values())
        with unittest.mock.patch.dict(AC.VIEWS, views):
            for view in views:
                for cs in cases:
                    used = {c["component_id"] for c in AC.view_adjustment(cs, view)[1]}
                    for c in cs:
                        self.assertFalse(c["component_id"] in used and c.get("within_component_id") in used,
                                         f"{view}: {c['component_id']} and its parent both counted")
            # the nested one is used only to split its parent
            self.assertEqual(AC.view_adjustment([outer, inner], "split_cap"), (-600, [inner]))
            self.assertEqual(AC.view_adjustment(beside, "both")[0], 900)
            self.assertEqual(AC.view_adjustment([outer, inner], "both")[0], -900)

    def test_the_committed_data_is_sound(self):
        self.assertEqual(AC.data_errors(STAGED), [])

    def test_a_retired_id_is_never_reused(self):
        data = copy.deepcopy(STAGED)
        data["observation_components_retired"] = [data["observation_components"][0]["component_id"]]
        self.assertTrue(any("retired" in e for e in AC.data_errors(data)))


class AttributeSchema(unittest.TestCase):
    def check(self, *rows):
        return AC.attribute_errors(list(rows), {"ACC-X"}, {"SRC-A"})

    def test_values_and_evidence(self):
        self.assertEqual(self.check(attr()), [])
        self.assertTrue(self.check(attr(value="discretionery")))
        self.assertTrue(self.check(attr(evidence="")))
        self.assertTrue(self.check(attr(attribute="budget_subfunction", value="55")))
        self.assertTrue(self.check(attr(attribute="account_id_code", value="075-849-0-1-550")))
        self.assertTrue(self.check(attr(fy=2016)))

    def test_one_value_per_account_year(self):
        self.assertTrue(self.check(attr(), attr(aid="AAT-LHHS-0002", value="mandatory")))
        self.assertEqual(self.check(attr(), attr(aid="AAT-LHHS-0002", fy=2025)), [])

    def test_stage_basis(self):
        # an enacted and a request value for the same account-year are two records; anything else is refused
        self.assertEqual(self.check(attr(), attr(aid="AAT-LHHS-0002", value="mixed", stage_basis="request")), [])
        self.assertTrue(self.check(attr(stage_basis="house")))
        self.assertTrue(self.check(attr(stage_basis="")))

    def test_fund_type_is_the_codes_fund_digit(self):
        code = attr(attribute="account_id_code", value="075-8175-0-7-551")
        self.assertEqual(self.check(code, attr(aid="AAT-LHHS-0002", attribute="fund_type", value="trust")), [])
        self.assertTrue(self.check(code, attr(aid="AAT-LHHS-0002", attribute="fund_type", value="general")))

    def test_mixed_is_a_class(self):
        self.assertEqual(self.check(attr(value="mixed")), [])

    def test_the_president_s_budget_takes_the_request_value_where_there_is_one(self):
        rows = AC.attribute_index({"account_attributes": [
            attr(), attr(aid="AAT-LHHS-0002", value="mandatory", stage_basis="request")]})
        self.assertEqual(AC.attribute_value(rows, "ACC-X", 2024, "budget_class", "President's Budget")["value"],
                         "mandatory")
        self.assertEqual(AC.attribute_value(rows, "ACC-X", 2024, "budget_class", "House Reported")["value"],
                         "discretionary")
        self.assertIsNone(AC.attribute_value(rows, "ACC-X", 2025, "budget_class", "Enacted"))

    def test_committed_attributes_are_up_to_date(self):
        import appendix_attributes as A
        data = copy.deepcopy(STAGED)
        recs, notes, _ = A.build(data, json.loads(A.EXTRACT.read_text()), A.read_map())
        recs, retired = A.assign_ids(data, recs)
        self.assertEqual(recs, STAGED["account_attributes"])
        self.assertEqual(notes, STAGED["account_attribute_notes"])
        self.assertEqual(retired, STAGED.get("account_attributes_retired", []))
        self.assertEqual(AC.attribute_errors(recs, {a["canonical_account_id"] for a in STAGED["accounts"]},
                                             {d["document_id"] for d in STAGED["source_docs"]}), [])

    def test_a_mixed_account_year_takes_the_class_of_its_report_marks(self):
        import appendix_attributes as A
        marks = A.read_marks()
        attrs = AC.attribute_index(STAGED)
        for (acc, fy, basis), rows in marks.items():
            kinds = {A.MARK_CLASS[r["mark"]] for r in rows}
            rec = attrs.get((acc, fy, "budget_class", basis))
            if rec and len(kinds) == 1:
                self.assertNotEqual(rec["value"], "mixed", (acc, fy, basis))
                if rec["source_document_id"].startswith("SRC-CRPT-"):
                    self.assertEqual(rec["value"], kinds.pop())
                    self.assertIn("The Appendix account is mixed", rec["evidence"])

    def test_codes_are_fy_ns_own_volume_and_late_classes_provisional(self):
        notes = {(n["canonical_account_id"], n["fiscal_year"], n["stage_basis"]): n["note"]
                 for n in STAGED["account_attribute_notes"]}
        for r in STAGED["account_attributes"]:
            fy, basis = int(r["fiscal_year"]), r["stage_basis"]
            if r["attribute"] in ("account_id_code", "fund_type", "budget_subfunction"):
                own = r["source_document_id"].startswith(f"SRC-BUDGET-APP-FY{fy}-")
                self.assertTrue(own or "read from the FY" in notes.get((r["canonical_account_id"], fy, basis), ""),
                                r["account_attribute_id"])
            late = basis == "enacted" and r["attribute"] == "budget_class" and fy >= 2026
            self.assertEqual(bool(r["provisional"]), late, r["account_attribute_id"])

    def test_the_account_tabs_fund_type_is_the_appendixs(self):
        fund = collections.defaultdict(set)
        for r in STAGED["account_attributes"]:
            if r["attribute"] == "fund_type":
                fund[r["canonical_account_id"]].add(r["value"])
        self.assertEqual([a["canonical_account_id"] for a in STAGED["accounts"] if a.get("fund_type")
                          and fund[a["canonical_account_id"]] and a["fund_type"] not in fund[a["canonical_account_id"]]],
                         [])

    def test_totals_carry_no_attributes_of_their_own(self):
        totals = {a["canonical_account_id"] for a in STAGED["accounts"] if a.get("total_scope")}
        self.assertFalse([r for r in STAGED["account_attributes"] if r["canonical_account_id"] in totals])

    def test_subfunction_is_the_codes_last_three_digits(self):
        code = attr(attribute="account_id_code", value="075-0849-0-1-550")
        self.assertEqual(self.check(code, attr(aid="AAT-LHHS-0002", attribute="budget_subfunction", value="550")), [])
        self.assertTrue(self.check(code, attr(aid="AAT-LHHS-0002", attribute="budget_subfunction", value="551")))


class Views(unittest.TestCase):
    def test_discretionary_adjustment(self):
        cs = [comp(t="advance_enacted", amount=300, in_headline="FALSE"),
              comp(cid="CMP-LHHS-9002", t="mandatory_portion", amount=200, in_headline="TRUE")]
        self.assertEqual(AC.discretionary_adjustment(1000, cs, "discretionary"), (0, []))
        # a mandatory account comes off whole, as the bill appropriates it (advances when enacted)
        self.assertEqual(AC.discretionary_adjustment(1000, cs, "mandatory")[0], -1300)
        # a mixed account: only its mandatory_portion comes off
        self.assertEqual(AC.discretionary_adjustment(1000, cs, "mixed")[0], -200)
        self.assertIsNone(AC.discretionary_adjustment(1000, cs, None))

    def test_base_and_views(self):
        cs = [comp(t="advance_enacted", amount=300, in_headline="TRUE"),
              comp(cid="CMP-LHHS-9002", t="advance_available", amount=250, in_headline="FALSE"),
              comp(cid="CMP-LHHS-9003", t="transfer_in", amount=40, in_headline="FALSE", transfer_from="241"),
              comp(cid="CMP-LHHS-9004", t="designated", designation="emergency", amount=100, in_headline="TRUE")]
        base, inside, beside = AC.split(1000, cs)
        self.assertEqual((base, len(inside), len(beside)), (600, 2, 2))
        self.assertEqual(AC.view_amount(1000, cs, "as_printed"), 1000)
        self.assertEqual(AC.view_amount(1000, cs, "advances_when_available"), 950)
        self.assertEqual(AC.view_amount(1000, cs, "advances_when_enacted"), 1000)
        self.assertEqual(AC.view_amount(1000, cs, "program_level"), 1040)
        self.assertEqual(AC.view_amount(1000, cs, "excluding_emergencies"), 900)


class BuildAndLoaderRefuse(unittest.TestCase):
    def bad(self):
        data = copy.deepcopy(STAGED)
        c = data["observation_components"][0]
        c["amount"] = -abs(c["amount"]) if c["component_type"] not in AC.NEGATIVE_TYPES else abs(c["amount"]) or 1
        return data

    def test_the_build_stops_on_a_bad_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            (Path(tmp) / "build").mkdir()
            (Path(tmp) / "data" / "staged.json").write_text(json.dumps(self.bad()))
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_workbook.py")], cwd=tmp,
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("BUILD STOPPED: accounting", r.stdout + r.stderr)

    def test_the_loader_refuses_a_bad_component(self):
        rows = {"Appropriations Observation": [dict(headline(), _row=2)],
                "Source Document": [{"document_id": "SRC-A"}], "Account": [{"canonical_account_id": "ACC-X"}],
                "Observation Component": [dict(comp(t="rescission", amount=5, in_headline=1), _row=2)],
                "Account Attribute": [dict(attr(evidence=None), _row=2)]}
        errs = S.accounting_errors(rows)
        self.assertTrue(any("negative" in e for e in errs))
        self.assertTrue(any("evidence" in e for e in errs))
        rows["Observation Component"] = [dict(comp(in_headline=1, designation=None, transfer_from=None), _row=2)]
        rows["Account Attribute"] = []
        self.assertEqual(S.accounting_errors(rows), [])


def amt(amount, before, after=""):
    return {"amount": amount, "block": 1, "proviso": False, "phrase": f"{before} [${amount:,}] {after}"}


class TaggingRules(unittest.TestCase):
    def kind(self, a):
        r = TC.classify(a)
        return r and r[:2]

    def test_phrase_rules(self):
        self.assertEqual(self.kind(amt(5, "Provided, That", "is designated by the Congress as being for an "
                                                             "emergency requirement")), ("designated", "emergency"))
        self.assertEqual(self.kind(amt(5, "for the first quarter of fiscal year 2025,")), ("advance_enacted", ""))
        self.assertEqual(self.kind(amt(5, "and", ", which shall become available on October 1, 2024")),
                         ("advance_enacted", ""))
        self.assertEqual(self.kind(amt(5, "Provided, That", "shall be available under section 241 of the PHS Act")),
                         ("transfer_in", ""))
        self.assertEqual(self.kind(amt(5, "Provided, That not to exceed", "shall be available from the Trust Fund")),
                         ("trust_fund_limitation", ""))
        self.assertEqual(self.kind(amt(5, "Provided, That", "shall be for the projects specified in the table")),
                         ("earmark", ""))
        self.assertIsNone(TC.classify(amt(5, "Provided, That", "shall be for grants")))

    def test_the_owners_rules_of_2026_10_11(self):
        self.assertEqual(self.kind(amt(915, "program management,", ", to remain available through September 30, "
                                   "2025, to be transferred from the Federal Hospital Insura")), ("trust_fund_limitation", ""))
        self.assertEqual(self.kind(amt(604, "Act of 1985, and", "is additional new budget authority specified for "
                                   "purposes of section 251(b)(2)(C) of such Act")), ("designated", "cap_adjustment"))
        self.assertIsNone(TC.classify(amt(311, "Provided further, That of the amount provided under this heading,",
                                          "is provided to meet the terms of section 251(b)(2)(C)(ii) of the Act")))
        self.assertEqual(TC.not_a_component(amt(311, "Provided further, That", "is provided to meet the terms of "
                                                "section 251(b)(2)(C)(ii) of the Act")), "cap-adjustment base")
        self.assertEqual(self.kind(amt(407, "described in section 1001(b)(4) of the 21st Century Cures Act, in "
                                   "addition to amounts available,", ", to remain available")), ("designated", "other"))
        self.assertEqual(TC.classify(amt(12, "In addition,", "is appropriated from the 10-year Pediatric Research "
                                     "Initiative Fund described in"))[:3],
                         ("transfer_in", "", "10-year Pediatric Research Initiative Fund (PRIF)"))
        self.assertEqual(self.kind(amt(5, "Provided further, That", "shall be transferred to and merged with the "
                                   "appropriation for the “Office of Inspector General”")), ("transfer_out", ""))
        self.assertEqual(self.kind(amt(244, "Provided, That in addition to amounts provided under this heading,",
                                   "shall be available for surveillance from amounts made available under section "
                                   "4002 of the PPACA")), ("transfer_in", ""))
        self.assertIsNone(TC.classify(amt(35, "the Secretary shall provide not less than", "from amounts made "
                                           "available under this heading")))
        self.assertEqual(TC.not_a_component(amt(285, "is provided, not less than", "is provided for HEAL")),
                         "sub-allocation")
        self.assertEqual(TC.not_a_component(amt(363, "health,", ", which shall be for the purposes and in the amounts "
                                                "specified in the table under this heading")), "program table")

    def test_the_cr_drops_only_what_an_anomaly_changes(self):
        sections, changes, stated = TC.CR_ANOMALIES["ACC-HHS-SAMHSA-PROGRAM-SUPPORT"]
        self.assertEqual(stated, [])
        self.assertTrue(AC._selected(comp(t="earmark"), changes))
        self.assertFalse(AC._selected(comp(t="transfer_in", transfer_from="241"), changes))
        carried = [c for c in STAGED["observation_components"] if c["tag_method"] == "carried_forward"]
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        self.assertIn(("ACC-HHS-SAMHSA-PROGRAM-SUPPORT", "transfer_in"),
                      {(obs[c["observation_id"]]["canonical_account_id"], c["component_type"]) for c in carried})
        self.assertNotIn("earmark", {c["component_type"] for c in carried})

    def test_an_anomaly_that_states_the_amount_gives_a_new_fy2025_component(self):
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        got = {(obs[c["observation_id"]]["canonical_account_id"], c["component_type"], c["designation"]): c
               for c in STAGED["observation_components"] if c["tag_method"] == "cr_anomaly"}
        want = {("ACC-HHS-ACF-CHILD-SUPPORT", "advance_enacted", ""): 1600000000,
                ("ACC-HHS-ACF-FOSTER-CARE", "advance_enacted", ""): 3600000000,
                ("ACC-HHS-CMS-HCFAC", "trust_fund_limitation", ""): 941000000,
                ("ACC-HHS-CMS-HCFAC", "designated", "cap_adjustment"): 630000000,
                ("ACC-HHS-NIH-CURES", "designated", "other"): 127000000}
        self.assertEqual({k: c["amount"] for k, c in got.items()}, want)
        for c in got.values():
            self.assertEqual(c["source_document_id"], "SRC-PLAW-119PUBL4")
            self.assertIn(f"{c['amount']:,}", c["quoted_text"])
            self.assertEqual(obs[c["observation_id"]]["fiscal_year"], 2025)
        cap = got[("ACC-HHS-CMS-HCFAC", "designated", "cap_adjustment")]
        self.assertEqual(cap["within_component_id"], got[("ACC-HHS-CMS-HCFAC", "trust_fund_limitation", "")]["component_id"])
        # Medicaid's FY2026 advance is already held from the table line, and equals sec. 1109(b)(2)
        self.assertIn(261063820000, {c["amount"] for c in STAGED["observation_components"]
                                     if obs[c["observation_id"]]["canonical_account_id"] == "ACC-HHS-CMS-MEDICAID"
                                     and obs[c["observation_id"]]["fiscal_year"] == 2025
                                     and c["component_type"] == "advance_enacted"})

    def test_the_cr_list_names_every_fy2024_component(self):
        _, _, report = TC.build(STAGED)
        fy24 = [c for c in STAGED["observation_components"] if c["observation_id"] in
                {o["observation_id"] for o in STAGED["observations"] if o["fiscal_year"] == 2024
                 and o["stage"] == "Enacted" and o["canonical_account_id"].startswith("ACC-HHS-")}
              and c["component_type"] != "advance_available"]   # read from the FY2023 law, never carried
        listed = " ".join(w for _, w, _ in report["cr_list"])
        for c in fy24:
            self.assertIn(c["component_id"], listed)
        self.assertEqual({k for k, _, _ in report["cr_list"]} - {"carried forward", "dropped", "replaced"},
                         {"not carried: FY2025 already holds the type"})

    def test_pphf_allocation_rows(self):
        rows = [c for c in STAGED["observation_components"] if c["transfer_from"] == TC.PPHF]
        self.assertTrue(rows)
        self.assertTrue(all(c["component_type"] == "transfer_in" and c["in_headline"] == "FALSE" for c in rows))
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        cdc = collections.Counter()
        for c in rows:
            o = obs[c["observation_id"]]
            if o["canonical_account_id"].startswith("ACC-HHS-CDC-"):
                cdc[(o["fiscal_year"], o["stage"])] += c["amount"]
        # the Fund's CDC share after sequestration, each cell (the reports' totals); FY2023 House and Senate count
        # the $160,000,000 block grant on the CDC-wide line since D-2 recorded its headlines from the bill text
        # (decision 4 A); FY2027 House from H.Rept. 119-696's allocation table (D-2)
        want = {(fy, st): v for fy, h, s, e in ((2017, 908300000, 891300000, 891300000),
                                               (2018, 840600000, 800900000, 800900000),
                                               (2019, 848000000, 808300000, 804500000),
                                               (2020, 854250000, None, 854250000),
                                               (2021, 856150000, 856150000, 856150000),
                                               (2022, 903300000, 903300000, 903300000),
                                               (2023, 903300000, 903300000, 903300000),
                                               (2024, 1186200000, 1186200000, 1186200000),
                                               (2025, 1186200000, 1186200000, 1186200000),
                                               (2026, 1398375000, 1398375000, 1398375000),
                                               (2027, 1398375000, None, None))
                for st, v in (("House Reported", h), ("Senate Reported", s), ("Enacted", e)) if v}
        self.assertEqual(dict(cdc), want)

    def test_onc_headline_is_zero_with_its_241_transfer(self):
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        cells = {(2024, "House Reported"), (2024, "Senate Reported"), (2025, "Senate Reported"), (2025, "Enacted"),
                 (2026, "House Reported"), (2026, "Senate Reported")}
        heads = {(o["fiscal_year"], o["stage"]): o for o in STAGED["observations"]
                 if o["canonical_account_id"] == "ACC-HHS-OS-ONC" and not o["component"]
                 and (o["fiscal_year"], o["stage"]) in cells}
        self.assertEqual(set(heads), cells)
        for o in heads.values():
            self.assertEqual(o["amount"], 0)
            t = [c for c in STAGED["observation_components"] if c["observation_id"] == o["observation_id"]
                 and c["component_type"] == "transfer_in"]
            self.assertEqual(len(t), 1)
            self.assertEqual(t[0]["in_headline"], "FALSE")

    def test_acf_senate_earmarks_and_emergencies_after_the_head_start_list(self):
        # read only since the parser keeps a paragraph whole past its enumerated list (S. 2624, S. 4942)
        acf = {(o["fiscal_year"], o["stage"]): o["observation_id"] for o in STAGED["observations"]
               if o["canonical_account_id"] == "ACC-HHS-ACF-CFSP" and not o["component"]
               and o["stage"] == "Senate Reported"}
        for fy, earmark, emergency in ((2024, 40011000, 275000000), (2025, 42850000, 700000000)):
            got = {(c["component_type"], c["designation"], c["amount"], c["in_headline"])
                   for c in STAGED["observation_components"] if c["observation_id"] == acf[(fy, "Senate Reported")]}
            self.assertIn(("earmark", "", earmark, "TRUE"), got, fy)
            self.assertIn(("designated", "emergency", emergency, "TRUE"), got, fy)

    def test_a_241_list_tags_each_numbered_item(self):
        p = [amt(79, "in addition to amounts provided herein, the following amounts shall be available under "
                     "section 241 of the PHS Act: (1)", "to carry out subpart II"),
             amt(2, "to carry out subpart II $79; and (2)", "to evaluate programs: Provided further, That"),
             amt(9, "to evaluate programs: Provided further, That", "shall be for grants")]
        open_list, got = False, []
        for a in p:
            item, open_list = TC.list_241(a, open_list)
            got.append(item)
        self.assertEqual(got, [True, True, False])


class MeasureReconcile(unittest.TestCase):
    def test_no_base_is_negative_in_the_committed_data(self):
        _, outcomes = MR.run(STAGED)
        self.assertEqual([o for o in outcomes if o["kind"] == "base" and o["result"] == "fail"], [])

    def test_a_negative_base_fails_for_review(self):
        data = copy.deepcopy(STAGED)
        c = next(c for c in data["observation_components"] if c["in_headline"] == "TRUE" and c["amount"] > 0)
        h = next(o for o in data["observations"] if o["observation_id"] == c["observation_id"])
        c["amount"] = h["amount"] + 1
        records, _ = MR.run(data)
        r = next(r for r in records if r["observation_id"] == h["observation_id"] and r["validation_id"].startswith("VAL-MR-"))
        self.assertEqual((r["result"], r["human_review_status"]), ("fail", "pending"))
        self.assertEqual(h["amount"], next(o for o in data["observations"]
                                           if o["observation_id"] == h["observation_id"])["amount"])

    def test_one_review_item_per_cause(self):
        records, outcomes = MR.run(STAGED)
        causes = {o["cause"] for o in outcomes if o.get("cause")}
        pending = [r for r in records if r["human_review_status"] == "pending" and r["validation_id"].startswith("VAL-MRC-")]
        self.assertEqual(len(pending), len(causes))
        self.assertFalse([r for r in records if r["validation_id"].startswith("VAL-MRV-") and r["result"] == "flag"])
        self.assertNotIn("account_not_held", causes)
        self.assertTrue(any("account_not_held" in o["detail"] for o in outcomes if o["result"] == "not checked"))

    def test_every_printed_discretionary_total_is_checked(self):
        _, outcomes = MR.run(STAGED)
        disc = [o for o in outcomes if o.get("view") == "discretionary"]
        self.assertEqual(len(disc), 26)
        self.assertEqual([o for o in disc if o["result"] == "not checked"], [])
        self.assertEqual(sum(o["result"] == "pass" for o in disc), 25)

    def test_committed_records_are_up_to_date(self):
        records, _ = MR.run(STAGED)
        self.assertEqual(MR.merged(STAGED, records), STAGED["validations"])

    def test_never_a_confirming_rule(self):
        import validate_approps as V
        self.assertNotIn("measure_reconcile", V.CONFIRMING_RULES)


class D2(unittest.TestCase):
    """D-2: the President's Budget request language, the earmark rule check, CJS FY2024-FY2025."""

    def test_earmark_rule_check_passes_and_fails_when_a_component_is_missing(self):
        _, outcomes = MR.run(STAGED)
        got = {o["observation_id"]: o["result"] for o in outcomes if o["kind"] == "earmark"}
        self.assertEqual(got, {"OBS-LHHS-4109": "pass", "OBS-LHHS-4110": "pass", "OBS-LHHS-4111": "pass",
                               "OBS-LHHS-4112": "pass"})
        data = copy.deepcopy(STAGED)
        data["observation_components"] = [c for c in data["observation_components"]
                                          if not (c["observation_id"] == "OBS-LHHS-4112" and c["component_type"] == "earmark")]
        records, outcomes = MR.run(data)
        r = next(r for r in records if r["validation_id"] == "VAL-MRE-LHHS-4112")
        self.assertEqual((r["result"], r["human_review_status"]), ("fail", "pending"))
        self.assertIn("differs by $54,611,000", r["observed_result"])
        # it never adjusts the figure
        self.assertEqual(next(o for o in data["observations"] if o["observation_id"] == "OBS-LHHS-4112")["amount"],
                         175866000)

    def test_request_language_coverage_is_account_by_account(self):
        rl = TC.request_language()
        read = next(k for k, r in rl.items() if r["status"] == "read")
        missing = next(k for k, r in rl.items() if r["status"] == "not found")
        acc = {a["canonical_account_id"]: a["subcommittee"] for a in STAGED["accounts"]}
        self.assertTrue(TC.covered(read[0], acc[read[0]], read[1], "President's Budget"))
        self.assertFalse(TC.covered(missing[0], acc[missing[0]], missing[1], "President's Budget"))
        self.assertIn("request language not found", TC.not_covered_reason(missing[0], missing[1], "President's Budget"))
        # every appendix_text component cites a Budget Appendix chapter or (the 7d ruling) the report's own line
        for c in STAGED["observation_components"]:
            if c["tag_method"] == "appendix_text":
                self.assertTrue(c["source_document_id"].startswith("SRC-BUDGET-APP-"), c["component_id"])

    def test_a_default_never_leaves_a_negative_base(self):
        _, outcomes = MR.run(STAGED)
        self.assertFalse([o for o in outcomes if o["kind"] == "base" and o["result"] == "fail"])
        c = next(c for c in STAGED["observation_components"] if c["observation_id"] == "OBS-LHHS-0209"
                 and c["component_type"] == "trust_fund_limitation")
        self.assertEqual(c["in_headline"], "FALSE")
        self.assertIn("cannot be inside the headline", c["notes"])

    def test_cjs_fy2024_fy2025_are_auto_tagged_and_fy2025_follows_the_cr(self):
        for fy in (2024, 2025):
            for st in TC.TEXT_STAGES:
                self.assertIn(("CJS", fy, st), TC.AUTO_CELLS)
        self.assertIn(("CJS", 2025, "Enacted"), TC.CR_CELLS)
        obs = {o["observation_id"]: o for o in STAGED["observations"]}
        cjs25 = [c for c in STAGED["observation_components"] if obs[c["observation_id"]]["canonical_account_id"]
                 .startswith(("ACC-NASA", "ACC-NSF", "ACC-DOJ", "ACC-NOAA", "ACC-OSTP", "ACC-NSC"))
                 and obs[c["observation_id"]]["fiscal_year"] == 2025 and obs[c["observation_id"]]["stage"] == "Enacted"]
        # sec. 1111: FY2024 earmark language has no legal effect for FY2025
        self.assertFalse([c for c in cjs25 if c["component_type"] == "earmark"])


class LikeWithLike(unittest.TestCase):
    def test_a_trust_fund_limitation_matches_its_component_not_the_headline(self):
        p = {"paragraph_id": "P1", "division": None, "title": "TITLE II", "heading_path": ["X"],
             "account_heading": "X", "heading_notes": [], "blocks": 1, "location": {"position": 1},
             "amounts": [amt(15_200_000, "Provided, That for necessary administrative expenses, not to exceed",
                             "shall be available from the Trust Fund to the Secretary.")]}
        lim = comp(t="trust_fund_limitation", amount=15_200_000, tag_method="table_line")
        r, figure, _ = C.compare({"amount": 287_262_000}, p, [], comps=[lim])
        self.assertEqual((r, figure), ("component match", 15_200_000))
        self.assertEqual(C.RESULT_CODE["component match"], "info")
        # a component fitted to the text itself (auto_text) is not used
        r, _, _ = C.compare({"amount": 287_262_000}, p, [], comps=[dict(lim, tag_method="auto_text")])
        self.assertEqual(r, "ambiguous")

    def test_a_transfer_beside_the_headline_gives_the_text_figure(self):
        tap = comp(t="transfer_in", amount=60, in_headline="FALSE", transfer_from="241", tag_method="retagged")
        self.assertEqual(C.like_with_like({"amount": 0}, 60, [60], [tap])[0], 0)
        self.assertIsNone(C.like_with_like({"amount": 5}, 60, [60], [tap]))


if __name__ == "__main__":
    unittest.main()
