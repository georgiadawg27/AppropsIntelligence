"""
Fuzzy account matching for OCR labels (accounts.py).

Run:  python -m unittest tests.test_accounts -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import accounts as A  # noqa: E402

NASA = "Nat ional Aeronautics and Space Administrati on"      # as OCR printed the heading
NSF = "National Science Foundation"


def m(label, agency=NASA, kind="line"):
    return A.match_label(label, agency, kind)


class Threshold(unittest.TestCase):
    def test_ocr_spacing_is_free(self):
        for label, acct in (("Expl oration", "ACC-NASA-EXPLORATION"), ("Office of Sci ence and Technology Policy", "ACC-OSTP")):
            r = m(label, None if acct == "ACC-OSTP" else NASA)
            self.assertEqual((r["match"], r["canonical_account_id"], r["distance"]), ("exact", acct, 0))

    def test_ocr_character_errors_within_threshold(self):
        for label, agency, acct in (("Sci e nee", NASA, "ACC-NASA-SCIENCE"),                  # c -> e
                                    ("STEH Education", NSF, "ACC-NSF-STEM-EDUCATION"),         # M -> H
                                    ("Hajor Research Equipment and Facilities Construction", NSF, "ACC-NSF-MREFC")):
            r = m(label, agency)
            self.assertEqual((r["match"], r["canonical_account_id"], r["distance"]), ("ocr_corrected", acct, 1), label)

    def test_beyond_threshold_is_not_matched(self):
        self.assertEqual(m("Exploration Tech")["match"], "unmatched")                   # 4 edits from "Exploration"
        self.assertEqual(m("Scinnce Hq")["canonical_account_id"], None)
        self.assertEqual(A.allowed_distance(A.norm("Science")), 1)                       # 15% of 7 letters
        self.assertEqual(A.allowed_distance(A.norm("Construction and Environmental Compliance and Restoration")), 2)
        self.assertEqual(A.allowed_distance(A.norm("NSB")), 0)                           # < 7 letters: exact only

    def test_agency_scope_separates_same_named_accounts(self):
        self.assertEqual(m("Office of Inspector General", NASA)["canonical_account_id"], "ACC-NASA-OIG")
        self.assertEqual(m("Office of Inspector General", NSF)["canonical_account_id"], "ACC-NSF-OIG")

    def test_ambiguous_is_not_matched(self):
        accts = [{"canonical_account_id": "A", "canonical_name": "Salaries and expenses", "agency": "X"},
                 {"canonical_account_id": "B", "canonical_name": "Salaries and expense", "agency": "X"}]
        r = A.match_label("Salaries and expensex", None, "line", accts)
        self.assertEqual((r["match"], r["canonical_account_id"]), ("ambiguous", None))

    def test_emergency_line_is_a_component(self):
        r = m("Hajor Research Equipment and Facilities Construction (emergency)", NSF)
        self.assertEqual((r["canonical_account_id"], r["component"]), ("ACC-NSF-MREFC", "emergency"))

    def test_totals(self):
        self.assertEqual(m("Total , National Science Foundation", NSF, "total")["canonical_account_id"], "ACC-NSF-TOTAL")
        self.assertIsNone(m("Total , Title III , Sc ience", None, "total"))              # not an account
        self.assertIsNone(m("Subtotal , Exploration", NASA, "subtotal"))

    def test_former_names_share_the_pool_and_the_rule(self):
        r = m("Deep Space Exploration Systems")
        self.assertEqual((r["canonical_account_id"], r["match"], r["via"]), ("ACC-NASA-EXPLORATION", "exact", "historical_name"))
        r = m("Deep Spaee Exploratlon Systems")                                  # OCR-garbled former name
        self.assertEqual((r["canonical_account_id"], r["match"], r["distance"]), ("ACC-NASA-EXPLORATION", "ocr_corrected", 2))
        self.assertEqual(m("Exploration")["via"], "canonical")
        self.assertEqual(m("Education and Human Resources", NSF)["canonical_account_id"], "ACC-NSF-STEM-EDUCATION")

    def test_an_accounts_own_names_never_make_it_ambiguous(self):
        accts = [{"canonical_account_id": "A", "canonical_name": "Salaries and expenses", "agency": "X",
                  "historical_names": ["Salaries and expense"]}]
        r = A.match_label("Salaries and expensez", None, "line", accts)
        self.assertEqual((r["match"], r["canonical_account_id"]), ("ocr_corrected", "A"))

    def test_close_names_on_different_accounts_are_ambiguous(self):
        accts = [{"canonical_account_id": "A", "canonical_name": "Research and facilities", "agency": "X"},
                 {"canonical_account_id": "B", "canonical_name": "Other things", "agency": "X",
                  "historical_names": ["Research and facilitiez"]}]
        r = A.match_label("Research and facilitiex", None, "line", accts)
        self.assertEqual((r["match"], r["canonical_account_id"]), ("ambiguous", None))

    def test_a_single_former_name_may_be_a_string(self):
        accts = [{"canonical_account_id": "A", "canonical_name": "Space Operation", "agency": "X",
                  "historical_names": "LEO and Spaceflight Operations"}]
        self.assertEqual(A.match_label("LEO and Spaceflight Operations", None, "line", accts)["canonical_account_id"], "A")


if __name__ == "__main__":
    unittest.main()
