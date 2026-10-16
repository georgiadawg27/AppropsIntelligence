"""
measure_reconcile: each headline against its tagged components, and each printed view total against the view
computed from them (accounting views, phase 1; accounting.py, reference/ACCOUNTING_RULES.md). Offline.

    python scripts/measure_reconcile.py            # the summary and every failure
    python scripts/measure_reconcile.py --write    # write the records (and the statuses they give) into data/staged/
    python scripts/measure_reconcile.py --ci       # fail when data/staged/ is not up to date with this check

1. headline = base + the components the headline includes. Base is what is left, and it must never be negative:
   a negative base is a 'fail' (pending review), on the headline. One record per headline with components,
   VAL-MR-<observation>.
2. A printed parallel total (Component vocabulary, kind 'view') that a computed view stands for (accounting.
   PRINTED_VIEW: appropriated in this bill, available this fiscal year, program level, excluding emergencies)
   must equal the view computed from its headline and the components: exactly, to the dollar. An agency total's
   view is its printed headline plus what the view changes on each of its accounts (approps_store.agency_members:
   the agency's accounts of its title, no parent, not totals). A difference is a 'flag', pending review, on the
   printed view line -- the tagging may be incomplete; the figure is not in question. VAL-MRV-<observation>.
   A view is checked where its components were tagged: the auto-tagged cells (tag_components.coverage(): every
   cell whose bill or law text is committed, and the full-year CR cells), or, outside them, an account's own view
   where its headline carries a component the view uses. Every other printed view is counted as not checked yet, with the reason.
3. A printed 'discretionary' agency or title total (phase 2): the total less what each of its accounts holds
   that is not discretionary by the account's budget_class (account_attributes, from the Budget Appendix): a
   mandatory account's amount as the bill appropriates it, a mixed account's mandatory_portion components
   (discretionary_view). Checked in every cell; a difference is a 'flag', as in 2.

The check never changes a figure. A pass never confirms one (measure_reconcile is not a confirming rule) and a
flag counts neither way (validate_approps.NEUTRAL_FLAG_RULES); a fail flags the headline.
"""

import argparse
import collections
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import accounting as AC  # noqa: E402
import rules_registry as R  # noqa: E402
import tag_components as TC  # noqa: E402
import validate_approps as V  # noqa: E402
import staged_data  # noqa: E402

RULE = "measure_reconcile"
PREFIXES = ("VAL-MR-", "VAL-MRV-", "VAL-MRC-", "VAL-MRE-", "VAL-MRS-")
# D-7: the printed summary lines of a title or bill total (accounts.SUMMARY_COMPONENTS) a computed figure stands
# for: line -> what is summed from the accounts behind the total (summary_view)
SUMMARY_CHECKED = ("mandatory", "emergency_appropriations", "advance_appropriations", "rescissions")
# D-7: the titles whose every account is held, so a printed title (or bill) summary line can be checked against its
# accounts. Labor-HHS Title II reconciles as its agency totals + General Provisions lines in every printed column
# (reference/review/lhhs/title_ii_totals.py); Labor-HHS Titles I, III, IV and every CJS title are held only in part.
TITLES_HELD = {"LHHS": ("Title II",)}
# D-7: why a printed summary line differs, read from the title's own table for the cell (total, fiscal year, stage,
# line) -> cause. Each is reviewed once for every cell it covers; none changes a figure. D-7's three
# (emergency_line_untagged FY2023 and FY2024 Senate, advance_line_untagged FY2025 Senate) are tagged from the bill
# text now (D-7 cleanup: provision_lines.csv; the FY2024 $75 million under ASPR Manufacturing and Production), so
# none is listed; a new difference is 'unexplained' until its cause is read.
SUMMARY_CAUSES = {}
# the earmark rule (owner 2026-10-11): a headline that counts the account's congressionally directed spending /
# community project funding inside, replacing the table's account line
EARMARK_RULE = re.compile(r"Congressionally Directed Spending|Community Project Funding", re.I)

