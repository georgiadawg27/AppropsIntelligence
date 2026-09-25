"""
accounts.py

Match an OCR-read row label to a canonical account (reference/accounts.json,
built from the Account tab). This is for OCR noise -- "Sci e nee", "STEH
Education", "Hajor Research ..." -- so it matches canonical_name only;
historical_names is reserved for genuine renames and isn't consulted.

Rule (proposal, see ACCEPT_*):
  - Compare letters only, lower-cased: OCR's stray spaces and punctuation
    cost nothing ("Sci e nee" -> "scienee").
  - Candidates are the accounts of the row's agency (its outermost heading,
    itself matched the same way), so NASA's and NSF's "Office of Inspector
    General" can't be confused; a row with no agency heading is matched
    against every account.
  - distance 0 -> "exact".
  - 1 <= distance <= min(2, 15% of the canonical name's length), and the
    runner-up is at least 2 edits further away -> "ocr_corrected". A name
    shorter than 7 letters must match exactly.
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


def allowed_distance(canonical_norm):
    if len(canonical_norm) < 7:
        return 0
    return min(ACCEPT_MAX_DISTANCE, int(ACCEPT_MAX_FRACTION * len(canonical_norm)))


def best_match(text, candidates, name_of):
    """-> (match kind, candidate or None, distance or None)."""
    t = norm(text)
    if not t or not candidates:
        return "unmatched", None, None
    scored = sorted((distance(t, norm(name_of(c))), i, c) for i, c in enumerate(candidates))
    d, _, best = scored[0]
    runner = scored[1][0] if len(scored) > 1 else None
    if d == 0:
        return ("exact", best, 0) if runner != 0 else ("ambiguous", None, 0)
    if d > allowed_distance(norm(name_of(best))):
        return "unmatched", None, d
    if runner is not None and runner < d + AMBIGUITY_MARGIN:
        return "ambiguous", None, d
    return "ocr_corrected", best, d


def agency_accounts(agency_heading, accounts):
    if not agency_heading:
        return accounts
    agencies = sorted({a["agency"] for a in accounts})
    kind, agency, _ = best_match(agency_heading, agencies, lambda x: x)
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
        kind, acct, d = best_match(TOTAL_PREFIX_RE.sub("", text), pool,
                                   lambda a: AGENCY_TOTAL_RE.sub("", a["canonical_name"]))
        if acct is None:
            return None                     # a title or bureau total: not an account
    else:
        if EMERGENCY_RE.search(text):
            component = "emergency"
            text = EMERGENCY_RE.sub("", text)
        pool = [a for a in agency_accounts(agency_heading, accounts) if not AGENCY_TOTAL_RE.search(a["canonical_name"])]
        kind, acct, d = best_match(text, pool, lambda a: a["canonical_name"])
    return {"canonical_account_id": acct["canonical_account_id"] if acct else None,
            "canonical_name": acct["canonical_name"] if acct else None,
            "component": component, "match": kind, "distance": d}


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
                m = dict(parent, component=norm(n.label) or None, match="inherited", distance=None)
        out[n.id] = m
    return out
