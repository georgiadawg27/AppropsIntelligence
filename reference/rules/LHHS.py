"""
Labor-HHS-Education (LHHS): the rules true only of its documents and accounts, moved out of the core
(G-1). Each rule names its scope, the hook it acts at (rules_registry.HOOKS) and where it lived before.
Tests: tests/test_any_subcommittee.py MovedRules (each fires on its real case, and not outside its scope).
"""

import csv
import re

import paths
from rules_registry import ANY, NotComponent, Rule, Scope

CODE = "LHHS"
ALL = Scope(CODE, accounts=ANY, fiscal_years=ANY, stages=ANY)
PHS_241 = "section 241 of the PHS Act (evaluation funds)"
PPHF = "the Prevention and Public Health Fund (section 4002 of the PPACA)"
CR_ENACTED = dict(fiscal_years=(2025, 2025), stages={"Enacted"})


def parts(x, ctx):
    seg_b, seg_a = ctx.tc.segments(x)
    return seg_b, seg_a, seg_b + " [$] " + seg_a


# ------------------------------------------------------------------------------------------------ layout, appendix

LAYOUT = {
    # an agency's other names in the bill text (SAMHSA is 'Substance Use and Mental Health Services Administration' in
    # the FY2023 bills)
    "agency_aliases": {"Substance Abuse and Mental Health Services Administration":
                       ["substance use and mental health services administration"]},
}

# ------------------------------------------------------------------------------------------------ phrase rules

# section 241 of the PHS Act (the evaluation tap): money from it is a transfer_in beside the headline
TRANSFER_241 = re.compile(r"available under section 241", re.I)
SUB_ALLOCATION = re.compile(r"of (?:the )?(?:amounts?|funds)\b[^$]*\bsection 241\b", re.I)
# the core transfer rule's alternatives plus this rule's, as one regex: which phrase is printed first decides the
# source the transfer names
TRANSFER_ANY = re.compile(r"derived by transfer|available under section 241|from (?:amounts|funds) made available under"
                          r"|from (?:amounts|funds) made ava[\w ]*$", re.I)


def available_241(x, ctx):
    """'... available under section 241 ...' where the core's transfer phrases are not printed: a transfer_in
    from section 241 (the heading's own amounts and a part of a section 241 amount excepted)."""
    seg_b, seg_a, both = parts(x, ctx)
    m = TRANSFER_241.search(both)
    if not m or ctx.tc.TRANSFER.search(both) or ctx.tc.THIS_HEADING.search(seg_a) or SUB_ALLOCATION.search(seg_b):
        return None
    return "transfer_in", "", PHS_241, TRANSFER_ANY.search(both).group(0)


def sub_allocation_241(x, ctx):
    """'of the amounts ... section 241' before an amount the core's transfer rule reads: a part of an amount from
    section 241, already tagged with it -- not a transfer of its own (vetoes the core's transfer rule). Where no core
    transfer phrase is printed, LHHS-241-AVAILABLE makes the same exception."""
    seg_b, seg_a, both = parts(x, ctx)
    if not ctx.tc.TRANSFER.search(both) or ctx.tc.THIS_HEADING.search(seg_a):
        return None
    return NotComponent(None) if SUB_ALLOCATION.search(seg_b) else None


# an evaluation tap worded 'in addition to amounts provided herein, $X is available to this appropriation, for the
# purposes under this heading, from amounts provided pursuant to section 241' (S. 3062, CDC FY2022 Senate; D-1). The
# held phrase window may end inside 'pursuant', so the source is named only when '241' is printed in it.
EVAL_TAP = re.compile(r"^\W*(?:is|shall be) available to this appropriation\b[^$;:]{0,80}?\bfrom (?:amounts|funds) prov\w*", re.I)


def evaluation_tap(x, ctx):
    seg_b, seg_a, _ = parts(x, ctx)
    if EVAL_TAP.search(seg_a):
        return ("transfer_in", "", PHS_241 if "241" in seg_a else "not named in the phrase",
                "is (shall be) available to this appropriation ... from amounts provided")
    return None


def source_241(x, t, ctx):
    """The transfer's source is section 241 when the first transfer phrase printed names it, or 'from amounts made
    available under' is followed by 'section 241'."""
    _, _, both = parts(x, ctx)
    u = TRANSFER_ANY.search(both)
    if u and ("241" in u.group(0) or (u.group(0).lower().startswith("from")
                                      and re.match(r"\W*section 241", ctx.tc.after_text(x)))):
        return PHS_241, u.group(0)
    return None


