"""
site_accounting.py -- the accounting layer of the site data (UI-0): what the page needs to offer the Amount basis
views, the Account type filter and the defense column without computing anything in the browser.

    layer = Layer(data)            # data: data/staged/, or approps_store.accounting_data(conn) (the same shape)
    layer.annotate(grid)           # grid: approps_store.subcommittee_grid(); adds the fields below in place

Every cell whose headline holds a printed figure (a value or a printed zero; the headline: the observation the grid
shows for the cell) gets:

  views.<basis>      [{amount, timing, status, reason, ...}] -- accounting.BASES. One entry for timing "available"
                     and, only where it differs, one for "enacted" (no "enacted" entry: the "available" one holds
                     for both). printed has one entry, timing null. status:
                       checked        a printed total confirms it (measure_reconcile passed that printed view
                                      line: printed_observation_id, validation_id)
                       computed       the headline less and plus its tagged components (component_ids); no
                                      printed total confirms it (reason says why)
                       excluded       the basis leaves the account out (a mandatory account under Discretionary)
                       not_computable no amount, with the reason
  components[]       every component tagged on the headline
  earmark            {amount, component_ids} -- only where earmark components are tagged (none tagged is not zero)
  defense_portion    {amount, component_ids} -- only where defense_portion components are tagged

A total (agency, title or bill) gets, on every basis and timing and in each exclusion field, a figure built down from
its printed anchor (owner, D-8; reference/specs/anchored-totals.md, ACCOUNTING_RULES 'Totals'): the build-up starts
from the total as printed (P) and adds the known adjustments of its lines (an agency total's accounts; a title or
bill total's accounts plus each agency held only as its total). Lines outside their life are not behind it; a
confirmed absence counts as nothing. Status:
  matches        a printed line of the total for the basis (anchor) equals P + the itemised adjustments
  computed       against an anchor, the anchor with the rest as one 'not itemised' remainder (remainder: amount,
                 accounts, why); with no anchor, P + adjustments where every part is known
  excluded       every line is left out by the basis
  not_computable no anchor and a part unknown: a line not computable or with no figure, money in no account held
                 (cause, one of CAUSES)
Every total entry carries accounts_behind; an anchored one its anchor {observation_id, label, document, page, kind}.
As printed stays the printed total. Every not computable entry, account or total, carries a cause.

Every entry with an amount lists adjustments (D-6): what turns the As printed figure into it. An account's: per
component the view takes out (sign -) or adds (+): {component_id, type, label, sign, amount, source_document_id,
source_page, source_location}. A total's (its build-up): grouped by component type and sign, {type, label, sign,
amount, accounts: [{canonical_account_id, amount, components}]}, then the accounts the basis leaves out (type
left_out_by_basis, their As printed figure), the held lines it doesn't count separately (type held_line_not_counted)
and, against an anchor, the remainder (type not_itemised). As printed + the adjustments = the amount, exactly; equal
to As printed: an empty list (unless two adjustments offset, e.g. an advance in and out).

Every row gets attributes.<FY> from account_attributes (the enacted basis, with each attribute's citation; the
request basis under "request" where the President's Budget has its own value).

The arithmetic is accounting.py's (the code measure_reconcile uses), and "checked" is measure_reconcile.run()'s own
outcome for the printed view line. Nothing here changes or adds a figure.
"""

import collections
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import accounting as AC  # noqa: E402
import measure_reconcile as M  # noqa: E402
import tag_components as TC  # noqa: E402

# a printed parallel total (Component vocabulary, kind 'view') -> the (basis, timing) it can confirm; timing None:
# the printed total keeps its headline's own advance convention, so it confirms whichever timing it equals
PRINTED_BASIS = {
    "appropriated_in_this_bill": [("all_appropriations", "enacted")],
    "available_this_fiscal_year": [("all_appropriations", "available")],
    "program_level": [("program_level", None)],
    "program_level_available_this_fiscal_year": [("program_level", "available")],
    "discretionary": [("discretionary_designated", None), ("discretionary", None)],
    # owner, 2026-10-12: confirms discretionary only where every designated amount is emergency and nothing in scope
    # is mandatory (only_emergency_designated); otherwise unused (Layer.unused)
    "excluding_emergencies": [("discretionary", None)],
}
ATTR_FIELDS = AC.ATTRIBUTES
# UI-0b: the cell field each exclusion view is published under; D-8 (owner decision D): both together, so the
# Non-defense filter and Exclude earmarks can be on at once
EXCLUSION_FIELDS = {"excluding_earmarks": "views_excluding_earmarks", "nondefense": "views_nondefense",
                    "nondefense_excluding_earmarks": "views_nondefense_excluding_earmarks"}
EXCLUSION_PARTS = {"excluding_earmarks": ("excluding_earmarks",), "nondefense": ("nondefense",),
                   "nondefense_excluding_earmarks": ("excluding_earmarks", "nondefense")}
# the component types each exclusion takes out (accounting.VIEWS' remove)
EXCLUSION_TYPES = {x: tuple(AC.EXCLUSIONS[p] for p in parts) for x, parts in EXCLUSION_PARTS.items()}
EXCLUSION_WORD = {"excluding_earmarks": "excluding earmarks", "nondefense": "excluding defense",
                  "nondefense_excluding_earmarks": "excluding defense and earmarks"}
UNTAGGED = {"excluding_earmarks": "earmarks not tagged for this stage",
            "nondefense": "defense not tagged for this stage",
            "nondefense_excluding_earmarks": "earmarks or defense not tagged for this stage"}

# D-8: why an entry is not computable, a code on every not computable entry (spec rule 5), most telling first
MISSING_FIGURE = "missing_figure"       # a line behind the total has no figure
NOT_HELD = "not_held"                   # the total prints money held in no account, and no line to build down from
NO_ANCHOR = "no_anchor"                 # no account behind the total is held in the cell, and no line for the basis
NO_CLASS = "no_class"                   # no budget_class on file for the year and stage
COMPONENTS_UNKNOWN = "components_unknown"   # the components the view needs are not known (not tagged, not read)
CAUSES = [MISSING_FIGURE, NOT_HELD, NO_ANCHOR, NO_CLASS, COMPONENTS_UNKNOWN]


# a total's entry that reaches its printed anchor with every step itemised (D-8) is "matches"
MATCHES = "matches"
VALUED = ("checked", "computed", MATCHES)
# the printed defense lines of a cell (Component vocabulary): NSF R&RA's 'Defense function'
DEFENSE_LINES = ("defense", "defense_inside")
STATE_WORDS = {"missing": "document on file, figure not yet entered", "not_collected": "not yet collected",
               "not_enacted": "not yet enacted", "not_reported": "not reported", "no_printed_total": "no printed total"}


# D-7 cleanup: the structural rule for a rescission line's excluding-earmarks view
RESCISSION_NO_EARMARKS = "a rescission carries no earmarks (CDS / CPF are appropriations)"

# D-6: the words an adjustment uses for a component type
TYPE_LABEL = {"advance_enacted": "advance appropriation enacted in this bill",
              "advance_available": "advance appropriation available this year",
              "designated": "designated amount", "transfer_in": "transfer in",
              "transfer_out": "transfer out", "trust_fund_limitation": "trust-fund limitation",
              "fees_offsets": "fees and offsetting collections", "rescission": "rescission",
              "defense_portion": "defense portion", "other_law": "amount provided by another law",
              "earmark": "earmark (CDS / CPF)", "mandatory_portion": "mandatory portion"}
