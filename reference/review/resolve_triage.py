"""
Apply the owner's triage decisions on reference/review/triage_proposal.csv (PR #29) to data/staged.json,
then recompute every verification_status by the standard rule (which no longer counts a resolved record).

    python reference/review/resolve_triage.py           # what would change
    python reference/review/resolve_triage.py --write   # apply

Decisions (2026-10-08):
  - every A group, as proposed: the row's reason goes into `resolution`
  - the 12 FY2025 Enacted estimate records (family cr-estimate): resolved with the owner's reason
  - everything else stays pending (the Title II scope totals, the FY2026 Senate sums, Global Health,
    and the B records the decisions didn't name)
Each record: human_review_status = resolved, reviewer = REVIEWER. No ID is added, removed or renumbered;
no amount changes. Observations set by their own steps (human-verified, provisional, superseded) keep
their status.
"""

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
PROPOSAL = HERE / "triage_proposal.csv"
REVIEWER = "owner (group approval 2026-10-08)"
CR_REASON = ("Owner decision: FY2025 was funded by a full-year CR (P.L. 119-4) with no dollar level; the enacted figure "
             "is the FY2026 House report's FY2025 estimate, as decided earlier. The FY2025 (CR) column label discloses this.")
KEEP_STATUS = ("human-verified", "provisional", "superseded")


def decisions():
    """validation_id -> resolution text, from the proposal."""
    with open(PROPOSAL, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["id"].startswith("VAL-")]
    out = {}
    for r in rows:
        if r["proposed_disposition"] == "A":
            out[r["id"]] = r["reason"]
        elif r["family"] == "cr-estimate":
            out[r["id"]] = CR_REASON
    return out


def checks(vals):
    return [(v["rule_applied"], v["result"], v["expected_result"], v["human_review_status"], v["resolution"]) for v in vals]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    resolve = decisions()
    by_id = {v["validation_id"]: v for v in data["validations"]}
    for vid in resolve:
        v = by_id[vid]
        assert v["human_review_status"] == "pending", (vid, v["human_review_status"])
        v.update({"human_review_status": "resolved", "reviewer": REVIEWER, "resolution": resolve[vid]})
    by_obs = collections.defaultdict(list)
    for v in data["validations"]:
        by_obs[v["observation_id"]].append(v)
    sc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    changed = collections.Counter()
    for o in data["observations"]:
        if o["verification_status"] in KEEP_STATUS:
            continue
        new = V.verification_status(o["confidence"], checks(by_obs[o["observation_id"]]))
        if new != o["verification_status"]:
            changed[(sc[o["canonical_account_id"]], o["verification_status"], new)] += 1
            o["verification_status"] = new
    print(f"{len(resolve)} records resolved; "
          f"{sum(v['human_review_status'] == 'pending' for v in data['validations'])} still pending")
    for (s, old, new), n in sorted(changed.items()):
        print(f"  {s}: {old} -> {new}: {n}")
    if a.write:
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