def source_pphf(x, t, ctx):
    """'from amounts made available under section 4002 / the Prevention and Public Health Fund'."""
    _, _, both = parts(x, ctx)
    u = TRANSFER_ANY.search(both)
    rest = ctx.tc.after_text(x)
    if (u and "241" not in u.group(0) and u.group(0).lower().startswith("from")
            and not re.match(r"\W*section 241", rest)
            and re.match(r"\W*section 4002|\W*the Prevention and Public Health Fund", rest, re.I)):
        return PPHF, u.group(0)
    return None


# SAMHSA's 'in addition to amounts provided herein, the following amounts shall be available under section 241 of
# the PHS Act: (1) $79,200,000 ...; and (2) $2,000,000 ...': each numbered item is a transfer_in beside the main amount
LIST_241 = re.compile(r"following amounts shall be available under section 241", re.I)
LIST_ITEM = re.compile(r"^[;:,\s]*(?:and\s+)?\(\d+\)\s*$")


def list_241(x, ctx):
    """A numbered item of a 'following amounts shall be available under section 241' list (ctx.state carries
    whether the list is still open)."""
    before, _ = ctx.tc.CT.split_phrase(x)
    ms = list(ctx.tc.NUMBER.finditer(before))
    since = before[ms[-1].end():] if ms else before
    item = False
    if LIST_241.search(since):
        item = True
    elif ctx.state.get("list_241") and LIST_ITEM.match(ctx.tc.segments(x)[0]):
        item = True
    ctx.state["list_241"] = item
    return ("transfer_in", "", PHS_241,
            "the following amounts shall be available under section 241 (a numbered item)") if item else None


# D-3b: a section 241 amount whose source is named past the phrase window, 'in addition to amounts provided under
# this heading, $100,000,000 shall be available for implementation of the Community Violence Intervention initiative
# from amounts made available under section 241 of the PHS Act' (CDC Injury Prevention, FY2025 request, Budget
# Appendix p.405). Only this wording is read on into the paragraph's text (no other phrase is widened); never PPHF.
PAST_WINDOW_241 = re.compile(r"^\W*shall be available for [^$;:]{0,120}?\bfrom amounts made available under section "
                             r"241 of the PHS Act\b", re.I)


def past_window_241(x, ctx):
    before, after = ctx.tc.CT.split_phrase(x)
    printed = re.search(r"\[(\$[^\]]*)\]", x["phrase"])
    if not printed or not after.strip():
        return None
    key = before[-60:] + printed.group(1) + after
    for t in ctx.para.get("text") or []:
        k = t.find(key)
        if k >= 0 and PAST_WINDOW_241.match(t[k + len(before[-60:]) + len(printed.group(1)):]):
            return ("transfer_in", "", PHS_241,
                    "from amounts made available under section 241 of the PHS Act (read past the phrase window, D-3b)")
    return None


CAP_ADJUSTMENT = re.compile(r"is additional new budget authority specified for purposes of section 251\(b\)\(2\)\(C\)",
                            re.I)
CURES = re.compile(r"section 1001\(b\)\(4\) of the 21st Century Cures Act", re.I)
PRIF = re.compile(r"from the 10-year Pediatric Research Initiative Fund", re.I)
# 'of the amounts made available under this heading, up to $1,000,000 from amounts made available for Quarantine
# appropriations shall remain available until expended' (CDC Emerging and Zoonotic, FY2026 on): the availability of
# the heading's own money, not a transfer (owner, D-1)
QUARANTINE = re.compile(r"^\W*from amounts made available for Quarantine", re.I)


def cap_adjustment(x, ctx):
    if CAP_ADJUSTMENT.search(parts(x, ctx)[1]):
        return ("designated", "cap_adjustment", "",
                "additional new budget authority specified for purposes of section 251(b)(2)(C)")
    return None


def cures(x, ctx):
    if CURES.search(parts(x, ctx)[0]):
        return "designated", "other", "", "21st Century Cures (NIH Innovation Account, sec. 1001(b)(4))"
    return None


def prif(x, ctx):
    m = PRIF.search(parts(x, ctx)[1])
    return ("transfer_in", "", "10-year Pediatric Research Initiative Fund (PRIF)", m.group(0)) if m else None


def quarantine(x, ctx):
    return NotComponent(None) if QUARANTINE.search(parts(x, ctx)[1]) else None


