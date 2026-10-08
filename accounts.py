"""
accounts.py

Match an OCR-read row label to a canonical account (reference/accounts.json,
built from the Account tab). One pool of names per account: its canonical
name and its genuine former names (historical_names, e.g. "Deep Space
Exploration Systems" for Exploration). Both go through the same fuzzy rule,
since a former name can be OCR-garbled in an older document exactly like a
current one.

Rule (proposal, see ACCEPT_*):
  - Compare letters only, lower-cased: OCR's stray spaces and punctuation
    cost nothing ("Sci e nee" -> "scienee").
  - Candidates are the accounts of the row's agency (its outermost heading,
    itself matched the same way), so NASA's and NSF's "Office of Inspector
    General" can't be confused; a row with no agency heading is matched
    against every account.
  - An account's distance is its closest name; "via" records whether that
    was its canonical or a historical name.
  - distance 0 -> "exact".
  - 1 <= distance <= min(2, 15% of the matched name's length), and the
    runner-up account is at least 2 edits further away -> "ocr_corrected".
    A name shorter than 7 letters must match exactly. An account's own names
    never compete with each other.
  - Otherwise -> "unmatched" (nothing within the threshold) or "ambiguous"
    (a runner-up too close): no account is assigned, and the observation's
    account_identity check is flagged for human review -- never silently
    auto-matched.
  - A line nested under a matched account line that doesn't itself match
    (NSF "Defense function" under "Research and related activities") takes
    its parent's account with its own label as the component -> "inherited",
    also flagged for review.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / "reference" / "accounts.json"
ACCEPT_MAX_DISTANCE = 2
ACCEPT_MAX_FRACTION = 0.15
AMBIGUITY_MARGIN = 2

EMERGENCY_RE = re.compile(r"\(\s*emergency\s*\)\s*$", re.I)
AGENCY_TOTAL_RE = re.compile(r"\s*\(agency total\)\s*$", re.I)
TOTAL_PREFIX_RE = re.compile(r"^\s*(sub)?total\s*[,.]?\s*", re.I)


def norm(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def distance(a, b):
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@lru_cache(maxsize=1)
def load(path=REFERENCE):
    return json.loads(Path(path).read_text())["accounts"]


def names_list(v):
    """historical_names as a list of names (a single name may come as a string)."""
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def allowed_distance(canonical_norm):
    if len(canonical_norm) < 7:
        return 0
    return min(ACCEPT_MAX_DISTANCE, int(ACCEPT_MAX_FRACTION * len(canonical_norm)))


def best_match(text, candidates, names_of):
    """-> (match kind, candidate or None, distance or None, matched name).
    names_of(candidate) -> its names; a candidate's distance is its closest."""
    t = norm(text)
    if not t or not candidates:
        return "unmatched", None, None, None
    scored = []
    for i, c in enumerate(candidates):
        names = [n for n in names_of(c) if norm(n)]
        if names:
            d, name = min((distance(t, norm(n)), n) for n in names)
            scored.append((d, i, c, name))
    if not scored:
        return "unmatched", None, None, None
    scored.sort(key=lambda x: (x[0], x[1]))
    d, _, best, name = scored[0]
    runner = scored[1][0] if len(scored) > 1 else None
    if d == 0:
        return ("exact", best, 0, name) if runner != 0 else ("ambiguous", None, 0, None)
    if d > allowed_distance(norm(name)):
        return "unmatched", None, d, None
    if runner is not None and runner < d + AMBIGUITY_MARGIN:
        return "ambiguous", None, d, None
    return "ocr_corrected", best, d, name


def agency_accounts(agency_heading, accounts):
    if not agency_heading:
        return accounts
    agencies = sorted({a["agency"] for a in accounts})
    kind, agency, _, _ = best_match(agency_heading, agencies, lambda x: [x])
    return [a for a in accounts if a["agency"] == agency] if agency else []


