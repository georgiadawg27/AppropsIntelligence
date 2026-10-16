"""
Tag each headline's components (accounting views, phase 1; accounting.py has the schema, reference/
ACCOUNTING_RULES.md the rules). Offline and deterministic: it reads data/staged/, the committed bill and law
text (data/legislative_text/) and the table lines read for components (reference/review/accounting/
table_lines.csv), and gives the same records every run.

    python scripts/tag_components.py              # what it would record; counts
    python scripts/tag_components.py --write      # write data/staged/'s observation_components
    python scripts/tag_components.py --ci         # fail when data/staged/ is not up to date with this
    python scripts/tag_components.py --report X   # every tag and every untagged component-like phrase as CSV

This is the core: the general rules only. What is true of one subcommittee's documents or accounts lives in its
rule file, reference/rules/<CODE>.py, called at the hook points rules_registry.HOOKS names (phrase, list_item,
past_window, transfer_source, not_component, line, table_line, components, extract, advances, carry_forward).

Which cells are read (coverage(), G-1: from the data, not a list): every (subcommittee, fiscal year, stage) whose
bill or law text is committed (data/legislative_text/, the document its Bill Report Reference names), every
Enacted cell under a full-year CR (Bill Report Reference funding_type full_year_cr: its components continue from
the year before), and each President's Budget account whose request language was read from the Appendix extract
(reference/review/accounting/request_language.json).

Six sources, in this order (a later one never records money an earlier one already holds for the headline):

  retagged         lines we already hold that act as components of their headline (the same account, year and
                   stage): a supplemental '(emergency)' line -> designated/emergency; a separate supplemental act's
                   line -> other_law; the table's new advance -> advance_enacted and its prior-year advance ->
                   advance_available; congressionally directed spending -> earmark (inside the headline); and the
                   rule files' line rules (hook 'line'). The line keeps its ID; the component names it
                   (source_observation_id).
  table_line       a line the headline's own table prints inside it or beside it, read for this (table_lines.csv):
                   the CDS line beside the account line -> earmark; other lines by the rule files (hook 'table_line',
                   which says whether the line is inside the headline); and the rule files' own sources (hook
                   'components')
  mandatory lines  the mandatory part of a mixed account (mandatory_lines.csv, phase 2) -> mandatory_portion
  auto_text        the covered cells: each amount in the headline's own bill or law paragraph, read by the phrase
                   rules below and the rule files' (appendix_text: the President's Budget request language)
  carried_forward  an Enacted cell under a full-year CR: the year before's Enacted components continue, except what
                   an anomaly naming the account changes (hook 'carry_forward') or the CR ends (CR_LAWS)
  advances         the accounts a rule file names as appropriating the next year's first-quarter advance (hook
                   'advances'), every year and stage: the request's advance (advance_lines.csv, appendix_text) and,
                   on every headline, the advance an earlier law appropriated for this year -> advance_available

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
import ids as IDS  # noqa: E402
import legislative_text as L  # noqa: E402
import paths  # noqa: E402
import rules_registry as R  # noqa: E402
import staged_data  # noqa: E402

ACCOUNTING = paths.accounting_dir()
TABLE_LINES = ACCOUNTING / "table_lines.csv"
MANDATORY_LINES = ACCOUNTING / "mandatory_lines.csv"
ADVANCE_LINES = ACCOUNTING / "advance_lines.csv"
# supplemental lines the headline's own table prints inside it (D-2): line -> headline, with the printed arithmetic
INSIDE_LINES = ACCOUNTING / "inside_lines.csv"
TEXT_STAGES = ("House Reported", "Senate Reported", "Enacted")
# The President's Budget (D-2): each held account's proposed language in FY N's Budget Appendix, read by
# reference/review/accounting/request_language.py; an account whose language was read is read for components
# (appendix_text) like an auto-tagged cell, account by account. 'not found' stays outside, with its reason.
REQUEST_LANGUAGE = ACCOUNTING / "request_language.json"
REQUEST_STAGE = "President's Budget"
_REQUEST = {}
_COVERAGE = {}


def request_language():
    """{(account, fiscal_year): the request_language.json record}"""
    if not _REQUEST and REQUEST_LANGUAGE.exists():
        for r in json.loads(REQUEST_LANGUAGE.read_text())["accounts"]:
            _REQUEST[(r["account"], r["fiscal_year"])] = r
    return _REQUEST


def text_cells(data, text_dir=None):
    """Every (subcommittee, fiscal year, stage) whose bill or law text is committed: the document its Bill Report
    Reference names (legislative_text.required_documents) has a text file."""
    text_dir = Path(text_dir or L.TEXT_DIR)
    held = {p.stem for p in text_dir.glob("*.json") if p.name != "exemptions.json"}
    reqs, _ = L.required_documents(data)
    refs = {r["reference_id"]: r for r in data["bill_report_refs"]}
    return {(refs[ref]["subcommittee"], int(refs[ref]["fiscal_year"]), refs[ref]["stage"])
            for doc, r in reqs.items() if doc in held for ref in r["references"]}


def cr_laws(data):
    """{(subcommittee, fiscal_year): the law} for every Enacted cell under a full-year CR (Bill Report Reference
    funding_type full_year_cr): its components continue from the year before (carry_forward)."""
    return {(r["subcommittee"], int(r["fiscal_year"])): r.get("bill_id") or "" for r in data["bill_report_refs"]
            if r["stage"] == "Enacted" and r.get("funding_type") == "full_year_cr"}


def coverage(data=None):
    """-> (auto cells, CR cells): the cells read for components, from the data (G-1). data None: data/staged/
    (paths.staged()), read once."""
    if data is None:
        if "default" not in _COVERAGE:
            _COVERAGE["default"] = coverage(staged_data.load())
        return _COVERAGE["default"]
    cr = {(sc, fy, "Enacted") for sc, fy in cr_laws(data)}
    return text_cells(data) | cr, cr


def auto_cells(data=None):
    return coverage(data)[0]


def cr_cells(data=None):
    return coverage(data)[1]


def covered(account, subcommittee, fiscal_year, stage):
    """Was the account's headline in this cell read for components: an auto-tagged cell, or a President's Budget
    account whose request language was read."""
    if (subcommittee, int(fiscal_year), stage) in auto_cells():
        return True
    if stage == REQUEST_STAGE:
        r = request_language().get((account, int(fiscal_year)))
        return bool(r and r["status"] == "read")
    return False


def not_covered_reason(account, fiscal_year, stage):
    """Why a cell's account was not read for components."""
    if stage == REQUEST_STAGE:
        r = request_language().get((account, int(fiscal_year)))
        if r:
            return f"request language not found: {r['reason']}"
        return "no request language read for the account"
    return "outside the auto-tagged cells"


