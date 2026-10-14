"""
measure_reconcile: each headline against its tagged components, and each printed view total against the view
computed from them (accounting views, phase 1; accounting.py, reference/ACCOUNTING_RULES.md). Offline.

    python scripts/measure_reconcile.py            # the summary and every failure
    python scripts/measure_reconcile.py --write    # write the records (and the statuses they give) into data/staged.json
    python scripts/measure_reconcile.py --ci       # fail when data/staged.json is not up to date with this check

1. headline = base + the components the headline includes. Base is what is left, and it must never be negative:
   a negative base is a 'fail' (pending review), on the headline. One record per headline with components,
   VAL-MR-<observation>.
2. A printed parallel total (Component vocabulary, kind 'view') that a computed view stands for (accounting.
   PRINTED_VIEW: appropriated in this bill, available this fiscal year, program level, excluding emergencies)
   must equal the view computed from its headline and the components: exactly, to the dollar. An agency total's
   view is its printed headline plus what the view changes on each of its accounts (approps_store.agency_members:
   the agency's accounts of its title, no parent, not totals). A difference is a 'flag', pending review, on the
   printed view line -- the tagging may be incomplete; the figure is not in question. VAL-MRV-<observation>.
   A view is checked where its components were tagged: the auto-tagged cells (tag_components.AUTO_CELLS: the pilot,
   Labor-HHS FY2024-FY2026 and CJS FY2026, and the component backfill, both subcommittees FY2017-FY2023; House,
   Senate, Enacted), or, outside them, an account's own view where its headline carries a component the
   view uses. Every other printed view is counted as not checked yet, with the reason.
3. A printed 'discretionary' agency or title total (phase 2): the total less what each of its accounts holds
   that is not discretionary by the account's budget_class (account_attributes, from the Budget Appendix): a
   mandatory account's amount as the bill appropriates it, a mixed account's mandatory_portion components
   (discretionary_view). Checked in every cell; a difference is a 'flag', as in 2.

The check never changes a figure. A pass never confirms one (measure_reconcile is not a confirming rule) and a
flag counts neither way (validate_approps.NEUTRAL_FLAG_RULES); a fail flags the headline.
"""

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import accounting as AC  # noqa: E402
import legislative_text as L  # noqa: E402
import tag_components as TC  # noqa: E402
import validate_approps as V  # noqa: E402

RULE = "measure_reconcile"
PREFIXES = ("VAL-MR-", "VAL-MRV-", "VAL-MRC-", "VAL-MRE-")
# the earmark rule (owner 2026-10-11): a headline that counts the account's congressionally directed spending /
# community project funding inside, replacing the table's account line
EARMARK_RULE = re.compile(r"Congressionally Directed Spending|Community Project Funding", re.I)

