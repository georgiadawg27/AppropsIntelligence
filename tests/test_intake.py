"""
Brief D-5: the daily intake check (scripts/intake_watch.py, on recorded govinfo answers: no network), the committee
draft's official version (scripts/reconcile_draft.py) and the intake data the site publishes (stage_status,
each draft figure's source label, ops.json).

Run:  python -m unittest tests.test_intake -v
"""

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import approps_store as S  # noqa: E402
import intake_watch as W  # noqa: E402
import reconcile_draft as R  # noqa: E402

STAGED = json.loads((ROOT / "data" / "staged.json").read_text())
RECORDED = ROOT / "tests" / "fixtures" / "intake_watch" / "recorded_2026-10-16.json"
SINCE = "https://api.govinfo.gov/collections/{}/2026-09-16T00:00:00Z?pageSize=1000&offsetMark=*"


def recorded(extra=None, drop=()):
    """The recorded answers, with packages added (extra: {collection: [package]}) or dropped (by id)."""
    answers = copy.deepcopy(json.loads(RECORDED.read_text())["answers"])
    for c, pkgs in (extra or {}).items():
        answers[SINCE.format(c)]["packages"] += pkgs
    for page in answers.values():
        page["packages"] = [p for p in page["packages"] if p["packageId"] not in drop]
    return answers


def packages(answers):
    get = lambda url: answers[url]                                            # noqa: E731
    return [p for c in W.COLLECTIONS for p in W.listing(c, "2026-09-16", get)]


CJS_BILL = {"packageId": "BILLS-119hr9990rh", "lastModified": "2026-10-14T10:00:00Z", "dateIssued": "2026-10-13",
            "docClass": "hr", "title": "Making appropriations for the Departments of Commerce and Justice, Science, and "
            "Related Agencies for the fiscal year ending September 30, 2027, and for other purposes."}


# ---- a small data set: one CJS account, a FY2027 House Reported stage from a committee draft
def synthetic():
    acct = {"canonical_account_id": "ACC-SYN-ONE", "subcommittee": "CJS"}
    acct2 = {"canonical_account_id": "ACC-SYN-TWO", "subcommittee": "CJS"}
    acct3 = {"canonical_account_id": "ACC-SYN-THREE", "subcommittee": "CJS"}
    docs = [{"document_id": "SRC-DRAFT-SYN", "url_or_identifier": "https://appropriations.house.gov/draft.pdf",
             "publication_date": "2026-06-01"},
            {"document_id": "SRC-CRPT-119HRPT9990", "url_or_identifier":
             "https://www.govinfo.gov/content/pkg/CRPT-119hrpt9990/pdf/CRPT-119hrpt9990.pdf",
             "publication_date": "2026-10-13"}]
    refs = [{"reference_id": "BR-CJS-FY2027-HOUSE", "subcommittee": "CJS", "fiscal_year": 2027,
             "stage": "House Reported", "draft": "TRUE"}]

    def obs(oid, aid, amount, page):
        return {"observation_id": oid, "canonical_account_id": aid, "fiscal_year": 2027, "stage": "House Reported",
                "chamber": "House", "amount": amount, "amount_type": "budget authority", "component": "",
                "headline_observation_id": "", "offsetting_collections": "FALSE", "transfer_link_account_id": "",
                "source_document_id": "SRC-DRAFT-SYN", "source_page": page, "extraction_method": "human-entered",
                "confidence": 1.0, "verification_status": "unverified", "added_date": "2026-10-01"}
    return {"accounts": [acct, acct2, acct3], "source_docs": docs, "bill_report_refs": refs,
            "observations": [obs("OBS-9001", "ACC-SYN-ONE", 100_000_000, "4"),
                             obs("OBS-9002", "ACC-SYN-TWO", 50_000_000, "5")],
            "validations": []}


