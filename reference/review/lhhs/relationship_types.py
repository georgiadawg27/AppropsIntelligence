"""
Make every Account Relationship's type explicit (owner, 2026-10-09): renamed, merged_into, split_from, moved or
proposed_move. Appends nothing; changes only relationship_type, never an ID, figure or status.

    python reference/review/lhhs/relationship_types.py           # what would change
    python reference/review/lhhs/relationship_types.py --write   # apply

The old types were 'moved_reclassified' and 'consolidated'. A type is mapped only where the relationship's own
evidence decides it:
  moved_reclassified, evidence 'Proposed, not enacted ...'   -> proposed_move
  moved_reclassified, enacted (the law moved it)              -> moved
  consolidated, evidence 'Proposed, not enacted ...'          -> proposed_move (the list's only proposal type;
                                                                 a proposed consolidation is a proposed move)
Anything else with an old type is reported as ambiguous and left unchanged.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGED = ROOT / "data" / "staged.json"
TYPES = ("renamed", "merged_into", "split_from", "moved", "proposed_move")
PROPOSED = "Proposed, not enacted"
# the one enacted move: the law itself moves it (REL-LHHS-0018, the Strategic National Stockpile, P.L. 116-94)
ENACTED_MOVE = "appropriates it under"


def explicit(r):
    """-> (new type, reason) or (None, why ambiguous)."""
    t, ev = r["relationship_type"], r.get("evidence") or ""
    if t in TYPES:
        return t, "already explicit"
    proposed = ev.startswith(PROPOSED) or (ev.startswith("As REL-") and t == "consolidated")
    if t in ("moved_reclassified", "consolidated") and proposed:
        return "proposed_move", f"{t}; its evidence: 'Proposed, not enacted'"
    if t == "moved_reclassified" and ENACTED_MOVE in ev:
        return "moved", f"{t}; its evidence: the law '{ENACTED_MOVE}' the new heading"
    return None, f"{t}: the evidence does not say whether it was proposed or enacted"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    ambiguous = []
    for r in data["relationships"]:
        new, why = explicit(r)
        if new is None:
            ambiguous.append((r["relationship_id"], why))
            continue
        if new != r["relationship_type"]:
            print(f"{r['relationship_id']} {r['from_account_id']} -> {r['to_account_id']}: "
                  f"{r['relationship_type']} -> {new} ({why})")
            r["relationship_type"] = new
    for rid, why in ambiguous:
        print("AMBIGUOUS", rid, why)
    if a.write and not ambiguous:
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))
    return ambiguous


if __name__ == "__main__":
    main()
