"""
Commerce, Justice, Science (CJS): the rules true only of its documents and accounts, moved out of the core (G-1).
Each rule names its scope, the hook it acts at (rules_registry.HOOKS) and where it lived before.
Tests: tests/test_any_subcommittee.py MovedRules (each fires on its real case, and not outside its scope).
"""

from rules_registry import ANY, Rule, Scope

CODE = "CJS"
ALL = Scope(CODE, accounts=ANY, fiscal_years=ANY, stages=ANY)
TC_FILE = "scripts/tag_components.py"

# the agency an account's heading sits under, as words of the enclosing headings and title
LAYOUT = {
    "agency_words": {
        "National Aeronautics and Space Administration": "aeronautics space administration",
        "National Science Foundation": "national science foundation",
        "Department of Justice": "justice",
        "Executive Office of the President": "science",          # Title III -- Science
    },
    "bureau_words": {"U.S. Patent and Trademark Office": "patent trademark",
                     "National Oceanic and Atmospheric Administration": "oceanic atmospheric"},
}


def nsf_defense(o, ctx):
    """NSF R&RA's 'Defense function' line: defense_portion, beside the headline (defense) or inside the bill or law
    paragraph's amount (defense_inside, D-3: it names that headline)."""
    if o["amount_type"] != "budget authority":
        return None
    if o.get("component") == "defense":
        return "defense_portion", "", "", "the table's 'Defense function' line (defense)"
    if o.get("component") == "defense_inside":
        return ("defense_portion", "", "", "the table's 'Defense function' line, inside the bill or law paragraph's "
                "amount (defense_inside; D-3)",
                {"names_headline": True,
                 "inside_note": "inside the headline: the bill or law paragraph's amount ({headline}) counts the "
                                "defense line"})
    return None


def fy2027_transfer(o, ctx):
    if o["amount_type"] != "transfer":
        return None
    if o["amount"] >= 0:
        return ("transfer_in", "", o.get("transfer_link_account_id") or ctx.tc.printed(o),
                "the table's '(by transfer)' line")
    return "transfer_out", "", "", "the table's transfer-out line"


def fy2027_offsetting(o, ctx):
    if o["amount_type"] == "offsetting_collection":
        return "fees_offsets", "", "", "the table's offsetting fee collections line"
    return None


# anomalies that name a held account but set only its level, changing none of its components (P.L. 119-4 div. A
# title III): each component continues, and the carry-forward list says so
CR_LEVEL_ONLY = {
    "ACC-NOAA-ORF": "P.L. 119-4 div. A sec. 1301(3) sets the level, $4,408,986,000 for 'Department of Commerce--National "
                    "Oceanic and Atmospheric Administration--Operations, Research and Facilities', and names no component",
    "ACC-NASA-SAFETY-SECURITY": "P.L. 119-4 div. A sec. 1301(6) sets the level, $3,092,327,000 for 'National Aeronautics "
                                "and Space Administration--Safety, Security and Mission Services', and names no component",
}


def cr_level_only(c, ctx):
    # an earmark ends under the CR's own rule (tag_components.CR_LAWS); the level sets none of it
    if ctx.account in CR_LEVEL_ONLY and c["component_type"] != "earmark":
        return {"note": CR_LEVEL_ONLY[ctx.account]}
    return None


FY2027 = dict(accounts=ANY, fiscal_years=(2027, 2027), stages=ANY)

RULES = [
    Rule("CJS-LAYOUT", "layout", ALL, data=LAYOUT,
         moved_from="scripts/check_legislative_text.py AGENCY_WORDS, BUREAU_WORDS",
         doc="the words CJS bills print over NASA, NSF, DOJ, OSTP (Title III -- Science), USPTO and NOAA paragraphs"),
    Rule("CJS-APPENDIX", "appendix", ALL, data={"chapters": ("COM", "DOJ", "NASA", "NSF", "EOP")},
         moved_from="scripts/appendix_attributes.py SUBCOMMITTEE_CHAPTERS", doc="the Budget Appendix chapters"),
    Rule("CJS-NSF-DEFENSE", "line", Scope(CODE, accounts={"ACC-NSF-RRA"}, fiscal_years=ANY, stages=ANY),
         fn=nsf_defense, moved_from=f"{TC_FILE} retag(): defense, defense_inside",
         doc="NSF R&RA's 'Defense function' line -> defense_portion (inside where defense_inside, D-3)"),
    Rule("CJS-FY2027-TRANSFER", "line", Scope(CODE, **FY2027), fn=fy2027_transfer,
         moved_from=f"{TC_FILE} retag(): CJS FY2027 transfer", doc="the FY2027 tables' '(by transfer)' lines"),
    Rule("CJS-FY2027-OFFSETTING", "line", Scope(CODE, **FY2027), fn=fy2027_offsetting,
         moved_from=f"{TC_FILE} retag(): CJS FY2027 offsetting_collection",
         doc="the FY2027 tables' offsetting fee collections lines -> fees_offsets"),
    Rule("CJS-CR-LEVEL-ONLY", "carry_forward",
         Scope(CODE, accounts=set(CR_LEVEL_ONLY), fiscal_years=(2025, 2025), stages={"Enacted"}), fn=cr_level_only,
         moved_from=f"{TC_FILE} CR_LEVEL_ONLY", doc="P.L. 119-4 anomalies that set only a CJS account's level"),
]