def match_label(label, agency_heading, row_kind, accounts=None):
    """
    -> {"canonical_account_id", "canonical_name", "component", "match", "distance"}
    or None when the row isn't an account row (rollups other than an agency
    total, memos).
    """
    accounts = accounts if accounts is not None else load()
    component = None
    text = label
    if row_kind in ("subtotal", "grand_total", "memo"):
        return None
    if row_kind == "total":
        pool = [a for a in agency_accounts(agency_heading, accounts) if AGENCY_TOTAL_RE.search(a["canonical_name"])] \
            or [a for a in accounts if AGENCY_TOTAL_RE.search(a["canonical_name"])]
        kind, acct, d, name = best_match(TOTAL_PREFIX_RE.sub("", text), pool,
                                         lambda a: [AGENCY_TOTAL_RE.sub("", a["canonical_name"])])
        if acct is None:
            return None                     # a title or bureau total: not an account
    else:
        if EMERGENCY_RE.search(text):
            component = "emergency"
            text = EMERGENCY_RE.sub("", text)
        pool = [a for a in agency_accounts(agency_heading, accounts) if not AGENCY_TOTAL_RE.search(a["canonical_name"])]
        kind, acct, d, name = best_match(text, pool, lambda a: [a["canonical_name"]] + names_list(a.get("historical_names")))
    return {"canonical_account_id": acct["canonical_account_id"] if acct else None,
            "canonical_name": acct["canonical_name"] if acct else None,
            "component": component, "match": kind, "distance": d, "matched_name": name,
            "via": None if not acct else ("canonical" if name in (acct["canonical_name"],
                                                                 AGENCY_TOTAL_RE.sub("", acct["canonical_name"]))
                                          else "historical_name")}