LEFT_OUT = "left_out_by_basis"
# D-8: a total's remainder against its anchor, one named line (the D-6 'printed_total' type is retired)
NOT_ITEMISED = "not_itemised"


def label_of(c):
    words = TYPE_LABEL.get(c["component_type"], c["component_type"])
    if c.get("designation"):
        words += f" ({c['designation']})"
    if c.get("transfer_from"):
        words += f", from {c['transfer_from']}"
    return words


def sign_of(effect):
    return "-" if effect < 0 else "+"


def account_adjustments(entry, components):
    """What turns an account's As printed headline into the entry's amount: one item per component the view used --
    one inside the headline is taken out (its effect: minus its amount), one beside it is added (plus)."""
    by_id = {c["component_id"]: c for c in components}
    out = []
    for cid in entry.get("component_ids") or []:
        c = by_id[cid]
        effect = -c["amount"] if c["in_headline"] == "TRUE" else c["amount"]
        if not effect:
            continue            # a $0 component changes nothing: an entry equal to As printed lists nothing
        out.append({"component_id": cid, "type": c["component_type"], "label": label_of(c),
                    "sign": sign_of(effect), "amount": abs(effect), "source_document_id": c["source_document_id"],
                    "source_page": page_of(c["source_location"]), "source_location": c["source_location"]})
    return out


def effect_of(adjustment):
    return adjustment["amount"] if adjustment["sign"] == "+" else -adjustment["amount"]


def grouped_adjustments(used, left, field, basis, timing):
    """A total's adjustments: its lines' adjustments grouped by component type and sign, with the accounts behind
    each, then the accounts the basis leaves out (their As printed figure, taken out)."""
    groups = {}
    for m, mc, e in used:
        for x in e.get("adjustments") or []:
            g = groups.setdefault((x["type"], x["sign"]), {"type": x["type"], "label": TYPE_LABEL.get(x["type"], x["type"]),
                                                           "sign": x["sign"], "amount": 0, "accounts": {}})
            g["amount"] += x["amount"]
            a = g["accounts"].setdefault(m, {"canonical_account_id": m, "amount": 0, "components": []})
            a["amount"] += x["amount"]
            a["components"].append({k: x[k] for k in ("component_id", "label", "amount", "source_document_id",
                                                      "source_page")})
    out = [dict(g, accounts=list(g["accounts"].values())) for _, g in sorted(groups.items())]
    printed = [(m, mc["views"]["printed"][0]["amount"]) for m, mc, _ in left]
    effect = -sum(x for _, x in printed)
    if effect:
        out.append({"type": LEFT_OUT, "label": "accounts this basis leaves out (their figure as printed)",
                    "sign": sign_of(effect), "amount": abs(effect),
                    "accounts": [{"canonical_account_id": m, "amount": x,
                                  "source_document_id": mc["views"]["printed"][0].get("source_document_id"),
                                  "printed_observation_id": mc["views"]["printed"][0]["printed_observation_id"]}
                                 for (m, x), (_, mc, _) in zip(printed, left)]})
    return out


HELD_LINE = "held_line_not_counted"
# component types a basis decides by itself (the account's view counts them or not): a held line matching one is never
# added on top
VIEW_DECIDED = ("advance_enacted", "advance_available", "transfer_in", "trust_fund_limitation", "fees_offsets",
                "transfer_out", "mandatory_portion")


# owner, D-6 round 3: a held line counts in a total's sum only where the table prints it inside that total; the
# evidence, one row per (total, line) with the page and the printed rows, is judged from total_lines.py's report
INSIDE_FILE = ROOT / "reference" / "review" / "accounting" / "total_lines_inside.csv"


def inside_lines(path=None):
    path = path or INSIDE_FILE
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["total_observation_id"], r["line_observation_id"]) for r in csv.DictReader(f)
                if r["inside"] == "yes"}


# owner, 2026-10-15 (D-9): a line counts in the total the table prints it under. An account the table prints under
# another agency's total in a cell (ARPA-H under the Office of the Secretary in H.Rept. 117-403) leaves the total it
# is held under and joins that one, for that cell and that table only; one row per (account, cell, document)
MEMBERS_FILE = ROOT / "reference" / "review" / "accounting" / "total_members_printed.csv"


def printed_members(path=None):
    """(account, fiscal year, stage, document) -> (total it is held under, total the table prints it under)."""
    path = path or MEMBERS_FILE
    if not path.exists():
        return {}
    with path.open() as f:
        return {(r["account"], int(r["fiscal_year"]), r["stage"], r["document"]): (r["held_under"], r["printed_under"])
                for r in csv.DictReader(f)}


def held_lines(lines):
    """The lines held in a total's accounts' cells beside their headlines (not the budget-authority headline, not a
    line inside it or a printed parallel total): a supplemental, an advance, a program line printed under the
    account, a rescission. Each with the component of the headline it matches (beside it, the same amount), if any."""
    out = []
    for m, mc in lines:
        for l in mc["lines"]:
            if (l["amount_type"] == "budget authority" and not l["component"]) or not l["observations"]:
                continue
            if l.get("component_kind") in ("contained", "view"):
                continue
            o = l["observations"][0]
            comp = next((c for c in mc.get("components") or [] if not c["in_headline"] and c["amount"] == o["amount"]),
                        None)
            out.append({"account": m, "observation_id": o["observation_id"], "amount": o["amount"],
                        "label": l["component_label"] or l["component"] or l["amount_type"],
                        "source_document_id": o["source_document_id"], "source_page": o.get("source_page"),
                        "component_id": comp["component_id"] if comp else None,
                        "component_type": comp["type"] if comp else None})
    return out


def line_words(x):
    return (f"{x['account']} {x['label']} {AC.money(x['amount'])} ({x['source_document_id']} "
            f"p.{x['source_page'] or '?'})")


def line_counts(x, entry, field, basis):
    """Whether a held line the total prints counts separately in the total's sum on a basis: -> (bool, why not).
    Its account left out by the basis takes it out too; a line matching a component the basis decides by itself
    (an advance by timing, a transfer in by program level) is in the account's view where the basis counts it, never
    added twice; a designated line (a supplemental) is out of Discretionary; a defense line out of Nondefense, an
    earmark out of excluding earmarks; anything else counts as printed."""
    if entry["status"] == "excluded":
        return False, "its account is left out by this basis"
    t = x["component_type"]
    if t == "defense_portion" and field == "views_nondefense":
        return False, "a defense line, out of the nondefense view"
    if t == "earmark" and field == "views_excluding_earmarks":
        return False, "an earmark, out of the excluding-earmarks view"
    if basis == "printed":
        return True, None
    if x["component_id"] and x["component_id"] in (entry.get("component_ids") or []):
        return False, f"already in the account's view ({x['component_id']})"
    if t in VIEW_DECIDED:
        return False, f"a {t} component this basis and timing don't count ({x['component_id']})"
    if t == "designated" and basis == "discretionary":
        return False, f"a designated amount, out of Discretionary ({x['component_id']})"
    return True, None


def pick(entries, timing):
    """The entry for a timing: its own, else the "available" one, which holds for both where no "enacted" entry is
    published (web/ui_contract.json shapes)."""
    return next((e for e in entries if e["timing"] == timing), None) or \
        next((e for e in entries if e["timing"] == "available"), entries[0])


