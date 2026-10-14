"""
SYNLB: a synthetic subcommittee for tests/test_any_subcommittee.py (no real figures), modelled on Legislative
Branch, where by custom the House bill carries no Senate items and the Senate bill no House items. Its one quirk:
a chamber's account missing from the other chamber's bill is "not carried by custom" -- neither a confirmed
absence nor a blank (hook 'absence', read by scripts/intake.py's missing-figure check).
"""

from rules_registry import ANY, Rule, Scope

CODE = "SYNLB"
ALL = Scope(CODE, accounts=ANY, fiscal_years=ANY, stages=ANY)

# each account's chamber (Legislative Branch: the House's own accounts, the Senate's own accounts)
CHAMBER = {"ACC-SYNLB-MEMBER-OPS": "House", "ACC-SYNLB-SENATOR-OPS": "Senate"}
OTHER_BILL = {"House Reported": "Senate", "Senate Reported": "House"}


def not_carried_by_custom(account, fiscal_year, stage, ctx):
    other = OTHER_BILL.get(stage)
    if other and CHAMBER.get(account) == other:
        bill = "House" if other == "Senate" else "Senate"
        return (f"not carried by custom: the {bill} bill carries no {other} items, so the {other}'s account has no "
                f"{stage} figure (rule SYNLB-CHAMBER-CUSTOM)")
    return None


RULES = [
    Rule("SYNLB-DISPLAY", "display", ALL, data={"name": "Synthetic Legislative Branch (test fixture)"},
         doc="the display name"),
    Rule("SYNLB-CHAMBER-CUSTOM", "absence",
         Scope(CODE, accounts=set(CHAMBER), fiscal_years=ANY, stages={"House Reported", "Senate Reported"}),
         fn=not_carried_by_custom, doc="a chamber's own account is not carried in the other chamber's bill"),
]