# phrases that look like components but are not (owner's list, 2026-10-11): never tagged, never reported.
# (kind, regex, where: before = the raw words before the amount, after = the amount's own words after it)
NOT_COMPONENT = [
    ("availability", QUARANTINE, "after"),
    # 'of the amounts provided under this heading from amounts available under section 241 of the PHS Act, $6,800,000
    # shall be available to carry out evaluations' (OS-GDM): part of the heading's section 241 amount, already tagged
    # with it (D-1)
    ("sub-allocation", re.compile(r"of the amounts provided under this heading from amounts available under section "
                                  r"241\b[^$]*$", re.I), "before"),
    ("cap-adjustment base", re.compile(r"is provided to meet the terms of section 251\(b\)\(2\)\(C\)\(ii\)", re.I),
     "after"),
]


def not_component(kind, rx, where_):
    def fn(x, ctx):
        raw_b, _, seg_a = ctx.tc.phrase_parts(x)
        return kind if rx.search(raw_b if where_ == "before" else seg_a) else None
    return fn


# ------------------------------------------------------------------------------------------------ the text check

def split(a):
    """(the words before the amount, the words after it) -- check_legislative_text.split_phrase"""
    m = re.match(r"(.*)\[\$[^\]]*\](.*)", a["phrase"], re.S)
    return (m.group(1), m.group(2)) if m else (a["phrase"], "")


def money(xs):
    return " + ".join(f"${x:,}" for x in xs)


# an amount printed as coming from section 241 is not the paragraph's main appropriated amount
NOT_MAIN_241 = re.compile(r"^\s*,?\s*(to be derived from amounts? (?:available|made available) under section 241|"
                          r"from (?:the )?(?:funds|amounts) (?:made )?available under section 241)", re.I)
TAP_241 = re.compile(r"^\s*,?\s*(?:shall|is to) be (?:from|derived from) (?:funds|amounts) (?:made )?available under "
                     r"section 241", re.I)


def not_main_241(a, ctx):
    return "from section 241 (PHS evaluation funds)" if NOT_MAIN_241.search(split(a)[1]) else None


def derivation_241(para, ctx):
    """'of which $X shall be from funds available under section 241': PHS evaluation funds, not new budget
    authority, taken off the main amount -> (amounts, how, how beside another term) or None."""
    tap = [a["amount"] for a in para["amounts"]
           if TAP_241.search(split(a)[1]) and re.search(r"of which\s*$", split(a)[0], re.I)]
    if not tap:
        return None
    return (tuple(tap), f" - {money(tap)} from section 241 (PHS evaluation funds, not new budget authority)",
            f" - {money(tap)} from section 241")


def no_main_241(para, ours, ctx):
    """A paragraph whose every amount is from section 241 and a $0 headline (ONC): no new budget authority."""
    tap = [a["amount"] for a in para["amounts"] if TAP_241.search(split(a)[1])]
    if tap and ours == 0 and len(tap) == len(para["amounts"]):
        return "match", 0, (f"no new budget authority: the paragraph's {money(tap)} is all from section 241 "
                            "(PHS evaluation funds)")
    return None


# ------------------------------------------------------------------------------------------------ the site

def onc_cell_note(rec, obs, ctx):
    """A $0 headline whose cell has a program-level line from the PHS evaluation set-aside (ONC FY2022): the note says
    the program is funded, just not by new budget authority -> (the headline, the note) or None."""
    if (rec["component"] != "program_level" or rec["verification_status"] == "superseded" or not rec["amount"]
            or not re.search(r"Evaluation Tap|section 241 of the PHS Act", rec.get("source_table_or_section") or "")):
        return None
    head = next((x for x in obs if x["observation_id"] == rec.get("headline_observation_id")), None)
    if head and head["amount"] == 0:
        return head, f"Funded through the PHS evaluation set-aside (${rec['amount']:,}), not new budget authority."
    return None


# ------------------------------------------------------------------------------------------------ lines

def onc_evaluation_tap(o, ctx):
    if o.get("component") == "program_level" and o["amount_type"] == "budget authority":
        return "transfer_in", "", PHS_241, "the table's 'Evaluation Tap Funding' line (recorded as program_level)"
    return None


def victf_line(row, ctx):
    """The Vaccine Injury Compensation Trust Fund's two lines inside 'Total, Vaccine Injury Compensation Trust Fund'."""
    if row["line"] not in ("HRSA Administrative expenses", "Post-FY 1988 Claims"):
        return None
    claims = row["line"] == "Post-FY 1988 Claims"
    return {"component_type": "mandatory_portion" if claims else "trust_fund_limitation",
            "tag_rule": f"the table's '{row['line']}' line, printed inside 'Total, Vaccine Injury Compensation Trust "
                        "Fund' (" + row["read_from"] + ")"
                        + ("; the claims are the mandatory part of the mixed account 075-8175" if claims else ""),
            "notes": "inside the headline: the table's total adds this line to the "
                     + ("administrative line" if claims else "claims line")}


