"""
Tag each headline's components (accounting views, phase 1; accounting.py has the schema, reference/
ACCOUNTING_RULES.md the rules). Offline and deterministic: it reads data/staged.json, the committed bill and law
text (data/legislative_text/) and the table lines read for components (reference/review/accounting/
table_lines.csv), and gives the same records every run.

    python scripts/tag_components.py              # what it would record; counts
    python scripts/tag_components.py --write      # write data/staged.json's observation_components
    python scripts/tag_components.py --ci         # fail when data/staged.json is not up to date with this
    python scripts/tag_components.py --report X   # every tag and every untagged component-like phrase as CSV

Four sources, in this order (a later one never records money an earlier one already holds for the headline):

  retagged         lines we already hold that act as components of their headline (the same account, year and
                   stage): a supplemental '(emergency)' line -> designated/emergency; a separate supplemental act's
                   line -> other_law; the NSF defense line -> defense_portion; Medicaid's new advance ->
                   advance_enacted and its prior-year advance -> advance_available; HRSA's congressionally
                   directed spending -> earmark (inside the headline); ONC's 'Evaluation Tap Funding' (recorded as
                   a program_level line) -> transfer_in from section 241; the CJS FY2027 transfers and offsetting
                   collections -> transfer_in / transfer_out / fees_offsets. The line keeps its ID; the component
                   names it (source_observation_id).
  table_line       a line the headline's own table prints inside it, read for this (table_lines.csv): VICTF's
                   'HRSA Administrative expenses' -> trust_fund_limitation
  auto_text        the pilot (Labor-HHS FY2024-FY2026 and CJS FY2026; House, Senate and Enacted): each amount in
                   the headline's own bill or law paragraph, read by the phrase rules below
  carried_forward  Labor-HHS FY2025 Enacted (the full-year CR, P.L. 119-4): the FY2024 Enacted components continue,
                   except what an anomaly naming the account changes (CR_ANOMALIES) or the CR ends (earmarks, sec. 1111)

A component is never fitted to a figure: its amount is printed in the text or the table. Only in_headline -- is
the money inside the printed headline or beside it -- is decided by arithmetic for an auto_text component: the
paragraph's main amount, less the of-which components left out, plus the separate ones counted, must give the
headline; the assignment closest to the default (of-which inside, separate beside) wins, and a component no
assignment fits keeps the default with a note saying so (measure_reconcile and the reviewer see it).
"""

import argparse
import collections
import csv
import itertools
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import accounting as AC  # noqa: E402
import check_legislative_text as CT  # noqa: E402
import legislative_text as L  # noqa: E402

TABLE_LINES = ROOT / "reference" / "review" / "accounting" / "table_lines.csv"
TEXT_STAGES = ("House Reported", "Senate Reported", "Enacted")
PILOT = {("LHHS", fy, st) for fy in (2024, 2025, 2026) for st in TEXT_STAGES} | {("CJS", 2026, st) for st in TEXT_STAGES}
CR_YEAR = ("LHHS", 2025, "Enacted")
# P.L. 119-4 div. A (the FY2025 full-year CR) names these accounts we hold; their FY2024 components do not carry
# forward (each to be read from the CR itself). Sec. 1111 ends every FY2024 earmark (no carry forward for earmarks).
# P.L. 119-4 anomalies that name an account we hold -> (the section, the components its text changes). Every
# other component of the account continues from FY2024 (owner, 2026-10-11): only what an anomaly changes is
# dropped. A selector is a component type, or (type, designation).
CR_ANOMALIES = {
    "ACC-HHS-CMS-MEDICAID": ("P.L. 119-4 div. A sec. 1109(b)(2) (the advance for the first quarter of FY2026)",
                             ("advance_enacted",)),
    "ACC-HHS-ACF-CHILD-SUPPORT": ("P.L. 119-4 div. A sec. 1109(b)(3) (the advance for the first quarter of FY2026)",
                                  ("advance_enacted",)),
    "ACC-HHS-ACF-FOSTER-CARE": ("P.L. 119-4 div. A sec. 1109(b)(4) (the advance for the first quarter of FY2026)",
                                ("advance_enacted",)),
    "ACC-HHS-CMS-HCFAC": ("P.L. 119-4 div. A sec. 1902(b) (level $941,000,000; cap adjustment $630,000,000)",
                          ("trust_fund_limitation", ("designated", "cap_adjustment"))),
    "ACC-HHS-HRSA-HEALTH-SYSTEMS": ("P.L. 119-4 div. A sec. 1904 (OPTN registration fees credited to the account)",
                                    ("fees_offsets",)),
    "ACC-HHS-NIH-CURES": ("P.L. 119-4 div. A sec. 1905 ($127,000,000 for $407,000,000)", (("designated", "other"),)),
    "ACC-HHS-HRSA-PROGRAM-SUPPORT": ("P.L. 119-4 div. A sec. 1908(2) (CDS $890,788,000 -> $0)", ("earmark",)),
    "ACC-HHS-SAMHSA-PROGRAM-SUPPORT": ("P.L. 119-4 div. A sec. 1908(3) (CDS $72,090,000 -> $0)", ("earmark",)),
    "ACC-HHS-ACF-CFSP": ("P.L. 119-4 div. A sec. 1908(4) (CDS $40,011,000 -> $0)", ("earmark",)),
}
CR_EARMARKS = "P.L. 119-4 div. A sec. 1111: FY2024 earmark language has no legal effect for FY2025"