# A full-year CR, by the law that enacts it (a general rule: it holds for every subcommittee the law covers). The
# year before's Enacted components continue (sec. 1101); every earmark ends (sec. 1111). An anomaly that names an
# account is the account's subcommittee's rule (hook 'carry_forward').
CR_LAWS = {
    "P.L.119-4": {
        "source": "SRC-PLAW-119PUBL4", "label": "P.L. 119-4",
        "continues": "FY2025 full-year CR (P.L. 119-4 div. A sec. 1101: FY2024 levels, authority and conditions)",
        "earmarks_end": "P.L. 119-4 div. A sec. 1111: FY2024 earmark language has no legal effect for FY2025",
    },
}

# ------------------------------------------------------------------------------------------------ phrase rules

EARMARK = re.compile(r"Community Project Funding|Congressionally Directed Spending|projects,? (?:and in the amounts,? )?"
                     r"specified in the table|projects financing the construction and renovation", re.I)
EMERGENCY = re.compile(r"designated by the Congress as (?:being )?for an emergency requirement", re.I)
ADVANCE_BEFORE = re.compile(r"first quarter of fiscal year \d{4},?\s*$", re.I)
ADVANCE_AFTER = re.compile(r"^\s*,?\s*(?:to remain available until expended,? )?(?:which )?shall become available on "
                           r"October 1", re.I)
TRANSFER = re.compile(r"derived by transfer|from (?:amounts|funds) made available under"
                      r"|from (?:amounts|funds) made ava[\w ]*$", re.I)
FROM_MADE_AVAILABLE = re.compile(r"from (?:amounts|funds) made ava\w*(?:\s+under)?", re.I)
THIS_HEADING = re.compile(r"from (?:amounts|funds) made available (?:under|in) this (?:heading|Act)", re.I)
# 'to be transferred (in appropriate part) from the Federal Hospital Insurance Trust Fund and the Federal
# Supplementary Medical Insurance Trust Fund' (HCFAC, Medicare hearings and appeals); the phrase window may end
# inside the fund's name
TRUST_TRANSFER = re.compile(r"transferred (?:in appropriate part )?from(?: the)?(?: Fed| [^$;:]{0,80}Trust Funds?|\s*$)",
                            re.I)
TO_OIG = re.compile(r"transferred to and merged with the appropriation for the\W{0,3}Office of Inspector General", re.I)
# phrases that look like components but are not (owner's list, 2026-10-11): never tagged, never reported. The rule
# files add their own (hook 'not_component').
NOT_COMPONENT = [
    ("set-aside", re.compile(r"shall be for Indian tribes|amounts required to be reserved|set[- ]aside", re.I), "both"),
    ("sub-allocation", re.compile(r"(?:not|no) less than\s*$|of (?:such|that|the) amounts?[^$]{0,60}?:\s*\(\d+\)\s*$", re.I),
     "before"),
    ("program table", re.compile(r"for the purposes and in the amounts specified in the table", re.I), "after"),
    ("sub-allocation", THIS_HEADING, "after"),
]
NOT_TO_EXCEED = re.compile(r"not to exceed\s*$", re.I)
TRUST_FUND = re.compile(r"trust fund", re.I)
# a phrase that looks like a component though no rule tags it (reported for review, never tagged); a rule file adds
# its subcommittee's words (hook 'looks_like')
LOOKS_LIKE = re.compile(r"transfer|trust fund|designated|rescind|offsetting|collections|fees|advance|"
                        r"Nonrecurring|in addition|derived from|first quarter|October 1|emergency|limitation", re.I)


def looks_like(text, ctx):
    if LOOKS_LIKE.search(text):
        return True
    pattern = R.active().data("looks_like", ctx.subcommittee, ctx.account, ctx.fiscal_year, ctx.stage).get("pattern")
    return bool(pattern and re.search(pattern, text, re.I))
NUMBER = re.compile(r"\$[\d,]+(?:\.\d+)?")
SEPARATE = re.compile(r"(?:\bin addition\b|together with|first quarter of fiscal year|and for such purposes)", re.I)


CLAUSE = re.compile(r"[:;]|\bProvided\b")
# the core's phrase rules, by the names a rule file's `overrides` may give
CORE_PHRASE = ("earmark", "emergency", "advance", "trust_fund_limitation", "trust_transfer", "transfer_out_oig",
               "this_heading", "transfer")


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


def phrase_parts(a):
    """-> (the raw words before the amount, since the amount before it; segments())"""
    before = CT.split_phrase(a)[0]
    ms = list(NUMBER.finditer(before))
    return (before[ms[-1].end():] if ms else before,) + segments(a)


def ctx_for(sc, account, fy, stage, para=None):
    return R.Ctx(sc, account, int(fy), stage, para=para, tc=sys.modules[__name__])


def line_of(a, ctx):
    return (f"{ctx.subcommittee} {ctx.account} FY{ctx.fiscal_year} {ctx.stage}: ${a['amount']:,} "
            f"'{a['phrase'][:120]}'")


def core_classify(a):
    """The core's phrase rules -> (name, (type, designation, transfer_from, rule) | None | match)."""
    seg_b, seg_a = segments(a)
    both = seg_b + " [$] " + seg_a
    if EARMARK.search(both):
        return "earmark", ("earmark", "", "", "earmark: " + EARMARK.search(both).group(0))
    if EMERGENCY.search(seg_a):
        return "emergency", ("designated", "emergency", "", "designated by the Congress as being for an emergency "
                             "requirement")
    if ADVANCE_BEFORE.search(seg_b) or ADVANCE_AFTER.search(seg_a):
        return "advance", ("advance_enacted", "", "", ("for the first quarter of fiscal year"
                                                       if ADVANCE_BEFORE.search(seg_b)
                                                       else "shall become available on October 1"))
    if NOT_TO_EXCEED.search(seg_b) and TRUST_FUND.search(seg_a + seg_b):
        return "trust_fund_limitation", ("trust_fund_limitation", "", "", "not to exceed ... from the ... Trust Fund")
    if TRUST_TRANSFER.search(seg_a):
        return "trust_transfer", ("trust_fund_limitation", "", "", "transferred from the ... Trust Fund(s)")
    if TO_OIG.search(seg_a):
        return "transfer_out_oig", ("transfer_out", "", "", "transferred to and merged with the appropriation for "
                                    "the Office of Inspector General")
    t = TRANSFER.search(both)
    if t and THIS_HEADING.search(seg_a):
        return "this_heading", None     # part of this heading's own amounts, not a transfer
    if t:
        return "transfer", t
    return None, None


