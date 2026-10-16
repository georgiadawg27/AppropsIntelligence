"""
The date each figure and document first entered the record (brief D-5, added_dates.py): every record in
data/staged.json carries one, no date ever changes once main holds it, and stamping dates only what has none.

Run:  python -m unittest tests.test_added_dates -v
"""

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import added_dates as A  # noqa: E402

STAGED = json.loads((ROOT / "data" / "staged.json").read_text())


def main_data():
    """origin/main's data/staged.json, or None where the ref is not fetched (a local shallow clone; CI fetches it)."""
    r = subprocess.run(["git", "show", "origin/main:data/staged.json"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


class AddedDates(unittest.TestCase):
    def test_every_figure_and_document_is_dated(self):
        self.assertEqual(A.problems(STAGED), [])
        for table, (_, field) in A.FIELDS.items():
            self.assertTrue(all(r[field] for r in STAGED[table]), table)

    def test_no_date_changes_once_main_holds_it(self):
        base = main_data()
        if base is None:
            if os.environ.get("GITHUB_ACTIONS"):
                self.fail("origin/main is not fetched; the workflow checks out with fetch-depth: 0")
            self.skipTest("origin/main not fetched here")
        self.assertEqual(A.problems(STAGED, base), [])

    def test_a_changed_date_is_named(self):
        base = copy.deepcopy(STAGED)
        data = copy.deepcopy(STAGED)
        data["observations"][0]["added_date"] = "2026-10-15" if base["observations"][0]["added_date"] != "2026-10-15" \
            else "2026-10-14"
        bad = A.problems(data, base)
        self.assertEqual(len(bad), 1)
        self.assertIn("never changed", bad[0])
        data["source_docs"][0]["first_added"] = ""
        data["observations"][1]["added_date"] = "2026-13-01x"
        data["observations"][2]["added_date"] = "2026-01-01"                  # before the baseline
        self.assertEqual(len(A.problems(data, base)), 4)

    def test_stamp_dates_only_what_has_none(self):
        data = copy.deepcopy(STAGED)
        held = {r["observation_id"]: r["added_date"] for r in data["observations"]}
        new = {"observation_id": "OBS-NEW-TEST", "source_document_id": "SRC-NEW-TEST"}
        data["observations"].append(new)
        data["source_docs"].append({"document_id": "SRC-NEW-TEST"})
        done = A.stamp(data, "2026-10-20")
        self.assertEqual(done, {"source_docs": ["SRC-NEW-TEST"], "observations": ["OBS-NEW-TEST"]})
        self.assertEqual(new["added_date"], "2026-10-20")
        self.assertEqual({r["observation_id"]: r["added_date"] for r in data["observations"][:-1]}, held)
        self.assertEqual(A.stamp(data, "2026-10-21"), {"source_docs": [], "observations": []})

    def test_the_baseline_is_when_the_data_entered_the_repository(self):
        # the records built before data/staged.json was committed carry the baseline ("on file by"), never a guess
        r = subprocess.run(["git", "log", "-1", "--format=%cs", A.BASELINE_COMMIT], cwd=ROOT, capture_output=True,
                           text=True)
        if r.returncode != 0:
            self.skipTest("history not fetched here")
        self.assertEqual(r.stdout.strip(), A.BASELINE)
        self.assertEqual(min(r["added_date"] for r in STAGED["observations"]), A.BASELINE)


if __name__ == "__main__":
    unittest.main()
