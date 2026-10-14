"""
site_accounting.py -- the accounting layer of the site data (UI-0): what the page needs to offer the Amount basis
views, the Account type filter and the defense column without computing anything in the browser.

    layer = Layer(data)            # data: data/staged.json, or approps_store.accounting_data(conn) (the same shape)
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

A total (agency, title or bill; owner, D-6) gets, on every basis and timing and in each exclusion field, the sum of
its lines' published entries: an agency total's accounts (measure_reconcile.members); a title or bill total's
accounts plus each agency held only as its total (that total is its one line, read as an account). Lines outside
their life are not behind it; a confirmed absence counts as nothing. Status:
  checked        a printed total measure_reconcile matched equals the sum (as before D-6)
  computed       "sum of N accounts; no printed total to check", or "...; differs from the printed total by $X
                 (VAL-MRC-<cause>)" where measure_reconcile flagged that printed total; accounts the basis leaves out
                 (excluded) are not summed and are counted ("N accounts, M left out by this basis")
  excluded       every line is left out by the basis
  not_computable a line is ("not computable: <account> is not computable (<its reason>)"), or has no figure, or
                 the lines as printed don't add up to the printed total (owner, 2026-10-14: "its N accounts as
                 printed sum to $Y, not the printed $Z: $X is in no account held"; the cells are listed in
                 reference/review/accounting/total_gaps.csv), unless lines the table prints inside the total that
                 we hold beside an account's headline make it equal exactly (owner, D-6 round 2: they count in the
                 sum, each as its basis counts it -- held_lines, line_counts). A printed total measure_reconcile
                 matched still gives its checked view there.
Every total entry but As printed carries accounts_behind (its lines in the cell). As printed stays the printed total.

Every entry with an amount lists adjustments (D-6): what turns the As printed figure into it. An account's: per
component the view takes out (sign -) or adds (+): {component_id, type, label, sign, amount, source_document_id,
source_page, source_location}. A total's: grouped by component type and sign, {type, label, sign, amount,
accounts: [{canonical_account_id, amount, components}]}, then the accounts the basis leaves out (type
left_out_by_basis, their As printed figure) and the held lines it doesn't count separately (type
held_line_not_counted) -- or, where a printed total gives the view without a sum, that printed
total (type printed_total). As printed + the adjustments = the amount, exactly; equal to As printed: an empty list
(unless two adjustments offset, e.g. an advance in and out).

Every row gets attributes.<FY> from account_attributes (the enacted basis, with each attribute's citation; the
request basis under "request" where the President's Budget has its own value).

The arithmetic is accounting.py's (the code measure_reconcile uses), and "checked" is measure_reconcile.run()'s own
outcome for the printed view line. Nothing here changes or adds a figure.
"""

import collections
import itertools
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
# UI-0b: the cell field each exclusion view is published under
EXCLUSION_FIELDS = {"excluding_earmarks": "views_excluding_earmarks", "nondefense": "views_nondefense"}
EXCLUSION_WORD = {"excluding_earmarks": "excluding earmarks", "nondefense": "excluding defense"}
UNTAGGED = {"excluding_earmarks": "earmarks not tagged for this stage",
            "nondefense": "defense not tagged for this stage"}


VALUED = ("checked", "computed")
# the printed defense lines of a cell (Component vocabulary): NSF R&RA's 'Defense function'
DEFENSE_LINES = ("defense", "defense_inside")
STATE_WORDS = {"missing": "document on file, figure not yet entered", "not_collected": "not yet collected",
               "not_enacted": "not yet enacted", "not_reported": "not reported", "no_printed_total": "no printed total"}


# D-6: the words an adjustment uses for a component type
TYPE_LABEL = {"advance_enacted": "advance appropriation enacted in this bill",
              "advance_available": "advance appropriation available this year",
              "designated": "designated amount", "transfer_in": "transfer in",
              "transfer_out": "transfer out", "trust_fund_limitation": "trust-fund limitation",
              "fees_offsets": "fees and offsetting collections", "rescission": "rescission",
              "defense_portion": "defense portion", "other_law": "amount provided by another law",
              "earmark": "earmark (CDS / CPF)", "mandatory_portion": "mandatory portion"}