# ------------------------------------------------------------------------------------------------ phrase rules

EARMARK = re.compile(r"Community Project Funding|Congressionally Directed Spending|projects,? (?:and in the amounts,? )?"
                     r"specified in the table|projects financing the construction and renovation", re.I)
EMERGENCY = re.compile(r"designated by the Congress as (?:being )?for an emergency requirement", re.I)
ADVANCE_BEFORE = re.compile(r"first quarter of fiscal year \d{4},?\s*$", re.I)
ADVANCE_AFTER = re.compile(r"^\s*,?\s*(?:to remain available until expended,? )?(?:which )?shall become available on "
                           r"October 1", re.I)
TRANSFER = re.compile(r"derived by transfer|available under section 241|from (?:amounts|funds) made available under"
                      r"|from (?:amounts|funds) made ava[\w ]*$", re.I)
FROM_MADE_AVAILABLE = re.compile(r"from (?:amounts|funds) made ava\w*(?:\s+under)?", re.I)
THIS_HEADING = re.compile(r"from (?:amounts|funds) made available (?:under|in) this (?:heading|Act)", re.I)
# 'to be transferred (in appropriate part) from the Federal Hospital Insurance Trust Fund and the Federal
# Supplementary Medical Insurance Trust Fund' (HCFAC, Medicare hearings and appeals); the phrase window may end
# inside the fund's name
TRUST_TRANSFER = re.compile(r"transferred (?:in appropriate part )?from(?: the)?(?: Fed| [^$;:]{0,80}Trust Funds?|\s*$)",
                            re.I)
CAP_ADJUSTMENT = re.compile(r"is additional new budget authority specified for purposes of section 251\(b\)\(2\)\(C\)",
                            re.I)
CURES = re.compile(r"section 1001\(b\)\(4\) of the 21st Century Cures Act", re.I)
PRIF = re.compile(r"from the 10-year Pediatric Research Initiative Fund", re.I)
TO_OIG = re.compile(r"transferred to and merged with the appropriation for the\W{0,3}Office of Inspector General", re.I)
# phrases that look like components but are not (owner's list, 2026-10-11): never tagged, never reported
NOT_COMPONENT = [
    ("set-aside", re.compile(r"shall be for Indian tribes|amounts required to be reserved|set[- ]aside", re.I), "both"),
    ("sub-allocation", re.compile(r"(?:not|no) less than\s*$|of (?:such|that|the) amounts?[^$]{0,60}?:\s*\(\d+\)\s*$", re.I),
     "before"),
    ("program table", re.compile(r"for the purposes and in the amounts specified in the table", re.I), "after"),
    ("sub-allocation", THIS_HEADING, "after"),
    ("cap-adjustment base", re.compile(r"is provided to meet the terms of section 251\(b\)\(2\)\(C\)\(ii\)", re.I),
     "after"),
]
NOT_TO_EXCEED = re.compile(r"not to exceed\s*$", re.I)
TRUST_FUND = re.compile(r"trust fund", re.I)
# a phrase that looks like a component though no rule above tags it (reported, never tagged)
LOOKS_LIKE = re.compile(r"transfer|trust fund|designated|rescind|offsetting|collections|fees|advance|"
                        r"Prevention and Public Health Fund|Nonrecurring|in addition|derived from|section 241|"
                        r"first quarter|October 1|emergency|limitation", re.I)
NUMBER = re.compile(r"\$[\d,]+(?:\.\d+)?")
SEPARATE = re.compile(r"(?:\bin addition\b|together with|first quarter of fiscal year|and for such purposes)", re.I)