def classify(a, ctx=None):
    """-> (component_type, designation, transfer_from, rule) or None for one amount of a paragraph: the core's rules
    and the in-scope phrase rules of the rule files (ctx: the cell; None: the core alone). A rule that acts where a
    core rule does, without naming it in `overrides`, or two rules that act: RuleConflict."""
    name, got = core_classify(a)
    if ctx is not None:
        hit = R.active().first("phrase", line_of(a, ctx), ctx, a)
        if hit:
            rule, res = hit
            if name is not None and name not in rule.overrides:
                raise R.RuleConflict(f"{line_of(a, ctx)}: rule {rule.id} and the core rule '{name}' both act on it")
            return None if isinstance(res, R.NotComponent) else res
    if name != "transfer":
        return got
    t = got
    src = R.active().first("transfer_source", line_of(a, ctx), ctx, a, t) if ctx is not None else None
    if src:
        return ("transfer_in", "") + tuple(src[1])
    if t.group(0).lower().startswith("from"):
        return "transfer_in", "", "not named in the phrase", t.group(0)
    seg_b, seg_a = segments(a)
    m = re.search(r"derived by transfer from (?:the )?([^,;:$]+)", seg_b + " [$] " + seg_a, re.I)
    return "transfer_in", "", m.group(1).strip() if m else "not named in the phrase", t.group(0)


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


def not_a_component(a, ctx=None):
    """-> the 'not a component' kind for a phrase (the core's list, then the rule files'), or None."""
    raw_b, seg_b, seg_a = phrase_parts(a)
    core = None
    for kind, rx, where_ in NOT_COMPONENT:
        if ((where_ == "before" and rx.search(raw_b)) or (where_ == "both" and rx.search(seg_b))
                or (where_ in ("after", "both") and rx.search(seg_a))):
            core = kind
            break
    hit = R.active().first("not_component", line_of(a, ctx), ctx, a) if ctx is not None else None
    if hit and core:
        raise R.RuleConflict(f"{line_of(a, ctx)}: rule {hit[0].id} and the core's '{core}' both name it")
    return hit[1] if hit else core




def relation(a, main, headline=None):
    """How an amount sits to the paragraph's main amount: 'main' (it is the main amount), 'separate' (beside it:
    'in addition', 'together with', an advance) or 'of_which' (part of it)."""
    if main is not None and a["phrase"] == main["phrase"] and a["amount"] == main["amount"]:
        return "main"
    seg_b, _ = segments(a)
    if SEPARATE.search(seg_b):
        return "separate"
    # nesting: an amount larger than the main amount cannot be part of it (CDC Buildings and Facilities FY2020 House:
    # '$30,000,000 ...: Provided, That in addition ..., $225,000,000 ... derived by transfer', where the phrase
    # window starts after 'in addition'). An amount equal to the headline is the headline's own money, not beside it.
    if main is not None and a["amount"] > main["amount"] and a["amount"] != headline:
        return "separate"
    return "of_which"


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
    inside_lines = {}
    if INSIDE_LINES.exists():
        with open(INSIDE_LINES) as f:
            inside_lines = {r["line_observation_id"]: r for r in csv.DictReader(f)}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    for o in data["observations"]:
        if o["verification_status"] == "superseded" or acc[o["canonical_account_id"]].get("total_scope"):
            continue
        a, fy, st = o["canonical_account_id"], int(o["fiscal_year"]), o["stage"]
        comp, at = o.get("component") or "", o["amount_type"]
        spec = None
        if at == "supplemental" and comp == "supplemental_act" and AC_EMERGENCY_LABEL.search(
                o.get("source_table_or_section") or ""):
            # another act's line the table labels '(emergency)' (ACF FY2023's P.L. 117-180 CR funding, D-2)
            spec = ("designated", "emergency", "", "the table's '(emergency)' line of another act (supplemental_act)")
        elif at == "supplemental" and comp == "supplemental_act":
            spec = ("other_law", "", "", "a separate supplemental appropriations act's line (supplemental_act)")
        elif at == "supplemental" and not comp and AC_EMERGENCY_LABEL.search(o.get("source_table_or_section") or ""):
            spec = ("designated", "emergency", "", "the table's '(emergency)' line")
        elif at == "advance" and not comp:
            spec = ("advance_enacted", "", "", "the table's new advance line (enacted in this bill, available the "
                                               "next fiscal year)")
        elif at == "prior_year_advance" and not comp:
            spec = ("advance_available", "", "", "the table's 'Less appropriations provided in prior years' line "
                                                 "(an earlier advance, available this fiscal year)")
        elif comp == "congressionally_directed_spending":
            spec = ("earmark", "", "", "the table's Community Project Funding / Congressionally Directed Spending "
                                       "line (inside the headline)")
        ctx = ctx_for(acc[a]["subcommittee"], a, fy, st)
        hit = R.active().first("line", f"{o['observation_id']} ({a} FY{fy} {st}, {at} {comp})", ctx, o)
        opts = {}
        if hit:
            if spec is not None:
                raise R.RuleConflict(f"{o['observation_id']}: rule {hit[0].id} and the core's line rule both act on it")
            spec = tuple(hit[1][:4])
            opts = hit[1][4] if len(hit[1]) > 4 else {}
        if spec is None:
            continue
        if o["amount"] == 0:
            report["retag_zero"].append(o["observation_id"])
            continue
        named = o.get("headline_observation_id") if (comp == "congressionally_directed_spending"
                                                     or opts.get("names_headline")) else \
            inside_lines.get(o["observation_id"], {}).get("headline_observation_id")
        t_inside = bool(named)
        if t_inside:
            # a CDS line names its headline; an '(emergency)' or other law's line the table prints inside the
            # account's total is named in inside_lines.csv (D-2: ACF FY2023 'Formula Grants (emergency)' inside
            # 'Total, LIHEAP'; SAMHSA Mental Health FY2023's P.L. 117-180 Suicide Lifeline line)
            h = next((x for x in heads[(a, fy, st)] if x["observation_id"] == named), None)
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
                    "in_headline": "TRUE" if t_inside else "FALSE",
                    "source_document_id": o["source_document_id"],
                    "source_location": f"p.{o.get('source_page') or '?'}",
                    "quoted_text": f"'{printed(o)}' {o['amount'] // 1000:,} (thousands; {o['observation_id']})",
                    "tag_method": "retagged", "tag_rule": rule, "source_observation_id": o["observation_id"],
                    "carried_from_component_id": "",
                    "notes": "inside the headline: the CDS line is printed as part of the account's total"
                             if t == "earmark" else
                             opts["inside_note"].format(headline=h["observation_id"]) if opts.get("inside_note") else
                             f"inside the headline: the table prints the line inside it ({h['observation_id']})"
                             if t_inside else "a line printed beside the headline; never inside it"})
    return out


AC_EMERGENCY_LABEL = re.compile(r"\(emergency\)", re.I)