# Component vocabulary (Appropriations Observation.component): canonical
# value -> the ways documents print it. A printed label is matched to a
# canonical value with the same rule as an account name (best_match: letters
# only, edit distance, unambiguous), after dropping a leading fiscal-year tag
# ("FY27 CHIMP"). No component (the account's own line) is None, never a
# string. A label that matches nothing is kept as printed, so the store holds
# it for review instead of inventing a component.
COMPONENTS = {
    "defense": ["Defense function"],            # NSF R&RA sub-line in the House and Senate tables
    "chimp": ["CHIMP"],                         # CVF, H.R. 8845 / CBO ("FY27 CHIMP")
    "chimp_pop_up": ["CHIMP Pop-Up"],           # ("FY27 CHIMP Pop-Up")
}
# Components set from where a figure sits in the table, never from its label:
# a separate supplemental appropriations act or a budget amendment listed in
# a table's "Other Appropriations" section. Not label-matched (no aliases), so
# a printed row label can't fuzzy-match onto one.
STRUCTURAL_COMPONENTS = ("supplemental_act", "budget_amendment")
# ...and each exists at one stage by definition: a supplemental appropriations
# act is enacted law; a budget amendment amends the President's request. The
# store's grid shows such a line only in its stage's column (elsewhere it
# isn't "missing" -- it can't exist), and the loader flags one recorded at
# any other stage.
COMPONENT_STAGE = {"supplemental_act": "Enacted", "budget_amendment": "President's Budget"}
# The components above are parts: an account's lines in a cell add up. These
# are not -- each is already inside, or another view of, the account's own
# line, so nothing ever adds it to that line:
#   CURES -- the NIH Innovation Account (21st Century Cures Act) line, kept
#            as its own row while NIH's headline is "Total, NIH (with CURES
#            Act funding)", which already counts it (decision 2026-09-27)
#   parallel scopes -- a total printed again with its scope in the label
#            (extract_approps.SCOPE_RES), stored under that printed scope:
#            Medicaid's three totals are "program_level_available_this_fiscal_year",
#            the plain current-year total (no component), and "appropriated_in_this_bill"
#   kids_first / congressionally_directed_spending -- a line printed inside the
#            account's figure (the Gabriella Miller Kids First line under the NIH
#            Office of the Director; HRSA's community project funding) (owner, 2026-10-08)
INCLUDED_COMPONENTS = ("CURES", "kids_first", "congressionally_directed_spending")
# A chamber's one-off line under an account's heading (Labor-HHS: Diaper Grants, House FY2022): a part of the
# account's cell, never a new account (owner, 2026-10-08)
PROPOSAL_COMPONENTS = ("chamber_proposal",)
PARALLEL_SCOPE_COMPONENTS = (
    "program_level", "fiscal_year_program_level",
    "program_level_with_cures_and_phs_evaluation_act_funding", "program_level_excluding_arpa_h",
    "program_level_available_this_fiscal_year", "program_level_including_emergencies", "available_this_fiscal_year",
    "current_year", "appropriated_in_this_bill", "available_in_this_bill",
    "excluding_emergencies", "including_phs_eval_tap", "discretionary",
)
BREAKDOWN_COMPONENTS = frozenset(INCLUDED_COMPONENTS) | frozenset(PARALLEL_SCOPE_COMPONENTS)
VOCABULARY = frozenset(COMPONENTS) | frozenset(STRUCTURAL_COMPONENTS) | BREAKDOWN_COMPONENTS
# The same, as the store's Component table rows (component, kind, description):
# what the loader seeds when a workbook has no Component tab yet.
COMPONENT_KINDS = (
    [(c, "part", f"a line printed under the account's own line ({', '.join(COMPONENTS[c])})") for c in COMPONENTS]
    + [("supplemental_act", "part", "a separate supplemental appropriations act (Other Appropriations)"),
       ("budget_amendment", "part", "a budget amendment to the President's request"),
       ("emergency", "part", "a separate line of the account designated emergency ('... (emergency)'), where the "
                             "account's amount type has no emergency form: an emergency rescission")]
    + [("CURES", "contained", "NIH Innovation Account (21st Century Cures Act): inside NIH's headline total"),
       ("kids_first", "contained", "the Gabriella Miller Kids First Research Act line: inside the NIH Office of the "
                                   "Director's figure"),
       ("congressionally_directed_spending", "contained", "community project funding / congressionally directed "
                                                          "spending printed as its own line inside the account's figure"),
       ("chamber_proposal", "part", "a one-off line a chamber proposed under the account's heading (a note)")]
    + [(c, "view", f"parallel total printed with scope '{c.replace('_', ' ')}'") for c in PARALLEL_SCOPE_COMPONENTS])
