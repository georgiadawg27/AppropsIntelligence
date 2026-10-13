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

A total (agency, title or bill) gets a view only where measure_reconcile holds a printed total for it that matches
(status checked); every other basis is not_computable, "not checked", with accounts_behind (its accounts with a
headline in the cell). Totals are never summed here.

Every row gets attributes.<FY> from account_attributes (the enacted basis, with each attribute's citation; the
request basis under "request" where the President's Budget has its own value).

The arithmetic is accounting.py's (the code measure_reconcile uses), and "checked" is measure_reconcile.run()'s own
outcome for the printed view line. Nothing here changes or adds a figure.
"""

import collections
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
        auto = (a["subcommittee"], fy, stage) in TC.AUTO_CELLS
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
                         "reason": "no component the view uses is tagged for this cell (outside the auto-tagged "
                                   "cells), so its components are not known"}
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

    def total_views(self, a, fy, stage, h):
        mh = self.member_heads(a, fy, stage, h)
        behind = len(mh)
        found = {}           # (basis, timing) -> entry
        mixed = {}           # basis -> why a matching printed total gives no timing
        for o, out in self.printed_checks(a["canonical_account_id"], fy, stage):
            if not out or out["result"] != "pass":
                continue
            vid = "VAL-MRV-" + M.suffix(o["observation_id"])
            if vid not in self.records:
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
        views = {"printed": [{"amount": h["amount"], "timing": None, "status": "checked",
                              "reason": "the figure as printed", "printed_observation_id": h["observation_id"]}]}
        for basis in AC.BASES[1:]:
            nc = {"amount": None, "status": "not_computable", "accounts_behind": behind,
                  "reason": mixed.get(basis) or f"not checked: no printed {basis} total that measure_reconcile "
                                                  f"matches ({behind} accounts behind it)"}
            avail = found.get((basis, "available")) or dict(nc, timing="available")
            enacted = found.get((basis, "enacted")) or dict(nc, timing="enacted")
            same = (avail["amount"], avail["status"]) == (enacted["amount"], enacted["status"])
            views[basis] = [avail] if same else [avail, enacted]
        return views

    # ------------------------------------------------------------------ excluding earmarks, nondefense (UI-0b)
    def untagged(self, exclusion, behind=None):
        """The entries of a cell whose stage the tagger has not read (outside tag_components.AUTO_CELLS): untagged is
        never 'none tagged'."""
        e = {"amount": None, "status": "not_computable", "reason": UNTAGGED[exclusion]}
        if behind is not None:
            e["accounts_behind"] = behind
        return {b: [dict(e, timing=None if b == "printed" else "available")] for b in AC.BASES}

    def account_exclusion(self, a, fy, stage, h, exclusion, views):
        """views_excluding_earmarks / views_nondefense of an account cell: each basis's view (account_views) with the
        exclusion's components inside the headline taken out (accounting.excluded_amount)."""
        if (a["subcommittee"], fy, stage) not in TC.AUTO_CELLS:
            return self.untagged(exclusion)
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

    def total_exclusion(self, a, fy, stage, h, exclusion, views):
        """A total's views_excluding_earmarks / views_nondefense: never summed. A basis keeps its checked view (a
        printed total measure_reconcile matched) only where the cell is auto-tagged and neither the total nor any of
        its accounts has a component of the exclusion's type tagged, so the printed total is the view; every other
        basis is not checked, with accounts_behind."""
        mh = self.member_heads(a, fy, stage, h)
        behind = len(mh)
        if (a["subcommittee"], fy, stage) not in TC.AUTO_CELLS:
            return self.untagged(exclusion, behind)
        ctype = AC.EXCLUSIONS[exclusion]
        tagged = [m for m, x in [(a["canonical_account_id"], h)] + mh
                  if any(c["component_type"] == ctype for c in self.comps.get(x["observation_id"], []))]
        word = EXCLUSION_WORD[exclusion]
        out = {}
        for basis in AC.BASES:
            if tagged:
                why = (f"not checked: {len(tagged)} of its accounts ha{'ve' if len(tagged) > 1 else 's'} {ctype} "
                       f"components tagged, and no printed total {word} that measure_reconcile matches "
                       f"({behind} accounts behind it)")
            else:
                why = (f"not checked: no printed {basis} total that measure_reconcile matches ({behind} accounts "
                       "behind it)")
            nc = {"amount": None, "status": "not_computable", "accounts_behind": behind, "reason": why}
            entries = []
            for e in views[basis]:
                if basis != "printed" and not tagged and e["status"] == "checked":
                    article = "an" if ctype[0] in "aeiou" else "a"
                    entries.append(dict(e, reason=f"{e['reason']}; none of its {behind} accounts has {article} "
                                                  f"{ctype} component tagged (an auto-tagged cell)"))
                else:
                    entries.append(dict(nc, timing=e["timing"]))
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
            total = bool(a.get("total_scope"))
            cell["views"] = (self.total_views if total else self.account_views)(a, fy, stage, h)
            for exclusion, field in EXCLUSION_FIELDS.items():
                cell[field] = (self.total_exclusion if total else self.account_exclusion)(
                    a, fy, stage, h, exclusion, cell["views"])
            cs = self.cell_components(h)
            cell["components"] = [component_record(c) for c in sorted(cs, key=lambda c: c["component_id"])]
            for field, ctype in (("earmark", "earmark"), ("defense_portion", "defense_portion")):
                picked = AC.counted([c for c in cs if c["component_type"] == ctype])
                if picked:
                    cell[field] = {"amount": sum(c["amount"] for c in picked),
                                   "component_ids": sorted(c["component_id"] for c in picked)}
        attrs = self.attributes(aid)
        if attrs:
            row["attributes"] = attrs

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
        rows = grid["rows"] + [t["total"] for t in grid.get("titles", []) if t.get("total")] + \
            ([grid["bill_total"]] if grid.get("bill_total") else [])
        for row in rows:
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