def request_evaluation_line(row, ctx):
    """A request's evaluation funds line (D-9 part 4, decision C): where the President's request has no Budget
    Appendix language of its own for the account, the line the House or Senate comparative table prints under the
    account in the request column ('Evaluation Funding (PHS Act Sec. 241)', 'Evaluation Tap Funding') is a
    transfer_in from section 241 beside the request's headline, cited to the report page."""
    if not re.search(r"PHS Act Sec\.? 241|Evaluation Tap Funding", row["line"]):
        return None
    return {"component_type": "transfer_in", "transfer_from": PHS_241, "in_headline": "FALSE",
            "tag_rule": f"the request column's '{row['line']}' line under the account in the report's comparative "
                        f"table: the request has no Appendix language of its own for the account (decision C; "
                        f"{row['read_from']})",
            "notes": "beside the headline: section 241 evaluation funds are transferred in, not new budget authority"}


def pphf_table(data, heads, report, ctx):
    """The Prevention and Public Health Fund allocation (pphf_table.csv): the general provision (sec. 222) transfers
    the Fund "to the accounts specified, in the amounts specified" in the report's table; each row is a transfer_in
    beside the receiving account's headline. A row for an account we don't hold (ACL) is reported, not recorded."""
    tc, out = ctx.tc, []
    path = paths.accounting_dir() / "pphf_table.csv"
    if path.exists():
        with open(path) as f:
            for r in csv.DictReader(f):
                fy = int(r["fiscal_year"])
                if not r["account_id"]:
                    report["pphf_not_held"].append(f"FY{fy} {r['stage']} {r['account_printed']}: {r['activity']} "
                                                   f"{tc.AC.money(int(r['amount']))}")
                    continue
                h = heads[(r["account_id"], fy, r["stage"])]
                h = tc.pick_headline(h, r["source_document_id"]) if h else None
                if h is None:
                    report["pphf_no_headline"].append(f"FY{fy} {r['stage']} {r['account_id']}: {r['activity']}")
                    continue
                table = ("the Prevention and Public Health Fund allocation table" if r["table"] == "allocation"
                         else f"the comparative table ({r['table'].split(', ', 1)[1]})")
                # the request allocates the Fund outside its appropriations language: no bill section (D-3)
                request = r["stage"] == tc.REQUEST_STAGE
                out.append({"observation_id": h["observation_id"], "component_type": "transfer_in", "designation": "",
                            "transfer_from": PPHF, "amount": int(r["amount"]), "in_headline": "FALSE",
                            "source_document_id": r["source_document_id"],
                            "source_location": f"p.{r['page']}, {table}, '{r['activity']}'"
                                               + ("" if request else f"; {r['bill_document_id']} {r['bill_section']}"),
                            "quoted_text": f"{r['account_printed']} | {r['printed_line']}",
                            "tag_method": "table_line",
                            "tag_rule": (f"PPHF allocation: the President's request allocates the Fund outside its "
                                         f"appropriations language; the report's comparative table prints the "
                                         f"request's Fund line ({r['read_from']})") if request else
                                        f"PPHF allocation: {r['bill_section']} transfers the Fund to the accounts and "
                                        f"in the amounts the report's table specifies ({r['read_from']})",
                            "source_observation_id": "", "carried_from_component_id": "",
                            "notes": "beside the headline: the Fund's money is transferred in, not appropriated here"})
    report["rule_summary"].append("PPHF rows for an account we don't hold: " + str(len(report["pphf_not_held"])) +
                                  "; PPHF rows with no headline in their cell: "
                                  + (", ".join(report["pphf_no_headline"]) or "none"))
    return out


# ------------------------------------------------------------------------------------------------ the FY2025 CR