# Each component's label (Component.label): the name as a source document
# prints it, taken from the document's text, never made up from the
# description -- (label, basis, where it is printed). basis "printed name":
# the document prints the component's own name (a parallel total's scope is
# the text after the account's name). basis "heading or line text": no name
# is printed for the component itself, so the label is the heading or line
# the tag was taken from.
COMPONENT_LABELS = {
    "defense": ("Defense function", "heading or line text",
                "the NSF Research and Related Activities sub-line: S.Rept. 118-62 p.220 'Defense function'"),
    # not in any document in document_store (H.R. 8845 / CBO): the text of v28's own
    # source_table_or_section for the two Crime Victims Fund rows ('Title VII, FY27 CHIMP')
    "chimp": ("FY27 CHIMP", "heading or line text",
              "v28 OBS-0966 source_table_or_section 'Title VII, FY27 CHIMP' (H.R. 8845 / CBO; not in document_store)"),
    "chimp_pop_up": ("FY27 CHIMP Pop-Up", "heading or line text",
                     "v28 OBS-0967 source_table_or_section 'Title VII, FY27 CHIMP Pop-Up' (not in document_store)"),
    "supplemental_act": ("OTHER APPROPRIATIONS", "heading or line text",
                         "the CJS tables' section heading the supplemental acts print under (H.Rept. 117-395 p.223)"),
    "budget_amendment": ("OTHER APPROPRIATIONS", "heading or line text",
                         "the section heading the FY2020 budget amendment prints under (H.Rept. 116-101 p.152)"),
    "emergency": ("(emergency)", "printed name",
                  "H.Rept. 118-585 p.339 / S.Rept. 118-207 p.467: 'Nonrecurring expenses fund, HHS (rescission) (emergency)'"),
    "CURES": ("CURES Act", "printed name", "the Labor-HHS Title II tables, under the title total: 'CURES Act'"),
    "kids_first": ("Gabriella Miller Kids First Research Act (Common Fund add)", "printed name",
                   "H.Rept. 117-96 p.477, under NIH's Office of the Director"),
    "congressionally_directed_spending": ("Community Project Funding / Congressionally Directed Spending", "printed name",
                                          "H.Rept. 118-585, HRSA-Wide Activities and Program Support"),
    "chamber_proposal": ("Diaper Grants", "heading or line text", "H.Rept. 117-96 p.485, under Social Services Block Grant"),
    "program_level": ("program level", "printed name", "e.g. 'Total, SAMHSA, program level' (Labor-HHS Title II tables)"),
    "fiscal_year_program_level": ("fiscal year program level", "printed name",
                                  "'Total, General Departmental Management fiscal year program level' (Labor-HHS)"),
    "program_level_with_cures_and_phs_evaluation_act_funding": (
        "program level (with CURES and PHS Evaluation Act Funding)", "printed name",
        "'Total, National Institutes of Health, program level (with CURES and PHS Evaluation Act Funding)'"),
    "program_level_excluding_arpa_h": ("program level (excluding ARPA-H)", "printed name",
                                       "'Total, NIH, program level (excluding ARPA-H)'"),
    "program_level_available_this_fiscal_year": ("program level, available this fiscal year", "printed name",
                                                 "'Total, Medicaid, program level, available this fiscal year'"),
    "program_level_including_emergencies": ("program level, including emergencies", "printed name",
                                            "'Total, SAMHSA, program level, including emergencies'"),
    "available_this_fiscal_year": ("available this fiscal year", "printed name",
                                   "'Total, Payments to States available this fiscal year'"),
    "current_year": ("Current Year", "printed name", "'Total, Current Year'"),
    "appropriated_in_this_bill": ("appropriated in this bill", "printed name",
                                  "'Total, Grants to States for Medicaid, appropriated in this bill'"),
    "available_in_this_bill": ("available in this bill", "printed name",
                               "'Total, Payments to States available in this bill'"),
    "excluding_emergencies": ("excluding emergencies", "printed name", "'Total, ACF (excluding emergencies)'"),
    "including_phs_eval_tap": ("including PHS Eval Tap", "printed name",
                               "'Subtotal, Mental Health, including PHS Eval Tap.' (H.Rept. 119-271)"),
    "discretionary": ("discretionary", "printed name",
                      "'Total, Title II, Department of Health and Human Services discretionary'"),
}
FY_TAG_RE = re.compile(r"^\s*FY\s*\d{2,4}\s+", re.I)


def match_component(label):
    """-> (canonical component or the label as printed, match kind)."""
    text = FY_TAG_RE.sub("", label or "").strip()
    kind, canonical, _, _ = best_match(text, sorted(COMPONENTS), lambda c: [c] + COMPONENTS[c])
    return (canonical, kind) if canonical else (text, kind)


def match_nodes(nodes, accounts=None):
    """Match every node; a nested line that doesn't match inherits its parent
    line's account. -> {node.id: match or None}."""
    out = {}
    for n in nodes:
        agency = n.frames[0] if n.frames else None
        m = match_label(n.label, agency, n.kind, accounts)
        if m and m["canonical_account_id"] is None and n.kind == "line" and n.parent_line is not None:
            parent = out.get(n.parent_line.id)
            if parent and parent["canonical_account_id"]:
                m = dict(parent, component=norm(n.label) or None, match="inherited", distance=None,
                         matched_name=None, via="parent_line")
        out[n.id] = m
    return out
