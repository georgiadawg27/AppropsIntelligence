"""
accounting.py -- accounting views, phase 1: the schema for an observation's tagged components and an
account's per-year attributes, their checks, and the views computed from them.

Every figure keeps its "As printed" headline (an Appropriations Observation with no component, budget
authority). A component record says what part of that headline -- or what money beside it -- is of a given
kind (an advance, an emergency designation, a transfer, an earmark ...), with its source and the quoted bill,
law or table text that justifies it. Later views (discretionary, program level, advances when available or
when enacted, defense split, without earmarks) are sums of the headline's components, never new figures.

    headline = base + the components the headline includes (in_headline TRUE)
    base     = what is left; never negative
    a view   = the headline, less the included components the view leaves out, plus the components beside the
               headline (in_headline FALSE) the view counts

Amounts are signed as their effect on budget authority: a rescission, a transfer out and an offsetting
collection are negative (printed negative in the tables), everything else is positive.

Used by scripts/build_workbook.py (the build stops on any error), approps_store.load (refused on any error),
scripts/tag_components.py (writes the component records) and scripts/measure_reconcile.py (the check).
The rules and the owner's defaults are written down in reference/ACCOUNTING_RULES.md.
"""

import re
from collections import defaultdict

COMPONENT_TYPES = ["advance_enacted", "advance_available", "designated", "transfer_in", "transfer_out",
                   "trust_fund_limitation", "fees_offsets", "rescission", "defense_portion", "other_law", "earmark"]
DESIGNATIONS = ["emergency", "disaster", "cap_adjustment", "other"]
# retagged: an existing line observation (source_observation_id) re-read as a component of its headline
# table_line: a line the figure's own table prints (the observation's document), read for this component
# auto_text: tagged from the committed bill or law text (data/legislative_text) by a phrase rule
# carried_forward: FY2025 full-year CR -- the FY2024 Enacted component, continued (carried_from_component_id)
TAG_METHODS = ["retagged", "table_line", "auto_text", "carried_forward"]
NEGATIVE_TYPES = ("rescission", "transfer_out", "fees_offsets")
BOOL = ("TRUE", "FALSE")

COMPONENT_COLUMNS = ["component_id", "observation_id", "component_type", "designation", "transfer_from", "amount",
                     "in_headline", "source_document_id", "source_location", "quoted_text", "tag_method", "tag_rule",
                     "source_observation_id", "carried_from_component_id", "notes",
                     # a component that is part of another component of the same headline (HCFAC's cap adjustment
                     # inside the amount transferred from the trust funds): the base counts only the outer one
                     "within_component_id"]
COMPONENT_REQUIRED = ["component_id", "observation_id", "component_type", "amount", "in_headline",
                      "source_document_id", "source_location", "quoted_text", "tag_method", "tag_rule"]
COMPONENT_ID = re.compile(r"CMP-(?:LHHS-)?\d{4}")

# Account attributes: one value per account, fiscal year and attribute, each with its evidence. Phase 1 adds the
# fields; phase 2 fills them from the Budget Appendix.
ATTRIBUTES = ["budget_class", "budget_subfunction", "fund_type", "account_id_code"]
BUDGET_CLASS = ["discretionary", "mandatory"]
ATTRIBUTE_FUND_TYPE = ["general", "trust", "special", "revolving"]
ATTRIBUTE_COLUMNS = ["account_attribute_id", "canonical_account_id", "fiscal_year", "attribute", "value",
                     "source_document_id", "source_location", "evidence"]
ATTRIBUTE_ID = re.compile(r"AAT-(?:LHHS-)?\d{4}")
SUBFUNCTION = re.compile(r"\d{3}")
# the Budget Appendix's account identification code: agency (3) - account (4) - transmittal (1) - fund (1) -
# subfunction (3), e.g. 075-0849-0-1-550
ACCOUNT_ID_CODE = re.compile(r"(\d{3})-(\d{4})-(\d)-(\d)-(\d{3})")
FIRST_FY, LAST_FY = 2017, 2027