def collapse(entries):
    """One entry where both timings give the same amount and status ("available" then holds for both)."""
    if len(entries) == 2 and (entries[0]["amount"], entries[0]["status"]) == (entries[1]["amount"], entries[1]["status"]):
        return entries[:1]
    return entries


def cause_of(reason):
    """The cause code of an account's not computable view, from accounting's reason."""
    return NO_CLASS if "budget_class" in (reason or "") else COMPONENTS_UNKNOWN


def anchor_of(o):
    """A total's anchor (spec rule 5): the printed line, in the report's own words, with its document and page.
    kind: table_line (a committee table's or explanatory statement's line) or text (bill or law text)."""
    doc = o["source_document_id"]
    return {"observation_id": o["observation_id"], "label": TC.printed(o), "document": doc,
            "page": o.get("source_page"),
            "kind": "text" if doc.startswith(("SRC-BILL", "SRC-PLAW")) else "table_line"}


def anchor_words(anchor):
    return f"'{anchor['label']}' ({anchor['document']} p.{anchor['page'] or '?'})"


def unknown_words(acc, u):
    if not u["account"]:
        return f"money in no account held ({u['why']})"       # its why carries the amount
    return f"{acc[u['account']]['canonical_name']}{' ' + AC.money(u['amount']) if u['amount'] is not None else ''} ({u['why']})"


def cell_state(cell):
    """The cell's headline state (approps_store.headline_state)."""
    head = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]), None) \
        or (cell["lines"][0] if cell["lines"] else None)
    return head["state"] if head else None


def page_of(location):
    m = re.match(r"\s*p(?:p)?\.\s*(\d+)", location or "")
    return m.group(1) if m else None


def component_record(c):
    return {"component_id": c["component_id"], "type": c["component_type"], "amount": c["amount"],
            "in_headline": c["in_headline"] == "TRUE", "within_component_id": c.get("within_component_id") or None,
            "designation": c.get("designation") or None, "transfer_from": c.get("transfer_from") or None,
            "source_document_id": c["source_document_id"], "source_page": page_of(c["source_location"]),
            "source_table_or_section": c["source_location"]}


def headline_of_cell(cell):
    """The observation the grid shows for a cell (the page's headOf), when the cell holds a printed figure: a value,
    or a printed zero (ONC's $0 headline has a program level). A confirmed absence holds no observation. A cell with
    no budget-authority line whose figure is a rescission (a rescission-only account: the HHS general provisions'
    Nonrecurring Expenses Fund and Adoption Incentives rescissions) takes its rescission line (D-7 cleanup)."""
    line = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]), None)
    if line is None:
        line = rescission_line(cell)
    if line is None or line["state"] not in ("value", "not_funded") or not line["observations"]:
        return None
    return line["observations"][0]


def rescission_line(cell):
    """A cell's rescission line, where the cell has no budget-authority line at all: the line amount_type
    'rescission' with no component (its '(emergency)' rescission, a separate line, stays a line beside it)."""
    if any(l["amount_type"] == "budget authority" for l in cell["lines"]):
        return None
    return next((l for l in cell["lines"] if l["amount_type"] == "rescission" and not l["component"]), None)