# Why a printed view differs from the computed one (owner, 2026-10-11): one review item per cause, not one per
# cell. account_not_held is no failure: the money sits in an account we don't hold, so the view is not checked.
# Causes found from the data: member_no_headline, cr_anomaly_dropped. Causes known from reading one subcommittee's
# documents live in its rule file (hook 'checks': known_causes, cell_causes, member_cause, cause_text). Anything
# else is unexplained.
CAUSE_TEXT = {
    "member_no_headline": "an account of the agency has no headline in the cell, so its components are missing",
    "cr_anomaly_dropped": "a component a P.L. 119-4 anomaly changed was not carried into FY2025",
    "mandatory_advance_untagged": "a mandatory account's headline is printed as available in this bill, and its "
                                  "advances are not tagged, so the advance the total counts "
                                  "is not taken off",
    "request_stage_class": "an account's request-stage budget_class (what the President's Budget proposed, read "
                           "from the request's own Appendix) differs from how the report's total treats it",
    "headline_superseded": "the printed view counts the report's own headline, which the bill or law text "
                           "superseded with a figure that differs by the rounding of the report's thousands",
    "agency_total_only": "the agency is held only as its total (no account lines), and a total's paragraph is not "
                         "read for components, so the evaluation tap its program level counts is not tagged",
    "emergency_line_untagged": "the title's table prints an '(emergency)' line under an account we hold, and no "
                               "component of that account is tagged designated emergency, so the title's printed "
                               "'Emergency appropriations' line counts money the computed one misses",
    "advance_line_untagged": "the title's table prints a 'New advance' line under an account we hold, and no "
                             "advance_enacted component of that account is tagged, so the title's printed 'Advance "
                             "appropriations' line counts money the computed one misses",
    "unexplained": "not explained yet",
}


def checks(sc):
    """The subcommittee's measure_reconcile data (rule files, hook 'checks')."""
    return R.active().data("checks", sc)


def named_headline(obs, v):
    """The headline a printed view names; one the bill or law text superseded is followed to its replacement (the
    components sit there)."""
    h = obs.get(v.get("headline_observation_id") or "")
    while h is not None and h["verification_status"] == "superseded" and h.get("superseded_by_observation_id"):
        h = obs.get(h["superseded_by_observation_id"])
    return h


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


def not_held(data, a):
    """D-7: why a title or bill total's printed lines can't be checked against its accounts -- None when every
    account of it is held (a title TITLES_HELD lists; a bill whose every printed
    title is held). A total whose accounts aren't all held is 'not checked', never a failure."""
    scope = a.get("total_scope")
    if scope not in ("title", "bill"):
        return None
    held = set(TITLES_HELD.get(a["subcommittee"], ()))
    if scope == "title":
        if (a.get("title") or "") in held:
            return None
        n = len(title_members(data, a)) + sum(1 for x in data["accounts"] if x["subcommittee"] == a["subcommittee"]
                                               and x.get("total_scope") == "agency" and x.get("title") == a.get("title")
                                               and not members(data, x))
        return (f"not checked: accounts not held ({n} of this title's accounts are held; the rest are not)" if n
                else "not checked: accounts not held (no account of this title is held)")
    titles = sorted({x.get("title") or "" for x in data["accounts"] if x["subcommittee"] == a["subcommittee"]
                     and x.get("total_scope") == "title"} - held)
    return ("not checked: accounts not held (titles not held: " + ", ".join(titles) + ")") if titles else None


