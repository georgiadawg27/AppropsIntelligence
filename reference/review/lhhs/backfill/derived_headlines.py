"""
Headlines derived from printed lines (owner's rule, 2026-10-08). Where a table prints an account's lines but not its
headline, the headline is derived only where the later years' tables define it exactly; never a confirmed absence on
an account the document funds. Where it can't be derived exactly, the build stops and says so (the owner decides).

    Grants to States for Medicaid: headline = 'appropriated in this bill' - 'new advance, 1st quarter, FY<N+1>'
        (holds on every FY2024-FY2027 cell that prints all three: check_rule)

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
                and o.get("verification_status") != "superseded":
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