def attribute_value_error(attribute, value):
    if attribute == "budget_class" and value not in BUDGET_CLASS:
        return f"budget_class {value!r} (allowed: {BUDGET_CLASS})"
    if attribute == "fund_type" and value not in ATTRIBUTE_FUND_TYPE:
        return f"fund_type {value!r} (allowed: {ATTRIBUTE_FUND_TYPE})"
    if attribute == "budget_subfunction" and not SUBFUNCTION.fullmatch(value or ""):
        return f"budget_subfunction {value!r} is not three digits"
    if attribute == "account_id_code" and not ACCOUNT_ID_CODE.fullmatch(value or ""):
        return f"account_id_code {value!r} is not a Budget Appendix code (e.g. 075-0849-0-1-550)"
    return None


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    return None


def component_errors(components, observations, documents):
    """Problems with the component records -> list of strings (empty when every record is sound).
    components: dicts with COMPONENT_COLUMNS; observations: {observation_id: observation dict};
    documents: a set of document IDs."""
    errs = []
    seen, keys = set(), set()
    by_id = {c.get("component_id"): c for c in components}
    for c in components:
        cid = c.get("component_id") or "?"
        for k in COMPONENT_REQUIRED:
            if _blank(c.get(k)) and not (k == "amount" and c.get(k) == 0):
                errs.append(f"{cid}: {k} is blank")
        if not COMPONENT_ID.fullmatch(cid):
            errs.append(f"{cid}: component_id is not CMP-NNNN (CJS) or CMP-LHHS-NNNN (Labor-HHS)")
        if cid in seen:
            errs.append(f"duplicate component_id {cid}")
        seen.add(cid)
        t = c.get("component_type")
        if t not in COMPONENT_TYPES:
            errs.append(f"{cid}: component_type {t!r} (allowed: {COMPONENT_TYPES})")
        d = c.get("designation") or ""
        if t == "designated" and d not in DESIGNATIONS:
            errs.append(f"{cid}: a designated component needs a designation (allowed: {DESIGNATIONS}), not {d!r}")
        if t != "designated" and d:
            errs.append(f"{cid}: designation {d!r} on a {t} component (only designated carries one)")
        if t == "transfer_in" and _blank(c.get("transfer_from")):
            errs.append(f"{cid}: a transfer_in names where it comes from (transfer_from)")
        if t not in ("transfer_in",) and not _blank(c.get("transfer_from")):
            errs.append(f"{cid}: transfer_from on a {t} component")
        amt = _int(c.get("amount"))
        if amt is None:
            errs.append(f"{cid}: amount {c.get('amount')!r} is not whole dollars")
        elif t in NEGATIVE_TYPES and amt > 0:
            errs.append(f"{cid}: a {t} reduces budget authority -- its amount is negative, not {amt:,}")
        elif t in COMPONENT_TYPES and t not in NEGATIVE_TYPES and amt < 0:
            errs.append(f"{cid}: a {t} amount is positive, not {amt:,}")
        if c.get("in_headline") not in BOOL:
            errs.append(f"{cid}: in_headline {c.get('in_headline')!r} (TRUE or FALSE)")
        if c.get("source_document_id") and c["source_document_id"] not in documents:
            errs.append(f"{cid}: unknown source document {c['source_document_id']}")
        m = c.get("tag_method")
        if m not in TAG_METHODS:
            errs.append(f"{cid}: tag_method {m!r} (allowed: {TAG_METHODS})")
        o = observations.get(c.get("observation_id"))
        if o is None:
            errs.append(f"{cid}: unknown observation {c.get('observation_id')}")
            continue
        if o.get("component") or o.get("amount_type") != "budget authority":
            errs.append(f"{cid}: {o['observation_id']} is not a headline (budget authority, no component)")
        if o.get("verification_status") == "superseded":
            errs.append(f"{cid}: {o['observation_id']} is superseded -- move the component to "
                        f"{o.get('superseded_by_observation_id') or 'its replacement'}")
        src = c.get("source_observation_id") or ""
        if m == "retagged" and not src:
            errs.append(f"{cid}: a retagged component names the line it was read from (source_observation_id)")
        if src:
            s = observations.get(src)
            if s is None:
                errs.append(f"{cid}: unknown source observation {src}")
            elif any(s[k] != o[k] for k in ("canonical_account_id", "fiscal_year", "stage")):
                errs.append(f"{cid}: source observation {src} is another account, year or stage than {o['observation_id']}")
        cf = c.get("carried_from_component_id") or ""
        if (m == "carried_forward") != bool(cf):
            errs.append(f"{cid}: carried_from_component_id is set exactly when tag_method is carried_forward")
        if cf:
            f = by_id.get(cf)
            if f is None:
                errs.append(f"{cid}: carried from unknown component {cf}")
            elif f.get("component_type") != t or f.get("amount") != c.get("amount"):
                errs.append(f"{cid}: carried from {cf}, but its type or amount differs")
            else:
                fo = observations.get(f.get("observation_id"))
                if fo and fo["canonical_account_id"] != o["canonical_account_id"]:
                    errs.append(f"{cid}: carried from {cf}, another account")
        w = c.get("within_component_id") or ""
        if w:
            outer = by_id.get(w)
            if outer is None:
                errs.append(f"{cid}: within unknown component {w}")
            elif outer.get("observation_id") != c.get("observation_id"):
                errs.append(f"{cid}: within {w}, a component of another headline")
            elif outer.get("within_component_id"):
                errs.append(f"{cid}: within {w}, which is itself within another component (one level only)")
            elif c.get("in_headline") != outer.get("in_headline"):
                errs.append(f"{cid}: within {w}, but one is inside the headline and the other beside it")
            elif amt is not None and _int(outer.get("amount")) is not None and abs(amt) > abs(_int(outer["amount"])):
                errs.append(f"{cid}: within {w}, but larger than it")
        key = (c.get("observation_id"), t, d, c.get("amount"), c.get("source_document_id"), c.get("source_location"),
               src)
        if key in keys:
            errs.append(f"{cid}: the same component is recorded twice for {c.get('observation_id')}")
        keys.add(key)
    return errs