# Why a printed view differs from the computed one (owner, 2026-10-11): one review item per cause, not one per
# cell. account_not_held is no failure: the money sits in an account we don't hold, so the view is not checked.
# Causes found from the data: member_no_headline (onc_no_headline for ONC), cr_anomaly_dropped. Causes known
# from reading the documents (KNOWN_CAUSES). Anything else is unexplained.
KNOWN_CAUSES = {
    ("ACC-HHS-CDC-TOTAL", "program_level"): ("pphf_general_provision_table",
        "Prevention and Public Health Fund transfers made through the general provisions' allocation table, which "
        "no phrase rule reads (phase 1b)"),
    ("ACC-HHS-SAMHSA-TOTAL", "program_level"): ("pphf_general_provision_table",
        "Prevention and Public Health Fund transfers made through the general provisions' allocation table, which "
        "no phrase rule reads (phase 1b)"),
    ("ACC-HHS-ACL-TOTAL", "program_level"): ("account_not_held",
        "the program-level money is in an ACL account we don't hold"),
}
# Causes known from reading the documents for one cell (component backfill, FY2017-FY2023): (account, fiscal year,
# stage, view) -> cause. Each is reviewed once for every cell it covers; none changes a figure.
KNOWN_CELL_CAUSES = {
    **{(a, fy, st, "program_level"): "nef_transfer_untagged" for a, fy, st in (
        ("ACC-HHS-CDC-TOTAL", 2018, "Enacted"), ("ACC-HHS-CDC-TOTAL", 2020, "Enacted"),
        ("ACC-HHS-CDC-TOTAL", 2021, "Senate Reported"), ("ACC-HHS-OS-TOTAL", 2021, "Senate Reported"))},
    **{(a, 2017, st, "program_level"): "pandemic_flu_balances" for a in ("ACC-HHS-CDC-TOTAL", "ACC-HHS-OS-TOTAL")
       for st in ("Senate Reported", "Enacted")},
    **{("ACC-HHS-NIH-TOTAL", 2023, st, "program_level"): "prif_outside_printed_program_level"
       for st in ("House Reported", "Senate Reported")},
    ("ACC-HHS-ACF-TOTAL", 2023, "Enacted", "excluding_emergencies"): "emergency_lines_not_held",
    # D-3's request_evaluation_tap_past_window (CDC FY2025 request) closed in D-3b: the section 241 amount is tagged
    # (tag_components.PAST_WINDOW_241)
}
CAUSE_TEXT = {
    "onc_no_headline": "ONC has no headline in the cell, so its section 241 transfer has nothing to sit on (phase 1b)",
    "member_no_headline": "an account of the agency has no headline in the cell, so its components are missing",
    "cr_anomaly_dropped": "a component a P.L. 119-4 anomaly changed was not carried into FY2025",
    "mandatory_advance_untagged": "a mandatory account's headline is printed as available in this bill, and its "
                                  "advances are not tagged, so the advance the total counts "
                                  "is not taken off",
    "request_stage_class": "an account's request-stage budget_class (what the President's Budget proposed, read "
                           "from the request's own Appendix) differs from how the report's total treats it",
    "headline_superseded": "the printed view counts the report's own headline, which the bill or law text "
                           "superseded with a figure that differs by the rounding of the report's thousands",
    "nef_transfer_untagged": "Nonrecurring Expenses Fund money made available to the agency (a general provision, or "
                             "a proviso that names the Fund apart from the amount) is printed as a 'Transfers from "
                             "Nonrecurring Expenses Fund' line in the program level, and no phrase rule tags it",
    "pandemic_flu_balances": "the FY2017 program level counts the 'Pandemic Flu balances (Public Law 111-32)' line, "
                             "balances of another law that no phrase rule tags",
    "evaluation_tap_phrase_untagged": "the paragraph's evaluation tap reads 'is available to this appropriation ... "
                                      "from amounts provided pursuant to section 241', which no phrase rule reads",
    "agency_total_only": "the agency is held only as its total (no account lines), and a total's paragraph is not "
                         "read for components, so the evaluation tap its program level counts is not tagged",
    "prif_outside_printed_program_level": "the bill makes the 10-year Pediatric Research Initiative Fund transfer in "
                                          "addition to the Office of the Director's amount, and the report's program "
                                          "level does not count it",
    "onc_evaluation_tap_line": "the printed line is ONC's 'Evaluation Tap Funding' line ($0 here), not a program-level "
                               "total, so it cannot equal the view where ONC has its own appropriation",
    "emergency_lines_not_held": "the total counts emergency appropriations from the law's other divisions (P.L. "
                                "117-328), which are not lines we hold, so no designated component takes them off",
    "unexplained": "not explained yet",
}


def named_headline(obs, v):
    """The headline a printed view names; one the bill or law text superseded is followed to its replacement (the
    components sit there)."""
    h = obs.get(v.get("headline_observation_id") or "")
    while h is not None and h["verification_status"] == "superseded" and h.get("superseded_by_observation_id"):
        h = obs.get(h["superseded_by_observation_id"])
    return h


