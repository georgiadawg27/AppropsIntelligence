"""
Accounting views, phase 1: the component and attribute schema (accounting.py), the build and the loader
refusing bad records, the tagging rules (scripts/tag_components.py), the measure_reconcile check
(scripts/measure_reconcile.py) and the text check's like-with-like step. Offline: small inline fixtures and
the committed data/staged.json.
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
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
         "evidence": "Budget Appendix, HHS chapter: the account's schedule"}
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

    def test_subfunction_is_the_codes_last_three_digits(self):
        code = attr(attribute="account_id_code", value="075-0849-0-1-550")
        self.assertEqual(self.check(code, attr(aid="AAT-LHHS-0002", attribute="budget_subfunction", value="550")), [])
        self.assertTrue(self.check(code, attr(aid="AAT-LHHS-0002", attribute="budget_subfunction", value="551")))


class Views(unittest.TestCase):
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

    def test_committed_records_are_up_to_date(self):
        records, _ = MR.run(STAGED)
        self.assertEqual(MR.merged(STAGED, records), STAGED["validations"])

    def test_never_a_confirming_rule(self):
        import validate_approps as V
        self.assertNotIn("measure_reconcile", V.CONFIRMING_RULES)


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