def summary_view(data, a, v, h, heads, comps, attrs):
    """D-7: the figure a printed summary line of a title total stands for, from the accounts behind it in the cell
    (each one's headline from the total's own document): mandatory = the total less its discretionary view;
    emergency appropriations = the components designated emergency; advance appropriations = the advance_enacted
    components (both FY lines together); rescissions = the rescission lines (amount_type 'rescission').
    -> (computed, how) or the reason it is not checked."""
    line = v["component"]
    fy, stage = int(v["fiscal_year"]), v["stage"]
    if line == "mandatory":
        got = discretionary_view(data, a, v, h, heads, comps, attrs)
        if isinstance(got, str):
            return got
        return h["amount"] - got[0], f"the {a['total_scope']} total {AC.money(h['amount'])} less its discretionary " \
                                      f"view {AC.money(got[0])} ({got[1]})"
    ms = title_members(data, a) if a.get("total_scope") == "title" else [
        x["canonical_account_id"] for x in data["accounts"] if x["subcommittee"] == a["subcommittee"]
        and not x.get("total_scope") and not x.get("parent_account_id")]
    total, used = 0, []
    for m in ms:
        if line == "rescissions":
            for o in data["observations"]:
                if (o["canonical_account_id"] == m and int(o["fiscal_year"]) == fy and o["stage"] == stage
                        and o["amount_type"] == "rescission" and o["verification_status"] != "superseded"
                        and o["source_document_id"] == h["source_document_id"]):
                    total += o["amount"]
                    used.append(f"{o['observation_id']} {AC.money(o['amount'])}")
            continue
        hs = heads[(m, fy, stage)]
        mh = TC.pick_headline(hs, h["source_document_id"]) if hs else None
        for c in (comps[mh["observation_id"]] if mh else []):
            if (line == "emergency_appropriations" and (c["component_type"], c.get("designation")) == ("designated", "emergency")) \
                    or (line.startswith("advance_appropriations") and c["component_type"] == "advance_enacted"):
                total += c["amount"]
                used.append(f"{c['component_id']} {AC.money(c['amount'])}")
    what = {"emergency_appropriations": "the components designated emergency",
            "advance_appropriations": "the advance_enacted components", "rescissions": "the rescission lines"}[line]
    return total, f"{what} of the {a['total_scope']}'s accounts ({'; '.join(used) or 'none'})"


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
        bar = not_held(data, a)
        if bar:
            outcomes.append(dict(base_out, result="not checked", detail=bar))
            continue
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
        hit = R.active().first("checks", f"{v['observation_id']} [{comp}]",
                               TC.ctx_for(a["subcommittee"], a["canonical_account_id"], v["fiscal_year"], v["stage"]),
                               a, v, h, comps)
        if hit:
            # a printed line a rule file checks its own way (Labor-HHS: ONC's 'Evaluation Tap Funding' line against
            # the cell's section 241 transfers, D-1)
            computed, taps, expected = hit[1]
            if not TC.covered(a["canonical_account_id"], *cell) and not taps:
                outcomes.append(dict(base_out, result="not checked",
                                     detail="no component the view uses is tagged for this cell"))
                continue
            ok = computed == v["amount"]
            expected += f" ({describe(taps)}) = {AC.money(computed)}"
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
            if cell not in TC.auto_cells() and v["stage"] != TC.REQUEST_STAGE:
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
    # 4. printed summary lines (D-7): a title or bill total's mandatory, emergency, advance and rescission lines
    # against the figure its accounts give (summary_view); a total whose accounts aren't all held is not checked
    for v in data["observations"]:
        comp = v.get("component") or ""
        if comp not in SUMMARY_CHECKED or v["verification_status"] == "superseded":
            continue
        a = acc[v["canonical_account_id"]]
        base_out = {"kind": "summary", "observation_id": v["observation_id"], "account": a["canonical_account_id"],
                    "fy": v["fiscal_year"], "stage": v["stage"], "view": comp}
        bar = not_held(data, a)
        h = named_headline(obs, v)
        got = bar or ("names no headline" if h is None else summary_view(data, a, v, h, heads, comps, attrs))
        if isinstance(got, str):
            outcomes.append(dict(base_out, result="not checked", detail=got))
            continue
        computed, how = got
        printed = v["amount"]
        if comp == "advance_appropriations":
            # every advance line printed under the total (each fiscal year's, emergency or not) against the advances
            lines_ = [o for o in data["observations"] if o.get("headline_observation_id") == h["observation_id"]
                      and (o.get("component") or "").startswith("advance_appropriations")
                      and o["verification_status"] != "superseded"]
            printed = sum(o["amount"] for o in lines_)
            if len(lines_) > 1:
                how += " against the " + " + ".join(f"{o['component']} {AC.money(o['amount'])}" for o in lines_) \
                       + " printed"
        ok = computed == printed
        cause = None if ok else SUMMARY_CAUSES.get((a["canonical_account_id"], int(v["fiscal_year"]), v["stage"], comp),
                                                    "unexplained")
        expected = f"{comp.replace('_', ' ')}: {how} = {AC.money(computed)}"
        observed = (f"{AC.money(printed)} printed ('{TC.printed(v)}', {v['source_document_id']} "
                    f"p.{v.get('source_page') or '?'})" + (" -- equal" if ok else
                    f" -- differs by {AC.money(printed - computed)} (printed - computed); for review, no figure "
                    f"changed; cause: {cause} (reviewed once for every cell, VAL-MRC-{cause.upper().replace('_', '-')})"))
        records.append({"validation_id": "VAL-MRS-" + suffix(v["observation_id"]), "observation_id": v["observation_id"],
                        "rule_applied": RULE, "expected_result": expected, "observed_result": observed,
                        "result": "pass" if ok else "info", "human_review_status": "", "reviewer": "", "resolution": ""})
        outcomes.append(dict(base_out, result="pass" if ok else "fail", cause=cause,
                             detail=f"computed {AC.money(computed)}, printed {AC.money(printed)}, "
                                    f"difference {AC.money(printed - computed)}"))
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
    for r in R.active().by_hook.get("checks", []):
        if cause in (r.data or {}).get("cause_text", {}):
            return r.data["cause_text"][cause]
    return CAUSE_TEXT[cause]


