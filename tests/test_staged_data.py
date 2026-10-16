"""
data/staged/ (staged_data.py): the canonical data split into one file per subcommittee and record type plus a
manifest. The split gives back the same document, record for record and in order; a file that does not match its
manifest stops the load; the digest is the old single file's sha256.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import staged_data  # noqa: E402

DATA = staged_data.load()


def small():
    return {"accounts": [{"canonical_account_id": "A1", "subcommittee": "CJS"},
                         {"canonical_account_id": "A2", "subcommittee": "LHHS"}],
            "observations": [{"observation_id": "O1", "canonical_account_id": "A2"},
                             {"observation_id": "O2", "canonical_account_id": "A1"},
                             {"observation_id": "O3", "canonical_account_id": "A2"}],
            "validations": [{"validation_id": "V1", "observation_id": "O2"},
                            {"validation_id": "V2", "observation_id": None}],
            "source_docs": [{"document_id": "D1"}],
            "relationships": [],
            "observation_components_retired": ["CMP-0001"]}


class SplitData(unittest.TestCase):
    def test_the_committed_data_round_trips(self):
        manifest, files = staged_data.split(DATA)
        self.assertEqual(staged_data._join(manifest, lambda g, t: files.get((g, t), [])), DATA)
        self.assertEqual(list(manifest["tables"]), list(DATA))

    def test_each_file_holds_one_subcommittee(self):
        acc = {a["canonical_account_id"]: a["subcommittee"] for a in DATA["accounts"]}
        for p in staged_data.directory().glob("*/observations.json"):
            self.assertEqual({acc[o["canonical_account_id"]] for o in json.loads(p.read_text())}, {p.parent.name})

    def test_save_then_load(self):
        data = small()
        with tempfile.TemporaryDirectory() as d:
            staged_data.save(data, d)
            self.assertEqual(staged_data.load(d), data)
            names = sorted(str(p.relative_to(staged_data.directory(d))) for p in staged_data.directory(d).rglob("*.json"))
            self.assertEqual(names, ["CJS/accounts.json", "CJS/observations.json", "CJS/validations.json",
                                     "LHHS/accounts.json", "LHHS/observations.json", "manifest.json",
                                     "shared/observation_components_retired.json", "shared/source_docs.json",
                                     "shared/validations.json"])
            # a record type that loses its last record of a subcommittee loses its file
            data["validations"] = data["validations"][1:]
            staged_data.save(data, d)
            self.assertFalse((staged_data.directory(d) / "CJS" / "validations.json").exists())
            self.assertEqual(staged_data.load(d), data)

    def test_a_file_that_differs_from_its_manifest_stops_the_load(self):
        with tempfile.TemporaryDirectory() as d:
            staged_data.save(small(), d)
            p = staged_data.directory(d) / "LHHS" / "observations.json"
            p.write_text(json.dumps(json.loads(p.read_text())[:1]))
            with self.assertRaisesRegex(ValueError, "LHHS/observations.json holds 1 records, the manifest 2"):
                staged_data.load(d)

    def test_a_file_the_manifest_does_not_list_stops_the_load(self):
        with tempfile.TemporaryDirectory() as d:
            staged_data.save(small(), d)
            (staged_data.directory(d) / "LHHS" / "validations.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "LHHS/validations.json"):
                staged_data.load(d)

    def test_the_digest_is_the_single_files_sha256(self):
        import hashlib
        self.assertEqual(staged_data.digest(DATA),
                         hashlib.sha256(json.dumps(DATA, indent=1, ensure_ascii=False).encode()).hexdigest())

    def test_a_checkout_from_before_the_split(self):
        data = small()
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data").mkdir()
            (Path(d) / "data" / "staged.json").write_text(json.dumps(data))
            self.assertEqual(staged_data.load_tree(d), data)


if __name__ == "__main__":
    unittest.main()