CLAUSE = re.compile(r"[:;]|\bProvided\b")
SUB_ALLOCATION = re.compile(r"of (?:the )?(?:amounts?|funds)\b[^$]*\bsection 241\b", re.I)
# SAMHSA's 'in addition to amounts provided herein, the following amounts shall be available under section 241 of
# the PHS Act: (1) $79,200,000 ...; and (2) $2,000,000 ...': each numbered item is a transfer_in beside the main amount
LIST_241 = re.compile(r"following amounts shall be available under section 241", re.I)
LIST_ITEM = re.compile(r"^[;:,\s]*(?:and\s+)?\(\d+\)\s*$")


def list_241(x, open_list):
    """-> (in the list, still open) for one amount: a numbered item of a 'following amounts shall be available
    under section 241' list."""
    before, _ = CT.split_phrase(x)
    ms = list(NUMBER.finditer(before))
    since = before[ms[-1].end():] if ms else before
    if LIST_241.search(since):
        return True, True
    seg_b, _ = segments(x)
    if open_list and LIST_ITEM.match(seg_b):
        return True, True
    return False, False


def segments(a):
    """The words that belong to this amount alone: its own clause before it (from the last ':', ';' or 'Provided'
    after the amount before it) and the words after it up to the next amount."""
    before, after = CT.split_phrase(a)
    ms = list(NUMBER.finditer(before))
    seg_b = before[ms[-1].end():] if ms else before
    cuts = list(CLAUSE.finditer(seg_b))
    if cuts:
        seg_b = seg_b[cuts[-1].start():]
    m = NUMBER.search(after)
    seg_a = after[:m.start()] if m else after
    return seg_b, seg_a


def classify(a):
    """-> (component_type, designation, transfer_from, rule) or None for one amount of a paragraph."""
    seg_b, seg_a = segments(a)
    both = seg_b + " [$] " + seg_a
    if EARMARK.search(both):
        return "earmark", "", "", "earmark: " + EARMARK.search(both).group(0)
    if EMERGENCY.search(seg_a):
        return "designated", "emergency", "", "designated by the Congress as being for an emergency requirement"
    if ADVANCE_BEFORE.search(seg_b) or ADVANCE_AFTER.search(seg_a):
        return "advance_enacted", "", "", ("for the first quarter of fiscal year" if ADVANCE_BEFORE.search(seg_b)
                                           else "shall become available on October 1")
    if NOT_TO_EXCEED.search(seg_b) and TRUST_FUND.search(seg_a + seg_b):
        return "trust_fund_limitation", "", "", "not to exceed ... from the ... Trust Fund"
    if TRUST_TRANSFER.search(seg_a):
        return "trust_fund_limitation", "", "", "transferred from the ... Trust Fund(s)"
    if CAP_ADJUSTMENT.search(seg_a):
        return ("designated", "cap_adjustment", "",
                "additional new budget authority specified for purposes of section 251(b)(2)(C)")
    if CURES.search(seg_b):
        return "designated", "other", "", "21st Century Cures (NIH Innovation Account, sec. 1001(b)(4))"
    if PRIF.search(seg_a):
        return "transfer_in", "", "10-year Pediatric Research Initiative Fund (PRIF)", PRIF.search(seg_a).group(0)
    if TO_OIG.search(seg_a):
        return "transfer_out", "", "", "transferred to and merged with the appropriation for the Office of Inspector General"
    t = TRANSFER.search(both)
    if t and THIS_HEADING.search(seg_a):
        return None                     # part of this heading's own amounts, not a transfer
    if t and SUB_ALLOCATION.search(seg_b):
        return None                     # a part of an amount from section 241, already tagged with it
    if t:
        rest = after_text(a)
        if "241" in t.group(0) or (t.group(0).lower().startswith("from") and re.match(r"\W*section 241", rest)):
            src = "section 241 of the PHS Act (evaluation funds)"
        elif t.group(0).lower().startswith("from"):
            src = ("the Prevention and Public Health Fund (section 4002 of the PPACA)"
                   if re.match(r"\W*section 4002|\W*the Prevention and Public Health Fund", rest, re.I)
                   else "not named in the phrase")
        else:
            m = re.search(r"derived by transfer from (?:the )?([^,;:$]+)", both, re.I)
            src = m.group(1).strip() if m else "not named in the phrase"
        return "transfer_in", "", src, t.group(0)
    return None


def after_text(a):
    """The words right after a 'from amounts made available under' phrase, read on into the next amount's window
    when this one ends (the transfer's source)."""
    _, after = CT.split_phrase(a)
    m = FROM_MADE_AVAILABLE.search(after)
    rest = after[m.end():] if m else ""
    nxt = a.get("_next")
    if nxt and len(rest) < 40:
        b, _ = CT.split_phrase(nxt)
        k = b.find(after[-30:].strip()) if after.strip() else -1
        if k >= 0:
            rest = rest + b[k + len(after[-30:].strip()):]
    return rest