class Layer:
    def __init__(self, data):
        self.data = data
        self.obs = {o["observation_id"]: o for o in data["observations"]}
        self.acc = {a["canonical_account_id"]: a for a in data["accounts"]}
        self.comps = AC.components_by_observation(data)
        self.heads = TC.headline_of(data)
        self.attrs = AC.attribute_index(data)
        records, outcomes = M.run(data)
        self.records = {r["validation_id"]: r for r in records}
        # measure_reconcile's outcome for each printed view line
        self.outcome = {o["observation_id"]: o for o in outcomes if o["kind"] == "view"}
        # printed view lines by cell
        kinds = {c["component_id"]: c["kind"] for c in data.get("components", [])}
        self.unused = set()     # (printed view observation, why it confirms no basis): excluding_emergencies
        self.gaps = []          # D-6: total cells whose lines as printed don't add up to the printed total
        self.rows = {}
        # D-6 round 3: (total's printed observation, held line's observation) pairs the table prints inside the total
        self.inside = inside_lines()
        # D-9: accounts the table prints under another total in a cell (MEMBERS_FILE)
        self.moved_members = printed_members()
        # D-9: a total's own lines printed inside it (a 'contained' line on the total, e.g. NIH's proposed NIRSQ), by
        # the printed figure they sit in
        self.own_lines = collections.defaultdict(list)
        for o in data["observations"]:
            if (o.get("headline_observation_id") and kinds.get(o.get("component") or "") == "contained"
                    and o["verification_status"] != "superseded"):
                self.own_lines[o["headline_observation_id"]].append(o)
        # an agency held only as its total (no account lines): its total is its one line
        self.lone = {a["canonical_account_id"] for a in data["accounts"]
                     if a.get("total_scope") == "agency" and not M.members(data, a)}
        self.printed = collections.defaultdict(list)
        for o in data["observations"]:
            if kinds.get(o.get("component") or "") == "view" and o["verification_status"] != "superseded":
                self.printed[(o["canonical_account_id"], int(o["fiscal_year"]), o["stage"])].append(o)

    # ------------------------------------------------------------------ accounts
    def cell_components(self, h):
        return self.comps.get(h["observation_id"], [])

    def account_views(self, a, fy, stage, h):
        cs = self.cell_components(h)
        base = AC.split(h["amount"], cs)[0]
        # a rescission line is negative as printed (signed as its effect on budget authority): no negative base
        if base < 0 and h["amount_type"] != "rescission":
            why = (f"the base is negative ({AC.money(base)}): the components the headline includes exceed it "
                   f"(VAL-MR-{M.suffix(h['observation_id'])}, pending review)")
            return {b: [{"amount": None, "timing": None if b == "printed" else "available",
                         "status": "not_computable", "cause": COMPONENTS_UNKNOWN, "reason": why}] for b in AC.BASES}
        auto = TC.covered(a["canonical_account_id"], a["subcommittee"], fy, stage)
        rec = AC.attribute_value(self.attrs, a["canonical_account_id"], fy, "budget_class", stage)
        cls = rec["value"] if rec else None
        printed = self.printed_checks(a["canonical_account_id"], fy, stage)
        views = {"printed": [{"amount": h["amount"], "timing": None, "status": "checked",
                              "reason": "the figure as printed", "printed_observation_id": h["observation_id"]}]}
        for basis in AC.BASES[1:]:
            uses = AC.basis_uses(basis)
            relevant = [c for c in cs if c["component_type"] in uses]
            entries = []
            for timing in AC.TIMINGS:
                got = AC.basis_amount(h["amount"], cs, basis, timing, cls)
                if got[0] != "value":
                    e = {"amount": None, "timing": timing, "status": got[0], "reason": got[1]}
                    if got[0] == "not_computable":
                        e["cause"] = cause_of(got[1])
                elif not auto and not relevant and h["amount_type"] != "rescission":
                    e = {"amount": None, "timing": timing, "status": "not_computable", "cause": COMPONENTS_UNKNOWN,
                         "reason": "no component the view uses is tagged for this cell ("
                                   + TC.not_covered_reason(a["canonical_account_id"], fy, stage)
                                   + "), so its components are not known"}
                else:
                    _, amount, used, note = got
                    e = {"amount": amount, "timing": timing, "status": "computed",
                         "component_ids": sorted(c["component_id"] for c in used)}
                    e.update(self.confirm(printed, basis, timing, amount,
                                          self.emergency_scope([(a["canonical_account_id"], h)], fy, stage)))
                    if h["amount_type"] == "rescission" and not cs:
                        # D-7 cleanup: a rescission is budget authority (negative) on every basis; no component
                        # changes it (an advance, a transfer, a mandatory portion or a designation is not read
                        # into a rescission line: its '(emergency)' rescission prints as its own line)
                        e["reason"] = f"a rescission line, as printed: no component applies; {e['reason']}"
                    if note:
                        e["reason"] = f"{note}; {e['reason']}"
                entries.append(e)
            avail, enacted = entries
            same = (avail["amount"], avail["status"]) == (enacted["amount"], enacted["status"])
            views[basis] = [avail] if same else [avail, enacted]
        return views

    def printed_checks(self, account, fy, stage):
        """[(printed view observation, measure_reconcile's outcome)] for the cell."""
        return [(o, self.outcome.get(o["observation_id"])) for o in self.printed[(account, fy, stage)]]

    def emergency_scope(self, heads, fy, stage):
        """None when an 'excluding emergencies' printed total over these (account, headline) pairs is the discretionary
        view (every designated amount inside the headlines is emergency, and no account is mandatory, has a
        mandatory_portion or has no budget_class on file); else why not."""
        for m, x in heads:
            cs = self.comps.get(x["observation_id"], [])
            # a total carries no attributes: its own headline is judged by its components alone
            rec = ({"value": None} if self.acc[m].get("total_scope")
                   else AC.attribute_value(self.attrs, m, fy, "budget_class", stage))
            if rec is None:
                return f"{m} has no budget_class on file"
            if rec["value"] == "mandatory" or any(c["component_type"] == "mandatory_portion" for c in cs):
                return f"{m} holds mandatory budget authority"
            if any(c["component_type"] == "designated" and c["in_headline"] == "TRUE"
                   and c.get("designation") != "emergency" for c in cs):
                return f"{m} has a designated amount that is not emergency"
        return None

    def confirm(self, printed, basis, timing, amount, emergency_bar=None):
        """status and reason for a computed amount from the cell's printed view lines. emergency_bar: why an
        'excluding emergencies' printed total cannot confirm discretionary here (None: it can)."""
        notes = []
        for o, out in printed:
            fits = [b for b, t in PRINTED_BASIS.get(o["component"], []) if b == basis and t in (None, timing)]
            if not fits:
                continue
            if o["component"] == "excluding_emergencies" and emergency_bar:
                self.unused.add((o["observation_id"], emergency_bar))
                continue
            vid = "VAL-MRV-" + M.suffix(o["observation_id"])
            if out and out["result"] == "pass" and o["amount"] == amount and vid in self.records:
                return {"status": "checked", "printed_observation_id": o["observation_id"], "validation_id": vid,
                        "reason": f"equals the printed '{TC.printed(o)}' ({o['source_document_id']} "
                                  f"p.{o.get('source_page') or '?'})"}
            if out is None or out["result"] == "not checked":
                notes.append(f"printed '{TC.printed(o)}' not checked by measure_reconcile"
                             + (f": {out['detail']}" if out else ""))
            elif out["result"] == "fail":
                notes.append(f"differs from the printed '{TC.printed(o)}' {AC.money(o['amount'])} ({vid}, cause "
                             f"{out.get('cause')}, pending review)")
        return {"reason": "; ".join(notes) if notes else "no printed total for this view"}

    # ------------------------------------------------------------------ totals
    def total_members(self, a):
        scope = a.get("total_scope")
        if scope == "agency":
            return M.members(self.data, a)
        if scope == "title":
            return M.title_members(self.data, a)
        return [x["canonical_account_id"] for x in self.data["accounts"]
                if x["subcommittee"] == a["subcommittee"] and not x.get("total_scope")
                and not x.get("parent_account_id")]

    def member_heads(self, a, fy, stage, h):
        out = []
        for m in self.total_members(a):
            hs = self.heads[(m, fy, stage)]
            mh = TC.pick_headline(hs, h["source_document_id"]) if hs else None
            if mh is not None:
                out.append((m, mh))
        return out

    def convention(self, headlines, moved=()):
        """The advance timing a sum of printed headlines keeps: 'both' (no advance tagged on any), 'available',
        'enacted', or None (mixed). moved: components beside a headline whose line the table prints inside the
        total (D-8): inside the sum."""
        cs = [c for h in headlines for c in self.comps.get(h["observation_id"], [])
              if c["component_type"] in ("advance_enacted", "advance_available")]
        if not cs:
            return "both"
        inside = {c["component_type"] for c in cs if c["in_headline"] == "TRUE" or c["component_id"] in moved}
        beside = {c["component_type"] for c in cs if c["in_headline"] != "TRUE" and c["component_id"] not in moved}
        if inside <= {"advance_enacted"} and beside <= {"advance_available"}:
            return "enacted"
        if inside <= {"advance_available"} and beside <= {"advance_enacted"}:
            return "available"
        return None

    # ------------------------------------------------------------------ totals from their accounts (D-6)
    def lines_behind(self, a):
        """The accounts a total adds up (owner, D-6): an agency total's accounts (measure_reconcile.members); a title
        or bill total's accounts, plus each agency of it held only as its total (no account lines: its total is its
        one line)."""
        scope = a.get("total_scope")
        if scope == "agency":
            return M.members(self.data, a)
        lone = [x["canonical_account_id"] for x in self.data["accounts"]
                if x.get("total_scope") == "agency" and x["subcommittee"] == a["subcommittee"]
                and (scope != "title" or (x.get("title") or "") == (a.get("title") or ""))
                and not M.members(self.data, x)]
        return self.total_members(a) + lone

    def members_in(self, a, key, doc):
        """The accounts an agency total adds up in one cell of one table: lines_behind, less an account that table
        prints under another total, plus one it prints under this one (MEMBERS_FILE)."""
        ms = self.lines_behind(a)
        if a.get("total_scope") != "agency" or not doc:
            return ms
        fy, stage = key.split("|")
        aid = a["canonical_account_id"]
        moved = {m: v for (m, y, st, d), v in self.moved_members.items() if (y, st, d) == (int(fy), stage, doc)}
        return [m for m in ms if moved.get(m, (None,))[0] != aid] + sorted(
            m for m, (_, to) in moved.items() if to == aid and m not in ms)

    def behind(self, a, key, doc=None):
        """The total's lines in one cell: -> (lines [(account, cell)] holding a printed figure, accounts with a
        confirmed absence (nothing), lines with no figure [(account, state)]). A line outside its life, or before it
        existed (no_figure), is not behind the total in that cell. doc: the total's table (members_in)."""
        lines, absent, nofig = [], [], []
        for m in self.members_in(a, key, doc):
            row = self.rows.get(m)
            mc = row["cells"].get(key) if row else None
            if mc is None or mc.get("outside_life"):
                continue
            if mc.get("views") and rescission_line(mc) is not None:
                where = self.rescission_place(a, mc, doc)
                if where == "outside":
                    continue
                if where != "inside":
                    nofig.append((m, where))
                    continue
            if mc.get("views"):
                lines.append((m, mc))
                continue
            st = cell_state(mc)
            if st == "no_figure":
                continue
            if st == "not_funded":
                absent.append(m)
            else:
                nofig.append((m, st))
        return lines, absent, nofig

    def rescission_place(self, a, mc, doc):
        """D-7 cleanup: where a title or bill total's table prints a rescission-only line (the HHS general provisions'
        rescissions), from the line's own record: -> 'inside' (the total's own table prints it inside the total: a
        title's '<title>, ...' section, or any title's for a bill total), 'outside' (the total's own table prints it
        outside its title, e.g. the Senate reports' 'Discretionary Rescissions' after the grand total: not behind a
        title total), or why it is not known (another document records it, so the total's table is not read for it;
        a bill total's line printed outside every title)."""
        o = self.obs[rescission_line(mc)["observations"][0]["observation_id"]]
        section = (o.get("source_table_or_section") or "").split(" -- ")[0]
        page = f"{o['source_document_id']} p.{o.get('source_page') or '?'}"
        if o["source_document_id"] != doc:
            return (f"rescission: the rescission is recorded from {page}, not from this total's table, so whether "
                    f"the table prints it inside the total is not read")
        if a.get("total_scope") == "title":
            return "inside" if section.startswith(f"{a.get('title')},") else "outside"
        if section.startswith("Title "):
            return "inside"
        return (f"rescission: the table prints the rescission outside every title ('{section}', {page}); whether "
                f"its bill total counts it is not read")

    def total_layer(self, a, fy, stage, h, cell):
        """views and the exclusion fields of a total (owner, D-8: reference/specs/anchored-totals.md): on every basis
        and timing, built down from the printed anchor. The build-up starts from the total as printed (P), so money
        printed in the total but held in no account stays in, and adds the known adjustments of each line whose view
        is known (anchored). With an anchor A the total is A; R = A - P - known adjustments is one named 'not
        itemised' line, never spread over accounts. With no anchor it is P + adjustments where every part is known,
        otherwise not computable with a cause."""
        key = f"{fy}|{stage}"
        self.cell_key = (fy, stage)
        self.cell_head = h
        lines, absent, nofig = self.behind(a, key, h["source_document_id"])
        n = len(lines) + len(absent)
        held = sum(mc["views"]["printed"][0]["amount"] for _, mc in lines)
        beside = held_lines(lines)
        # owner, D-6 round 3: only the held lines the table prints inside this total (INSIDE_FILE's evidence)
        inside = [x for x in beside if (h["observation_id"], x["observation_id"]) in self.inside]
        # owner, 2026-10-15 (D-9): an advance tagged from the bill or law text beside a headline, which the table prints
        # inside the total (its 'New advance, 1st quarter' row), counts as held (the evidence row names the component)
        for m, mc in lines:
            have = {x["component_id"] for x in beside if x["account"] == m and x["component_id"]}
            for c in mc.get("components") or []:
                if (not c["in_headline"] and c["type"] == "advance_enacted" and c["component_id"] not in have
                        and (h["observation_id"], c["component_id"]) in self.inside):
                    inside.append({"account": m, "observation_id": c["component_id"], "amount": c["amount"],
                                   "label": c["type"], "source_document_id": c.get("source_document_id"),
                                   "source_page": c.get("source_page"), "component_id": c["component_id"],
                                   "component_type": c["type"]})
        # D-9: the total's own lines the table prints inside it, with evidence: in the printed figure, held on the total
        own = [{"account": a["canonical_account_id"], "observation_id": o["observation_id"], "amount": o["amount"],
                "label": o["component"], "source_document_id": o["source_document_id"],
                "source_page": o.get("source_page"), "component_id": None, "component_type": None}
               for o in self.own_lines.get(h["observation_id"], [])
               if (h["observation_id"], o["observation_id"]) in self.inside]
        unheld = h["amount"] - held - sum(x["amount"] for x in inside + own)
        if not nofig and n and held != h["amount"]:
            self.gaps.append({"subcommittee": a["subcommittee"], "total": a["canonical_account_id"],
                              "agency": a["agency"], "fiscal_year": fy, "stage": stage, "printed": h["amount"],
                              "printed_observation_id": h["observation_id"],
                              "source_document_id": h["source_document_id"], "source_page": h.get("source_page"),
                              "held": held, "gap": h["amount"] - held, "lines": lines, "beside": beside,
                              "inside": inside + own, "counted": inside + own if not unheld else []})
        heads = [h] + [self.obs[mc["views"]["printed"][0]["printed_observation_id"]] for _, mc in lines]
        prints = [o for o in self.printed[(a["canonical_account_id"], fy, stage)]
                  if o["source_document_id"] == h["source_document_id"]]
        parts = {"lines": lines, "absent": absent, "nofig": nofig, "inside": inside, "unheld": unheld, "n": n,
                 "heads": heads, "prints": prints}
        out = {"views": {"printed": [{"amount": h["amount"], "timing": None, "status": "checked",
                                      "reason": "the figure as printed", "printed_observation_id": h["observation_id"],
                                      "accounts_behind": n, "adjustments": []}]}}
        unknown = {("printed", None): self.line_parts(parts, "views", "printed", None)[3]}
        for basis in AC.BASES[1:]:
            entries = []
            for timing in AC.TIMINGS:
                e, unknown[(basis, timing)] = self.anchored(a, h, parts, basis, timing)
                entries.append(e)
            out["views"][basis] = collapse(entries)
        for exclusion, field in EXCLUSION_FIELDS.items():
            out[field] = {}
            for basis in AC.BASES:
                entries = [self.total_exclusion(a, h, parts, exclusion, basis, timing, out["views"],
                                                unknown.get((basis, timing), []))
                           for timing in ([None] if basis == "printed" else AC.TIMINGS)]
                out[field][basis] = collapse(entries)
        return out

    def line_parts(self, parts, field, basis, timing):
        """A total's parts on one field, basis and timing: -> (used [(account, cell, entry)] with a known view, left
        [(account, cell, entry)] the basis leaves out, line_adj: held lines printed inside the total that the basis
        doesn't count separately, unknown: what the build-up can't itemise -- [{account, amount, cause, why}], account
        None for money held in no account)."""
        used, left, unknown = [], [], []
        for m, mc in parts["lines"]:
            e = pick(mc[field][basis], timing)
            if e["status"] in VALUED:
                used.append((m, mc, e))
            elif e["status"] == "excluded":
                left.append((m, mc, e))
            else:
                unknown.append({"account": m, "amount": mc["views"]["printed"][0]["amount"],
                                "cause": e.get("cause") or COMPONENTS_UNKNOWN, "why": e["reason"]})
        for m, st in parts["nofig"]:
            unknown.append({"account": m, "amount": None, "cause": MISSING_FIGURE,
                            "why": ("its figure is not a budget-authority headline, so it has no published view"
                                    if st == "value" else st.removeprefix("rescission: ")
                                    if st.startswith("rescission: ") else f"no figure: {STATE_WORDS.get(st, st)}")})
        entry_of = {m: e for m, _, e in used + left}
        line_adj = []
        for x in parts["inside"]:
            e = entry_of.get(x["account"])
            if e is None:
                continue            # its account is unknown: the line goes with it
            counted, why = line_counts(x, e, field, basis)
            if not counted:
                line_adj.append(dict(x, why=why))
        if parts["unheld"]:
            unknown.append({"account": None, "amount": parts["unheld"], "cause": NOT_HELD if parts["n"] else NO_ANCHOR,
                            "why": f"the total as printed less its accounts: {AC.money(parts['unheld'])} is in no account held"
                                   + (" (or in the lines above with no figure)" if parts["nofig"] else "")
                                   + ("" if parts["n"] else " (no account behind it is held in this cell)")})
        return used, left, line_adj, unknown

    def anchor_for(self, basis, timing, parts, known):
        """The printed line a basis and timing must reach (spec rule 1): a printed parallel total of the cell, from
        the total's own document, whose scope is the basis (PRINTED_BASIS). A line that doesn't say its timing keeps
        its table's advance convention (convention): where the lines print no advance and every part is known, it
        holds for both timings; where some part is unknown, only for the committee tables' own convention, advances
        counted in the bill that appropriates them ('enacted'). 'discretionary' stands for Discretionary (without
        designated amounts) and 'excluding emergencies' for Discretionary only where every part is known and the
        scope allows it (as measure_reconcile reads them). -> the observation, or None."""
        # the headlines the basis counts (a line it leaves out is not in the printed line either), with the advance
        # lines the table prints inside the total read as inside it
        out_ids = {m for m, mc in parts["lines"] if pick(mc["views"][basis], timing)["status"] == "excluded"}
        heads = [parts["heads"][0]] + [x for x in parts["heads"][1:] if x["canonical_account_id"] not in out_ids]
        moved = {x["component_id"] for x in parts["inside"] if x["component_id"]}
        for o in parts["prints"]:
            for b, t in PRINTED_BASIS.get(o["component"], []):
                if b != basis:
                    continue
                if t is None:
                    conv = self.convention(heads, moved)
                    timings = {"both": AC.TIMINGS if known else ["enacted"], "available": ["available"],
                               "enacted": ["enacted"]}.get(conv, [])
                    if timing not in timings:
                        continue
                elif t != timing:
                    continue
                if o["component"] == "discretionary" and basis == "discretionary":
                    if not known or any(c["component_type"] == "designated" and c["in_headline"] == "TRUE"
                                        for x in heads for c in self.comps.get(x["observation_id"], [])):
                        continue    # the printed total counts designated amounts: discretionary_designated
                if o["component"] == "excluding_emergencies":
                    fy, stage = self.cell_key
                    bar = None if known else "a part of the total is not known"
                    bar = bar or self.emergency_scope([(x["canonical_account_id"], x) for x in heads], fy, stage)
                    if bar:
                        self.unused.add((o["observation_id"], bar))
                        continue
                return o
        return None

    def anchored(self, a, h, parts, basis, timing):
        """One views entry of a total (spec rules 2-3) -> (entry, its unknown parts)."""
        used, left, line_adj, unknown = self.line_parts(parts, "views", basis, timing)
        adjustments = grouped_adjustments(used, left, "views", basis, timing)
        if line_adj:
            effect = -sum(x["amount"] for x in line_adj)
            adjustments.append({"type": HELD_LINE, "label": "lines printed inside the total, held beside an account's "
                                                            "headline, that this basis doesn't count separately",
                                "sign": sign_of(effect), "amount": abs(effect),
                                "lines": [{k: x[k] for k in ("account", "observation_id", "label", "amount",
                                                             "source_document_id", "source_page", "why")}
                                          for x in line_adj]})
        known = sum(effect_of(x) for x in adjustments)
        n = parts["n"]
        head = (f"the total as printed and the adjustments of its {n} account{'s' if n != 1 else ''}"
                + (f" ({len(left)} left out by this basis)" if left else "")
                + (f" ({len(parts['absent'])} with a confirmed absence, counted as nothing)" if parts["absent"] else ""))
        o = self.anchor_for(basis, timing, parts, not unknown)
        e = {"timing": timing, "accounts_behind": n}
        if o is not None:
            anchor = anchor_of(o)
            r = o["amount"] - h["amount"] - known
            e.update(amount=o["amount"], anchor=anchor)
            if not r:
                e.update(status=MATCHES, reason=f"{head} reach the report's {anchor_words(anchor)}: matches")
            else:
                why = ("the report's line less the total as printed and its itemised adjustments"
                       + (": " + "; ".join(unknown_words(self.acc, u) for u in unknown) if unknown else
                          ": every account behind it is itemised, so the difference is the report's own")
                       + self.reconcile_note(o))
                remainder = {"amount": r, "accounts": [{k: u[k] for k in ("account", "amount", "cause")}
                                                       for u in unknown], "why": why}
                adjustments = adjustments + [{"type": NOT_ITEMISED, "label": "not itemised", "sign": sign_of(r),
                                              "amount": abs(r), "printed_observation_id": o["observation_id"],
                                              "source_document_id": o["source_document_id"],
                                              "source_page": o.get("source_page"),
                                              "source_location": f"not itemised against {anchor_words(anchor)}"}]
                e.update(status="computed", remainder=remainder,
                         reason=f"the report's {anchor_words(anchor)}; {head}, and {AC.money(r)} not itemised ({why})")
        elif unknown:
            u = min(unknown, key=lambda u: CAUSES.index(u["cause"]))
            return dict(e, amount=None, status="not_computable", cause=u["cause"],
                        reason="not computable: " + "; ".join(unknown_words(self.acc, x) for x in unknown)
                               + "; the report prints no line for this basis to build down from"), unknown
        elif not used and not parts["absent"]:
            return dict(e, amount=None, status="excluded",
                        reason=f"every one of its {n} accounts is left out by this basis"), unknown
        else:
            e.update(amount=h["amount"] + known, status="computed",
                     reason=f"{head}; the report prints no line for this basis")
        e["adjustments"] = adjustments
        return e, unknown

    def reconcile_note(self, o):
        out = self.outcome.get(o["observation_id"])
        if out and out["result"] == "fail":
            cause = (out.get("cause") or "unexplained").upper().replace("_", "-")
            return f" (VAL-MRV-{M.suffix(o['observation_id'])}, VAL-MRC-{cause}, pending review)"
        return ""

    def total_exclusion(self, a, h, parts, exclusion, basis, timing, views, unknown):
        """One exclusion entry of a total: its views entry (anchored) less what the exclusion takes out of each
        line. Every part must be known for the exclusion: a line whose own exclusion entry is, a part the views
        entry could not itemise only where a structural rule says the exclusion takes nothing out of it (a request
        carries no earmarks; an account outside function 050)."""
        base = pick(views[basis], timing) if basis != "printed" else views["printed"][0]
        e = {k: v for k, v in base.items() if k not in ("adjustments", "printed_observation_id", "validation_id")}
        e["timing"] = timing
        if base["status"] not in VALUED:
            return e
        field = EXCLUSION_FIELDS[exclusion]
        fy, stage = self.cell_key
        gone, missing = [], []
        for m, mc in parts["lines"]:
            v, x = pick(mc["views"][basis], timing), pick(mc[field][basis], timing)
            if v["status"] == "excluded":
                continue
            if v["status"] in VALUED and x["status"] in VALUED:
                ids = {y.get("component_id") for y in v.get("adjustments") or []}
                for y in x.get("adjustments") or []:
                    if y.get("component_id") not in ids:
                        gone.append((m, y))
            elif v["status"] in VALUED or not self.takes_nothing(m, mc, exclusion):
                missing.append({"account": m, "cause": x.get("cause") or COMPONENTS_UNKNOWN, "why": x["reason"]})
        # a held line printed inside the total that the views entry counts but the exclusion doesn't (NSF's request
        # defense line under Non-defense)
        dropped = []
        for x in parts["inside"]:
            m = x["account"]
            mc = next(mc for k, mc in parts["lines"] if k == m)
            v, y = pick(mc["views"][basis], timing), pick(mc[field][basis], timing)
            if v["status"] in VALUED and y["status"] in VALUED and line_counts(x, v, "views", basis)[0] \
                    and not line_counts(x, y, field, basis)[0]:
                dropped.append(dict(x, why=line_counts(x, y, field, basis)[1]))
        # parts the views entry could not itemise and no line of it holds: a line with no figure, money in no account
        # held -- known to hold none of the exclusion only where the stage carries none (a request, earmarks)
        held_ids = {m for m, _ in parts["lines"]}
        none_here = stage == TC.REQUEST_STAGE and EXCLUSION_PARTS[exclusion] == ("excluding_earmarks",)
        for u in unknown:
            if u["account"] not in held_ids and not none_here:
                missing.append(dict(u, why=f"{u['why']}; what it holds to exclude is not known"))
        word = EXCLUSION_WORD[exclusion]
        if missing:
            u = min(missing, key=lambda u: CAUSES.index(u["cause"]))
            return {"amount": None, "timing": timing, "status": "not_computable", "cause": u["cause"],
                    "accounts_behind": parts["n"],
                    "reason": f"not computable {word}: "
                              + "; ".join(f"{self.acc[x['account']]['canonical_name'] if x['account'] else 'money in no account held'}"
                                          f" ({x['why']})" for x in missing)}
        taken = sum(effect_of(y) for _, y in gone) - sum(x["amount"] for x in dropped)
        groups = {}
        for m, y in gone:
            g = groups.setdefault((y["type"], y["sign"]), {"type": y["type"], "label": TYPE_LABEL.get(y["type"], y["type"]),
                                                           "sign": y["sign"], "amount": 0, "accounts": {}})
            g["amount"] += y["amount"]
            acc = g["accounts"].setdefault(m, {"canonical_account_id": m, "amount": 0, "components": []})
            acc["amount"] += y["amount"]
            acc["components"].append({k: y[k] for k in ("component_id", "label", "amount", "source_document_id",
                                                        "source_page")})
        e["amount"] = base["amount"] + taken
        e["adjustments"] = list(base.get("adjustments") or []) + [dict(g, accounts=list(g["accounts"].values()))
                                                                  for _, g in sorted(groups.items())]
        if dropped:
            e["adjustments"].append({"type": HELD_LINE, "label": "lines printed inside the total, held beside an "
                                                                 "account's headline, that this view leaves out",
                                     "sign": "-", "amount": sum(x["amount"] for x in dropped),
                                     "lines": [{k: x[k] for k in ("account", "observation_id", "label", "amount",
                                                                  "source_document_id", "source_page", "why")}
                                               for x in dropped]})
        if taken or basis == "printed":
            e["status"] = "computed"
            e["reason"] = f"{base['reason']}; {word}: less {AC.money(-taken)} tagged in its accounts"
        else:
            e["reason"] = f"{base['reason']}; {word}: nothing tagged to take out of its accounts"
        return e

    def takes_nothing(self, m, mc, exclusion):
        """A structural rule says the exclusion takes nothing out of this line, whatever was read (structural)."""
        fy, stage = self.cell_key
        x = self.obs[mc["views"]["printed"][0]["printed_observation_id"]]
        return all(self.structural(self.acc[m], fy, stage, x, one, mc) for one in EXCLUSION_PARTS[exclusion])

    # ------------------------------------------------------------------ excluding earmarks, nondefense (UI-0b)
    def untagged(self, exclusion, behind=None, why=None):
        """The entries of a cell whose stage the tagger has not read (outside tag_components.coverage(), or a
        President's Budget account whose request language was not found): untagged is never 'none tagged'."""
        e = {"amount": None, "status": "not_computable", "cause": COMPONENTS_UNKNOWN,
             "reason": UNTAGGED[exclusion] + (f" ({why})" if why else "")}
        if behind is not None:
            e["accounts_behind"] = behind
        return {b: [dict(e, timing=None if b == "printed" else "available")] for b in AC.BASES}

    def structural(self, a, fy, stage, h, exclusion, cell):
        """A structural rule (owner, D-6; ACCOUNTING_RULES 'Structural rules') that makes the exclusion take nothing
        out of the cell, whatever was read: -> the reason, or None.
          excluding_earmarks: a request carries no earmarks (CDS / CPF are added by Congress), so every President's
            Budget cell's excluding-earmarks view is its view; nor does a rescission line (D-7 cleanup).
          nondefense: an account whose subfunction (FY N's own Budget Appendix volume) is outside function 050
            (not 05x), with no defense line printed in the cell and no defense_portion tagged, holds no defense.
            Subfunction 999 (spans functions) or none on file doesn't qualify."""
        if exclusion == "excluding_earmarks":
            if stage == TC.REQUEST_STAGE:
                return "a request carries no earmarks"
            return RESCISSION_NO_EARMARKS if h["amount_type"] == "rescission" else None
        rec = AC.attribute_value(self.attrs, a["canonical_account_id"], fy, "budget_subfunction", stage)
        if not rec or not rec["source_document_id"].startswith(f"SRC-BUDGET-APP-FY{fy}"):
            return None
        sub = rec["value"]
        if sub == "999" or sub.startswith("05"):
            return None
        if any(l["component"] in DEFENSE_LINES and l["observations"] for l in (cell or {}).get("lines", [])):
            return None
        if any(c["component_type"] == "defense_portion" for c in self.cell_components(h)):
            return None
        return f"not a defense function (subfunction {sub})"

    @staticmethod
    def same_as_views(views, rule):
        """The cell's views, entry for entry, as the exclusion view a structural rule gives. A view that is itself
        excluded or not computable stays so (the rule fills in nothing)."""
        out = {}
        for basis, entries in views.items():
            out[basis] = []
            for e in entries:
                if e["status"] in VALUED:
                    e = {k: v for k, v in e.items() if k not in ("printed_observation_id", "validation_id")} | {
                        "status": "computed",
                        "reason": f"{rule}: equals the {'figure as printed' if basis == 'printed' else basis + ' view'}"
                                  + (f" (checked: {e['validation_id']})" if e["status"] == "checked"
                                     and e.get("validation_id") else "")}
                    e.setdefault("component_ids", [])
                out[basis].append(dict(e))
        return out

    def account_exclusion(self, a, fy, stage, h, exclusion, views, cell=None):
        """views_excluding_earmarks / views_nondefense of an account cell: each basis's view (account_views) with the
        exclusion's components inside the headline taken out (accounting.excluded_amount). Both together (D-8): where
        a structural rule clears one of them, the other's entries; where it clears both, the views."""
        if len(EXCLUSION_PARTS[exclusion]) > 1:
            rules = {p: self.structural(a, fy, stage, h, p, cell) for p in EXCLUSION_PARTS[exclusion]}
            if all(rules.values()):
                return self.same_as_views(views, "; ".join(rules.values()))
            for p, rule in rules.items():
                if rule:
                    other = EXCLUSION_FIELDS[next(q for q in rules if q != p)]
                    # the rule fills in nothing: a not computable or excluded entry stays as the other's
                    return {b: [dict(e, reason=f"{rule}; {e['reason']}") if e["status"] in VALUED else dict(e)
                                for e in es] for b, es in cell[other].items()}
        else:
            rule = self.structural(a, fy, stage, h, exclusion, cell)
            if rule:
                return self.same_as_views(views, rule)
        if not TC.covered(a["canonical_account_id"], a["subcommittee"], fy, stage):
            return self.untagged(exclusion, why=TC.not_covered_reason(a["canonical_account_id"], fy, stage)
                                 if stage == TC.REQUEST_STAGE else None)
        types = EXCLUSION_TYPES[exclusion]
        ctype = " or ".join(types)
        cs = self.cell_components(h)
        if AC.split(h["amount"], cs)[0] < 0 and h["amount_type"] != "rescission":
            # account_views' negative base: the same not_computable entries
            return {b: [dict(e) for e in es] for b, es in views.items()}
        beside = [c for c in cs if c["component_type"] in types and c["in_headline"] != "TRUE"]
        rec = AC.attribute_value(self.attrs, a["canonical_account_id"], fy, "budget_class", stage)
        cls = rec["value"] if rec else None
        out = {}
        for basis in AC.BASES:
            entries = []
            for timing in ([None] if basis == "printed" else AC.TIMINGS):
                got = AC.excluded_amount(h["amount"], cs, basis, timing, cls, exclusion)
                if got[0] != "value":
                    entries.append({"amount": None, "timing": timing, "status": got[0], "reason": got[1]}
                                   | ({"cause": cause_of(got[1])} if got[0] == "not_computable" else {}))
                    continue
                _, amount, used, note = got
                taken = [c for c in used if c["component_type"] in types]
                view = "the figure as printed" if basis == "printed" else f"the {basis} view"
                if taken:
                    why = (f"{view} less {len(taken)} {ctype} component{'s' if len(taken) > 1 else ''} inside the "
                           f"headline ({AC.money(sum(c['amount'] for c in taken))})")
                elif beside:
                    why = (f"its {ctype} component{'s are' if len(beside) > 1 else ' is'} printed beside the headline, "
                           f"not in it: equals {view}")
                else:
                    why = f"no {ctype} component tagged in this auto-tagged cell: equals {view}"
                if note:
                    why = f"{note}; {why}"
                base = next((e for e in views[basis] if e["timing"] == timing), views[basis][0])
                if basis != "printed" and base["status"] == "checked":
                    why += f" (checked: {base['validation_id']})"
                entries.append({"amount": amount, "timing": timing, "status": "computed", "reason": why,
                                "component_ids": sorted(c["component_id"] for c in used)})
            if len(entries) == 2 and (entries[0]["amount"], entries[0]["status"]) == \
                    (entries[1]["amount"], entries[1]["status"]):
                entries = entries[:1]
            out[basis] = entries
        return out

    # ------------------------------------------------------------------ the grid
    def annotate_row(self, row):
        aid = row["account"]["canonical_account_id"]
        a = self.acc[aid]
        for key, cell in row["cells"].items():
            h = headline_of_cell(cell)
            if h is None or h["observation_id"] not in self.obs:
                continue
            fy, stage = (lambda y, s: (int(y), s))(*key.split("|", 1))
            h = self.obs[h["observation_id"]]
            if h["verification_status"] == "superseded":
                continue
            cs = self.cell_components(h)
            cell["components"] = [component_record(c) for c in sorted(cs, key=lambda c: c["component_id"])]
            if a.get("total_scope") and aid not in self.lone:
                cell.update(self.total_layer(a, fy, stage, h, cell))
            else:
                # an account, or an agency held only as its total (its one line: its own headline and components)
                cell["views"] = self.account_views(a, fy, stage, h)
                for exclusion, field in EXCLUSION_FIELDS.items():
                    cell[field] = self.account_exclusion(a, fy, stage, h, exclusion, cell["views"], cell)
                    if aid in self.lone:
                        self.lone_checks(cell, field)
                for field in ("views",) + tuple(EXCLUSION_FIELDS.values()):
                    for entries in cell[field].values():
                        for e in entries:
                            if e["amount"] is not None:
                                e["adjustments"] = account_adjustments(e, cs)
            for field, ctype in (("earmark", "earmark"), ("defense_portion", "defense_portion")):
                picked = AC.counted([c for c in cs if c["component_type"] == ctype])
                if picked:
                    cell[field] = {"amount": sum(c["amount"] for c in picked),
                                   "component_ids": sorted(c["component_id"] for c in picked)}
        attrs = self.attributes(aid)
        if attrs:
            row["attributes"] = attrs

    def lone_checks(self, cell, field):
        """An agency held only as its total keeps a checked exclusion view where its views entry is checked (a
        printed total measure_reconcile matched) and the exclusion takes nothing out (as before D-6)."""
        for basis, entries in cell[field].items():
            for i, e in enumerate(entries):
                if basis == "printed" or e["status"] != "computed":
                    continue
                base = pick(cell["views"][basis], e["timing"])
                if base["status"] == "checked" and base["amount"] == e["amount"]:
                    entries[i] = dict(e, status="checked", printed_observation_id=base["printed_observation_id"],
                                      validation_id=base["validation_id"])

    def attributes(self, aid):
        out = {}
        for fy in range(AC.FIRST_FY, AC.LAST_FY + 1):
            entry = {}
            for basis in AC.STAGE_BASES:
                recs = {f: self.attrs.get((aid, fy, f, basis)) for f in ATTR_FIELDS}
                recs = {f: r for f, r in recs.items() if r}
                if not recs:
                    continue
                lead = recs.get("budget_class") or recs.get("account_id_code") or next(iter(recs.values()))
                e = {f: r["value"] for f, r in recs.items()}
                e.update({"stage_basis": basis, "source_document_id": lead["source_document_id"],
                          "source_page": page_of(lead["source_location"]),
                          "citations": {f: {"account_attribute_id": r["account_attribute_id"],
                                            "source_document_id": r["source_document_id"],
                                            "source_page": page_of(r["source_location"])} for f, r in recs.items()}})
                if "budget_class" in recs:
                    e["provisional"] = bool(recs["budget_class"].get("provisional"))
                if basis == "enacted":
                    entry.update(e)
                else:
                    entry["request"] = e
            if entry:
                out[str(fy)] = entry
        return out

    def annotate(self, grid):
        """Accounts first, then agency totals, then title and bill totals: a total sums its lines' published
        views (D-6)."""
        rows = grid["rows"] + [t["total"] for t in grid.get("titles", []) if t.get("total")] + \
            ([grid["bill_total"]] if grid.get("bill_total") else [])
        self.rows = {r["account"]["canonical_account_id"]: r for r in rows}
        rank = {"": 0, "agency": 1, "title": 2, "bill": 3}
        for row in sorted(rows, key=lambda r: rank.get(
                self.acc[r["account"]["canonical_account_id"]].get("total_scope") or "", 3)):
            self.annotate_row(row)
        return grid


def status_counts(grid, field="views"):
    """{basis: {timing:status: entries}} over every row, title total and bill total of a subcommittee grid, for
    views or one of EXCLUSION_FIELDS."""
    out = {b: collections.Counter() for b in AC.BASES}
    rows = grid["rows"] + [t["total"] for t in grid.get("titles", []) if t.get("total")] + \
        ([grid["bill_total"]] if grid.get("bill_total") else [])
    for row in rows:
        for cell in row["cells"].values():
            for b, entries in (cell.get(field) or {}).items():
                for e in entries:
                    out[b][(e["timing"] or "-", e["status"])] += 1
    return {b: {f"{t}:{s}": n for (t, s), n in sorted(c.items())} for b, c in out.items()}
