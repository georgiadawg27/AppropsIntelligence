"""
Headlines derived from printed lines (owner's rule, 2026-10-08). Where a table prints an account's lines but not its
headline, the headline is derived only where the later years' tables define it exactly; never a confirmed absence on
an account the document funds. Where it can't be derived exactly, the build stops and says so (the owner decides).

    Grants to States for Medicaid: headline = 'appropriated in this bill' - 'new advance, 1st quarter, FY<N+1>'
        (holds on every FY2024-FY2027 cell that prints all three: check_rule)
    CDC-Wide Activities and Program Support: headline = the sum of the budget-authority lines printed under the
        heading (the later tables' 'Subtotal, CDC-Wide Activities'; a Prevention and Public Health Fund line is a
        transfer memo, not in it -- check_sum_rule)

A derived observation has extraction_method 'derived', the document and page(s) of its two lines, a note naming
them, and a structural (arithmetic) validation record.
"""

RULES = {
    # account: (minuend key, subtrahend key) as (amount_type, component)
    "ACC-HHS-CMS-MEDICAID": (("budget authority", "appropriated_in_this_bill"), ("advance", "")),
}
FIRST_PRINTED_FY = {"ACC-HHS-CMS-MEDICAID": 2024}    # the years whose tables print the headline the rule is checked on


def check_rule(observations, aid):
    """Every stage cell from FIRST_PRINTED_FY on that prints the headline and both lines: headline = a - b.
    Returns (cells checked, failures)."""
    (ta, ca), (tb, cb) = RULES[aid]
    cells = {}
    for o in observations:
        if o["canonical_account_id"] == aid and o["fiscal_year"] >= FIRST_PRINTED_FY[aid] \
                and o.get("verification_status") != "superseded" and o.get("extraction_method") != "derived":
            cells.setdefault((o["fiscal_year"], o["stage"]), {})[(o["amount_type"], o["component"] or "")] = o["amount"]
    checked, bad = 0, []
    for k, v in sorted(cells.items()):
        h, a, b = v.get(("budget authority", "")), v.get((ta, ca)), v.get((tb, cb))
        if None in (h, a, b):
            continue
        checked += 1
        if a - b != h:
            bad.append((k, h, a, b))
    return checked, bad


def derive(aid, lines, label_of, page_of):
    """lines: {(amount_type, component): amount}. Returns (amount, note, arithmetic text) or None."""
    if aid not in RULES:
        return None
    ka, kb = RULES[aid]
    if ka not in lines or kb not in lines:
        return None
    a, b = lines[ka], lines[kb]
    amount = a - b
    note = (f"derived, not a printed line: '{label_of(ka)}' {a // 1000:,} (p.{page_of(ka)}) - '{label_of(kb)}' "
            f"{b // 1000:,} (p.{page_of(kb)}) = {amount // 1000:,} (thousands); the FY{FIRST_PRINTED_FY[aid]}-FY2027 "
            f"tables print the headline as exactly this difference")
    arith = (f"headline = appropriated in this bill - the new advance for the next fiscal year: {a // 1000:,} - "
             f"{b // 1000:,} = {amount // 1000:,} (thousands)", f"{amount // 1000:,} as recorded")
    return amount, note, arith


# headline = the sum of the lines printed under a heading: account -> (agency section, heading in the row's path,
# the later tables' subtotal label)
SUM_RULES = {
    "ACC-HHS-CDC-PROGRAM-SUPPORT": ("CDC", "CDC-Wide Activities and Program Support", r"subtotal, cdc.wide activities$"),
}


def lines_under(rows, heading):
    """The budget-authority lines printed under the heading: from its first row to the next subtotal, not memo
    lines (a row's printed path can lose the heading, so position decides, not the path)."""
    norm = lambda t: t.replace("\u2013", "-").lower()
    start = next((i for i, f in enumerate(rows) if norm(heading) in norm(f["account_path"])), None)
    if start is None:
        return []
    out = []
    for f in rows[start:]:
        label = " ".join(norm(f["account_name_as_written"]).split()).rstrip(" .")
        if f["is_rollup"] or label.startswith(("subtotal", "total")):
            # a nested subtotal ('Subtotal, Public Health Infrastructure and Capacity') stays inside the heading;
            # the heading's own subtotal (unlabeled, or 'Subtotal, CDC-Wide Activities') or a total ends it
            if label in ("subtotal", "subtotal (including transfers)") or label.startswith("total") \
                    or norm(heading).split(" and ")[0].lower() in label:
                break
            continue
        if not f.get("is_memo"):
            out.append(f)
    return out


def check_sum_rule(columns, aid):
    """columns: [(name, rows of the agency section)] from the later tables. Where the subtotal is printed, the lines
    printed under the heading before it (lines_under) sum to it exactly. Returns (checked, bad)."""
    import re
    _, _, label = SUM_RULES[aid]
    checked, bad = 0, []
    for name, rows in columns:
        for i, f in enumerate(rows):
            if not re.match(label, " ".join(f["account_name_as_written"].lower().split()).replace("\u2013", "-")):
                continue
            parts = lines_under(rows[:i], SUM_RULES[aid][1])
            pphf = [g for g in rows[max(0, i - 3):i] if g.get("is_memo")
                    and "prevention and public health fund" in g["account_name_as_written"].lower()]
            if pphf and sum(g["amount"] or 0 for g in parts + pphf) == (f["amount"] or 0):
                continue    # a subtotal printed with the PPHF transfer in it (S.Rept. 115-289): another scope, not checked
            checked += 1
            if sum(g["amount"] or 0 for g in parts) != (f["amount"] or 0):
                bad.append((name, f["amount"], [(g["account_name_as_written"], g["amount"]) for g in parts]))
    return checked, bad