def attribute_errors(rows, accounts, documents):
    """Problems with the account attribute records -> list of strings. accounts: a set of account IDs."""
    errs = []
    seen, facts = set(), {}
    for r in rows:
        rid = r.get("account_attribute_id") or "?"
        for k in ATTRIBUTE_COLUMNS:
            if _blank(r.get(k)) and k != "source_location":
                errs.append(f"{rid}: {k} is blank (every value carries its evidence)")
        if not ATTRIBUTE_ID.fullmatch(rid):
            errs.append(f"{rid}: account_attribute_id is not AAT-NNNN or AAT-LHHS-NNNN")
        if rid in seen:
            errs.append(f"duplicate account_attribute_id {rid}")
        seen.add(rid)
        if r.get("canonical_account_id") not in accounts:
            errs.append(f"{rid}: unknown account {r.get('canonical_account_id')}")
        fy = _int(r.get("fiscal_year"))
        if fy is None or not FIRST_FY <= fy <= LAST_FY:
            errs.append(f"{rid}: fiscal_year {r.get('fiscal_year')!r} outside FY{FIRST_FY}-FY{LAST_FY}")
        a = r.get("attribute")
        if a not in ATTRIBUTES:
            errs.append(f"{rid}: attribute {a!r} (allowed: {ATTRIBUTES})")
        else:
            e = attribute_value_error(a, r.get("value"))
            if e:
                errs.append(f"{rid}: {e}")
        if r.get("source_document_id") and r["source_document_id"] not in documents:
            errs.append(f"{rid}: unknown source document {r['source_document_id']}")
        key = (r.get("canonical_account_id"), fy, a)
        if key in facts:
            errs.append(f"{rid}: {key[0]} FY{fy} already has a {a} ({facts[key]}) -- one value per account-year")
        facts[key] = rid
    # the subfunction is the code's last three digits wherever both are on file
    vals = {(r.get("canonical_account_id"), _int(r.get("fiscal_year")), r.get("attribute")): r for r in rows}
    for (acc, fy, a), r in vals.items():
        if a != "account_id_code":
            continue
        m = ACCOUNT_ID_CODE.fullmatch(r.get("value") or "")
        sub = vals.get((acc, fy, "budget_subfunction"))
        if m and sub and sub.get("value") != m.group(5):
            errs.append(f"{sub['account_attribute_id']}: budget_subfunction {sub.get('value')} differs from "
                        f"{r['account_attribute_id']}'s account_id_code {r['value']}")
    return errs


