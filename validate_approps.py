"""
validate_approps.py

The Step 3 validation checks that need nothing but the extracted document
itself (scoping doc, "The eight validation checks"). Run before anything is
compared across documents: a comparative table's own subtotals, totals and
delta column are enough to catch most misreads.

Implemented here:
  table_total  -- a "Total, <agency/title>" row equals the sum of its children
  structural   -- a Subtotal (or unlabeled ruled row, e.g. "Direct
                  appropriation") equals the lines it rolls up; each row's
                  delta column equals the difference of its two value columns;
                  a total's memo breakdown ("Appropriations", "Rescissions")
                  reconciles to the total
  source_text  -- the amount as printed appears in the row's raw text
  unit         -- the amount's unit matches the unit its own page declares
  semantic     -- non-budget-authority lines (transfers, rescissions,
                  offsetting collections) are always flagged for a human

Not run here (they need data outside this document): cross_document,
historical, account_identity.

All arithmetic is exact integer arithmetic in the document's own unit, so a
table stated in thousands is checked in thousands -- no rounding tolerance.
"""

import re
import uuid
from collections import Counter, defaultdict

AUTO_PUBLISH_CONFIDENCE = 0.90
FAILED_CONFIDENCE = 0.50
SEMANTIC_KEYWORDS = ("rescission", "chimp", "emergency", "advance appropriation", "transfer",
                     "offsetting", "fee collection", "cancellation")

NOT_RUN = {
    "cross_document": "needs the bill text (BILLS-119hr8845rh) extracted too",
    "historical": "needs prior-year observations for the same canonical accounts",
    "account_identity": "needs the Account table / historical_names (reconciliation pass)",
}


def _cell_value(node, col_index):
    if col_index is None or col_index >= len(node.cells):
        return None
    c = node.cells[col_index]
    if c["kind"] == "blank":
        return 0
    return c["value"]


def _fmt(v):
    return "None" if v is None else f"{v:,}"