# P.L. 119-4 div. A (the FY2025 full-year CR) anomalies that name an account we hold -> (the section, the
# components its text changes, the components its text states in their place). Every other component of the account
# continues from FY2024 (owner, 2026-10-11). Where the anomaly states the new amount, a new FY2025 component is
# recorded from its text (tag_method cr_anomaly); a changed component is dropped with nothing in its place only when
# the anomaly ends the item (CDS to $0; sec. 1111 for every earmark). A selector is a component type, or
# (type, designation). A stated component: (type, designation, amount, in_headline, the anomaly's words, within:
# the type of the stated component it is part of).
CR_ANOMALIES = {
    "ACC-HHS-CMS-MEDICAID": ("P.L. 119-4 div. A sec. 1109(b)(2)", ("advance_enacted",), [
        ("advance_enacted", "", 261063820000, "FALSE",
         "the following amounts shall be available for the following accounts for advance payments for the first "
         "quarter of fiscal year 2026: ... (2) “Department of Health and Human Services—Centers for "
         "Medicare & Medicaid Services—Grants to States for Medicaid”, ... $261,063,820,000", None)]),
    "ACC-HHS-ACF-CHILD-SUPPORT": ("P.L. 119-4 div. A sec. 1109(b)(3)", ("advance_enacted",), [
        ("advance_enacted", "", 1600000000, "TRUE",
         "the following amounts shall be available for the following accounts for advance payments for the first "
         "quarter of fiscal year 2026: ... (3) “Department of Health and Human Services—Administration "
         "for Children and Families—Payments to States for Child Support Enforcement and Family Support "
         "Programs”, ... $1,600,000,000", None)]),
    "ACC-HHS-ACF-FOSTER-CARE": ("P.L. 119-4 div. A sec. 1109(b)(4)", ("advance_enacted",), [
        ("advance_enacted", "", 3600000000, "TRUE",
         "the following amounts shall be available for the following accounts for advance payments for the first "
         "quarter of fiscal year 2026: ... (4) “Department of Health and Human Services—Administration "
         "for Children and Families—Payments for Foster Care and Permanency”, ... $3,600,000,000", None)]),
    "ACC-HHS-CMS-HCFAC": ("P.L. 119-4 div. A sec. 1902(b)", ("trust_fund_limitation", ("designated", "cap_adjustment")), [
        ("trust_fund_limitation", "", 941000000, "TRUE",
         "Notwithstanding section 1101, the level for “Department of Health and Human Services—Centers "
         "for Medicare & Medicaid Services—Health Care Fraud and Abuse Control Account” shall be "
         "$941,000,000", None),
        ("designated", "cap_adjustment", 630000000, "TRUE",
         "of the funds made available by section 1101 under such heading, $630,000,000 is additional new budget "
         "authority specified for purposes of section 251(b)(2)(C) of the Balanced Budget and Emergency Deficit "
         "Control Act of 1985", "trust_fund_limitation")]),
    "ACC-HHS-HRSA-HEALTH-SYSTEMS": ("P.L. 119-4 div. A sec. 1904 (OPTN registration fees credited to the account)",
                                    ("fees_offsets",), []),
    "ACC-HHS-NIH-CURES": ("P.L. 119-4 div. A sec. 1905", (("designated", "other"),), [
        ("designated", "other", 127000000, "TRUE",
         "the level for “Department of Health and Human Services—National Institutes of Health—NIH "
         "Innovation Account, CURES Act” shall be applied by substituting “$127,000,000” for "
         "“$407,000,000”", None)]),
    "ACC-HHS-HRSA-PROGRAM-SUPPORT": ("P.L. 119-4 div. A sec. 1908(2) (CDS $890,788,000 -> $0)", ("earmark",), []),
    "ACC-HHS-SAMHSA-PROGRAM-SUPPORT": ("P.L. 119-4 div. A sec. 1908(3) (CDS $72,090,000 -> $0)", ("earmark",), []),
    "ACC-HHS-ACF-CFSP": ("P.L. 119-4 div. A sec. 1908(4) (CDS $40,011,000 -> $0)", ("earmark",), []),
}


def cr_anomaly(c, ctx):
    a = CR_ANOMALIES.get(ctx.account)
    if a and ctx.tc.AC._selected(c, a[1]):
        return {"anomaly": (a[0], a[2])}
    return None


# P.L. 119-4 div. A sec. 1906: the FY2025 Fund transferred "in the amounts specified ... in subsection (a) of section
# 222 of division D of Public Law 118-47" -- the FY2024 allocation, continued
CR_PPHF = "P.L. 119-4 div. A sec. 1906: the FY2024 Prevention and Public Health Fund allocation (P.L. 118-47 div. D sec. 222(a))"


def cr_pphf(c, ctx):
    return {"rule": CR_PPHF} if c.get("transfer_from") == PPHF else None


# ------------------------------------------------------------------------------------------------ measure_reconcile

def is_evaluation_tap_line(v, ctx):
    return v.get("component") == "program_level" and "Evaluation Tap Funding" in ctx.tc.printed(v)


