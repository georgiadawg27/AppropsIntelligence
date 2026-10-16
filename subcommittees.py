"""
The twelve appropriations subcommittees, by the words their bills' and
reports' titles use -- shared by the extractor (which subcommittee a
document's table belongs to) and the ingester (whether a /related package is
about the same subject as the bill it hangs off).

Titles spell the same subcommittee several ways ("COMMERCE, JUSTICE,
SCIENCE" in the bill, "COMMERCE AND JUSTICE, SCIENCE" in S.Rept. 119-44;
"LABOR, HEALTH AND HUMAN SERVICES" vs "LABOR, HEALTH, AND HUMAN SERVICES"
in H.Rept. 119-696), so both sides are compared with punctuation and the
word AND dropped.
"""

import re

SUBCOMMITTEES = {
    "COMMERCE, JUSTICE, SCIENCE": "CJS",
    "AGRICULTURE, RURAL DEVELOPMENT": "AG",
    "ENERGY AND WATER": "EW",
    "FINANCIAL SERVICES AND GENERAL GOVERNMENT": "FSGG",
    "HOMELAND SECURITY": "HS",
    "INTERIOR, ENVIRONMENT": "INT",
    "LABOR, HEALTH AND HUMAN SERVICES": "LHHS",
    "LEGISLATIVE BRANCH": "LEG",
    "MILITARY CONSTRUCTION, VETERANS AFFAIRS": "MCVA",
    # the House's title for the State-Foreign Ops bill: an alias, so one bill lineage keeps one code
    "NATIONAL SECURITY, DEPARTMENT OF STATE": "SFOPS",
    "STATE, FOREIGN OPERATIONS": "SFOPS",
    "TRANSPORTATION, HOUSING AND URBAN DEVELOPMENT": "THUD",
    "DEPARTMENT OF DEFENSE APPROPRIATIONS": "DEF",
}
# The values above are the stored subcommittee codes (Account.subcommittee,
# Bill Report Reference.subcommittee and lookup_key, the manifest): the short
# form, matching the ID prefixes (BR-LHHS-..., OBS-LHHS-...), approved by the
# owner 2026-10-15 (CLAUDE.md, "Standing decisions"). Never renamed: a code is
# in every ID it mints. A name for people to read is a display mapping, never
# stored; a code not listed displays as itself.
DISPLAY_NAMES = {
    "AG": "Agriculture-FDA",
    "CJS": "Commerce, Justice, Science",
    "DEF": "Defense",
    "EW": "Energy-Water",
    "FSGG": "Financial Services and General Government",
    "HS": "Homeland Security",
    "INT": "Interior-Environment",
    "LEG": "Legislative Branch",
    "LHHS": "Labor-HHS-Education",
    "MCVA": "MilCon-VA",
    "SFOPS": "State-Foreign Operations",
    "THUD": "Transportation-HUD",
}
# codes retired before use: never a subcommittee's code, never in an ID (ids.check_code)
RETIRED_CODES = {"NSRP"}           # State-Foreign Ops' House title; SFOPS holds the lineage (owner, 2026-10-15)
# the subcommittee the page opens on, published with the data (docs/data/index.json and /api/features
# "default_subcommittee"); used only when the data holds it, else the first code
DEFAULT_SUBCOMMITTEE = "LHHS"


def display_name(code):
    """The name people read: DISPLAY_NAMES, else the subcommittee's rule file (hook 'display'), else the code."""
    if code in DISPLAY_NAMES:
        return DISPLAY_NAMES[code]
    import rules_registry
    return rules_registry.active().data("display", code).get("name") or code


def default_subcommittee(codes_):
    """DEFAULT_SUBCOMMITTEE when the data holds it, else the first of codes_ (None for none)."""
    codes_ = list(codes_)
    return DEFAULT_SUBCOMMITTEE if DEFAULT_SUBCOMMITTEE in codes_ else (codes_[0] if codes_ else None)


def codes(data):
    """The subcommittees the data holds (every account's subcommittee), sorted: the list the workbook, the export
    and the page read -- never a list in code."""
    return sorted({a["subcommittee"] for a in data["accounts"]})


def known():
    """Every subcommittee code the code knows: SUBCOMMITTEES' values and every rule file's (reference/rules/)."""
    import rules_registry
    return (set(SUBCOMMITTEES.values()) | set(rules_registry.active().codes)) - RETIRED_CODES


def normalize(text):
    words = re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split()
    return " " + " ".join(w for w in words if w != "AND") + " "


def subcommittees_named(text):
    """Every subcommittee whose name (or an alias) appears in text, once each, in SUBCOMMITTEES order."""
    t = normalize(text)
    named = [code for needle, code in SUBCOMMITTEES.items() if normalize(needle) in t]
    return list(dict.fromkeys(named))          # an alias names its code once


def subcommittee_of(text):
    """The first subcommittee named in text, or None."""
    names = subcommittees_named(text)
    return names[0] if names else None