def data_errors(data):
    """Both checks over a data/staged.json document."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    docs = {d["document_id"] for d in data["source_docs"]}
    accs = {a["canonical_account_id"] for a in data["accounts"]}
    errs = (component_errors(data.get("observation_components", []), obs, docs)
            + attribute_errors(data.get("account_attributes", []), accs, docs))
    # IDs are never reused: a retired component ID (observation_components_retired) stays retired
    live = {c.get("component_id") for c in data.get("observation_components", [])}
    errs += [f"{r}: a retired component ID is in use again" for r in data.get("observation_components_retired", [])
             if (r.get("component_id") if isinstance(r, dict) else r) in live]
    return errs


# ------------------------------------------------------------------------------------------------- views

# A view is the headline with some components taken out (those it includes but the view leaves out) and some
# added (those beside it the view counts). A selector is a component type, or (type, designation).
VIEWS = {
    "as_printed": {"label": "As printed", "remove": (), "add": ()},
    # CBO convention: an advance counts in the year it becomes available
    "advances_when_available": {"label": "advances counted when available",
                                "remove": ("advance_enacted",), "add": ("advance_available",)},
    "advances_when_enacted": {"label": "advances counted when enacted (appropriated in this bill)",
                              "remove": ("advance_available",), "add": ("advance_enacted",)},
    "program_level": {"label": "program level (with transfers in and trust-fund limitations)",
                      "remove": (), "add": ("transfer_in", "trust_fund_limitation")},
    "program_level_when_available": {"label": "program level, advances counted when available",
                                     "remove": ("advance_enacted",),
                                     "add": ("advance_available", "transfer_in", "trust_fund_limitation")},
    "excluding_emergencies": {"label": "excluding emergency designations",
                              "remove": (("designated", "emergency"),), "add": ()},
    "excluding_earmarks": {"label": "excluding earmarks", "remove": ("earmark",), "add": ()},
    "nondefense": {"label": "nondefense (defense split out)", "remove": ("defense_portion",), "add": ()},
}
# The printed parallel totals (Component vocabulary, kind 'view') a computed view is checked against. A printed
# view not listed here (e.g. 'discretionary', which needs every account's budget_class -- phase 2) is not checked.
PRINTED_VIEW = {
    "appropriated_in_this_bill": "advances_when_enacted",
    "available_this_fiscal_year": "advances_when_available",
    "program_level": "program_level",
    "program_level_available_this_fiscal_year": "program_level_when_available",
    "excluding_emergencies": "excluding_emergencies",
}
NOT_CHECKED_YET = {"discretionary": "needs every account's budget_class (phase 2)"}


def _selected(c, selectors):
    for s in selectors:
        if isinstance(s, tuple):
            if c["component_type"] == s[0] and (c.get("designation") or "") == s[1]:
                return True
        elif c["component_type"] == s:
            return True
    return False


def split(headline_amount, components):
    """-> (base, [included], [beside]) for one headline and its components. A component within another one is
    already counted in it, so the base subtracts only the outer one."""
    inside = [c for c in components if c["in_headline"] == "TRUE"]
    beside = [c for c in components if c["in_headline"] != "TRUE"]
    return headline_amount - sum(c["amount"] for c in inside if not c.get("within_component_id")), inside, beside


def view_adjustment(components, view):
    """What a view changes on top of the headline: -> (delta, [the components it took out or added])."""
    v = VIEWS[view]
    out = [c for c in components if c["in_headline"] == "TRUE" and _selected(c, v["remove"])]
    add = [c for c in components if c["in_headline"] != "TRUE" and _selected(c, v["add"])]
    # a component within one the view already counts is not counted again
    out = [c for c in out if c.get("within_component_id") not in {x["component_id"] for x in out}]
    add = [c for c in add if c.get("within_component_id") not in {x["component_id"] for x in add}]
    return sum(c["amount"] for c in add) - sum(c["amount"] for c in out), out + add


def view_amount(headline_amount, components, view):
    return headline_amount + view_adjustment(components, view)[0]


def components_by_observation(data):
    out = defaultdict(list)
    for c in data.get("observation_components", []):
        out[c["observation_id"]].append(c)
    return out


def money(x):
    return f"-${-x:,}" if x < 0 else f"${x:,}"