CDS_LINES = ("Congressionally Directed Spending", "Community Project Funding/Congressionally Directed Spending",
             "Community Project Funding / Congressionally Directed Spending")


def table_lines(data, heads, report):
    out = []
    if not TABLE_LINES.exists():
        return out
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    with open(TABLE_LINES) as f:
        for r in csv.DictReader(f):
            o = obs[r["observation_id"]]
            if r["line"] in CDS_LINES:
                # the table's congressionally directed spending line beside the account line, which the earmark
                # rule's headline (account line + CDS) counts inside (owner 2026-10-11; D-2 item 5a)
                out.append({"observation_id": o["observation_id"], "component_type": "earmark", "designation": "",
                            "transfer_from": "", "amount": int(r["amount"]), "in_headline": "TRUE",
                            "source_document_id": r["source_document_id"], "source_location": f"p.{r['page']}",
                            "quoted_text": r["printed_line"] + " | " + r["printed_total"], "tag_method": "table_line",
                            "tag_rule": f"the table's '{r['line']}' line ({r['read_from']})",
                            "source_observation_id": "", "carried_from_component_id": "",
                            "notes": "inside the headline: the earmark rule's headline is the account line plus this "
                                     "line (owner 2026-10-11)"})
                continue
            a = o["canonical_account_id"]
            ctx = ctx_for(acc[a]["subcommittee"], a, o["fiscal_year"], o["stage"])
            hit = R.active().first("table_line", f"table_lines.csv {o['observation_id']} '{r['line']}'", ctx, r)
            if hit is None:
                # a line of a kind neither the core nor a rule reads: for review, never forced into a rule
                report["unknown_table_line"].append(f"{o['observation_id']} ({a} FY{o['fiscal_year']} {o['stage']}): "
                                                    f"'{r['line']}' {r['amount']}")
                continue
            x = hit[1]
            # a rule says where the line sits (default: inside the total the table prints it in) and what a transfer
            # line is transferred from
            out.append({"observation_id": o["observation_id"], "component_type": x["component_type"],
                        "designation": "", "transfer_from": x.get("transfer_from", ""), "amount": int(r["amount"]),
                        "in_headline": x.get("in_headline", "TRUE"),
                        "source_document_id": r["source_document_id"], "source_location": f"p.{r['page']}",
                        "quoted_text": r["printed_line"] + " | " + r["printed_total"],
                        "tag_method": "table_line", "tag_rule": x["tag_rule"],
                        "source_observation_id": "", "carried_from_component_id": "", "notes": x["notes"]})
    return out


def rule_components(data, heads, report):
    """The rule files' own component sources (hook 'components'), subcommittee by subcommittee."""
    out = []
    for sc in sorted({a["subcommittee"] for a in data["accounts"]}):
        for rule in R.active().rules("components", sc):
            out += rule.fn(data, heads, report, ctx_for(sc, None, 0, None))
    return out


def mandatory_lines(data, heads):
    """The mandatory part of a mixed account printed inside its headline (mandatory_lines.csv, phase 2): PSSF's
    section 436 amount (bill and law text; the request's Appendix language) and EEOICP (the report states it is
    mandatory). A mandatory_portion inside the headline; the discretionary view leaves it out."""
    out = []
    if not MANDATORY_LINES.exists():
        return out
    with open(MANDATORY_LINES) as f:
        for r in csv.DictReader(f):
            out.append({"observation_id": r["observation_id"], "component_type": r["component_type"],
                        "designation": "", "transfer_from": "", "amount": int(r["amount"]), "in_headline": "TRUE",
                        "source_document_id": r["source_document_id"], "source_location": r["source_location"],
                        "quoted_text": r["quoted_text"], "tag_method": r["tag_method"], "tag_rule": r["tag_rule"],
                        "source_observation_id": "", "carried_from_component_id": "", "notes": r["notes"]})
    return out



def total_only_outcomes(data, texts, outcomes, report):
    """An agency held only as its total (no account lines; a rule file names it, hook 'extract'): the total's
    headline is read from the agency's one appropriation paragraph, as an account's would be (owner, D-1 ruling 2).
    -> {observation_id: outcome}, in the shape check_legislative_text.check gives, for the auto-tagged cells where
    the document has exactly one paragraph under the agency's heading."""
    cell_doc = {(r["subcommittee"], r["fiscal_year"], r["stage"]): r["document"] for r in outcomes.values()
                if r.get("document")}
    refs = {(r["subcommittee"], int(r["fiscal_year"]), r["stage"]): r for r in data["bill_report_refs"]}
    accounts = data["accounts"]
    cells = auto_cells(data)
    out = {}
    for a in accounts:
        if not R.active().rules("extract", a["subcommittee"], a["canonical_account_id"]) or any(
                x["subcommittee"] == a["subcommittee"] and x["agency"] == a["agency"] and not x.get("total_scope")
                for x in accounts):
            continue
        for o in data["observations"]:
            if (o["canonical_account_id"] != a["canonical_account_id"] or o["amount_type"] != "budget authority"
                    or o.get("component") or o["verification_status"] == "superseded"):
                continue
            cell = (a["subcommittee"], int(o["fiscal_year"]), o["stage"])
            doc = cell_doc.get(cell)
            if cell not in cells or doc not in texts:
                continue
            division = (refs[cell].get("division") or "").strip()
            agency = a["agency"].upper()
            paras = [p for p in texts[doc]["paragraphs"] if (not division or not p["division"]
                     or p["division"] == division) and any(h.upper() == agency for h in p["heading_path"])
                     and not any("SEC." in h.upper() or "GENERAL PROVISION" in h.upper() for h in p["heading_path"])]
            if len(paras) != 1:
                report["pilot_no_paragraph"].append(f"{o['observation_id']} ({a['canonical_name']} FY{cell[1]} "
                                                    f"{cell[2]}): {len(paras)} paragraphs under '{a['agency']}'")
                continue
            out[o["observation_id"]] = {"observation_id": o["observation_id"], "subcommittee": cell[0],
                                        "fiscal_year": cell[1], "stage": cell[2], "account": a["canonical_account_id"],
                                        "name": a["canonical_name"], "document": doc, "paragraph": paras[0],
                                        "result": "match"}
    return out