def not_a_component(a):
    """-> the owner's 'not a component' kind for a phrase, or None."""
    seg_b, seg_a = segments(a)
    for kind, rx, where_ in NOT_COMPONENT:
        if (where_ in ("before", "both") and rx.search(seg_b)) or (where_ in ("after", "both") and rx.search(seg_a)):
            return kind
    return None


def relation(a, main):
    """How an amount sits to the paragraph's main amount: 'main' (it is the main amount), 'separate' (beside it:
    'in addition', 'together with', an advance) or 'of_which' (part of it)."""
    if main is not None and a["phrase"] == main["phrase"] and a["amount"] == main["amount"]:
        return "main"
    seg_b, _ = segments(a)
    return "separate" if SEPARATE.search(seg_b) else "of_which"


def in_headline_by_arithmetic(headline, main, known, tagged):
    """known: the components already held for the headline (retagged lines, table lines), each fixed inside or
    beside it, and either part of the paragraph's main amount or not (unknown, so both are tried); tagged: [(the
    auto_text component, relation)]. -> a tuple of booleans (inside the headline?) for tagged: the assignment
    that turns the paragraph's main amount into the headline with the fewest changes from the default (of-which
    and main inside, separate beside); None when none does, or two do equally well."""
    if len(tagged) > 12:
        return None
    if main is None:
        # no main amount (every amount is an of-which, limitation, transfer or advance): nothing is part of a main
        # amount, so each component is beside it, and the main amount is 0
        main, tagged = {"amount": 0}, [(c, "separate") for c, _ in tagged]
    sums = {0}
    for c in known:
        amt = abs(c["amount"])
        sums = sums | {s + (amt if c["in_headline"] == "TRUE" else -amt) for s in sums}
    default = [r != "separate" for _, r in tagged]
    best, ties = None, 0
    for pattern in itertools.product((True, False), repeat=len(tagged)):
        total = main["amount"]
        for inside, (c, r) in zip(pattern, tagged):
            if r in ("of_which", "main") and not inside:
                total -= abs(c["amount"])
            if r == "separate" and inside:
                total += abs(c["amount"])
        if headline - total not in sums:
            continue
        flips = sum(p != d for p, d in zip(pattern, default))
        if best is None or flips < best[0]:
            best, ties = (flips, pattern), 1
        elif flips == best[0]:
            ties += 1
    if best is None or ties > 1:
        return None
    return best[1]


# ------------------------------------------------------------------------------------------------ sources

def headline_of(data):
    """{(account, fiscal_year, stage): [headline observations, not superseded]}"""
    out = collections.defaultdict(list)
    for o in data["observations"]:
        if (not o.get("component") and o["amount_type"] == "budget authority"
                and o["verification_status"] != "superseded"):
            out[(o["canonical_account_id"], int(o["fiscal_year"]), o["stage"])].append(o)
    return out


def pick_headline(heads, doc):
    """The headline a line belongs to: the one from the line's own document, else the only one."""
    same = [h for h in heads if h["source_document_id"] == doc]
    if len(same) == 1:
        return same[0]
    return heads[0] if len(heads) == 1 else None


def printed(o):
    m = re.search(r"printed as '([^']+)'", o.get("source_table_or_section") or "")
    return m.group(1) if m else (o.get("source_table_or_section") or "")[:160]