def is_evaluation_tap_line(a, v):
    """ONC's 'Evaluation Tap Funding' line, recorded as program_level (the tagger's retag rule reads it as ONC's
    section 241 transfer_in)."""
    return (a["canonical_account_id"] == "ACC-HHS-OS-ONC" and v.get("component") == "program_level"
            and "Evaluation Tap Funding" in TC.printed(v))


def suffix(oid):
    return oid.removeprefix("OBS-")


def members(data, rollup):
    return [a["canonical_account_id"] for a in data["accounts"]
            if a["subcommittee"] == rollup["subcommittee"] and a["agency"] == rollup["agency"]
            and (a.get("title") or "") == (rollup.get("title") or "") and not a.get("total_scope")
            and not a.get("parent_account_id")]


def title_members(data, rollup):
    """A title total's accounts: every account of the subcommittee's title, no parent, not a total."""
    return [a["canonical_account_id"] for a in data["accounts"]
            if a["subcommittee"] == rollup["subcommittee"] and (a.get("title") or "") == (rollup.get("title") or "")
            and not a.get("total_scope") and not a.get("parent_account_id")]


def discretionary_view(data, a, v, h, heads, comps, attrs):
    """The discretionary view of an agency or title total (phase 2): the printed total less, for each of its
    accounts, what is not discretionary by the account's budget_class for the year and stage (accounting.
    discretionary_adjustment: a mandatory account's amount as the bill appropriates it, a mixed account's
    mandatory_portion components). An account with no budget_class on file is counted as printed and named.
    -> (computed, how, the cause to name when it differs) or the reason it is not checked."""
    scope = a.get("total_scope")
    if scope not in ("agency", "title"):
        return f"a {scope or 'non-total'} line: no discretionary view computed"
    fy, stage = int(v["fiscal_year"]), v["stage"]
    acc = {x["canonical_account_id"]: x for x in data["accounts"]}
    delta, mand, mixed, unknown, cause = 0, [], [], [], "unexplained"
    request_used, advance_gap = False, False
    for m in (members(data, a) if scope == "agency" else title_members(data, a)):
        hs = heads[(m, fy, stage)]
        mh = TC.pick_headline(hs, h["source_document_id"]) if hs else None
        if mh is None:
            continue
        rec = AC.attribute_value(attrs, m, fy, "budget_class", stage)
        cs_all = comps[mh["observation_id"]]
        got = AC.discretionary_adjustment(mh["amount"], cs_all, rec["value"] if rec else None)
        if got is None:
            if mh["amount"] or cs_all:
                unknown.append(f"{m} {AC.money(mh['amount'])}")
            continue
        d, cs = got
        delta += d
        request_used |= rec["stage_basis"] == "request"
        if rec["value"] == "mandatory":
            mand.append(f"{acc[m]['canonical_name']} {AC.money(-d)}")
            if (not any(c["component_type"].startswith("advance") for c in cs_all)
                    and "available" in TC.printed(mh).lower()):
                advance_gap = True
        elif rec["value"] == "mixed" and cs:
            mixed.append(f"{acc[m]['canonical_name']} {describe(cs)}")
    if request_used:
        cause = "request_stage_class"
    elif advance_gap:
        cause = "mandatory_advance_untagged"
    how = (f"the {scope} total {AC.money(h['amount'])} ({h['observation_id']}) less its mandatory accounts as the "
           f"bill appropriates them ({'; '.join(mand) or 'none'}) and the mandatory_portion components of its mixed "
           f"accounts ({'; '.join(mixed) or 'none'})"
           + (f"; accounts with no budget_class on file, counted as printed: {'; '.join(unknown)}" if unknown else ""))
    return h["amount"] + delta, how, cause


def describe(cs):
    return "; ".join(f"{c['component_id']} {c['component_type']}{'/' + c['designation'] if c.get('designation') else ''}"
                     f" {AC.money(c['amount'])}" for c in cs) or "none"