def official_rows(one=100_000_000, two=None, three=None):
    rows = [{"canonical_account_id": "ACC-SYN-ONE", "amount_type": "budget authority", "amount": one,
             "source_page": "12", "source_table_or_section": "Title I"}]
    if two is not None:
        rows.append({"canonical_account_id": "ACC-SYN-TWO", "amount_type": "budget authority", "amount": two,
                     "source_page": "13"})
    if three is not None:
        rows.append({"canonical_account_id": "ACC-SYN-THREE", "amount_type": "budget authority", "amount": three,
                     "source_page": "14"})
    return rows


class DailyCheck(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(W.title_fiscal_year(CJS_BILL["title"]), 2027)
        self.assertEqual(W.title_fiscal_year("COMMERCE, JUSTICE, SCIENCE, AND RELATED AGENCIES APPROPRIATIONS BILL, "
                                             "2027"), 2027)
        self.assertEqual(W.title_fiscal_year("Continuing Appropriations and Extensions Act, 2027"), 2027)
        self.assertIsNone(W.title_fiscal_year("NATIONAL CEMETERY ADMINISTRATION ANNUAL REPORT ACT OF 2026"))
        self.assertEqual([W.stage_of(p) for p in ("BILLS-119hr8845rh", "BILLS-119s100rs", "BILLS-119hr1ih",
                                                   "CRPT-119hrpt652", "CRPT-119srpt44", "PLAW-119publ4")],
                         ["House Reported", "Senate Reported", None, "House Reported", "Senate Reported", "Enacted"])

    def test_nothing_new_means_no_commit_and_one_line(self):
        # the recorded day without the FY2027 continuing resolution: nothing the data lacks
        answers = recorded(drop={"PLAW-119publ103"})
        self.assertEqual(W.plan(STAGED, packages(answers), "2026-10-16"), [])
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "rec.json"
            rec.write_text(json.dumps(answers))
            before = (ROOT / "data" / "staged.json").read_bytes()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(W.main(["--recorded", str(rec), "--today", "2026-10-16", "--since-days", "30",
                                         "--plan-out", str(Path(d) / "plan.json")]), 0)
            self.assertTrue(out.getvalue().startswith("Intake check 2026-10-16: nothing new or changed on govinfo"))
            self.assertEqual(json.loads((Path(d) / "plan.json").read_text())["documents"], [])
            self.assertEqual((ROOT / "data" / "staged.json").read_bytes(), before)

    def test_the_recorded_day_finds_the_continuing_resolution(self):
        found = W.plan(STAGED, packages(recorded()), "2026-10-16")
        self.assertEqual([(f["package_id"], f["status"], f["fiscal_year"], f["stage"], f["subcommittees"])
                          for f in found], [("PLAW-119publ103", "new", 2027, "Enacted", ["CJS", "LHHS"])])

    def test_one_new_package_is_one_pr_plan(self):
        found = W.plan(STAGED, packages(recorded(extra={"BILLS": [CJS_BILL]}, drop={"PLAW-119publ103"})),
                       "2026-10-16")
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["package_id"], f["status"], f["branch"], f["cells"], f["reconcile"]),
                         ("BILLS-119hr9990rh", "new", "intake/BILLS-119hr9990rh",
                          [{"subcommittee": "CJS", "fiscal_year": 2027, "stage": "House Reported"}], []))
        self.assertIn("python scripts/intake.py --subcommittee CJS --fy 2027 --stage House Reported --write", f["steps"])

    def test_another_subcommittee_or_year_is_left_out(self):
        other = dict(CJS_BILL, packageId="BILLS-119hr9991rh", title=CJS_BILL["title"].replace(
            "Commerce and Justice, Science, and Related Agencies", "Energy and Water Development and Related Agencies"))
        old = dict(CJS_BILL, packageId="BILLS-119hr9992rh", title=CJS_BILL["title"].replace("2027", "2025"))
        self.assertEqual(W.plan(STAGED, [other, old], "2026-10-16"), [])

    def test_a_held_package_modified_after_its_text_is_changed(self):
        held = W.held(STAGED)
        self.assertIn("CRPT-119HRPT652", held)
        pkg = {"packageId": "CRPT-119hrpt652", "title": "COMMERCE, JUSTICE, SCIENCE, AND RELATED AGENCIES "
               "APPROPRIATIONS BILL, 2027", "lastModified": "2099-01-01T00:00:00Z"}
        self.assertEqual([f["status"] for f in W.plan(STAGED, [pkg], "2026-10-16")], ["changed"])
        pkg["lastModified"] = "2000-01-01T00:00:00Z"
        self.assertEqual(W.plan(STAGED, [pkg], "2026-10-16"), [])

    def test_a_package_for_a_draft_stage_plans_the_reconcile(self):
        data = synthetic()
        found = W.plan(data, [CJS_BILL], "2026-10-16", root=tempfile.gettempdir())
        self.assertEqual(found[0]["reconcile"], [{"cell": {"subcommittee": "CJS", "fiscal_year": 2027,
                                                           "stage": "House Reported"}, "drafts": ["SRC-DRAFT-SYN"]}])
        self.assertTrue(any("reconcile_draft.py" in s for s in found[0]["steps"]))

    def test_the_workflow_is_read_only(self):
        wf = (ROOT / ".github" / "workflows" / "intake-watch.yml").read_text()
        self.assertIn("schedule:", wf)
        self.assertIn("workflow_dispatch:", wf)
        self.assertIn("contents: read", wf)
        self.assertNotIn("write", wf.split("permissions:", 1)[1].split("concurrency:", 1)[0])
        self.assertNotIn("git push", wf)
        self.assertNotIn("echo $GOVINFO", wf.replace("{", "").replace("}", ""))