def retag(data, heads, report):
    out = []
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    for o in data["observations"]:
        if o["verification_status"] == "superseded" or acc[o["canonical_account_id"]].get("total_scope"):
            continue
        a, fy, st = o["canonical_account_id"], int(o["fiscal_year"]), o["stage"]
        comp, at = o.get("component") or "", o["amount_type"]
        spec = None
        if at == "supplemental" and comp == "supplemental_act":
            spec = ("other_law", "", "", "a separate supplemental appropriations act's line (supplemental_act)")
        elif at == "supplemental" and not comp and AC_EMERGENCY_LABEL.search(o.get("source_table_or_section") or ""):
            spec = ("designated", "emergency", "", "the table's '(emergency)' line")
        elif comp == "defense" and at == "budget authority":
            spec = ("defense_portion", "", "", "the table's 'Defense function' line (defense)")
        elif at == "advance" and not comp:
            spec = ("advance_enacted", "", "", "the table's new advance line (enacted in this bill, available the "
                                               "next fiscal year)")
        elif at == "prior_year_advance" and not comp:
            spec = ("advance_available", "", "", "the table's 'Less appropriations provided in prior years' line "
                                                 "(an earlier advance, available this fiscal year)")
        elif comp == "congressionally_directed_spending":
            spec = ("earmark", "", "", "the table's Community Project Funding / Congressionally Directed Spending "
                                       "line (inside the headline)")
        elif comp == "program_level" and a == "ACC-HHS-OS-ONC":
            spec = ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)",
                    "the table's 'Evaluation Tap Funding' line (recorded as program_level)")
        elif at == "transfer" and acc[a]["subcommittee"] == "CJS" and fy == 2027:
            if o["amount"] >= 0:
                spec = ("transfer_in", "", o.get("transfer_link_account_id") or printed(o), "the table's '(by transfer)' line")
            else:
                spec = ("transfer_out", "", "", "the table's transfer-out line")
        elif at == "offsetting_collection" and acc[a]["subcommittee"] == "CJS" and fy == 2027:
            spec = ("fees_offsets", "", "", "the table's offsetting fee collections line")
        if spec is None:
            continue
        if o["amount"] == 0:
            report["retag_zero"].append(o["observation_id"])
            continue
        if comp == "congressionally_directed_spending":
            h = next((x for x in heads[(a, fy, st)] if x["observation_id"] == o.get("headline_observation_id")), None)
        else:
            h = pick_headline(heads[(a, fy, st)], o["source_document_id"])
        if h is None:
            report["retag_no_headline"].append(f"{o['observation_id']} ({a} FY{fy} {st}, {at} {comp or ''})")
            continue
        t, d, frm, rule = spec
        amount = abs(o["amount"])
        if t in AC.NEGATIVE_TYPES:
            amount = -amount
        out.append({"observation_id": h["observation_id"], "component_type": t, "designation": d,
                    "transfer_from": frm, "amount": amount,
                    "in_headline": "TRUE" if t == "earmark" else "FALSE",
                    "source_document_id": o["source_document_id"],
                    "source_location": f"p.{o.get('source_page') or '?'}",
                    "quoted_text": f"'{printed(o)}' {o['amount'] // 1000:,} (thousands; {o['observation_id']})",
                    "tag_method": "retagged", "tag_rule": rule, "source_observation_id": o["observation_id"],
                    "carried_from_component_id": "",
                    "notes": "inside the headline: the CDS line is printed as part of the account's total"
                             if t == "earmark" else "a line printed beside the headline; never inside it"})
    return out


AC_EMERGENCY_LABEL = re.compile(r"\(emergency\)", re.I)


def table_lines(data, heads):
    out = []
    if not TABLE_LINES.exists():
        return out
    obs = {o["observation_id"]: o for o in data["observations"]}
    with open(TABLE_LINES) as f:
        for r in csv.DictReader(f):
            o = obs[r["observation_id"]]
            out.append({"observation_id": o["observation_id"], "component_type": "trust_fund_limitation",
                        "designation": "", "transfer_from": "", "amount": int(r["amount"]), "in_headline": "TRUE",
                        "source_document_id": r["source_document_id"], "source_location": f"p.{r['page']}",
                        "quoted_text": r["printed_line"] + " | " + r["printed_total"],
                        "tag_method": "table_line", "tag_rule": "the table's 'HRSA Administrative expenses' line, "
                        "printed inside 'Total, Vaccine Injury Compensation Trust Fund' (" + r["read_from"] + ")",
                        "source_observation_id": "", "carried_from_component_id": "",
                        "notes": "inside the headline: the table's total adds this line to the claims line"})
    return out