def run(data):
    """-> (records, outcomes) -- outcomes: [{kind, observation_id, result, ...}] for the summary."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    kinds = {c["component_id"]: c["kind"] for c in data.get("components", [])}
    comps = AC.components_by_observation(data)
    heads = TC.headline_of(data)
    attrs = AC.attribute_index(data)
    records, outcomes = [], []
    # 1. base
    for oid in sorted(comps):
        h = obs[oid]
        base, inside, beside = AC.split(h["amount"], comps[oid])
        expected = (f"headline {AC.money(h['amount'])} = base + the components it includes ({describe(inside)}); "
                    f"beside it: {describe(beside)}")
        ok = base >= 0
        observed = (f"base {AC.money(base)}" + ("" if ok else " -- negative: the components the headline includes "
                                                           "exceed it; for review, no figure changed"))
        records.append({"validation_id": "VAL-MR-" + suffix(oid), "observation_id": oid, "rule_applied": RULE,
                        "expected_result": expected, "observed_result": observed,
                        "result": "pass" if ok else "fail", "human_review_status": "" if ok else "pending",
                        "reviewer": "", "resolution": ""})
        outcomes.append({"kind": "base", "observation_id": oid, "result": "pass" if ok else "fail",
                         "account": h["canonical_account_id"], "fy": h["fiscal_year"], "stage": h["stage"],
                         "detail": observed})
    # 1b. the earmark rule (D-2 item 5a, permanent): where a headline set by the earmark rule differs from the
    # table's account line it replaced, its earmark components inside must equal the difference. Fails, never adjusts.
    for o in data["observations"]:
        h = obs.get(o.get("superseded_by_observation_id") or "")
        if (o["verification_status"] != "superseded" or h is None or h["verification_status"] == "superseded"
                or not EARMARK_RULE.search(h.get("source_table_or_section") or "")):
            continue
        diff = h["amount"] - o["amount"]
        if diff == 0:
            continue                    # the account line already is the headline (its CDS printed inside it)
        inside = [c for c in comps.get(h["observation_id"], []) if c["component_type"] == "earmark"
                  and c["in_headline"] == "TRUE"]
        got = sum(c["amount"] for c in inside)
        ok = got == diff
        expected = (f"earmark rule: the headline {AC.money(h['amount'])} ({h['observation_id']}) less the table's "
                    f"account line {AC.money(o['amount'])} ({o['observation_id']}) = {AC.money(diff)} = the earmark "
                    f"components inside the headline")
        observed = (f"earmark components inside: {describe(inside)} = {AC.money(got)}"
                    + ("" if ok else f" -- differs by {AC.money(diff - got)}; for review, no figure changed"))
        records.append({"validation_id": "VAL-MRE-" + suffix(h["observation_id"]), "observation_id": h["observation_id"],
                        "rule_applied": RULE, "expected_result": expected, "observed_result": observed,
                        "result": "pass" if ok else "fail", "human_review_status": "" if ok else "pending",
                        "reviewer": "", "resolution": ""})
        outcomes.append({"kind": "earmark", "observation_id": h["observation_id"], "result": "pass" if ok else "fail",
                         "account": h["canonical_account_id"], "fy": h["fiscal_year"], "stage": h["stage"],
                         "detail": observed})
    # 2. printed views
    for v in data["observations"]:
        comp = v.get("component") or ""
        if kinds.get(comp) != "view" or v["verification_status"] == "superseded":
            continue
        a = acc[v["canonical_account_id"]]
        cell = (a["subcommittee"], int(v["fiscal_year"]), v["stage"])
        base_out = {"kind": "view", "observation_id": v["observation_id"], "account": a["canonical_account_id"],
                    "fy": v["fiscal_year"], "stage": v["stage"], "view": comp}
        if comp == "discretionary":
            h = named_headline(obs, v)
            got = discretionary_view(data, a, v, h, heads, comps, attrs) if h else "names no headline"
            if isinstance(got, str):
                outcomes.append(dict(base_out, result="not checked", detail=got))
                continue
            computed, how, cause_hint = got
            ok = computed == v["amount"]
            cause = None if ok else cause_hint
            expected = f"discretionary: {how} = {AC.money(computed)}"
            observed = (f"{AC.money(v['amount'])} printed ('{TC.printed(v)}', {v['source_document_id']} "
                        f"p.{v.get('source_page') or '?'})" + (" -- equal" if ok else
                        f" -- differs by {AC.money(v['amount'] - computed)} (printed - computed); for review, no "
                        "figure changed"))
            if cause:
                observed += f"; cause: {cause} (reviewed once for every cell, VAL-MRC-{cause.upper().replace('_', '-')})"
            records.append({"validation_id": "VAL-MRV-" + suffix(v["observation_id"]),
                            "observation_id": v["observation_id"], "rule_applied": RULE, "expected_result": expected,
                            "observed_result": observed, "result": "pass" if ok else "info",
                            "human_review_status": "", "reviewer": "", "resolution": ""})
            outcomes.append(dict(base_out, result="pass" if ok else "fail", cause=cause,
                                 detail=f"computed {AC.money(computed)}, printed {AC.money(v['amount'])}, "
                                        f"difference {AC.money(v['amount'] - computed)}"))
            continue
        if comp in AC.NOT_CHECKED_YET:
            outcomes.append(dict(base_out, result="not checked", detail=AC.NOT_CHECKED_YET[comp]))
            continue
        if comp not in AC.PRINTED_VIEW:
            outcomes.append(dict(base_out, result="not checked", detail="no computed view stands for this printed scope"))
            continue
        view = AC.PRINTED_VIEW[comp]
        h = named_headline(obs, v)
        if h is None:
            outcomes.append(dict(base_out, result="not checked", detail="names no headline"))
            continue
        if is_evaluation_tap_line(a, v):
            # ONC's 'Evaluation Tap Funding' line is the line the tagger reads as ONC's section 241 transfer_in
            # (retagged), not a program-level total: it is checked against the cell's section 241 transfers (D-1)
            taps = [c for c in comps[h["observation_id"]] if c["component_type"] == "transfer_in"
                    and "241" in (c.get("transfer_from") or "")]
            if not TC.covered(a["canonical_account_id"], *cell) and not taps:
                outcomes.append(dict(base_out, result="not checked",
                                     detail="no component the view uses is tagged for this cell"))
                continue
            computed = sum(c["amount"] for c in taps)
            ok = computed == v["amount"]
            expected = (f"the 'Evaluation Tap Funding' line is ONC's section 241 transfer_in, not a program-level "
                        f"total: the section 241 transfer_in components of the headline {h['observation_id']} "
                        f"({describe(taps)}) = {AC.money(computed)}")
            observed = (f"{AC.money(v['amount'])} printed ('{TC.printed(v)}', {v['source_document_id']} "
                        f"p.{v.get('source_page') or '?'})" + (" -- equal" if ok else
                        f" -- differs by {AC.money(v['amount'] - computed)} (printed - computed); for review, no "
                        "figure changed; cause: unexplained (reviewed once for every cell, VAL-MRC-UNEXPLAINED)"))
            records.append({"validation_id": "VAL-MRV-" + suffix(v["observation_id"]),
                            "observation_id": v["observation_id"], "rule_applied": RULE, "expected_result": expected,
                            "observed_result": observed, "result": "pass" if ok else "info",
                            "human_review_status": "", "reviewer": "", "resolution": ""})
            outcomes.append(dict(base_out, result="pass" if ok else "fail", cause=None if ok else "unexplained",
                                 detail=f"computed {AC.money(computed)}, printed {AC.money(v['amount'])}, "
                                        f"difference {AC.money(v['amount'] - computed)}"))
            continue
        used = []
        if a.get("total_scope") == "agency":
            if cell not in TC.AUTO_CELLS and v["stage"] != TC.REQUEST_STAGE:
                outcomes.append(dict(base_out, result="not checked", detail="an agency total outside the tagged cells"))
                continue
            if v["stage"] == TC.REQUEST_STAGE:
                # the President's Budget (D-2): checked where every account behind the total had its request
                # language read
                ms = [m for m in members(data, a) if heads[(m, int(v["fiscal_year"]), v["stage"])]]
                unread = [m for m in ms if not TC.covered(m, *cell)]
                if not ms and not TC.covered(a["canonical_account_id"], *cell):
                    # an agency held only as its total whose own language was not read (the Administration for a
                    # Healthy America): its components are not known
                    outcomes.append(dict(base_out, result="not checked",
                                         detail="a President's Budget agency total held only as its total, whose "
                                                "request language was not read"))
                    continue
                if unread:
                    outcomes.append(dict(base_out, result="not checked",
                                         detail="a President's Budget agency total with an account whose request "
                                                "language was not found"))
                    continue
            delta = 0
            for m in members(data, a):
                hs = heads[(m, int(v["fiscal_year"]), v["stage"])]
                mh = TC.pick_headline(hs, h["source_document_id"]) if hs else None
                if mh is None:
                    continue
                d, cs = AC.view_adjustment(comps[mh["observation_id"]], view)
                delta += d
                used += cs
            d, cs = AC.view_adjustment(comps[h["observation_id"]], view)
            delta += d
            used += cs
            # a supplemental '(emergency)' line the total's own table prints after an account's total is beside
            # that account's headline but inside the agency total (D-2: ACF FY2023 Enacted's CR-funding lines,
            # S.Rept. 118-84 pp.384-385): a view leaving out emergency designations leaves it out of the total too
            for m in members(data, a):
                hs = heads[(m, int(v["fiscal_year"]), v["stage"])]
                mh = TC.pick_headline(hs, h["source_document_id"]) if hs else None
                for c in (comps[mh["observation_id"]] if mh else []):
                    if (c["in_headline"] != "TRUE" and c["tag_method"] == "retagged"
                            and c["source_document_id"] == h["source_document_id"]
                            and AC._selected(c, AC.VIEWS[view]["remove"])
                            and (c["component_type"], c.get("designation")) == ("designated", "emergency")):
                        delta -= c["amount"]
                        used.append(c)
            computed = h["amount"] + delta
            how = f"the agency total {AC.money(h['amount'])} ({h['observation_id']}) + its accounts' components"
        elif a.get("total_scope"):
            outcomes.append(dict(base_out, result="not checked", detail=f"a {a['total_scope']} total"))
            continue
        else:
            sel = AC.VIEWS[view]
            relevant = [c for c in comps[h["observation_id"]]
                        if AC._selected(c, sel["remove"]) or AC._selected(c, sel["add"])]
            if not TC.covered(a["canonical_account_id"], *cell) and not relevant:
                outcomes.append(dict(base_out, result="not checked",
                                     detail="no component the view uses is tagged for this cell"))
                continue
            delta, used = AC.view_adjustment(comps[h["observation_id"]], view)
            computed = h["amount"] + delta
            how = f"the headline {AC.money(h['amount'])} ({h['observation_id']})"
        ok = computed == v["amount"]
        cause = None if ok else why(data, a, v, view, v["amount"] - computed, heads, comps)
        if cause == "account_not_held":
            outcomes.append(dict(base_out, result="not checked", detail="the money is in an account we don't hold "
                                 "(account_not_held)"))
            continue
        expected = (f"{AC.VIEWS[view]['label']}: {how}, less the included components the view leaves out, plus the "
                    f"components beside it the view counts ({describe(used)}) = {AC.money(computed)}")
        observed = (f"{AC.money(v['amount'])} printed ('{TC.printed(v)}', {v['source_document_id']} "
                    f"p.{v.get('source_page') or '?'})" + (" -- equal" if ok else
                    f" -- differs by {AC.money(v['amount'] - computed)} (printed - computed); for review, no figure "
                    "changed"))
        if cause:
            observed += f"; cause: {cause} (reviewed once for every cell, VAL-MRC-{cause.upper().replace('_', '-')})"
        records.append({"validation_id": "VAL-MRV-" + suffix(v["observation_id"]), "observation_id": v["observation_id"],
                        "rule_applied": RULE, "expected_result": expected, "observed_result": observed,
                        "result": "pass" if ok else "info", "human_review_status": "",
                        "reviewer": "", "resolution": ""})
        outcomes.append(dict(base_out, result="pass" if ok else "fail", cause=cause,
                             detail=f"computed {AC.money(computed)}, printed {AC.money(v['amount'])}, "
                                    f"difference {AC.money(v['amount'] - computed)}"))
    # one review item per cause, on the first cell it covers
    by_cause = collections.defaultdict(list)
    for o in outcomes:
        if o.get("cause"):
            by_cause[o["cause"]].append(o)
    for cause, os_ in sorted(by_cause.items()):
        os_ = sorted(os_, key=lambda o: o["observation_id"])
        records.append({"validation_id": "VAL-MRC-" + cause.upper().replace("_", "-"),
                        "observation_id": os_[0]["observation_id"], "rule_applied": RULE,
                        "expected_result": f"printed view totals equal the views computed from the components; cause "
                                           f"{cause}: {cause_text(cause)}",
                        "observed_result": f"{len(os_)} printed view(s) differ: " + "; ".join(
                            f"{o['observation_id']} {o['account']} FY{o['fy']} {o['stage']} [{o['view']}] "
                            f"{o['detail'].rsplit('difference ', 1)[1]}" for o in os_)
                            + " -- for review, no figure changed",
                        "result": "flag", "human_review_status": "pending", "reviewer": "", "resolution": ""})
    return records, outcomes


def cause_text(cause):
    for k, (c, t) in KNOWN_CAUSES.items():
        if c == cause:
            return t
    return CAUSE_TEXT[cause]


def why(data, a, v, view, diff, heads, comps):
    """The cause of a printed view's difference (KNOWN_CAUSES, CAUSE_TEXT)."""
    fy, stage = int(v["fiscal_year"]), v["stage"]
    sel = AC.VIEWS[view]
    if a.get("total_scope") == "agency":
        for m in members(data, a):
            if heads[(m, fy, stage)]:
                continue
            uses = any(AC._selected(c, sel["add"]) or AC._selected(c, sel["remove"])
                       for (acc, _, _), hs in heads.items() if acc == m for h in hs for c in comps[h["observation_id"]])
            if uses:
                return "onc_no_headline" if m == "ACC-HHS-OS-ONC" else "member_no_headline"
        if (a["subcommittee"], fy, stage) in TC.CR_CELLS:
            dropped = 0
            for m in members(data, a):
                if m not in TC.CR_ANOMALIES:
                    continue
                for h in heads[(m, fy - 1, "Enacted")]:
                    for c in comps[h["observation_id"]]:
                        if AC._selected(c, TC.CR_ANOMALIES[m][1]):
                            if c["in_headline"] != "TRUE" and AC._selected(c, sel["add"]):
                                dropped += c["amount"]
                            if c["in_headline"] == "TRUE" and AC._selected(c, sel["remove"]):
                                dropped -= c["amount"]
            if dropped and dropped == diff:
                return "cr_anomaly_dropped"
    named = obs_of(data).get(v.get("headline_observation_id") or "")
    h = named_headline(obs_of(data), v)
    if named is not None and h is not named and diff == named["amount"] - h["amount"]:
        return "headline_superseded"
    cell = KNOWN_CELL_CAUSES.get((a["canonical_account_id"], fy, stage, view))
    if cell:
        return cell
    known = KNOWN_CAUSES.get((a["canonical_account_id"], view))
    return known[0] if known else "unexplained"