LEFT_OUT = "left_out_by_basis"
PRINTED_TOTAL = "printed_total"


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
    or a printed zero (ONC's $0 headline has a program level). A confirmed absence holds no observation."""
    line = next((l for l in cell["lines"] if l["amount_type"] == "budget authority" and not l["component"]), None)
    if line is None or line["state"] not in ("value", "not_funded") or not line["observations"]:
        return None
    return line["observations"][0]


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
        if base < 0:
            why = (f"the base is negative ({AC.money(base)}): the components the headline includes exceed it "
                   f"(VAL-MR-{M.suffix(h['observation_id'])}, pending review)")
            return {b: [{"amount": None, "timing": None if b == "printed" else "available",
                         "status": "not_computable", "reason": why}] for b in AC.BASES}
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
                elif not auto and not relevant:
                    e = {"amount": None, "timing": timing, "status": "not_computable",
                         "reason": "no component the view uses is tagged for this cell ("
                                   + TC.not_covered_reason(a["canonical_account_id"], fy, stage)
                                   + "), so its components are not known"}
                else:
                    _, amount, used, note = got
                    e = {"amount": amount, "timing": timing, "status": "computed",
                         "component_ids": sorted(c["component_id"] for c in used)}
                    e.update(self.confirm(printed, basis, timing, amount,
                                          self.emergency_scope([(a["canonical_account_id"], h)], fy, stage)))
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

    def convention(self, headlines):
        """The advance timing a sum of printed headlines keeps: 'both' (no advance tagged on any), 'available',
        'enacted', or None (mixed)."""
        cs = [c for h in headlines for c in self.comps.get(h["observation_id"], [])
              if c["component_type"] in ("advance_enacted", "advance_available")]
        if not cs:
            return "both"
        inside = {c["component_type"] for c in cs if c["in_headline"] == "TRUE"}
        beside = {c["component_type"] for c in cs if c["in_headline"] != "TRUE"}
        if inside <= {"advance_enacted"} and beside <= {"advance_available"}:
            return "enacted"
        if inside <= {"advance_available"} and beside <= {"advance_enacted"}:
            return "available"
        return None

    def printed_found(self, a, fy, stage, h, mh):
        """The total's printed view lines measure_reconcile matched: -> (found {(basis, timing): checked entry},
        mixed {basis: why a matching printed total gives no timing}, failed {(basis, timing): (line, outcome)})."""
        found, mixed, failed = {}, {}, {}
        for o, out in self.printed_checks(a["canonical_account_id"], fy, stage):
            if not out:
                continue
            vid = "VAL-MRV-" + M.suffix(o["observation_id"])
            if out["result"] == "fail":
                for basis, timing in PRINTED_BASIS.get(o["component"], []):
                    for t in ([timing] if timing else AC.TIMINGS):
                        failed.setdefault((basis, t), (o, out))
                continue
            if out["result"] != "pass" or vid not in self.records:
                continue
            if o["component"] == "excluding_emergencies":
                bar = self.emergency_scope([(a["canonical_account_id"], h)] + mh, fy, stage)
                if bar:
                    self.unused.add((o["observation_id"], bar))
                    continue
            for basis, timing in PRINTED_BASIS.get(o["component"], []):
                heads = [h] + [x for _, x in mh]
                if o["component"] == "discretionary":
                    heads = [h] + [x for m, x in mh if (AC.attribute_value(self.attrs, m, fy, "budget_class", stage)
                                                        or {}).get("value") != "mandatory"]
                    designated = any(c["component_type"] == "designated" and c["in_headline"] == "TRUE"
                                     for x in heads for c in self.comps.get(x["observation_id"], []))
                    if basis == "discretionary" and designated:
                        continue        # the printed total counts designated amounts: discretionary_designated
                timings = [timing] if timing else (
                    {"both": ["available", "enacted"], "available": ["available"], "enacted": ["enacted"]}
                    .get(self.convention(heads), []))
                if not timings:
                    mixed.setdefault(basis, f"the printed '{TC.printed(o)}' ({vid}) matches measure_reconcile, but its "
                                            "accounts print advances on different timings, so it is neither the "
                                            "available nor the enacted view")
                for t in timings:
                    found.setdefault((basis, t), {
                        "amount": o["amount"], "timing": t, "status": "checked",
                        "printed_observation_id": o["observation_id"], "validation_id": vid,
                        "reason": f"the printed '{TC.printed(o)}' ({o['source_document_id']} "
                                  f"p.{o.get('source_page') or '?'}), equal to the view measure_reconcile computes"})
        return found, mixed, failed

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

    def behind(self, a, key):
        """The total's lines in one cell: -> (lines [(account, cell)] holding a printed figure, accounts with a
        confirmed absence (nothing), lines with no figure [(account, state)]). A line outside its life, or before it
        existed (no_figure), is not behind the total in that cell."""
        lines, absent, nofig = [], [], []
        for m in self.lines_behind(a):
            row = self.rows.get(m)
            mc = row["cells"].get(key) if row else None
            if mc is None or mc.get("outside_life"):
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

    def total_layer(self, a, fy, stage, h, cell):
        """views, views_excluding_earmarks and views_nondefense of a total: on every basis and timing, the sum of
        its lines' published views (owner, D-6). A total whose own lines don't add up to it as printed (money in no
        account held), or with a line that has no figure, is not computable on every basis; As printed keeps the
        printed total."""
        key = f"{fy}|{stage}"
        self.cell_key = (fy, stage)
        self.cell_head = h
        lines, absent, nofig = self.behind(a, key)
        n = len(lines) + len(absent)
        printed = {"amount": h["amount"], "timing": None, "status": "checked", "reason": "the figure as printed",
                   "printed_observation_id": h["observation_id"], "adjustments": []}
        held = sum(mc["views"]["printed"][0]["amount"] for _, mc in lines)
        bar = None
        if nofig:
            m, st = nofig[0]
            bar = (f"not computable: {self.acc[m]['canonical_name']} is not computable ("
                   + ("its figure is not a budget-authority headline, so it has no published view" if st == "value"
                      else f"no figure: {STATE_WORDS.get(st, st)}") + ")" + (f"; and {len(nofig) - 1} more" if len(nofig) > 1 else ""))
        elif not n:
            bar = "not computable: no account behind it is inside its life in this cell"
        extra = []
        if not bar and n and held != h["amount"]:
            # owner, D-6 round 2: lines the table prints inside the total that we hold beside an account's headline
            # (a supplemental, Medicaid's advance for next year, NSF's request defense line ...) count in the sum
            # when the fewest of them that add up to the difference make it equal the printed total exactly
            beside = held_lines(lines)
            extra = next((list(c) for k in range(1, min(len(beside), 8) + 1)
                          for c in itertools.combinations(beside, k)
                          if sum(x["amount"] for x in c) == h["amount"] - held), [])
            self.gaps.append({"subcommittee": a["subcommittee"], "total": a["canonical_account_id"],
                              "agency": a["agency"], "fiscal_year": fy, "stage": stage, "printed": h["amount"],
                              "printed_observation_id": h["observation_id"],
                              "source_document_id": h["source_document_id"], "source_page": h.get("source_page"),
                              "held": held, "gap": h["amount"] - held, "lines": lines, "beside": beside,
                              "counted": extra})
            if not extra:
                bar = (f"not computable: its {n} accounts as printed sum to {AC.money(held)}, not the printed "
                       f"{AC.money(h['amount'])}: {AC.money(h['amount'] - held)} is in no account held")
        mh = [(m, self.obs[mc["views"]["printed"][0]["printed_observation_id"]]) for m, mc in lines]
        found, mixed, failed = self.printed_found(a, fy, stage, h, mh)
        out = {}
        for field in ("views",) + tuple(EXCLUSION_FIELDS.values()):
            out[field] = {}
            for basis in AC.BASES:
                if field == "views" and basis == "printed":
                    out[field][basis] = [dict(printed)]
                    continue
                entries = []
                for timing in ([None] if basis == "printed" else AC.TIMINGS):
                    if bar:
                        e = self.kept_check(field, basis, timing, lines, found, out, cell, bar) or \
                            {"amount": None, "timing": timing, "status": "not_computable", "reason": bar}
                    else:
                        e = self.total_sum(a, lines, absent, field, basis, timing, found, mixed, failed, out, extra)
                        if e["status"] == "not_computable":
                            e = self.kept_check(field, basis, timing, lines, found, out, cell, e["reason"]) or e
                    e["accounts_behind"] = n
                    entries.append(e)
                if len(entries) == 2 and (entries[0]["amount"], entries[0]["status"]) == \
                        (entries[1]["amount"], entries[1]["status"]):
                    entries = entries[:1]
                out[field][basis] = entries
        return out

    def kept_check(self, field, basis, timing, lines, found, out, cell, bar):
        """A total that cannot be summed (bar: why) keeps the view a printed total measure_reconcile matched, as
        before D-6: views' checked entry; an exclusion view reuses it where no line, nor the total, has a component
        of the exclusion's type and every line's own exclusion entry is known."""
        if basis == "printed":
            return None
        why = "; not summed: " + bar.removeprefix("not computable: ")
        if field == "views":
            hit = found.get((basis, timing))
            if not hit:
                return None
            o = self.obs[hit["printed_observation_id"]]
            effect = hit["amount"] - self.cell_head["amount"]
            # the lines aren't summed: the printed parallel total itself turns As printed into the view
            adj = [{"type": PRINTED_TOTAL, "label": f"the printed '{TC.printed(o)}' total (measure_reconcile matched "
                                                     f"it) less the total as printed",
                    "sign": sign_of(effect), "amount": abs(effect), "printed_observation_id": o["observation_id"],
                    "source_document_id": o["source_document_id"], "source_page": o.get("source_page")}] \
                if effect else []
            return dict(hit, reason=hit["reason"] + why, adjustments=adj)
        exclusion = next(x for x, f in EXCLUSION_FIELDS.items() if f == field)
        ctype = AC.EXCLUSIONS[exclusion]
        base = pick(out["views"][basis], timing)
        if base["status"] != "checked":
            return None
        if any(c["type"] == ctype for c in cell.get("components") or []) or any(
                c["type"] == ctype for _, mc in lines for c in mc.get("components") or []):
            return None
        # owner, #62: reused only when every account behind it is inside coverage
        fy, stage = self.cell_key
        if any(not TC.covered(m, self.acc[m]["subcommittee"], fy, stage) for m, _ in lines):
            return None
        article = "an" if ctype[0] in "aeiou" else "a"
        return dict(base, reason=f"{base['reason'].split('; not summed: ')[0]}; none of its accounts has {article} "
                                 f"{ctype} component tagged{why}")

    def total_sum(self, a, lines, absent, field, basis, timing, found, mixed, failed, out, extra=()):
        """One entry of a total: the sum of its lines' entries for the field, basis and timing, plus the held lines
        printed under them that the total counts (extra), each as its basis counts it (line_counts)."""
        total, used, left = 0, [], []
        for m, mc in lines:
            e = pick(mc[field][basis], timing)
            if e["status"] in VALUED:
                total += e["amount"]
                used.append((m, mc, e))
            elif e["status"] == "excluded":
                left.append((m, mc, e))
            else:
                return {"amount": None, "timing": timing, "status": "not_computable",
                        "reason": f"not computable: {self.acc[m]['canonical_name']} is not computable ({e['reason']})"}
        n = len(lines) + len(absent)
        entry_of = {m: e for m, _, e in used + left}
        line_adj = []
        for x in extra:
            counted, why = line_counts(x, entry_of[x["account"]], field, basis)
            if counted:
                total += x["amount"]
            else:
                line_adj.append(dict(x, why=why))
        if not used and not absent:
            return {"amount": None, "timing": timing, "status": "excluded",
                    "reason": f"every one of its {n} accounts is left out by this basis"}
        head = (f"sum of {n} accounts" + (f", {len(left)} left out by this basis" if left else "")
                + (f" ({len(absent)} with a confirmed absence, counted as nothing)" if absent else ""))
        if extra:
            head += (f", and {len(extra)} line{'s' if len(extra) > 1 else ''} the table prints inside the total, held "
                     "beside an account's headline: " + "; ".join(line_words(x) for x in extra)
                     + (f" ({len(line_adj)} not counted separately on this basis)" if line_adj else ""))
        e = {"amount": total, "timing": timing, "status": "computed"}
        adjustments = grouped_adjustments(used, left, field, basis, timing)
        if line_adj:
            effect = -sum(x["amount"] for x in line_adj)
            adjustments.append({"type": HELD_LINE, "label": "lines printed inside the total, held beside an account's "
                                                            "headline, that this basis doesn't count separately",
                                "sign": sign_of(effect), "amount": abs(effect),
                                "lines": [{k: x[k] for k in ("account", "observation_id", "label", "amount",
                                                             "source_document_id", "source_page", "why")}
                                          for x in line_adj]})
        if field == "views":
            hit = found.get((basis, timing))
            if hit and hit["amount"] == total:
                e = dict(hit, reason=f"{head}; equals {hit['reason']}")
            elif hit:
                e.update(reason=f"{head}; differs from the printed total by {AC.money(hit['amount'] - total)} "
                                f"({hit['validation_id']})", printed_observation_id=hit["printed_observation_id"],
                         validation_id=hit["validation_id"])
            elif (basis, timing) in failed:
                o, res = failed[(basis, timing)]
                cause = (res.get("cause") or "unexplained").upper().replace("_", "-")
                e.update(reason=f"{head}; differs from the printed total by {AC.money(o['amount'] - total)} "
                                f"(VAL-MRC-{cause})", printed_observation_id=o["observation_id"],
                         validation_id="VAL-MRV-" + M.suffix(o["observation_id"]))
            elif basis in mixed:
                e["reason"] = f"{head}; {mixed[basis]}"
            else:
                e["reason"] = f"{head}; no printed total to check"
        else:
            # an exclusion view: checked where the views entry is checked and no line carries a component of the
            # exclusion's type (the printed total is then the view)
            ctype = AC.EXCLUSIONS[next(x for x, f in EXCLUSION_FIELDS.items() if f == field)]
            base = pick(out["views"][basis], timing)
            tagged = any(c["type"] == ctype for _, mc, _ in used + left for c in mc.get("components") or [])
            if basis != "printed" and base["status"] == "checked" and not tagged and base["amount"] == total:
                e = dict(base, reason=f"{base['reason']}; none of its accounts has {'an' if ctype[0] in 'aeiou' else 'a'} "
                                      f"{ctype} component tagged")
            else:
                word = EXCLUSION_WORD[next(x for x, f in EXCLUSION_FIELDS.items() if f == field)]
                e["reason"] = f"{head}; no printed total {word} to check"
        e["adjustments"] = adjustments
        return e

    # ------------------------------------------------------------------ excluding earmarks, nondefense (UI-0b)
    def untagged(self, exclusion, behind=None, why=None):
        """The entries of a cell whose stage the tagger has not read (outside tag_components.coverage(), or a
        President's Budget account whose request language was not found): untagged is never 'none tagged'."""
        e = {"amount": None, "status": "not_computable",
             "reason": UNTAGGED[exclusion] + (f" ({why})" if why else "")}
        if behind is not None:
            e["accounts_behind"] = behind
        return {b: [dict(e, timing=None if b == "printed" else "available")] for b in AC.BASES}

    def structural(self, a, fy, stage, h, exclusion, cell):
        """A structural rule (owner, D-6; ACCOUNTING_RULES 'Structural rules') that makes the exclusion take nothing
        out of the cell, whatever was read: -> the reason, or None.
          excluding_earmarks: a request carries no earmarks (CDS / CPF are added by Congress), so every President's
            Budget cell's excluding-earmarks view is its view.
          nondefense: an account whose subfunction (FY N's own Budget Appendix volume) is outside function 050
            (not 05x), with no defense line printed in the cell and no defense_portion tagged, holds no defense.
            Subfunction 999 (spans functions) or none on file doesn't qualify."""
        if exclusion == "excluding_earmarks":
            return "a request carries no earmarks" if stage == TC.REQUEST_STAGE else None
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
        exclusion's components inside the headline taken out (accounting.excluded_amount)."""
        rule = self.structural(a, fy, stage, h, exclusion, cell)
        if rule:
            return self.same_as_views(views, rule)
        if not TC.covered(a["canonical_account_id"], a["subcommittee"], fy, stage):
            return self.untagged(exclusion, why=TC.not_covered_reason(a["canonical_account_id"], fy, stage)
                                 if stage == TC.REQUEST_STAGE else None)
        ctype = AC.EXCLUSIONS[exclusion]
        cs = self.cell_components(h)
        if AC.split(h["amount"], cs)[0] < 0:          # account_views' negative base: the same not_computable entries
            return {b: [dict(e) for e in es] for b, es in views.items()}
        beside = [c for c in cs if c["component_type"] == ctype and c["in_headline"] != "TRUE"]
        rec = AC.attribute_value(self.attrs, a["canonical_account_id"], fy, "budget_class", stage)
        cls = rec["value"] if rec else None
        out = {}
        for basis in AC.BASES:
            entries = []
            for timing in ([None] if basis == "printed" else AC.TIMINGS):
                got = AC.excluded_amount(h["amount"], cs, basis, timing, cls, exclusion)
                if got[0] != "value":
                    entries.append({"amount": None, "timing": timing, "status": got[0], "reason": got[1]})
                    continue
                _, amount, used, note = got
                taken = [c for c in used if c["component_type"] == ctype]
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