def onc_view_check(a, v, h, comps, ctx):
    """ONC's 'Evaluation Tap Funding' line is the line the tagger reads as ONC's section 241 transfer_in
    (retagged), not a program-level total: it is checked against the cell's section 241 transfers (D-1)."""
    if not is_evaluation_tap_line(v, ctx):
        return None
    taps = [c for c in comps[h["observation_id"]] if c["component_type"] == "transfer_in"
            and "241" in (c.get("transfer_from") or "")]
    return (sum(c["amount"] for c in taps), taps,
            "the 'Evaluation Tap Funding' line is ONC's section 241 transfer_in, not a program-level total: the "
            f"section 241 transfer_in components of the headline {h['observation_id']}")


CHECKS = {
    # Why a printed view differs from the computed one (owner, 2026-10-11): one review item per cause, not one per
    # cell. account_not_held is no failure: the money sits in an account we don't hold, so the view is not checked.
    "known_causes": {
        ("ACC-HHS-CDC-TOTAL", "program_level"): "pphf_general_provision_table",
        ("ACC-HHS-SAMHSA-TOTAL", "program_level"): "pphf_general_provision_table",
        ("ACC-HHS-ACL-TOTAL", "program_level"): "account_not_held",
    },
    # Causes known from reading the documents for one cell (component backfill, FY2017-FY2023): (account, fiscal
    # year, stage, view) -> cause. Each is reviewed once for every cell it covers; none changes a figure.
    "cell_causes": {
        **{(a, fy, st, "program_level"): "nef_transfer_untagged" for a, fy, st in (
            ("ACC-HHS-CDC-TOTAL", 2018, "Enacted"), ("ACC-HHS-CDC-TOTAL", 2020, "Enacted"),
            ("ACC-HHS-CDC-TOTAL", 2021, "Senate Reported"), ("ACC-HHS-OS-TOTAL", 2021, "Senate Reported"))},
        **{(a, 2017, st, "program_level"): "pandemic_flu_balances" for a in ("ACC-HHS-CDC-TOTAL", "ACC-HHS-OS-TOTAL")
           for st in ("Senate Reported", "Enacted")},
        **{("ACC-HHS-NIH-TOTAL", 2023, st, "program_level"): "prif_outside_printed_program_level"
           for st in ("House Reported", "Senate Reported")},
        ("ACC-HHS-ACF-TOTAL", 2023, "Enacted", "excluding_emergencies"): "emergency_lines_not_held",
        # D-3's request_evaluation_tap_past_window (CDC FY2025 request) closed in D-3b: the section 241 amount is
        # tagged (PAST_WINDOW_241)
    },
    # an agency member with no headline in the cell
    "member_cause": {"ACC-HHS-OS-ONC": "onc_no_headline"},
    "cause_text": {
        "pphf_general_provision_table": "Prevention and Public Health Fund transfers made through the general "
                                        "provisions' allocation table, which no phrase rule reads (phase 1b)",
        "account_not_held": "the program-level money is in an ACL account we don't hold",
        "onc_no_headline": "ONC has no headline in the cell, so its section 241 transfer has nothing to sit on "
                           "(phase 1b)",
        "nef_transfer_untagged": "Nonrecurring Expenses Fund money made available to the agency (a general provision, "
                                 "or a proviso that names the Fund apart from the amount) is printed as a 'Transfers "
                                 "from Nonrecurring Expenses Fund' line in the program level, and no phrase rule "
                                 "tags it",
        "pandemic_flu_balances": "the FY2017 program level counts the 'Pandemic Flu balances (Public Law 111-32)' "
                                 "line, balances of another law that no phrase rule tags",
        "evaluation_tap_phrase_untagged": "the paragraph's evaluation tap reads 'is available to this appropriation "
                                          "... from amounts provided pursuant to section 241', which no phrase rule "
                                          "reads",
        "prif_outside_printed_program_level": "the bill makes the 10-year Pediatric Research Initiative Fund transfer "
                                              "in addition to the Office of the Director's amount, and the report's "
                                              "program level does not count it",
        "onc_evaluation_tap_line": "the printed line is ONC's 'Evaluation Tap Funding' line ($0 here), not a "
                                   "program-level total, so it cannot equal the view where ONC has its own "
                                   "appropriation",
        "emergency_lines_not_held": "the total counts emergency appropriations from the law's other divisions (P.L. "
                                    "117-328), which are not lines we hold, so no designated component takes them off",
    },
}

ADVANCE_ACCOUNTS = {"ACC-HHS-ACF-CHILD-SUPPORT", "ACC-HHS-ACF-FOSTER-CARE", "ACC-HHS-CMS-MEDICAID"}
TOTAL_ONLY = {"ACC-HHS-AHRQ-TOTAL", "ACC-HHS-ACL-TOTAL"}
TC_FILE = "scripts/tag_components.py"
CT_FILE = "scripts/check_legislative_text.py"
MR_FILE = "scripts/measure_reconcile.py"