class OfficialReplacesDraft(unittest.TestCase):
    def run_(self, rows):
        data = synthetic()
        return data, R.reconcile(data, "CJS", 2027, "House Reported", "SRC-CRPT-119HRPT9990", rows, when="2026-10-16")

    def obs(self, data, oid):
        return next(o for o in data["observations"] if o["observation_id"] == oid)

    def test_equal_is_confirmed_and_cites_the_official_page(self):
        data, out = self.run_(official_rows(two=50_000_000))
        self.assertEqual((len(out["confirmed"]), out["superseded"], out["draft_only"], out["official_only"],
                          out["review"]), (2, [], [], [], []))
        old = self.obs(data, "OBS-9001")
        new = self.obs(data, old["superseded_by_observation_id"])
        self.assertEqual(old["verification_status"], "superseded")
        self.assertEqual((new["amount"], new["source_document_id"], new["source_page"], new["added_date"]),
                         (100_000_000, "SRC-CRPT-119HRPT9990", "12", "2026-10-16"))
        self.assertEqual(old["amount"], 100_000_000)                         # the draft's record is kept as it was
        v = [x for x in data["validations"] if x["observation_id"] == new["observation_id"]]
        self.assertEqual([(x["rule_applied"], x["result"]) for x in v], [("cross_document", "pass")])
        self.assertIn("SRC-DRAFT-SYN p.4", v[0]["expected_result"])

    def test_a_change_supersedes_with_one_review_item(self):
        data, out = self.run_(official_rows(one=120_000_000, two=50_000_000))
        self.assertEqual((len(out["confirmed"]), len(out["superseded"]), len(out["review"])), (1, 1, 1))
        old = self.obs(data, "OBS-9001")
        new = self.obs(data, old["superseded_by_observation_id"])
        self.assertEqual((old["amount"], new["amount"]), (100_000_000, 120_000_000))
        v = next(x for x in data["validations"] if x["validation_id"] == out["review"][0])
        self.assertEqual((v["result"], v["human_review_status"]), ("flag", "pending"))
        self.assertIn("100,000,000 per SRC-DRAFT-SYN p.4", v["expected_result"])
        self.assertIn("120,000,000 per SRC-CRPT-119HRPT9990 p.12", v["observed_result"])

    def test_a_row_in_only_one_version_is_a_review_item(self):
        data, out = self.run_(official_rows(three=7_000_000))           # TWO only in the draft, THREE only official
        self.assertEqual((out["draft_only"], len(out["official_only"]), len(out["review"])), (["OBS-9002"], 1, 2))
        self.assertEqual(self.obs(data, "OBS-9002")["verification_status"], "unverified")  # stays; not an absence
        self.assertEqual(self.obs(data, out["official_only"][0])["amount"], 7_000_000)

    def test_ids_are_new_and_never_reused(self):
        data = synthetic()
        R.reconcile(data, "CJS", 2027, "House Reported", "SRC-CRPT-119HRPT9990", official_rows(), retired=["OBS-9050"])
        self.assertEqual([o["observation_id"] for o in data["observations"]][-1], "OBS-9051")

    def test_missing_input_stops(self):
        data = synthetic()
        for args, said in ((("SRC-NOT-HELD", official_rows()), "not a source document"),
                           (("SRC-DRAFT-SYN", official_rows()), "not govinfo or Congress.gov"),
                           (("SRC-CRPT-119HRPT9990", []), "no official figures"),
                           (("SRC-CRPT-119HRPT9990", [dict(official_rows()[0], source_page="")]), "no source_page")):
            with self.subTest(said=said), self.assertRaisesRegex(R.MissingInput, said):
                R.reconcile(copy.deepcopy(data), "CJS", 2027, "House Reported", *args)
        with self.assertRaisesRegex(R.MissingInput, "no committee draft"):
            R.reconcile(copy.deepcopy(data), "CJS", 2026, "House Reported", "SRC-CRPT-119HRPT9990", official_rows())