def autotag(data, heads, held, report):
    """The auto-tagged cells (coverage()): each headline's own paragraph, amount by amount."""
    texts, exempt = CT.load_texts(L.TEXT_DIR), CT.load_exempt(L.TEXT_DIR)
    outcomes = CT.check(data, texts, exempt)
    outcomes.update(total_only_outcomes(data, texts, outcomes, report))
    cells = auto_cells(data)
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
        if (res["subcommittee"], res["fiscal_year"], res["stage"]) not in cells:
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
        ctx = ctx_for(res["subcommittee"], res["account"], res["fiscal_year"], res["stage"], para)
        main, _ = CT.main_amount(para, ctx)
        if main is None and res["result"] == "ambiguous":
            report["pilot_no_paragraph"].append(f"{oid} ({res['name']} FY{res['fiscal_year']} {res['stage']}): "
                                                "no main amount in the paragraph")
        tagged = read_amounts(oid, doc, para, main, o["amount"], CT.where(para), report, ctx)
        names = CT.account_names(a, by_acc[a["canonical_account_id"]], former)
        own_gp = 0                      # general provisions' additional amounts that are the headline's own money
        for amt, w in CT.provision_amounts(a, names, paras):
            p = next(p for p in paras if CT.where(p) == w)
            if not re.match(re.escape((a.get("title") or "").upper()) + r"\b", p["title"]):
                continue                # a general provision of another title (Education's sec. 311 'Congressionally
                                        # Directed Spending' is no heading of HRSA's)
            x = next(x for x in p["amounts"] if x["amount"] == amt)
            pctx = ctx_for(res["subcommittee"], res["account"], res["fiscal_year"], res["stage"], p)
            cl = classify(x, pctx)
            if cl is None and main is not None and main["amount"] + amt == o["amount"]:
                # the headline prints the paragraph's amount plus this one (OS-GDM FY2023 Senate: $565,394,000 and
                # sec. 243's additional $350,000,000 = $915,394,000): the headline's own appropriation (D-1)
                if looks_like(" ".join(segments(x)), pctx):
                    report["not_a_component"]["the headline's own appropriation"] += 1
                own_gp += amt
                continue
            if cl is None:
                report["untagged"].append({"observation_id": oid, "document": doc, "where": w, "amount": amt,
                                           "phrase": x["phrase"] + " [a general provision's additional amount; "
                                                                   "no type rule fits]"})
                continue
            tagged.append((x, cl, "separate", w))
        comps = place(oid, o, para, main, tagged, held, report, doc, own_gp, "auto_text", ctx)
        if comps:
            out += comps
    return out