def autotag(data, heads, held, report):
    """The pilot: each headline's own paragraph, amount by amount."""
    texts, exempt = CT.load_texts(L.TEXT_DIR), CT.load_exempt(L.TEXT_DIR)
    outcomes = CT.check(data, texts, exempt)
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    former = collections.defaultdict(list)
    for h in data.get("historical_names_tab", []):
        former[h["canonical_account_id"]].append(h["former_name"])
    by_acc = collections.defaultdict(list)
    for o in data["observations"]:
        by_acc[o["canonical_account_id"]].append(o)
    refs = {(r["subcommittee"], int(r["fiscal_year"]), r["stage"]): r for r in data["bill_report_refs"]}
    out = []
    for oid, res in sorted(outcomes.items()):
        if (res["subcommittee"], res["fiscal_year"], res["stage"]) not in PILOT:
            continue
        report["pilot_headlines"] += 1
        para = res.get("paragraph")
        if para is None:
            report["pilot_no_paragraph"].append(f"{oid} ({res['name']} FY{res['fiscal_year']} {res['stage']}): "
                                                f"{res['result']}")
            continue
        o, a = obs[oid], acc[res["account"]]
        doc = res["document"]
        text = texts[doc]
        division = (refs[(res["subcommittee"], res["fiscal_year"], res["stage"])].get("division") or "").strip()
        paras = [p for p in text["paragraphs"] if not division or not p["division"] or p["division"] == division]
        main, _ = CT.main_amount(para)
        if main is None and res["result"] == "ambiguous":
            report["pilot_no_paragraph"].append(f"{oid} ({res['name']} FY{res['fiscal_year']} {res['stage']}): "
                                                "no main amount in the paragraph")
        tagged = []
        open_list = False
        own = [x["amount"] for x in CT.appropriating(para)] + ([main["amount"]] if main else [])
        for i, x in enumerate(para["amounts"]):
            x = dict(x, _next=para["amounts"][i + 1] if i + 1 < len(para["amounts"]) else None)
            item, open_list = list_241(x, open_list)
            if item:
                tagged.append((x, ("transfer_in", "", "section 241 of the PHS Act (evaluation funds)",
                                   "the following amounts shall be available under section 241 (a numbered item)"),
                               "separate", CT.where(para)))
                continue
            cl = classify(x)
            if cl is None:
                seg_b, seg_a = segments(x)
                kind = not_a_component(x) or ("the headline's own appropriation" if x["amount"] in own else None)
                if kind and LOOKS_LIKE.search(seg_b + " " + seg_a):
                    report["not_a_component"][kind] += 1
                elif LOOKS_LIKE.search(seg_b + " " + seg_a):
                    report["untagged"].append({"observation_id": oid, "document": doc, "where": CT.where(para),
                                               "amount": x["amount"], "phrase": x["phrase"]})
                continue
            tagged.append((x, cl, relation(x, main), CT.where(para)))
        names = CT.account_names(a, by_acc[a["canonical_account_id"]], former)
        for amt, w in CT.provision_amounts(a, names, paras):
            p = next(p for p in paras if CT.where(p) == w)
            if not re.match(re.escape((a.get("title") or "").upper()) + r"\b", p["title"]):
                continue                # a general provision of another title (Education's sec. 311 'Congressionally
                                        # Directed Spending' is no heading of HRSA's)
            x = next(x for x in p["amounts"] if x["amount"] == amt)
            cl = classify(x)
            if cl is None:
                report["untagged"].append({"observation_id": oid, "document": doc, "where": w, "amount": amt,
                                           "phrase": x["phrase"] + " [a general provision's additional amount; "
                                                                   "no type rule fits]"})
                continue
            tagged.append((x, cl, "separate", w))
        comps = []
        for x, (t, d, frm, rule), rel, w in tagged:
            amount = -x["amount"] if t in AC.NEGATIVE_TYPES else x["amount"]
            if any((h["component_type"], h["amount"]) == (t, amount) for h in held[oid]):
                report["auto_already_held"].append(f"{oid} {t} {amount:,}")
                continue
            comps.append(({"observation_id": oid, "component_type": t, "designation": d, "transfer_from": frm,
                           "amount": amount, "in_headline": "", "source_document_id": doc, "source_location": w,
                           "quoted_text": x["phrase"], "tag_method": "auto_text", "tag_rule": rule,
                           "source_observation_id": "", "carried_from_component_id": "", "notes": ""}, rel))
        if not comps:
            continue
        # in_headline: the main amount less the of-which parts left out plus the separate parts counted gives the
        # headline. Components already held (retagged, table lines) are part of that arithmetic too.
        pattern = in_headline_by_arithmetic(o["amount"], main, held[oid], comps)
        ap = [x["amount"] for x in CT.appropriating(para)]
        if pattern is None and len(ap) > 1:
            # the paragraph appropriates more than one amount (GDM's 'for ... cybersecurity threats, $108,983,000'):
            # their sum is the paragraph's figure
            main = {"amount": sum(ap)}
            pattern = in_headline_by_arithmetic(o["amount"], main, held[oid], comps)
        if pattern is not None:
            for p, (c, rel) in zip(pattern, comps):
                c["in_headline"] = "TRUE" if p else "FALSE"
                c["notes"] = ("in_headline by arithmetic: the paragraph's amount "
                              + (AC.money(main["amount"]) if main else "(none: every amount is an of-which, "
                                 "limitation, transfer or advance)")
                              + f" and its components give the headline {AC.money(o['amount'])}")
        else:
            for c, rel in comps:
                c["in_headline"] = "FALSE" if rel == "separate" else "TRUE"
                c["notes"] = ("in_headline by the default (" + ("beside: stated in addition to the main amount"
                              if rel == "separate" else "inside: part of the main amount") + "); not confirmed by "
                              "arithmetic -- no single assignment turns the paragraph into the headline "
                              f"{AC.money(o['amount'])}")
                report["auto_default"].append(f"{oid} {c['component_type']} {AC.money(c['amount'])}")
        # the paragraph's main amount is itself a component (HCFAC: all of it transferred from the trust funds):
        # each of-which component on the same side of the headline is within it
        outer = next((c for c, rel in comps if rel == "main"), None)
        if outer is not None:
            for c, rel in comps:
                if rel == "of_which" and c["in_headline"] == outer["in_headline"] and abs(c["amount"]) <= abs(outer["amount"]):
                    c["within_component_id"] = ("KEY", key(outer))
        out += [c for c, _ in comps]
    return out