_OBS = {}


def obs_of(data):
    if id(data) not in _OBS:
        _OBS.clear()
        _OBS[id(data)] = {o["observation_id"]: o for o in data["observations"]}
    return _OBS[id(data)]


MACHINE = ("observation_id", "rule_applied", "expected_result", "observed_result", "result")


def merged(data, records):
    """data's validations with this check's records brought up to date (a person's resolution stays while the
    comparison is unchanged); records this check no longer gives are dropped."""
    want = {r["validation_id"]: r for r in records}
    out, seen = [], set()
    for v in data["validations"]:
        vid = v["validation_id"]
        if vid.startswith(PREFIXES):
            if vid in want:
                seen.add(vid)
                w = want[vid]
                out.append(v if all(v.get(k) == w[k] for k in MACHINE) else w)
            continue
        out.append(v)
    out += [want[k] for k in sorted(want) if k not in seen]
    return out


def statuses(data, validations):
    checks = collections.defaultdict(list)
    for v in validations:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v.get("expected_result"),
                                           v.get("human_review_status"), v.get("resolution")))
    out = {}
    for o in data["observations"]:
        if o["verification_status"] in ("human-verified", "provisional", "superseded"):
            out[o["observation_id"]] = o["verification_status"]
        else:
            out[o["observation_id"]] = V.observation_status(o, checks[o["observation_id"]])
    return out


