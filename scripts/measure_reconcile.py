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
   A view is checked where its components were tagged: the pilot cells (Labor-HHS FY2024-FY2026 and CJS FY2026;
   House, Senate, Enacted), or, outside them, an account's own view where its headline carries a component the
   view uses. Every other printed view is counted as not checked yet, with the reason ('discretionary' needs
   every account's budget_class -- phase 2).

The check never changes a figure. A pass never confirms one (measure_reconcile is not a confirming rule) and a
flag counts neither way (validate_approps.NEUTRAL_FLAG_RULES); a fail flags the headline.
"""

import argparse
import collections
import json
import os
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
PREFIXES = ("VAL-MR-", "VAL-MRV-")


def suffix(oid):
    return oid.removeprefix("OBS-")


def members(data, rollup):
    return [a["canonical_account_id"] for a in data["accounts"]
            if a["subcommittee"] == rollup["subcommittee"] and a["agency"] == rollup["agency"]
            and (a.get("title") or "") == (rollup.get("title") or "") and not a.get("total_scope")
            and not a.get("parent_account_id")]


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
    # 2. printed views
    for v in data["observations"]:
        comp = v.get("component") or ""
        if kinds.get(comp) != "view" or v["verification_status"] == "superseded":
            continue
        a = acc[v["canonical_account_id"]]
        cell = (a["subcommittee"], int(v["fiscal_year"]), v["stage"])
        base_out = {"kind": "view", "observation_id": v["observation_id"], "account": a["canonical_account_id"],
                    "fy": v["fiscal_year"], "stage": v["stage"], "view": comp}
        if comp in AC.NOT_CHECKED_YET:
            outcomes.append(dict(base_out, result="not checked", detail=AC.NOT_CHECKED_YET[comp]))
            continue
        if comp not in AC.PRINTED_VIEW:
            outcomes.append(dict(base_out, result="not checked", detail="no computed view stands for this printed scope"))
            continue
        view = AC.PRINTED_VIEW[comp]
        h = obs.get(v.get("headline_observation_id") or "")
        if h is None:
            outcomes.append(dict(base_out, result="not checked", detail="names no headline"))
            continue
        used = []
        if a.get("total_scope") == "agency":
            if cell not in TC.PILOT:
                outcomes.append(dict(base_out, result="not checked", detail="an agency total outside the pilot cells"))
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
            computed = h["amount"] + delta
            how = f"the agency total {AC.money(h['amount'])} ({h['observation_id']}) + its accounts' components"
        elif a.get("total_scope"):
            outcomes.append(dict(base_out, result="not checked", detail=f"a {a['total_scope']} total"))
            continue
        else:
            sel = AC.VIEWS[view]
            relevant = [c for c in comps[h["observation_id"]]
                        if AC._selected(c, sel["remove"]) or AC._selected(c, sel["add"])]
            if cell not in TC.PILOT and not relevant:
                outcomes.append(dict(base_out, result="not checked",
                                     detail="no component the view uses is tagged for this cell"))
                continue
            delta, used = AC.view_adjustment(comps[h["observation_id"]], view)
            computed = h["amount"] + delta
            how = f"the headline {AC.money(h['amount'])} ({h['observation_id']})"
        ok = computed == v["amount"]
        expected = (f"{AC.VIEWS[view]['label']}: {how}, less the included components the view leaves out, plus the "
                    f"components beside it the view counts ({describe(used)}) = {AC.money(computed)}")
        observed = (f"{AC.money(v['amount'])} printed ('{TC.printed(v)}', {v['source_document_id']} "
                    f"p.{v.get('source_page') or '?'})" + (" -- equal" if ok else
                    f" -- differs by {AC.money(v['amount'] - computed)} (printed - computed); for review, no figure "
                    "changed"))
        records.append({"validation_id": "VAL-MRV-" + suffix(v["observation_id"]), "observation_id": v["observation_id"],
                        "rule_applied": RULE, "expected_result": expected, "observed_result": observed,
                        "result": "pass" if ok else "flag", "human_review_status": "" if ok else "pending",
                        "reviewer": "", "resolution": ""})
        outcomes.append(dict(base_out, result="pass" if ok else "fail", detail=f"computed {AC.money(computed)}, "
                             f"printed {AC.money(v['amount'])}, difference {AC.money(v['amount'] - computed)}"))
    return records, outcomes


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
              f"{c[('view', 'not checked')]} |", ""]
    nc = collections.Counter(o["detail"] for o in outcomes if o["result"] == "not checked")
    if nc:
        lines += ["Not checked yet: " + "; ".join(f"{k} ({n})" for k, n in nc.most_common()), ""]
    fails = [o for o in outcomes if o["result"] == "fail"]
    if fails:
        lines += ["**Failures (pending review)**", ""]
        lines += [f"- {o['observation_id']} {o['account']} FY{o['fy']} {o['stage']}"
                  + (f" [{o['view']}]" if o.get("view") else " [base]") + f": {o['detail']}" for o in fails]
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