def validate(nodes, cols, observations, page_meta, unit):
    obs_by = {(o["node_id"], o["column_index"]): o for o in observations}
    records = []
    results_by_obs = defaultdict(list)
    arithmetic_pass = set()         # observation ids confirmed by some sum
    implicated = set()              # observation ids feeding a failed sum
    rollup_lines = []

    def record(obs, rule, expected, observed, result):
        rec = {
            "validation_id": str(uuid.uuid4()),
            "observation_id": obs["observation_id"],
            "rule_applied": rule,
            "expected_result": expected,
            "observed_result": observed,
            "result": result,
            "human_review_status": "pending" if result in ("fail", "flag") else None,
            "reviewer": None,
            "resolution": None,
        }
        records.append(rec)
        results_by_obs[obs["observation_id"]].append(result)
        return rec

    value_cols = [c for c in cols if c["kind"] == "value"]
    delta_cols = [c for c in cols if c["kind"] == "delta"]

    # --- rollups: table_total / structural -------------------------------
    for node in nodes:
        if node.kind not in ("subtotal", "total", "grand_total"):
            continue
        rule = "structural" if node.kind == "subtotal" else "table_total"
        for col in value_cols + delta_cols:
            stated = _cell_value(node, col["index"])
            if stated is None:
                continue
            kids = [_cell_value(ch, col["index"]) for ch in node.children]
            computed = None if any(k is None for k in kids) else sum(kids)
            if not node.children:
                result, note = "flag", "no children found"
            elif not node.complete:
                result, note = "flag", f"children not fully extracted ({node.match})"
            elif computed is None:
                result, note = "fail", "a child value could not be parsed"
            else:
                result, note = ("pass" if computed == stated else "fail"), ""
            tag = {"pass": "PASS", "fail": "FAIL", "flag": "FLAG"}[result]
            rollup_lines.append(
                f"{tag} p{node.page} {node.label!r} [{col['header']}]: stated {_fmt(stated)}, "
                f"sum of {len(node.children)} children {_fmt(computed)}"
                + (f" -- {note}" if note else ""))
            if col["kind"] != "value":
                continue
            obs = obs_by.get((node.id, col["index"]))
            if obs is None:
                continue
            child_labels = "; ".join(ch.label for ch in node.children)
            record(obs, rule,
                   f"{_fmt(computed)} = sum of [{child_labels}] ({unit})",
                   f"{_fmt(stated)} ({unit}) as printed" + (f"; {note}" if note else ""),
                   result)
            child_obs = [obs_by.get((ch.id, col["index"])) for ch in node.children]
            if result == "pass":
                arithmetic_pass.add(obs["observation_id"])
                arithmetic_pass.update(o["observation_id"] for o in child_obs if o)
            elif result == "fail":
                implicated.update(o["observation_id"] for o in child_obs if o)

        # memo breakdown, e.g. Total -> (Appropriations) (Rescissions)
        memos = [m for m in node.memos if "transfer" not in m.label.lower()]
        if memos:
            for col in value_cols:
                stated = _cell_value(node, col["index"])
                obs = obs_by.get((node.id, col["index"]))
                vals = [_cell_value(m, col["index"]) for m in memos]
                if stated is None or obs is None or any(v is None for v in vals):
                    continue
                ok = sum(vals) == stated
                record(obs, "structural",
                       f"memo breakdown [{'; '.join(m.label for m in memos)}] sums to {_fmt(sum(vals))}",
                       f"{_fmt(stated)} as printed",
                       "pass" if ok else "flag")

    # --- delta column: Bill - Enacted == "Bill vs. Enacted" ---------------
    for node in nodes:
        if node.kind in ("heading", "title_heading"):
            continue
        for d in delta_cols:
            mi, si = d.get("minuend_index"), d.get("subtrahend_index")
            if mi is None or si is None:
                continue
            m, s, dv = (_cell_value(node, mi), _cell_value(node, si), _cell_value(node, d["index"]))
            if node.cells[d["index"]]["kind"] == "blank" or m is None or s is None or dv is None:
                continue
            ok = (m - s) == dv
            for idx in (mi, si):
                obs = obs_by.get((node.id, idx))
                if obs is None:
                    continue
                record(obs, "structural",
                       f"[{d['header']}] = {_fmt(m)} - {_fmt(s)} = {_fmt(m - s)}",
                       f"{_fmt(dv)} as printed in [{d['header']}]",
                       "pass" if ok else "fail")
                if ok:
                    arithmetic_pass.add(obs["observation_id"])
                else:
                    implicated.add(obs["observation_id"])

    # --- per-observation checks ------------------------------------------
    for o in observations:
        raw = re.sub(r"\s+", " ", o["raw_text_excerpt"] or "")
        printed = o["amount_as_printed"]
        label_words = [w for w in re.findall(r"[A-Za-z]{3,}", o["account_name_as_written"])]
        amount_ok = o["amount"] is not None and printed and printed in raw
        label_ok = not label_words or label_words[0].lower() in raw.lower()
        record(o, "source_text",
               f"amount {printed!r} and label {o['account_name_as_written']!r} in the transcribed row",
               raw or "(no raw text)",
               "pass" if amount_ok and label_ok else "fail")

        declared = page_meta[int(o["source_page"])]["units_declared"]
        page_unit = page_meta[int(o["source_page"])]["units_parsed"]
        record(o, "unit", f"{o['amount_unit']} (applied, x{_mult(o['amount_unit']):,} to dollars)",
               f"page {o['source_page']} declares {declared!r}",
               "pass" if page_unit == o["amount_unit"] else "fail")

        low = o["account_name_as_written"].lower()
        if o["amount_type"] != "budget authority" or any(k in low for k in SEMANTIC_KEYWORDS):
            record(o, "semantic", f"amount_type fits the row label ({o['amount_type']})",
                   f"label {o['account_name_as_written']!r}; memo={o['is_memo']}", "flag")

    # --- confidence and verification_status -------------------------------
    for o in observations:
        res = results_by_obs[o["observation_id"]]
        oid = o["observation_id"]
        if "fail" in res or oid in implicated:
            o["extraction_confidence"] = min(o["extraction_confidence"], FAILED_CONFIDENCE)
            o["verification_status"] = "flagged"
        elif "flag" in res or oid not in arithmetic_pass:
            o["verification_status"] = "unverified"
        elif o["extraction_confidence"] >= AUTO_PUBLISH_CONFIDENCE:
            o["verification_status"] = "auto-validated"
        else:
            o["verification_status"] = "unverified"

    by_rule = defaultdict(Counter)
    for r in records:
        by_rule[r["rule_applied"]][r["result"]] += 1
    summary = {
        "by_rule": {k: dict(v) for k, v in by_rule.items()},
        "not_run": NOT_RUN,
        "rollup_lines": rollup_lines,
        "failures": sum(1 for r in records if r["result"] == "fail"),
        "verification_status_counts": dict(Counter(o["verification_status"] for o in observations)),
    }
    return records, summary


def _mult(unit):
    return {"dollars": 1, "thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}[unit]