def request_text(data, heads, held, report):
    """The President's Budget (D-2): each read account's proposed language from FY N's Budget Appendix
    (request_language.json), amount by amount, by the same phrase rules (appendix_text). The advances of the
    accounts a rule file names (hook 'advances') are left to advances() (advance_lines.csv and the retagged lines:
    the existing request records are kept). General provisions are not read for the request."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    out = []
    for (account, fy), r in sorted(request_language().items()):
        sc = acc[account]["subcommittee"]
        if r["status"] != "read":
            report["request"][(sc, fy, "not found")] += 1
            continue
        oid = r["headline_observation_id"]
        o = obs.get(oid)
        if o is None or o["verification_status"] == "superseded" or o["stage"] != REQUEST_STAGE:
            report["request_stale"].append(f"{account} FY{fy}: {oid} is no longer the request headline -- rerun "
                                           "reference/review/accounting/request_language.py")
            continue
        para, doc = r["paragraph"], r["document_id"]
        ctx = ctx_for(sc, account, fy, REQUEST_STAGE, para)
        main, _ = CT.main_amount(para, ctx)
        loc = f"{para['location']['pages']}, proposed appropriations language under '{para['account_heading']}'"
        tagged = read_amounts(oid, doc, para, main, o["amount"], loc, report, ctx)
        if R.active().rules("advances", sc, account, fy, REQUEST_STAGE):
            for x in [t for t in tagged if t[1][0] in ("advance_enacted", "advance_available")]:
                report["request_advance_kept"].append(f"{oid} {x[1][0]} {AC.money(x[0]['amount'])}")
            tagged = [t for t in tagged if t[1][0] not in ("advance_enacted", "advance_available")]
        comps = place(oid, o, para, main, tagged, held, report, doc, 0, "appendix_text", ctx)
        report["request"][(sc, fy, "read" if comps else "read, no match")] += 1
        out += comps
    return out


def read_amounts(oid, doc, para, main, headline, loc, report, ctx):
    """The phrase rules over one paragraph's amounts -> [(amount, (type, designation, from, rule), relation, where)];
    untagged component-like phrases go to the report (for review: never forced into the nearest rule)."""
    tagged = []
    ctx.state = {}
    own = [x["amount"] for x in CT.appropriating(para, ctx)] + ([main["amount"]] if main else [])
    for i, x in enumerate(para["amounts"]):
        x = dict(x, _next=para["amounts"][i + 1] if i + 1 < len(para["amounts"]) else None)
        item = R.active().first("list_item", line_of(x, ctx), ctx, x)
        if item:
            core = core_classify(x)[0]
            if core is not None:
                raise R.RuleConflict(f"{line_of(x, ctx)}: rule {item[0].id} and the core rule '{core}' both act on it")
            tagged.append((x, item[1], "separate", loc))
            continue
        cl = classify(x, ctx)
        if cl is None:
            past = R.active().first("past_window", line_of(x, ctx), ctx, x)
            cl = past[1] if past else None
        if cl is None:
            seg_b, seg_a = segments(x)
            kind = not_a_component(x, ctx) or ("the headline's own appropriation" if x["amount"] in own else None)
            if kind and looks_like(seg_b + " " + seg_a, ctx):
                report["not_a_component"][kind] += 1
            elif looks_like(seg_b + " " + seg_a, ctx):
                report["untagged"].append({"observation_id": oid, "document": doc, "where": loc,
                                           "amount": x["amount"], "phrase": x["phrase"]})
            continue
        tagged.append((x, cl, relation(x, main, headline), loc))
    return tagged


def place(oid, o, para, main, tagged, held, report, doc, own_gp=0, method="auto_text", ctx=None):
    """Component records for the tagged amounts of one headline's paragraph, with in_headline by arithmetic (the
    paragraph's main amount less the of-which parts left out plus the separate parts counted gives the headline;
    components already held count too) or, where no single assignment does, by the default with a note."""
    comps = []
    for x, (t, d, frm, rule), rel, w in tagged:
        amount = -x["amount"] if t in AC.NEGATIVE_TYPES else x["amount"]
        if any((h["component_type"], h["amount"]) == (t, amount) for h in held[oid]):
            report["auto_already_held"].append(f"{oid} {t} {amount:,}")
            continue
        comps.append(({"observation_id": oid, "component_type": t, "designation": d, "transfer_from": frm,
                       "amount": amount, "in_headline": "", "source_document_id": doc, "source_location": w,
                       "quoted_text": x["phrase"], "tag_method": method, "tag_rule": rule,
                       "source_observation_id": "", "carried_from_component_id": "", "notes": ""}, rel))
    if not comps:
        return []
    # in_headline: the main amount less the of-which parts left out plus the separate parts counted gives the
    # headline. Components already held (retagged, table lines) are part of that arithmetic too.
    gp_note = ""
    if own_gp and main is not None:
        # the arithmetic counts the general provision's additional amount with the paragraph's (owner, D-1 7a)
        main = {"amount": main["amount"] + own_gp}
        gp_note = (f" (with {AC.money(own_gp)} a general provision appropriates under the account's heading, the "
                   "headline's own appropriation)")
    pattern = in_headline_by_arithmetic(o["amount"], main, held[oid], comps)
    ap = [x["amount"] for x in CT.appropriating(para, ctx)]
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
                          + gp_note + f" and its components give the headline {AC.money(o['amount'])}")
    else:
        for c, rel in comps:
            c["in_headline"] = "FALSE" if rel == "separate" else "TRUE"
            c["notes"] = ("in_headline by the default (" + ("beside: stated in addition to the main amount"
                          if rel == "separate" else "inside: part of the main amount") + "); not confirmed by "
                          "arithmetic -- no single assignment turns the paragraph into the headline "
                          f"{AC.money(o['amount'])}")
            report["auto_default"].append(f"{oid} {c['component_type']} {AC.money(c['amount'])}")
        # the base is never negative (ACCOUNTING_RULES, Arithmetic): a default may not put inside the headline more
        # than it holds. Largest first, a default-inside component that would leave a negative base is beside
        # (D-2: CMS Program Management FY2026 request, whose Appendix limitation $3,518,440,000 exceeds the report's
        # request figure $3,464,391,000)
        held_in = sum(abs(h["amount"]) for h in held[oid] if h["in_headline"] == "TRUE")
        for c, rel in sorted(comps, key=lambda x: -abs(x[0]["amount"])):
            inside = held_in + sum(abs(x["amount"]) for x, _ in comps if x["in_headline"] == "TRUE")
            if c["in_headline"] == "TRUE" and inside > o["amount"]:
                c["in_headline"] = "FALSE"
                c["notes"] = (f"beside the headline: its amount {AC.money(c['amount'])} cannot be inside the headline "
                              f"{AC.money(o['amount'])} with the components already inside it (the base would be "
                              "negative); not confirmed by arithmetic -- no single assignment turns the paragraph "
                              "into the headline")
    # the paragraph's main amount is itself a component (HCFAC: all of it transferred from the trust funds):
    # each of-which component on the same side of the headline is within it
    outer = next((c for c, rel in comps if rel == "main"), None)
    if outer is not None:
        for c, rel in comps:
            if rel == "of_which" and c["in_headline"] == outer["in_headline"] and abs(c["amount"]) <= abs(outer["amount"]):
                c["within_component_id"] = ("KEY", key(outer))
    return [c for c, _ in comps]



def advances(data, heads, records, report):
    """The advances of the accounts a rule file names (hook 'advances': Labor-HHS's Child Support, Foster Care and
    Medicaid) in every year and stage (owner, component backfill):
    the request's advance (advance_lines.csv: FY N's Appendix language) -> advance_enacted on the President's Budget
    headline; and on every headline, the advance an earlier law appropriated for this year's first quarter ->
    advance_available: FY N-1's law text (the advance its paragraph appropriates, read by the advance phrase rule;
    the FY2025 full-year CR's anomaly for FY2026), FY2017 from the FY2017 Appendix's bracketed enacted text. A
    headline that already holds one (Medicaid's 'Less appropriations provided in prior years' line) keeps it.
    in_headline for an advance_available: inside only when the headline less the components inside it, less the
    advance, is the paragraph's (or the request language's) current-year amount; beside otherwise."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    by_obs = collections.defaultdict(list)
    for c in records:
        by_obs[c["observation_id"]].append(c)
    texts, exempt = CT.load_texts(L.TEXT_DIR), CT.load_exempt(L.TEXT_DIR)
    outcomes = CT.check(data, texts, exempt)
    out, current = [], {}
    if ADVANCE_LINES.exists():
        with open(ADVANCE_LINES) as f:
            for r in csv.DictReader(f):
                if r["request_current_year"]:
                    current[r["observation_id"]] = int(r["request_current_year"])
                if r["component_type"] == "advance_enacted" and any(
                        c["component_type"] == "advance_enacted" for c in by_obs[r["observation_id"]]):
                    continue
                out.append({"observation_id": r["observation_id"], "component_type": r["component_type"],
                            "designation": "", "transfer_from": "", "amount": int(r["amount"]),
                            "in_headline": r["in_headline"], "source_document_id": r["source_document_id"],
                            "source_location": r["source_location"], "quoted_text": r["quoted_text"],
                            "tag_method": r.get("tag_method") or "appendix_text", "tag_rule": r["tag_rule"],
                            "source_observation_id": "",
                            "carried_from_component_id": "", "notes": r["notes"]})

    acc_sc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    first_fy = {}
    for a in sorted(acc_sc):
        rs = R.active().rules("advances", acc_sc[a], a)
        if rs:
            first_fy[a] = min((r.data or {}).get("first_available_fy", 0) for r in rs)

    def law_advance(account, fy):
        """The advance FY fy's law appropriated for the next year's first quarter -> component fields, or None."""
        for h in heads[(account, fy, "Enacted")]:
            stated = [c for c in by_obs[h["observation_id"]] if c["component_type"] == "advance_enacted"
                      and c["tag_method"] in ("auto_text", "cr_anomaly")]
            if stated:
                return stated[0]
            res = outcomes.get(h["observation_id"])
            para = res and res.get("paragraph")
            for x in (para or {}).get("amounts", []):
                cl = classify(x, ctx_for(acc_sc[account], account, fy, "Enacted", para))
                if cl and cl[0] == "advance_enacted":
                    return {"amount": x["amount"], "source_document_id": res["document"],
                            "source_location": CT.where(para), "quoted_text": x["phrase"]}
        return None

    for account in first_fy:
        for (acc, fy, st), hs in sorted(heads.items()):
            if acc != account or fy < first_fy[account]:
                continue
            for h in hs:
                if any(c["component_type"] == "advance_available" for c in by_obs[h["observation_id"]]):
                    continue
                src = law_advance(account, fy - 1)
                if src is None:
                    report["advance_not_read"].append(f"{h['observation_id']} ({account} FY{fy} {st}): no FY{fy - 1} "
                                                      "law advance in the text we hold")
                    continue
                out.append({"observation_id": h["observation_id"], "component_type": "advance_available",
                            "designation": "", "transfer_from": "", "amount": src["amount"], "in_headline": "",
                            "source_document_id": src["source_document_id"],
                            "source_location": src["source_location"], "quoted_text": src["quoted_text"],
                            "tag_method": "auto_text",   # read from the law's text (the CR's anomaly for FY2026)
                            "tag_rule": f"the FY{fy - 1} law's advance for the first quarter of fiscal year {fy}, "
                                        "available this year", "source_observation_id": "",
                            "carried_from_component_id": "", "notes": ""})
    for c in out:
        if c["component_type"] != "advance_available":
            continue
        o = obs[c["observation_id"]]
        cur = current.get(o["observation_id"])
        if cur is None:
            res = outcomes.get(o["observation_id"])
            main = CT.main_amount(res["paragraph"], ctx_for(res["subcommittee"], res["account"], res["fiscal_year"],
                                                            res["stage"]))[0] if res and res.get("paragraph") else None
            cur = main["amount"] if main else None
        inside = sum(x["amount"] for x in by_obs[o["observation_id"]] + [y for y in out if y is not c and
                     y["observation_id"] == c["observation_id"]] if x["in_headline"] == "TRUE")
        base = o["amount"] - inside
        if cur is not None and base - c["amount"] == cur:
            c["in_headline"], c["notes"] = "TRUE", (f"inside the headline by arithmetic: the headline less what is "
                                                    f"inside it is the current-year amount {AC.money(cur)} plus "
                                                    "this advance")
        elif cur is not None and base == cur:
            c["in_headline"], c["notes"] = "FALSE", (f"beside the headline by arithmetic: the headline less what is "
                                                     f"inside it is the current-year amount {AC.money(cur)}")
        else:
            c["in_headline"], c["notes"] = "FALSE", ("beside the headline by default: an earlier law's advance; the "
                                                     "headline is not the current-year amount plus it")
            report["advance_default"].append(f"{o['observation_id']} advance_available {AC.money(c['amount'])}")
    return out