def why(data, a, v, view, diff, heads, comps):
    """The cause of a printed view's difference (CAUSE_TEXT; the rule files' hook 'checks')."""
    fy, stage = int(v["fiscal_year"]), v["stage"]
    sel = AC.VIEWS[view]
    if a.get("total_scope") == "agency":
        for m in members(data, a):
            if heads[(m, fy, stage)]:
                continue
            uses = any(AC._selected(c, sel["add"]) or AC._selected(c, sel["remove"])
                       for (acc, _, _), hs in heads.items() if acc == m for h in hs for c in comps[h["observation_id"]])
            if uses:
                return checks(a["subcommittee"]).get("member_cause", {}).get(m, "member_no_headline")
        if (a["subcommittee"], fy, stage) in TC.cr_cells():
            dropped = 0
            for m in members(data, a):
                anomaly = TC.cr_anomalies(a["subcommittee"], m, fy).get(m)
                if anomaly is None:
                    continue
                for h in heads[(m, fy - 1, "Enacted")]:
                    for c in comps[h["observation_id"]]:
                        if AC._selected(c, anomaly[1]):
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
    rules = checks(a["subcommittee"])
    cell = rules.get("cell_causes", {}).get((a["canonical_account_id"], fy, stage, view))
    if cell:
        return cell
    return rules.get("known_causes", {}).get((a["canonical_account_id"], view)) or "unexplained"


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
              f"{c[('earmark', 'fail')]} | - |",
              f"| printed summary line = computed (D-7) | {c[('summary', 'pass')]} | {c[('summary', 'fail')]} | "
              f"{c[('summary', 'not checked')]} |", ""]
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
    data = staged_data.load()
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
        staged_data.save(data)
        print("data/staged/ updated")
    if a.ci and stale:
        print("data/staged/ is not up to date with measure_reconcile -- run python scripts/measure_reconcile.py "
              "--write and commit data/staged/")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
