"""
Build the Senate Title III acceptance-test ground truth from the pilot
workbook, so no figure is hand-transcribed.

    python tests/ground_truth/build_senate_ground_truth.py path/to/CJS_Title_III_Science_Pilot_Schema_Loaded_v8.xlsx

Reads the workbook's Appropriations Observation tab. Each pilot observation is
selected by canonical_account_id + fiscal_year + stage + amount_type, plus the
component the pilot records in source_table_or_section's "(base)" /
"(defense)" / "(emergency)" suffix -- that suffix is what tells NSF R&RA's base
line from its Defense function line (both budget authority on ACC-NSF-RRA).

Base and emergency lines are never combined: the Senate table's base row is
matched to the pilot's budget authority observation and its "(emergency)" row
to the supplemental one, one entry each.

ACCOUNT_ROWS maps each pilot observation to the row of each report's
comparative table it corresponds to (the account_path the extractor gives
that row). None means the report prints no such row; the pilot still carries
a 0 there, and the acceptance test reports those separately.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
NASA = "National Aeronautics and Space Administration"
NSF = "National Science Foundation"

COMMON = {
    ("ACC-OSTP", "budget authority", None): "Office of Science and Technology Policy",
    ("ACC-NSC", "budget authority", None): "National Space Council",
    ("ACC-NASA-SCIENCE", "budget authority", None): f"{NASA} / Science",
    ("ACC-NASA-AERONAUTICS", "budget authority", None): f"{NASA} / Aeronautics",
    ("ACC-NASA-SPACETECH", "budget authority", None): f"{NASA} / Space Technology",
    ("ACC-NASA-SPACEOPS", "budget authority", None): f"{NASA} / Space Operations",
    ("ACC-NASA-STEM-ENGAGEMENT", "budget authority", None):
        f"{NASA} / Science, Technology, Engineering, and Mathematics Engagement",
    ("ACC-NASA-SAFETY-SECURITY", "budget authority", None): f"{NASA} / Safety, Security and Mission Services",
    ("ACC-NASA-CONSTRUCTION", "budget authority", "base"):
        f"{NASA} / Construction and environmental compliance and restoration",
    ("ACC-NASA-CONSTRUCTION", "supplemental", "emergency"):
        f"{NASA} / Construction and environmental compliance and restoration / "
        "Construction and environmental compliance and restoration (emergency)",
    ("ACC-NASA-OIG", "budget authority", None): f"{NASA} / Office of Inspector General",
    ("ACC-NASA-TOTAL", "budget authority", None): f"{NASA} / Total, {NASA}",
    ("ACC-NSF-RRA", "budget authority", "base"): f"{NSF} / Research and related activities",
    ("ACC-NSF-RRA", "budget authority", "defense"): f"{NSF} / Defense function",
    ("ACC-NSF-MREFC", "budget authority", "base"): f"{NSF} / Major Research Equipment and Facilities Construction",
    ("ACC-NSF-AGENCY-OPS", "budget authority", None): f"{NSF} / Agency Operations and Award Management",
    ("ACC-NSF-NSB", "budget authority", None): f"{NSF} / Office of the National Science Board",
    ("ACC-NSF-OIG", "budget authority", None): f"{NSF} / Office of Inspector General",
    ("ACC-NSF-TOTAL", "budget authority", None): f"{NSF} / Total, {NSF}",
}

ACCOUNT_ROWS = {
    # S.Rept. 119-44 (FY2026)
    "CRPT-119srpt44": {
        **COMMON,
        ("ACC-NASA-EXPLORATION", "budget authority", "base"): f"{NASA} / Exploration",
        ("ACC-NASA-EXPLORATION", "supplemental", "emergency"): f"{NASA} / Exploration (emergency)",
        ("ACC-NSF-RRA", "supplemental", "emergency"): None,
        ("ACC-NSF-MREFC", "supplemental", "emergency"):
            f"{NSF} / Major Research Equipment and Facilities Construction (emergency)",
        ("ACC-NSF-STEM-EDUCATION", "budget authority", None): f"{NSF} / STEM Education",
    },
    # S.Rept. 118-62 (FY2024): Exploration is "Deep Space Exploration Systems",
    # STEM Education is "Education and Human Resources"
    "CRPT-118srpt62": {
        **COMMON,
        ("ACC-NASA-EXPLORATION", "budget authority", "base"): f"{NASA} / Deep Space Exploration Systems",
        ("ACC-NASA-EXPLORATION", "supplemental", "emergency"):
            f"{NASA} / Deep Space Exploration Systems / Deep Space Exploration Systems (emergency)",
        ("ACC-NSF-RRA", "supplemental", "emergency"):
            f"{NSF} / Research and related activities / Research and related activities (emergency)",
        ("ACC-NSF-MREFC", "supplemental", "emergency"): None,
        ("ACC-NSF-STEM-EDUCATION", "budget authority", None): f"{NSF} / Education and Human Resources",
    },
}

TESTS = {
    "CRPT-119srpt44": [("Committee recommendation", 2026, "Senate Reported", "FY2026 Senate Mark"),
                       ("2025 appropriation", 2025, "Enacted", "FY2025 Enacted")],
    "CRPT-118srpt62": [("2023 appropriation", 2023, "Enacted", "FY2023 Enacted"),
                       ("Budget estimate", 2024, "President's Budget", "FY2024 President's Budget"),
                       ("Committee recommendation", 2024, "Senate Reported", "FY2024 Senate Mark")],
}
TITLE_III_PREFIXES = ("ACC-OSTP", "ACC-NSC", "ACC-NASA-", "ACC-NSF-")


def component(section):
    m = re.search(r"\((base|defense|emergency)\)\s*$", section or "")
    return m.group(1) if m else None


def load_pilot(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows = list(wb["Appropriations Observation"].iter_rows(values_only=True))
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:] if r[0]]


def build(workbook):
    pilot = load_pilot(workbook)
    digest = hashlib.sha256(Path(workbook).read_bytes()).hexdigest()
    for package_id, series in TESTS.items():
        rows_for = ACCOUNT_ROWS[package_id]
        checks = []
        for column_header, fy, stage, label in series:
            sel = [o for o in pilot if o["fiscal_year"] == fy and o["stage"] == stage
                   and o["canonical_account_id"].startswith(TITLE_III_PREFIXES)]
            expected, seen = [], set()
            for o in sel:
                key = (o["canonical_account_id"], o["amount_type"], component(o["source_table_or_section"]))
                if key not in rows_for:
                    key = (key[0], key[1], None)
                if key not in rows_for:
                    raise SystemExit(f"{package_id} {label}: no table row mapped for {o['observation_id']} "
                                     f"{o['canonical_account_id']} {o['amount_type']} {o['source_table_or_section']!r}")
                if key in seen:
                    raise SystemExit(f"{package_id} {label}: two pilot observations select {key}")
                seen.add(key)
                expected.append({
                    "name": f"{o['canonical_account_id']} {o['amount_type']}"
                            + (f" ({key[2]})" if key[2] else ""),
                    "account_path": rows_for[key],
                    "amount_dollars": int(o["amount"]),
                    "pilot_observation_id": o["observation_id"],
                    "pilot_source_document_id": o["source_document_id"],
                })
            checks.append({"column_header": column_header, "series": label,
                           "fiscal_year": fy, "stage": stage, "expected": expected})
        out = {
            "package_id": package_id, "title": "TITLE III", "unit": "dollars",
            "source": f"{Path(workbook).name} (sha256 {digest[:16]}), Appropriations Observation tab; "
                      f"generated by {Path(__file__).name} -- do not edit by hand.",
            "checks": checks,
        }
        dest = HERE / f"{package_id}_title_iii.json"
        dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        print(f"{dest.name}: " + ", ".join(f"{c['series']} {len(c['expected'])}" for c in checks))


if __name__ == "__main__":
    build(sys.argv[1])