class PublishedIntake(unittest.TestCase):
    """The committed export (docs/data/) against data/staged.json."""

    def test_drafts_are_the_committee_drafts_the_data_cites(self):
        ops = json.loads((ROOT / "docs" / "data" / "ops.json").read_text())["intake"]
        want = {(sc, fy, st, d) for (sc, fy, st), ds in W.drafts_by_cell(STAGED).items() for d in ds}
        self.assertEqual({(x["subcommittee"], x["fiscal_year"], x["stage"], x["document_id"]) for x in ops["drafts"]},
                         want)
        self.assertTrue(want)
        self.assertFalse([x for x in ops["drafts"] if x["document_id"].startswith(("SRC-BILLS-", "SRC-CRPT-"))])
        # every draft held is for a fiscal year long ended: none awaits an official version
        self.assertEqual([x for x in ops["drafts"] if x["awaiting_official"]], [])
        self.assertIsNone(ops["last_check"])
        index = json.loads((ROOT / "docs" / "data" / "index.json").read_text())
        self.assertEqual({(sc, int(fy), st) for sc, ys in index["stage_status"].items() for fy, sts in ys.items()
                          for st in sts}, {(sc, fy, st) for sc, fy, st, _ in want})

    def test_official_text_is_never_a_draft(self):
        self.assertTrue(S.is_official_text("https://www.govinfo.gov/content/pkg/BILLS-118hr5894ih/pdf/x.pdf"))
        self.assertTrue(S.is_official_text("https://www.congress.gov/118/crpt/hrpt1/CRPT-118hrpt1.pdf"))
        self.assertFalse(S.is_official_text("https://appropriations.house.gov/sites/x.pdf"))
        self.assertFalse(S.is_official_text("https://govinfo.gov.example.com/x.pdf"))

    def test_fiscal_year_of(self):
        self.assertEqual([S.fiscal_year_of(d) for d in ("2026-09-30", "2026-10-01")], [2026, 2027])


if __name__ == "__main__":
    unittest.main()
