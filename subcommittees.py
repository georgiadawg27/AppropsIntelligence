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
    "AGRICULTURE, RURAL DEVELOPMENT": "Agriculture-FDA",
    "ENERGY AND WATER": "Energy-Water",
    "FINANCIAL SERVICES AND GENERAL GOVERNMENT": "FSGG",
    "HOMELAND SECURITY": "Homeland Security",
    "INTERIOR, ENVIRONMENT": "Interior-Environment",
    "LABOR, HEALTH AND HUMAN SERVICES": "Labor-HHS-Education",
    "LEGISLATIVE BRANCH": "Legislative Branch",
    "MILITARY CONSTRUCTION, VETERANS AFFAIRS": "MilCon-VA",
    "NATIONAL SECURITY, DEPARTMENT OF STATE": "NSRP",
    "STATE, FOREIGN OPERATIONS": "SFOPS",
    "TRANSPORTATION, HOUSING AND URBAN DEVELOPMENT": "THUD",
    "DEPARTMENT OF DEFENSE APPROPRIATIONS": "Defense",
}


def normalize(text):
    words = re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split()
    return " " + " ".join(w for w in words if w != "AND") + " "


def subcommittees_named(text):
    """Every subcommittee whose name appears in text, in SUBCOMMITTEES order."""
    t = normalize(text)
    return [name for needle, name in SUBCOMMITTEES.items() if normalize(needle) in t]


def subcommittee_of(text):
    """The first subcommittee named in text, or None."""
    names = subcommittees_named(text)
    return names[0] if names else None