def carry_forward(data, heads, records, report):
    """FY2025 Enacted (Labor-HHS): the FY2024 Enacted components, continued."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    out = []
    by_obs = collections.defaultdict(list)
    for c in records:
        by_obs[c["observation_id"]].append(c)
    for c in records:
        o = obs[c["observation_id"]]
        a = o["canonical_account_id"]
        if not (int(o["fiscal_year"]) == 2024 and o["stage"] == "Enacted" and a.startswith("ACC-HHS-")):
            continue
        h = pick_headline(heads[(a, 2025, "Enacted")], None) if heads[(a, 2025, "Enacted")] else None
        if h is None:
            report["carry_no_headline"].append(f"{c['component_id']} ({a})")
            continue
        if a in CR_ANOMALIES and AC._selected(c, CR_ANOMALIES[a][1]):
            report["carry_anomaly"].append(f"{c['component_id']} {c['component_type']}"
                                           f"{'/' + c['designation'] if c.get('designation') else ''} "
                                           f"{AC.money(c['amount'])} ({a}): {CR_ANOMALIES[a][0]}")
            continue
        if c["component_type"] == "earmark":
            report["carry_anomaly"].append(f"{c['component_id']} earmark ({a}): {CR_EARMARKS}")
            continue
        if any(x["component_type"] == c["component_type"] for x in by_obs[h["observation_id"]]):
            report["carry_already_held"].append(f"{c['component_id']} -> {h['observation_id']}")
            continue
        out.append(dict(c, observation_id=h["observation_id"], tag_method="carried_forward",
                        tag_rule="FY2025 full-year CR (P.L. 119-4 div. A sec. 1101: FY2024 levels, authority and "
                                 "conditions); no anomaly names the account",
                        carried_from_component_id=c["component_id"], source_observation_id="",
                        notes=c.get("notes", "") + " | carried forward from FY2024 Enacted "
                                                   f"({c['observation_id']}); in_headline as there"))
    return out


# ------------------------------------------------------------------------------------------------ IDs

KEY = ("observation_id", "component_type", "designation", "source_document_id", "source_location",
       "source_observation_id", "carried_from_component_id", "amount")


def key(c):
    return tuple(c.get(k) for k in KEY)


def number(cid):
    return int(cid.rsplit("-", 1)[1])


def assign_ids(data, proposed, obs):
    """Keep the ID of every record whose identity is unchanged; give new ones the next number per subcommittee.
    A record no longer produced is retired: its ID goes to observation_components_retired, never reused."""
    old = {key(c): c for c in data.get("observation_components", [])}
    used = {c["component_id"] for c in data.get("observation_components", [])} | set(
        data.get("observation_components_retired", []))
    nxt = {"CJS": 1 + max([number(i) for i in used if not i.startswith("CMP-LHHS-")] or [0]),
           "LHHS": 1 + max([number(i) for i in used if i.startswith("CMP-LHHS-")] or [0])}
    out = []
    for c in proposed:
        k = key(c)
        if k in old:
            c = dict(c, component_id=old[k]["component_id"])
        else:
            sc = "LHHS" if obs[c["observation_id"]]["observation_id"].startswith("OBS-LHHS-") else "CJS"
            cid = f"CMP-LHHS-{nxt[sc]:04d}" if sc == "LHHS" else f"CMP-{nxt[sc]:04d}"
            nxt[sc] += 1
            c = dict(c, component_id=cid)
        out.append({k2: c.get(k2, "") for k2 in AC.COMPONENT_COLUMNS})
    keep = {c["component_id"] for c in out}
    retired = sorted(set(data.get("observation_components_retired", []))
                     | {c["component_id"] for c in data.get("observation_components", []) if c["component_id"] not in keep})
    return out, retired


def build(data):
    """-> (records, retired IDs, report)"""
    report = collections.defaultdict(list)
    report["not_a_component"] = collections.Counter()
    report["pilot_headlines"] = 0
    heads = headline_of(data)
    obs = {o["observation_id"]: o for o in data["observations"]}
    first = retag(data, heads, report)
    first += table_lines(data, heads)
    held = collections.defaultdict(list)
    for c in first:
        held[c["observation_id"]].append(c)
    auto = autotag(data, heads, held, report)
    records = first + auto
    # IDs for everything but the carried components first, so a carried one can name what it continues
    records, _ = assign_ids(data, records, obs)
    ids = {key(c): c["component_id"] for c in records}
    for c in records:
        if isinstance(c.get("within_component_id"), tuple):
            c["within_component_id"] = ids[c["within_component_id"][1]]
    carried = carry_forward(data, heads, records, report)
    records, retired = assign_ids(data, records + carried, obs)
    # a carried component within another names the carried copy of that one (or nothing, when it wasn't carried)
    copy_of = {(c["carried_from_component_id"], c["observation_id"]): c["component_id"] for c in records
               if c["carried_from_component_id"]}
    for c in records:
        if c["tag_method"] == "carried_forward" and c.get("within_component_id"):
            c["within_component_id"] = copy_of.get((c["within_component_id"], c["observation_id"]), "")
    records.sort(key=lambda c: (not c["component_id"].startswith("CMP-LHHS-"), number(c["component_id"])))
    return records, retired, report


def summarize(records, data, report):
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    by = collections.Counter()
    for c in records:
        o = obs[c["observation_id"]]
        by[(acc[o["canonical_account_id"]]["subcommittee"], int(o["fiscal_year"]), c["component_type"],
            c["tag_method"])] += 1
    lines = ["## Components", "", f"{len(records)} records: " + ", ".join(
        f"{m} {sum(1 for c in records if c['tag_method'] == m)}" for m in AC.TAG_METHODS), ""]
    lines += ["| subcommittee | FY | type | retagged | table_line | auto_text | carried_forward |", "|---" * 7 + "|"]
    for k in sorted({k[:3] for k in by}):
        lines.append(f"| {k[0]} | {k[1]} | {k[2]} | " + " | ".join(str(by[k + (m,)]) for m in AC.TAG_METHODS) + " |")
    lines += ["", f"Pilot headlines read: {report['pilot_headlines']}; no paragraph or no single paragraph: "
              f"{len(report['pilot_no_paragraph'])}; auto tags already held from a line: "
              f"{len(report['auto_already_held'])}; auto tags with in_headline by default (not by arithmetic): "
              f"{len(report['auto_default'])}; component-like phrases no rule tagged: {len(report['untagged'])}; "
              "not a component (owner's list): " + (", ".join(f"{k} {n}" for k, n in sorted(report["not_a_component"].items()))
                                                    or "none"),
              f"Re-tag: zero lines skipped {len(report['retag_zero'])}; lines with no headline in their cell "
              f"{len(report['retag_no_headline'])}",
              f"FY2025 CR carry-forward: carried {sum(1 for c in records if c['tag_method'] == 'carried_forward')}; "
              f"not carried (anomaly or sec. 1111) {len(report['carry_anomaly'])}; already held "
              f"{len(report['carry_already_held'])}; no FY2025 headline {len(report['carry_no_headline'])}", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    data = json.loads(L.STAGED.read_text())
    records, retired, report = build(data)
    errs = AC.component_errors(records, {o["observation_id"]: o for o in data["observations"]},
                               {d["document_id"] for d in data["source_docs"]})
    text = summarize(records, data, report)
    print(text)
    if a.ci and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    for k in ("retag_no_headline", "carry_anomaly", "pilot_no_paragraph", "auto_default"):
        for x in report[k]:
            print(f"  {k}: {x}")
    if errs:
        print("ERRORS:\n  " + "\n  ".join(errs[:40]))
        return 1
    stale = (records != data.get("observation_components", [])
             or retired != data.get("observation_components_retired", []))
    if a.report:
        with open(a.report, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["kind"] + AC.COMPONENT_COLUMNS)
            for c in records:
                w.writerow(["component"] + [c[k] for k in AC.COMPONENT_COLUMNS])
            for u in report["untagged"]:
                w.writerow(["untagged", "", u["observation_id"], "", "", "", u["amount"], "", u["document"], u["where"],
                            u["phrase"]])
    if a.write and stale:
        data["observation_components"] = records
        data["observation_components_retired"] = retired
        L.STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")
    if a.ci and stale:
        print("data/staged.json is not up to date with the component tagging -- run "
              "python scripts/tag_components.py --write and commit data/staged.json")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