def summary(outcomes):
    lines = ["## measure_reconcile", ""]
    c = collections.Counter((o["kind"], o["result"]) for o in outcomes)
    lines += ["| check | pass | fail | not checked |", "|---|---|---|---|",
              f"| headline = base + included components (base >= 0) | {c[('base', 'pass')]} | {c[('base', 'fail')]} | - |",
              f"| printed view = computed view | {c[('view', 'pass')]} | {c[('view', 'fail')]} | "
              f"{c[('view', 'not checked')]} |",
              f"| earmark rule: headline - account line = earmark components | {c[('earmark', 'pass')]} | "
              f"{c[('earmark', 'fail')]} | - |", ""]
    nc = collections.Counter(o["detail"] for o in outcomes if o["result"] == "not checked")
    if nc:
        lines += ["Not checked yet: " + "; ".join(f"{k} ({n})" for k, n in nc.most_common()), ""]
    fails = [o for o in outcomes if o["result"] == "fail"]
    if fails:
        lines += ["**Failures (pending review: one item per cause)**", ""]
        groups = collections.defaultdict(list)
        for o in fails:
            groups[o.get("cause") or ("earmark_rule" if o["kind"] == "earmark" else "base")].append(o)
        for cause, os_ in sorted(groups.items()):
            lines.append(f"- **{cause}** ({len(os_)}): " + ("" if cause in ("base", "earmark_rule") else cause_text(cause)))
            lines += [f"  - {o['observation_id']} {o['account']} FY{o['fy']} {o['stage']}"
                      + (f" [{o['view']}]" if o.get("view") else " [base]") + f": {o['detail']}" for o in os_]
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ci", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(L.STAGED.read_text())
    records, outcomes = run(data)
    new_vals = merged(data, records)
    want = statuses(data, new_vals)
    moved = collections.Counter((o["verification_status"], want[o["observation_id"]]) for o in data["observations"]
                                if o["verification_status"] != want[o["observation_id"]])
    stale = new_vals != data["validations"] or bool(moved)
    text = summary(outcomes)
    if moved:
        text += "\nStatus changes: " + ", ".join(f"{x} -> {y}: {n}" for (x, y), n in sorted(moved.items())) + "\n"
    print(text)
    if a.ci and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    if a.write and stale:
        data["validations"] = new_vals
        for o in data["observations"]:
            o["verification_status"] = want[o["observation_id"]]
        L.STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")
    if a.ci and stale:
        print("data/staged.json is not up to date with measure_reconcile -- run python scripts/measure_reconcile.py "
              "--write and commit data/staged.json")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