def carry_forward(data, heads, records, report):
    """Every Enacted cell under a full-year CR (cr_laws(): Bill Report Reference funding_type full_year_cr): the year
    before's Enacted components, continued; where an anomaly changes one (a rule file's hook 'carry_forward'), what
    the anomaly's text states in its place. report["cr_list"] lists each one: dropped, replaced (old -> new) or
    carried forward. A CR whose law CR_LAWS doesn't know carries nothing and is reported."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    acc_sc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    laws = {}
    for (sc, fy), law in cr_laws(data).items():
        if law in CR_LAWS:
            laws[(sc, fy)] = CR_LAWS[law]
        else:
            report["cr_unknown_law"].append(f"{sc} FY{fy} Enacted: full-year CR {law or '(no bill_id)'} -- not in "
                                            "tag_components.CR_LAWS; nothing carried")
    out = []
    by_obs = collections.defaultdict(list)
    for c in records:
        by_obs[c["observation_id"]].append(c)
    replaced = collections.defaultdict(list)
    for c in records:
        o = obs[c["observation_id"]]
        a = o["canonical_account_id"]
        sc, prior = acc_sc[a], int(o["fiscal_year"])
        fy = prior + 1
        if not (o["stage"] == "Enacted" and (sc, fy) in laws):
            continue
        law = laws[(sc, fy)]
        what = f"{c['component_id']} {c['component_type']}{'/' + c['designation'] if c.get('designation') else ''} " \
               f"{AC.money(c['amount'])} ({a})"
        h = pick_headline(heads[(a, fy, "Enacted")], None) if heads[(a, fy, "Enacted")] else None
        if h is None:
            report["carry_no_headline"].append(what)
            report["cr_list"].append((f"not carried: no FY{fy} Enacted headline", what, ""))
            continue
        ctx = ctx_for(sc, a, fy, "Enacted")
        hit = R.active().first("carry_forward", what, ctx, c)
        act = hit[1] if hit else {}
        if c["component_type"] == "earmark" and hit and "cr_earmarks" not in hit[0].overrides:
            raise R.RuleConflict(f"{what}: rule {hit[0].id} and the CR's earmark rule both act on it")
        if "anomaly" in act:
            section, stated = act["anomaly"]
            report["carry_anomaly"].append(f"{what}: {section}")
            if stated:
                replaced[(a, h["observation_id"])].append((c, section, stated, law))
            else:
                report["cr_list"].append(("dropped", what, f"{section} ends it"))
            continue
        if c["component_type"] == "earmark":
            report["carry_anomaly"].append(f"{what}: {law['earmarks_end']}")
            report["cr_list"].append(("dropped", what, law["earmarks_end"]))
            continue
        if any(x["component_type"] == c["component_type"] for x in by_obs[h["observation_id"]]):
            report["carry_already_held"].append(f"{c['component_id']} -> {h['observation_id']}")
            report["cr_list"].append((f"not carried: FY{fy} already holds the type", what, h["observation_id"]))
            continue
        rule = act.get("rule") or (f"{law['continues']}; no anomaly changes it"
                                   + (f" ({act['note']})" if act.get("note") else ""))
        out.append(dict(c, observation_id=h["observation_id"], tag_method="carried_forward", tag_rule=rule,
                        carried_from_component_id=c["component_id"], source_observation_id="",
                        notes=c.get("notes", "") + f" | carried forward from FY{prior} Enacted "
                                                   f"({c['observation_id']}); in_headline as there"))
        report["cr_list"].append(("carried forward", what, rule.split(":")[0]))
    for (a, hid), old in sorted(replaced.items()):
        _, section, stated, law = old[0]
        olds = ", ".join(f"{c['component_id']} {c['component_type']}"
                         f"{'/' + c['designation'] if c.get('designation') else ''} {AC.money(c['amount'])}"
                         for c, *_ in old)
        prior = int(obs[old[0][0]["observation_id"]]["fiscal_year"])
        for typ, des, amount, inside, words, within in stated:
            new = f"{typ}{'/' + des if des else ''} {AC.money(amount)}"
            held = [x for x in by_obs[hid] if x["component_type"] == typ and x.get("designation", "") == des
                    and int(x["amount"]) == amount]
            if held:
                report["cr_list"].append(("replaced", f"{olds} ({a})",
                                          f"-> {new}, {section}; already held as {held[0]['component_id']} "
                                          f"({held[0]['tag_method']}), which equals the anomaly's amount"))
                continue
            out.append({"observation_id": hid, "component_type": typ, "designation": des, "transfer_from": "",
                        "amount": amount, "in_headline": inside, "source_document_id": law["source"],
                        "source_location": section.replace(law["label"] + " ", ""), "quoted_text": words,
                        "tag_method": "cr_anomaly",
                        "tag_rule": f"FY{prior + 1} full-year CR anomaly: {section} states the amount in place of the "
                                    f"FY{prior} component",
                        "source_observation_id": "", "carried_from_component_id": "",
                        "notes": f"replaces {olds} (FY{prior} Enacted)",
                        "within_component_id": ("cr", within) if within else ""})
            report["cr_list"].append(("replaced", f"{olds} ({a})", f"-> {new}, {section}"))
    return out


def cr_anomalies(sc, account, fy):
    """{account: (section, selectors, stated)} of the anomalies a rule file holds for the cell (measure_reconcile)."""
    return R.active().data("carry_forward", sc, account, fy, "Enacted").get("anomalies", {})


# ------------------------------------------------------------------------------------------------ IDs

KEY = ("observation_id", "component_type", "designation", "source_document_id", "source_location",
       "source_observation_id", "carried_from_component_id", "amount")


def key(c):
    return tuple(c.get(k) for k in KEY)


def number(cid):
    return IDS.number(cid)


def assign_ids(data, proposed, obs):
    """Keep the ID of every record whose identity is unchanged; give new ones the next number in the headline's
    subcommittee's namespace (ids.Allocator: CMP-<CODE>-####, CJS's CMP-####). A record no longer produced is
    retired: its ID goes to observation_components_retired, never reused."""
    old = {key(c): c for c in data.get("observation_components", [])}
    sc_of = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    alloc = IDS.Allocator([c["component_id"] for c in data.get("observation_components", [])]
                          + list(data.get("observation_components_retired", [])))
    out = []
    for c in proposed:
        k = key(c)
        if k in old:
            c = dict(c, component_id=old[k]["component_id"])
        else:
            c = dict(c, component_id=alloc.next("CMP", sc_of[obs[c["observation_id"]]["canonical_account_id"]]))
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
    report["request"] = collections.Counter()
    heads = headline_of(data)
    obs = {o["observation_id"]: o for o in data["observations"]}
    first = retag(data, heads, report)
    first += table_lines(data, heads, report)
    first += rule_components(data, heads, report)
    first += mandatory_lines(data, heads)
    held = collections.defaultdict(list)
    for c in first:
        held[c["observation_id"]].append(c)
    auto = autotag(data, heads, held, report)
    auto += request_text(data, heads, held, report)
    records = first + auto
    # IDs for everything but the carried components first, so a carried one can name what it continues
    records, _ = assign_ids(data, records, obs)
    ids = {key(c): c["component_id"] for c in records}
    for c in records:
        if isinstance(c.get("within_component_id"), tuple):
            c["within_component_id"] = ids[c["within_component_id"][1]]
    carried = carry_forward(data, heads, records, report)
    adv = advances(data, heads, records + carried, report)
    records, retired = assign_ids(data, records + carried + adv, obs)
    # a carried component within another names the carried copy of that one (or nothing, when it wasn't carried)
    copy_of = {(c["carried_from_component_id"], c["observation_id"]): c["component_id"] for c in records
               if c["carried_from_component_id"]}
    for c in records:
        if c["tag_method"] == "carried_forward" and c.get("within_component_id"):
            c["within_component_id"] = copy_of.get((c["within_component_id"], c["observation_id"]), "")
    # a stated anomaly component within another names the stated component of that type on the same headline
    stated = {(c["observation_id"], c["component_type"]): c["component_id"] for c in records
              if c["tag_method"] == "cr_anomaly"}
    for c in records:
        if isinstance(c.get("within_component_id"), tuple) and c["within_component_id"][0] == "cr":
            c["within_component_id"] = stated[(c["observation_id"], c["within_component_id"][1])]
    order = IDS.order([c["component_id"] for c in data.get("observation_components", [])])
    records.sort(key=lambda c: order(c["component_id"]))
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
    lines += ["| subcommittee | FY | type | " + " | ".join(AC.TAG_METHODS) + " |", "|---" * (3 + len(AC.TAG_METHODS)) + "|"]
    for k in sorted({k[:3] for k in by}):
        lines.append(f"| {k[0]} | {k[1]} | {k[2]} | " + " | ".join(str(by[k + (m,)]) for m in AC.TAG_METHODS) + " |")
    lines += ["", f"Headlines read (auto-tagged cells): {report['pilot_headlines']}; no paragraph or no single paragraph: "
              f"{len(report['pilot_no_paragraph'])}; auto tags already held from a line: "
              f"{len(report['auto_already_held'])}; auto tags with in_headline by default (not by arithmetic): "
              f"{len(report['auto_default'])}; component-like phrases no rule tagged: {len(report['untagged'])}; "
              "not a component (owner's list): " + (", ".join(f"{k} {n}" for k, n in sorted(report["not_a_component"].items()))
                                                    or "none"),
              f"Re-tag: zero lines skipped {len(report['retag_zero'])}; lines with no headline in their cell "
              f"{len(report['retag_no_headline'])}",
              f"FY2025 CR carry-forward: carried {sum(1 for c in records if c['tag_method'] == 'carried_forward')}; "
              f"not carried (anomaly or sec. 1111) {len(report['carry_anomaly'])}; already held "
              f"{len(report['carry_already_held'])}; no FY2025 headline {len(report['carry_no_headline'])}; "
              f"stated by an anomaly {sum(1 for c in records if c['tag_method'] == 'cr_anomaly')}", ""]
    rq = report["request"]
    lines += ["### President's Budget request language (appendix_text, D-2)", "",
              "| subcommittee | FY | read, components | read, no match | not found |", "|---|---|---|---|---|"]
    for sc, fy in sorted({k[:2] for k in rq}):
        lines.append(f"| {sc} | {fy} | {rq[(sc, fy, 'read')]} | {rq[(sc, fy, 'read, no match')]} | "
                     f"{rq[(sc, fy, 'not found')]} |")
    lines += ["", f"Request advances left to the advance records: {len(report['request_advance_kept'])}; stale "
                  f"request_language.json rows: {len(report['request_stale'])}", ""]
    lines += ["### FY2025 full-year CR: each FY2024 Enacted component", "",
              "| outcome | FY2024 component | why / in its place |", "|---|---|---|"]
    lines += [f"| {k} | {w} | {x} |" for k, w, x in sorted(report["cr_list"])]
    lines += [""] + [x for s in report["rule_summary"] for x in (s, "")]
    if report["unknown_table_line"] or report["cr_unknown_law"]:
        lines += ["For review (no rule reads them): " + "; ".join(report["unknown_table_line"]
                                                                  + report["cr_unknown_law"]), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    data = staged_data.load()
    records, retired, report = build(data)
    errs = AC.component_errors(records, {o["observation_id"]: o for o in data["observations"]},
                               {d["document_id"] for d in data["source_docs"]})
    text = summarize(records, data, report)
    print(text)
    if a.ci and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    for k in ("retag_no_headline", "carry_anomaly", "pilot_no_paragraph", "auto_default", "advance_not_read",
              "advance_default", "request_stale", "unknown_table_line", "cr_unknown_law"):
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
        staged_data.save(data)
        print("data/staged/ updated")
    if a.ci and stale:
        print("data/staged/ is not up to date with the component tagging -- run "
              "python scripts/tag_components.py --write and commit data/staged/")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