RULES = [
    Rule("LHHS-LAYOUT", "layout", ALL, data=LAYOUT, moved_from="scripts/check_legislative_text.py AGENCY_ALIASES",
         doc="SAMHSA's other name in the FY2023 bills"),
    Rule("LHHS-APPENDIX", "appendix", ALL, data={"chapters": ("HHS",)},
         moved_from="scripts/appendix_attributes.py SUBCOMMITTEE_CHAPTERS", doc="the Budget Appendix chapter (HHS)"),
    Rule("LHHS-241-LIST", "list_item", ALL, fn=list_241, moved_from=f"{TC_FILE} LIST_241, LIST_ITEM, list_241()",
         doc="each numbered item of a 'following amounts shall be available under section 241' list"),
    Rule("LHHS-241-AVAILABLE", "phrase", ALL, fn=available_241,
         moved_from=f"{TC_FILE} TRANSFER's 'available under section 241' alternative (classify)",
         doc="'available under section 241' with no core transfer phrase: a section 241 transfer_in"),
    Rule("LHHS-241-SUBALLOCATION", "phrase", ALL, fn=sub_allocation_241, overrides=("transfer",),
         moved_from=f"{TC_FILE} SUB_ALLOCATION (classify)",
         doc="a part of a section 241 amount is not a transfer of its own"),
    Rule("LHHS-241-EVAL-TAP", "phrase", ALL, fn=evaluation_tap, moved_from=f"{TC_FILE} EVAL_TAP (classify)",
         doc="'is available to this appropriation ... from amounts provided (pursuant to section 241)'"),
    Rule("LHHS-241-SOURCE", "transfer_source", ALL, fn=source_241,
         moved_from=f"{TC_FILE} classify(): the section 241 source", doc="names section 241 as a transfer's source"),
    Rule("LHHS-PPHF-SOURCE", "transfer_source", ALL, fn=source_pphf,
         moved_from=f"{TC_FILE} classify(): the PPHF source", doc="names the PPHF as a transfer's source"),
    Rule("LHHS-241-PAST-WINDOW", "past_window", ALL, fn=past_window_241,
         moved_from=f"{TC_FILE} PAST_WINDOW_241, past_window_241()",
         doc="the D-3b wording read on past the phrase window"),
    Rule("LHHS-CAP-ADJUSTMENT", "phrase", ALL, fn=cap_adjustment, moved_from=f"{TC_FILE} CAP_ADJUSTMENT (classify)",
         doc="HCFAC's section 251(b)(2)(C) additional new budget authority: designated/cap_adjustment"),
    Rule("LHHS-CURES", "phrase", ALL, fn=cures, moved_from=f"{TC_FILE} CURES (classify)",
         doc="NIH Innovation Account (21st Century Cures sec. 1001(b)(4)): designated/other"),
    Rule("LHHS-PRIF", "phrase", ALL, fn=prif, moved_from=f"{TC_FILE} PRIF (classify)",
         doc="the 10-year Pediatric Research Initiative Fund: transfer_in"),
    Rule("LHHS-QUARANTINE", "phrase", ALL, fn=quarantine, overrides=("transfer",),
         moved_from=f"{TC_FILE} QUARANTINE (classify)",
         doc="CDC's 'from amounts made available for Quarantine': availability, not a transfer"),
    *[Rule(f"LHHS-NOT-COMPONENT-{kind.upper().replace(' ', '-')}", "not_component", ALL,
           fn=not_component(kind, rx, where_), moved_from=f"{TC_FILE} NOT_COMPONENT ({kind!r})",
           doc=f"not a component: {kind}") for kind, rx, where_ in NOT_COMPONENT],
    Rule("LHHS-LOOKS-LIKE", "looks_like", ALL, data={"pattern": r"Prevention and Public Health Fund|section 241"},
         moved_from=f"{TC_FILE} LOOKS_LIKE ('section 241', 'Prevention and Public Health Fund')",
         doc="the Labor-HHS words that make an unread phrase a review item"),
    Rule("LHHS-241-NOT-MAIN", "not_main", ALL, fn=not_main_241,
         moved_from=f"{CT_FILE} AFTER_NOT_MAIN (the two section 241 alternatives)",
         doc="an amount printed as from section 241 is not the paragraph's main amount"),
    Rule("LHHS-241-DERIVATION", "derivation", ALL, fn=derivation_241,
         moved_from=f"{CT_FILE} TAP_241 in derivations()", doc="the main amount less its section 241 of-which"),
    Rule("LHHS-241-NO-MAIN", "no_main", Scope(CODE, accounts={"ACC-HHS-OS-ONC"}, fiscal_years=ANY, stages=ANY),
         fn=no_main_241, moved_from=f"{CT_FILE} TAP_241 in compare()",
         doc="ONC: every amount from section 241 and a $0 headline -- no new budget authority, a match"),
    Rule("LHHS-ONC-CELL-NOTE", "cell_note", Scope(CODE, accounts={"ACC-HHS-OS-ONC"}, fiscal_years=ANY, stages=ANY),
         fn=onc_cell_note, moved_from="approps_store.py history(): the PHS evaluation set-aside note",
         doc="ONC's $0 headline: 'Funded through the PHS evaluation set-aside ..., not new budget authority.'"),
    Rule("LHHS-ONC-EVAL-TAP-LINE", "line", Scope(CODE, accounts={"ACC-HHS-OS-ONC"}, fiscal_years=ANY, stages=ANY),
         fn=onc_evaluation_tap, moved_from=f"{TC_FILE} retag(): ONC program_level",
         doc="ONC's 'Evaluation Tap Funding' line (recorded as program_level) -> transfer_in from section 241"),
    Rule("LHHS-VICTF-LINES", "table_line", Scope(CODE, accounts={"ACC-HHS-HRSA-VICTF"}, fiscal_years=ANY, stages=ANY),
         fn=victf_line, moved_from=f"{TC_FILE} table_lines(): the VICTF lines",
         doc="VICTF's 'HRSA Administrative expenses' -> trust_fund_limitation, 'Post-FY 1988 Claims' -> "
             "mandatory_portion"),
    Rule("LHHS-REQUEST-EVALUATION-LINE", "table_line", Scope(CODE, accounts=ANY, fiscal_years=ANY,
                                                             stages={"President's Budget"}),
         fn=request_evaluation_line,
         doc="a request's evaluation funds line in the report's comparative table (request column) -> transfer_in from "
             "section 241, beside the headline (D-9, decision C)"),
    Rule("LHHS-PPHF-TABLE", "components", ALL, fn=pphf_table, moved_from=f"{TC_FILE} pphf_table(), PPHF_TABLE",
         doc="the PPHF allocation (pphf_table.csv): a transfer_in beside each receiving account's headline"),
    Rule("LHHS-TOTAL-ONLY", "extract", Scope(CODE, accounts=TOTAL_ONLY, fiscal_years=ANY, stages=ANY),
         moved_from=f"{TC_FILE} TOTAL_ONLY", doc="AHRQ and ACL, held only as their agency totals (D-1 ruling 2; D-2)"),
    Rule("LHHS-ADVANCES", "advances", Scope(CODE, accounts=ADVANCE_ACCOUNTS, fiscal_years=ANY, stages=ANY),
         data={"first_available_fy": 2018}, moved_from=f"{TC_FILE} ADVANCE_ACCOUNTS",
         doc="Child Support, Foster Care and Medicaid appropriate the next year's first-quarter advance"),
    Rule("LHHS-CR-ANOMALIES", "carry_forward", Scope(CODE, accounts=set(CR_ANOMALIES), **CR_ENACTED),
         fn=cr_anomaly, data={"anomalies": CR_ANOMALIES}, overrides=("cr_earmarks",),
         moved_from=f"{TC_FILE} CR_ANOMALIES", doc="P.L. 119-4 div. A anomalies naming a held Labor-HHS account"),
    Rule("LHHS-CR-PPHF", "carry_forward", Scope(CODE, accounts=ANY, **CR_ENACTED), fn=cr_pphf,
         moved_from=f"{TC_FILE} CR_PPHF", doc="P.L. 119-4 sec. 1906 continues the FY2024 PPHF allocation"),
    Rule("LHHS-CHECKS", "checks", ALL, data=CHECKS,
         moved_from=f"{MR_FILE} KNOWN_CAUSES, KNOWN_CELL_CAUSES, CAUSE_TEXT (Labor-HHS causes), why() onc_no_headline",
         doc="measure_reconcile's Labor-HHS causes"),
    Rule("LHHS-ONC-VIEW-CHECK", "checks", Scope(CODE, accounts={"ACC-HHS-OS-ONC"}, fiscal_years=ANY, stages=ANY),
         fn=onc_view_check, moved_from=f"{MR_FILE} is_evaluation_tap_line() and its check in run()",
         doc="ONC's 'Evaluation Tap Funding' line checked against the cell's section 241 transfers"),
]
