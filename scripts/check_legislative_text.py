"""
The bill and law text check: every House, Senate and Enacted headline figure against the bill or law text for
that same stage. Offline: it reads only committed files (data/staged.json and data/legislative_text/), never
the network.

    python scripts/check_legislative_text.py            # compare; print the summary
    python scripts/check_legislative_text.py --write    # also write the results into data/staged.json
    python scripts/check_legislative_text.py --ci       # CI: fail on any gap (below); the summary goes to the
                                                        # job summary, with the change since --base-dir

For each observation -- stage House Reported / Senate Reported / Enacted, budget authority, no component (the
headline), not a rollup total, not superseded -- find its paragraph in that stage's text: the appropriation
heading with the account's name (canonical or former; data/staged.json historical names) under its agency,
in the stage's division. Compare the headline: the paragraph's main appropriated amount -- not an "of which",
a transfer, a limitation, an allowance or an advance for a later year. Where we record a derived headline
(Medicaid: appropriated in this bill - the new advance) the text's figure is derived the same way.

  match         a confirming check: rule bill_text (House, Senate bills) or law_text (enacted law), result pass.
                Counts toward auto-validation (validate_approps.CONFIRMING_RULES) because the text is a document
                other than the figure's source; when it is the same document, the record is info instead.
  mismatch      result fail, pending review, with both figures and both locations
  ambiguous     result flag, pending review: several plausible amounts; counts neither as a pass nor a fail
  no paragraph  result info: informational only (the program may be funded inside another account); never a
                failure and never a confirmed absence

The check never changes a figure: a disagreement only goes to review. Its records are VAL-TXT-<observation>,
one per observation, rewritten only when the comparison itself changes (a person's resolution stays while
the comparison is the same).

CI fails (--ci) when
  - a document at a bill or law stage has no committed text file and no exemption (exemptions.json, with a
    written reason), or an exemption is stale (the document has a text file, or names nothing we hold)
  - a text file was parsed by another parser version than legislative_text.py's (re-parse and commit:
    python scripts/fetch_legislative_text.py --reparse), or is malformed
  - data/staged.json holds other bill_text / law_text records or statuses than this check gives (run --write)
"""

import argparse
import collections
import itertools
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legislative_text as L  # noqa: E402
import validate_approps as V  # noqa: E402

RESULTS = ("match", "mismatch", "ambiguous", "no paragraph")
RESULT_CODE = {"match": "pass", "mismatch": "fail", "ambiguous": "flag", "no paragraph": "info"}
PREFIX = "VAL-TXT-"


# ---------------------------------------------------------------------------------------------- names and places

def norm(s):
    s = (s or "").lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in ("the",))


# the agency an account's heading sits under, as words of the enclosing headings and title
AGENCY_WORDS = {
    "National Aeronautics and Space Administration": "aeronautics space administration",
    "National Science Foundation": "national science foundation",
    "Department of Justice": "justice",
    "Executive Office of the President": "science",          # Title III -- Science
}
BUREAU_WORDS = {"U.S. Patent and Trademark Office": "patent trademark",
                "National Oceanic and Atmospheric Administration": "oceanic atmospheric"}


def agency_words(acc):
    return BUREAU_WORDS.get(acc["bureau"]) or AGENCY_WORDS.get(acc["agency"]) or norm(acc["agency"])


# an agency's other names in the bill text (SAMHSA is 'Substance Use and Mental Health Services Administration' in
# the FY2023 bills)
AGENCY_ALIASES = {"Substance Abuse and Mental Health Services Administration":
                  ["substance use and mental health services administration"]}


def in_context(acc, para):
    ctx = set(norm(" ".join(para["heading_path"][:-1] + [para["title"]])).split())
    return any(all(w in ctx for w in words.split())
               for words in [agency_words(acc)] + AGENCY_ALIASES.get(acc["agency"], []))


def other_agency(para, words):
    ctx = set(norm(" ".join(para["heading_path"][:-1])).split())
    return bool(words) and all(w in ctx for w in words.split())


GENERIC = {"national", "institute", "of", "on", "for", "and", "to", "office", "program", "programs", "fund"}


def similar_headings(acc, names, paras):
    """Headings under the account's agency whose name is close to one of the account's (most words shared):
    a name the mapping may lack. Reported, never matched."""
    out = []
    for p in paras:
        if not in_context(acc, p):
            continue
        h = set(norm(p["account_heading"]).split()) - GENERIC
        for n in names:
            w = set(n.split()) - GENERIC
            if h and w and h != w and len(h & w) / len(h | w) >= 0.6:
                out.append(f"'{p['account_heading']}' ({p['paragraph_id']})")
                break
    return out[:3]


def account_names(acc, obs_of_account, former):
    """Every name the account's heading may carry: canonical, former names, the labels the tables print."""
    out = {norm(acc["canonical_name"])}
    for x in re.split(r";| formerly ", acc.get("historical_names") or ""):
        out.add(norm(x))
    for x in former.get(acc["canonical_account_id"], []):
        out.add(norm(x))
    for o in obs_of_account:
        m = re.search(r"printed as '([^']+)'", o.get("source_table_or_section") or "")
        if m:
            label = norm(m.group(1))
            out.add(re.sub(r"^total ", "", label))
    return {n for n in out if n}


def heading_keys(para):
    """The heading as one name, and joined with the line(s) above it (a heading printed over two lines)."""
    path = para["heading_path"] or [para["account_heading"]]
    return {norm(" ".join(path[-k:])) for k in (1, 2, 3) if len(path) >= k}


# ---------------------------------------------------------------------------------------------- amounts

BEFORE_NOT_MAIN = re.compile(
    r"(of which|of the (?:total )?amounts?\b[^$]{0,60}|of such amounts?|of this amount|of the funds[^$]{0,40}|"
    r"not to exceed|not more than|not less than|no more than|no less than|up to|including|in addition to|"
    r"less than|greater than|exceed|transfer(?:red)?\b[^$]{0,40}|from (?:the )?amounts?\b[^$]{0,60}|"
    r"limitation[^$]{0,40}|than|plus|minus|totaling|total of|estimated at|reduced by|increased by|"
    r"first quarter of fiscal year \d{4},?|available on october 1, \d{4},?|rescinded[^$]{0,40}|"
    r"previously appropriated[^$]{0,40}|direct obligations[^$]{0,40})\s*$", re.I)
AFTER_NOT_MAIN = re.compile(r"^\s*,?\s*(for official (?:reception|representation)|per vehicle|per year|for each|"
                            r"is (?:hereby )?rescinded|are (?:hereby )?rescinded|shall be (?:derived|from)|"
                            r"to be derived from amounts? (?:available|made available) under section 241|"
                            r"shall become available on october 1|from (?:the )?(?:funds|amounts) (?:made )?available "
                            r"under section 241)", re.I)
TAP_241 = re.compile(r"^\s*,?\s*(?:shall|is to) be (?:from|derived from) (?:funds|amounts) (?:made )?available under "
                     r"section 241", re.I)
EMERGENCY = re.compile(r"^\s*,?\s*(?:is|are) designated by the Congress as (?:being )?for an emergency requirement", re.I)
TRUST_FUND_LIMIT = re.compile(r"^\s*,?\s*(?:to be )?transferred from the [^$]{0,80}trust fund", re.I)


def split_phrase(a):
    m = re.match(r"(.*)\[\$[^\]]*\](.*)", a["phrase"], re.S)
    return (m.group(1), m.group(2)) if m else (a["phrase"], "")


def is_main_candidate(a):
    before, after = split_phrase(a)
    return not BEFORE_NOT_MAIN.search(before[-120:]) and not AFTER_NOT_MAIN.search(after) and not is_advance(a)


def is_advance(a):
    """The next fiscal year's advance: '... for the first quarter of fiscal year 2025, $X' (or 'which shall become
    available on October 1')."""
    before, after = split_phrase(a)
    return bool(re.search(r"first quarter of fiscal year \d{4},?\s*$", before[-80:], re.I)
                or re.search(r"^\s*,?\s*(?:to remain available until expended,? )?which shall become available on "
                             r"october 1", after, re.I))


def appropriating(para):
    """The amounts the paragraph's appropriating clauses state (before any proviso), not of-which, limits,
    transfers or advances, in order."""
    return [a for a in para["amounts"] if not a.get("proviso") and is_main_candidate(a)]


def main_amount(para):
    """-> (the paragraph's main appropriated amount or None, how). A paragraph whose only appropriation is a
    trust-fund limitation ('not to exceed $X, to be transferred from the ... Trust Fund') has that as its main."""
    ap = appropriating(para)
    if ap:
        return ap[0], "the paragraph's main appropriated amount"
    loose = [a for a in para["amounts"] if is_main_candidate(a)]
    if loose:
        return loose[0], "the paragraph's main appropriated amount (stated in a proviso)"
    tf = next((a for a in para["amounts"] if TRUST_FUND_LIMIT.search(split_phrase(a)[1])), None)
    if tf:
        return tf, "the trust-fund limitation the paragraph appropriates"
    return None, ""


# ---------------------------------------------------------------------------------------------- the comparison

def other_law_amounts(validations):
    """Amounts from other laws recorded inside the figure: the other-law notes ('Includes $X from ... and $Y
    from ...') and an earlier law_text record's written derivation ('N as recorded = $A (this law) + $B ...')."""
    out = []
    for v in validations:
        obs = v.get("observed_result") or ""
        if "prints amounts from other laws" in (v.get("expected_result") or "") or "Includes $" in obs:
            out += [int(x.replace(",", "")) for pair in re.findall(r"Includes \$([\d,]+)|and \$([\d,]+)", obs)
                    for x in pair if x]
        m = re.match(r"[\d,]+ as recorded = \$[\d,]+ \([^)]*\)((?: \+ \$[\d,]+)+)", obs)
        if v["rule_applied"] == "law_text" and m:
            out += [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", m.group(1))]
    return sorted(set(out), key=out.index)


def money(xs):
    return " + ".join(f"${x:,}" for x in xs)


APPROPRIATED_HERE = re.compile(r"^\s*,?\s*(?:is|are) (?:hereby )?appropriated\b", re.I)


def derivations(para, main, recorded_extra, cell):
    """The text's figure, each way the headline may be derived from what the paragraph states -> [(amount, how)].
    Each is read from the text itself (or from a derivation recorded with the figure), never fitted to it:
      base   the main amount, or the sum of the paragraph's appropriating amounts
      plus   an amount a proviso itself says 'is appropriated'; the next year's advance (appropriated in this bill)
      less   an 'of which $X shall be from funds available under section 241' (PHS evaluation funds, not new
             budget authority); an amount the paragraph designates an emergency requirement
    and the derivations recorded with the figure: the account's own part lines (defense, CHIMP ...) and amounts
    from other laws printed within it."""
    m = main["amount"]
    ap = [a["amount"] for a in appropriating(para)]
    phr = [(a, *split_phrase(a)) for a in para["amounts"]]
    adv = [a["amount"] for a, _, _ in phr if is_advance(a)]
    extra = [a["amount"] for a, _, after in phr if a.get("proviso") and APPROPRIATED_HERE.search(after)]
    tap = [a["amount"] for a, before, after in phr if TAP_241.search(after) and re.search(r"of which\s*$", before, re.I)]
    emer = [a["amount"] for a, _, after in phr if EMERGENCY.search(after)]
    bases = [(m, f"the main amount ${m:,}")]
    if len(ap) > 1:
        bases.append((sum(ap), f"the paragraph's {len(ap)} appropriating amounts {money(ap)}"))
    plus = [((), "")]
    if extra:
        plus.append(((*extra,), f" + {money(extra)} a proviso appropriates"))
    if adv:
        plus.append(((*adv,), f" + the next year's advance {money(adv)} (appropriated in this bill)"))
    if extra and adv:
        plus.append(((*extra, *adv), f" + {money(extra)} a proviso appropriates + the next year's advance {money(adv)}"))
    less = [((), "")]
    if tap:
        less.append(((*tap,), f" - {money(tap)} from section 241 (PHS evaluation funds, not new budget authority)"))
    if emer:
        less.append(((*emer,), f" - {money(emer)} designated an emergency requirement"))
    if tap and emer:
        less.append(((*tap, *emer), f" - {money(tap)} from section 241 - {money(emer)} designated an emergency requirement"))
    out = []
    for (b, bw), (p, pw), (l, lw) in itertools.product(bases, plus, less):
        if bw.startswith("the main") and not pw and not lw:
            continue
        out.append((b + sum(p) - sum(l), bw + pw + lw))
    parts = [c for c in cell if c[2] == "part" and c[1] != "supplemental_act"]
    for k in range(1, min(3, len(parts)) + 1):
        for combo in itertools.combinations(parts, k):
            for (l, lw) in less:
                out.append((m - sum(l) - sum(c[3] for c in combo),
                            f"derived as recorded: the main amount ${m:,}{lw} = this headline + "
                            + " + ".join(f"the {c[1]} line ${c[3]:,} ({c[0]})" for c in combo)))
    if recorded_extra:
        out.append((m + sum(recorded_extra), f"derived as recorded: the main amount ${m:,} + {money(recorded_extra)} "
                                             "from other laws printed within the figure"))
    return out


def compare(o, para, recorded_extra, cell=()):
    """-> (result, text figure, how) for one observation against its paragraph. cell: the account's other
    recorded lines at the same year and stage, as (observation_id, component, kind, amount)."""
    ours = o["amount"]
    every = [a["amount"] for a in para["amounts"]]
    if not every:
        return "no paragraph", None, "the paragraph states no dollar amount (such sums as may be necessary)"
    main, how = main_amount(para)
    if main is None:
        tap = [a["amount"] for a in para["amounts"] if TAP_241.search(split_phrase(a)[1])]
        if tap and ours == 0 and len(tap) == len(every):
            return "match", 0, (f"no new budget authority: the paragraph's {money(tap)} is all from section 241 "
                                "(PHS evaluation funds)")
        if ours in every:
            return "ambiguous", None, ("no main appropriated amount -- every amount in the paragraph is an of-which, "
                                       f"limit, transfer or advance; ${ours:,} is one of them")
        return "ambiguous", None, ("no main appropriated amount -- every amount in the paragraph is an of-which, "
                                   "limit, transfer or advance")
    if ours == main["amount"]:
        return "match", ours, how
    for amt, why in derivations(para, main, recorded_extra, cell):
        if amt == ours:
            return "match", ours, why
    for c in cell:
        if c[2] in ("view", "contained") and c[3] == main["amount"]:
            return "ambiguous", main["amount"], (f"the paragraph's main amount is this account's recorded {c[1]} line "
                                                 f"({c[0]}), another scope than the headline; several plausible amounts")
    if ours in every:
        return "ambiguous", main["amount"], (f"${ours:,} is printed in the paragraph but is not its main amount "
                                             f"(${main['amount']:,}); several plausible amounts")
    # another scope: the main amount less (or plus) one or two other amounts printed in the same paragraph
    rest = [x for x in every if x != main["amount"]][:15]
    for k in (1, 2):
        for combo in itertools.combinations(rest, k):
            for signs in itertools.product((-1, 1), repeat=k):
                if main["amount"] + sum(sg * x for sg, x in zip(signs, combo)) == ours:
                    terms = " ".join(f"{'+' if sg > 0 else '-'} ${x:,}" for sg, x in zip(signs, combo))
                    return "ambiguous", main["amount"], (
                        f"${ours:,} = the main amount ${main['amount']:,} {terms} printed in the same paragraph; "
                        "several plausible amounts")
    # a mismatch: say how far the nearest reading of the text is from ours
    near = min([(main["amount"], how)] + derivations(para, main, recorded_extra, cell), key=lambda d: abs(ours - d[0]))
    if near[0] != main["amount"]:
        return "mismatch", main["amount"], (f"{how}; nearest reading {near[1]} = ${near[0]:,}, "
                                            f"ours - that = {ours - near[0]:+,}")
    return "mismatch", main["amount"], f"{how}; ours - the text = {ours - main['amount']:+,}"


def cite(ref, doc, text):
    bill = (ref.get("bill_id") or "").replace("H.R.", "H.R. ").replace("S.", "S. ").replace("P.L.", "P.L. ")
    bill = re.sub(r"\s+", " ", bill).strip()
    if ref["stage"] == "Enacted" and ref.get("division"):
        bill += f" div. {ref['division']}"
    rf = text["retrieved_from"]
    src = rf["package"] or "committee draft PDF"
    return f"{bill} ({text['version']}; {src}, {rf['format'].replace('_', ' ')})"


def where(para):
    loc = para["location"]
    if "stat_page" in loc:
        place = loc["stat_page"]
    elif "pdf_page" in loc:
        place = f"PDF p.{loc['pdf_page']}"
    else:
        place = loc.get("position", "")
    path = " > ".join(para["heading_path"])
    return f"{para['title']}, '{path}' ({place}; {para['paragraph_id']})"


def ours_at(o):
    return f"{o['source_document_id']} p.{o.get('source_page') or '?'}"


def check(data, texts, exempt):
    """-> {observation_id: outcome} for every observation in scope; outcome: result, rule, document, text figure,
    paragraph, how, ... plus the records it gives."""
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    former = collections.defaultdict(list)
    for h in data.get("historical_names_tab", []):
        former[h["canonical_account_id"]].append(h["former_name"])
    by_acc = collections.defaultdict(list)
    for o in data["observations"]:
        by_acc[o["canonical_account_id"]].append(o)
    vals = collections.defaultdict(list)
    for v in data["validations"]:
        if not v["validation_id"].startswith(PREFIX):
            vals[v["observation_id"]].append(v)
    reqs, _ = L.required_documents(data)
    ref_doc = {}
    for doc_id, r in reqs.items():
        for ref_id in r["references"]:
            ref_doc[ref_id] = doc_id
    refs = {(r["subcommittee"], int(r["fiscal_year"]), r["stage"]): r for r in data["bill_report_refs"]}
    comp_kind = {c["component_id"]: c["kind"] for c in data.get("components", [])}
    agencies = collections.defaultdict(set)
    for x in data["accounts"]:
        agencies[x["subcommittee"]].add(agency_words(x))
    out = {}
    for o in data["observations"]:
        a = acc[o["canonical_account_id"]]
        if (o["stage"] not in L.RULE_BY_STAGE or o["amount_type"] != "budget authority" or o.get("component")
                or a.get("total_scope") or o["verification_status"] == "superseded"):
            continue
        sc, fy, stage = a["subcommittee"], int(o["fiscal_year"]), o["stage"]
        res = {"observation_id": o["observation_id"], "subcommittee": sc, "fiscal_year": fy, "stage": stage,
               "account": a["canonical_account_id"], "name": a["canonical_name"], "ours": o["amount"],
               "ours_at": ours_at(o), "ours_where": o.get("source_table_or_section") or "",
               "rule": L.RULE_BY_STAGE[stage]}
        ref = refs.get((sc, fy, stage))
        if ref is None:
            res.update(result="no reference", how=f"no Bill Report Reference for {sc} FY{fy} {stage}")
            out[o["observation_id"]] = res
            continue
        doc_id = ref_doc.get(ref["reference_id"])
        if ref["reference_id"] in exempt or (doc_id and doc_id in exempt):
            res.update(result="exempt", document=doc_id, how=(exempt.get(ref["reference_id"])
                                                              or exempt.get(doc_id))["reason"])
            out[o["observation_id"]] = res
            continue
        text = texts.get(doc_id)
        if text is None:
            res.update(result="no text", document=doc_id, how=f"{doc_id} has no committed text file")
            out[o["observation_id"]] = res
            continue
        res["document"] = doc_id
        res["cite"] = cite(ref, None, text)
        division = (ref.get("division") or "").strip()
        paras = [p for p in text["paragraphs"] if not division or not p["division"] or p["division"] == division]
        names = account_names(a, by_acc[a["canonical_account_id"]], former)
        named = [p for p in paras if heading_keys(p) & names]
        placed = [p for p in named if in_context(a, p)]
        # a heading found under no agency heading at all is taken only from a PDF's text layer, which loses the
        # heading levels; structured text always shows the agency, so there it must be the account's own
        loose = text["retrieved_from"]["format"] in ("pdf_text", "htm_text")
        if not placed and named and (a.get("title") or "").startswith("Title "):
            # the heading sits under no agency heading of the account's (HHS's 'Departmental Management' over the
            # Office of the Secretary's accounts): the one in the account's own title of the bill (Title II for
            # HHS), unless another agency's heading is above it (NIH's Buildings and Facilities is not CDC's)
            same_title = [p for p in named if re.match(re.escape(a["title"].upper()) + r"\b", p["title"])
                          and not any(other_agency(p, w) for w in agencies[sc] - {agency_words(a)})]
            if len(same_title) == 1:
                placed = same_title
        hits = placed or (named if len(named) == 1 and loose else [])
        if not hits and len(named) > 1:
            res.update(result="ambiguous", paragraph=None, text_amount=None,
                       how=f"{len(named)} paragraphs carry the heading and none sits under "
                           f"'{a['agency']}': " + "; ".join(where(p) for p in named[:4]))
            out[o["observation_id"]] = res
            continue
        if not hits:
            near = similar_headings(a, names, paras)
            res.update(result="no paragraph", paragraph=None, text_amount=None,
                       how=f"no appropriation paragraph headed '{a['canonical_name']}' (or a former name)"
                           f" under '{a['agency']}'" + (f" in div. {division}" if division else "")
                           + (f"; a heading with a similar name, not matched: " + "; ".join(near) if near else ""),
                       similar=near)
            out[o["observation_id"]] = res
            continue
        cell = [(x["observation_id"], x["component"], comp_kind.get(x["component"]), x["amount"])
                for x in by_acc[a["canonical_account_id"]]
                if x["fiscal_year"] == o["fiscal_year"] and x["stage"] == stage and x.get("component")
                and x["amount_type"] == "budget authority" and x["verification_status"] != "superseded"]
        outcomes = [(compare(o, p, other_law_amounts(vals[o["observation_id"]]), cell), p) for p in hits]
        if len({(r, amt) for (r, amt, _), _ in outcomes}) > 1:
            best = next(((x, p) for x, p in outcomes if x[0] == "match"), None)
            if best is None:
                res.update(result="ambiguous", paragraph=outcomes[0][1], text_amount=outcomes[0][0][1],
                           how=f"{len(hits)} paragraphs under the heading with different amounts: "
                               + "; ".join(f"{where(p)} ${x[1]:,}" if x[1] is not None else where(p)
                                           for x, p in outcomes))
                out[o["observation_id"]] = res
                continue
            outcomes = [best]
        (result, amt, how), para = outcomes[0]
        res.update(result=result, paragraph=para, text_amount=amt, how=how,
                   same_document=(doc_id == o["source_document_id"]))
        out[o["observation_id"]] = res
    return out


def record_for(res):
    """The validation record a result gives (None for exempt / no text / no reference: nothing to record)."""
    if res["result"] not in RESULTS:
        return None
    para = res.get("paragraph")
    ours = f"${res['ours']:,} as recorded ({res['ours_at']})"
    if res["result"] == "no paragraph":
        expected = f"{res['cite']}: {res['how']}" if para is None else \
            f"{res['cite']}, {where(para)}: {res['how']}"
        observed = ours + "; informational only -- the program may be funded inside another account; " \
                          "not a confirmed absence"
    elif res["result"] == "match":
        expected = f"{res['cite']}, {where(para)}: ${res['text_amount']:,} ({res['how']})"
        observed = ours + (" -- the same document as the figure's source, so not a second document"
                           if res.get("same_document") else " -- matches")
    elif res["result"] == "mismatch":
        expected = f"{res['cite']}, {where(para)}: ${res['text_amount']:,} ({res['how']})"
        observed = (f"${res['ours']:,} as recorded ({res['ours_at']}; {res['ours_where']}) -- differs from the text "
                    f"by ${res['ours'] - res['text_amount']:,}; for review, the figure is unchanged")
    else:
        lead = f"${res['text_amount']:,} (the paragraph's main amount)" if res.get("text_amount") is not None else \
            "no single main amount"
        expected = f"{res['cite']}, {where(para)}: {lead}" if para else f"{res['cite']}: {lead}"
        observed = f"{ours}; {res['how']} -- for review, counts neither as a pass nor a fail"
    result = RESULT_CODE[res["result"]]
    if res["result"] == "match" and res.get("same_document"):
        result = "info"
    return {"validation_id": PREFIX + res["observation_id"].removeprefix("OBS-"),
            "observation_id": res["observation_id"], "rule_applied": res["rule"],
            "expected_result": expected, "observed_result": observed, "result": result,
            "human_review_status": "pending" if result in ("fail", "flag") else "", "reviewer": "", "resolution": ""}


MACHINE = ("observation_id", "rule_applied", "expected_result", "observed_result", "result")


def merged(data, outcomes):
    """data's validations with this check's records brought up to date: a record whose comparison is unchanged
    keeps its review fields (a person's resolution stays); a changed one starts over."""
    want = {}
    for res in outcomes.values():
        r = record_for(res)
        if r:
            want[r["validation_id"]] = r
    out, seen = [], set()
    for v in data["validations"]:
        vid = v["validation_id"]
        if vid in want:
            seen.add(vid)
            w = want[vid]
            if all(v.get(k) == w[k] for k in MACHINE):
                out.append(v)
            else:
                out.append(w)
        else:
            out.append(v)
    out += [want[k] for k in sorted(want) if k not in seen]
    return out


def statuses(data, validations):
    checks = collections.defaultdict(list)
    for v in validations:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v.get("expected_result"),
                                           v.get("human_review_status"), v.get("resolution")))
    out = {}
    for o in data["observations"]:
        if o["verification_status"] in ("human-verified", "provisional", "superseded"):
            out[o["observation_id"]] = o["verification_status"]
        else:
            out[o["observation_id"]] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    return out


# ---------------------------------------------------------------------------------------------- coverage (CI)

TEXT_KEYS = ("document_id", "source_url", "retrieved_from", "version_code", "version", "retrieved", "sha256",
             "bytes", "parser_version", "paragraphs")


def coverage(data, texts, exempt):
    """Problems that fail CI: missing text, stale exemptions, other parser versions, malformed files."""
    problems = []
    reqs, nodoc = L.required_documents(data)
    pv = L.parser_version()
    for doc_id, r in sorted(reqs.items()):
        has = doc_id in texts
        if not has and doc_id not in exempt:
            problems.append(f"{doc_id} ({', '.join(r['references']) or r['stage']}): no text file "
                            f"data/legislative_text/{doc_id}.json -- fetch it (scripts/fetch_legislative_text.py) "
                            "or exempt it with a reason (data/legislative_text/exemptions.json)")
        if has and doc_id in exempt:
            problems.append(f"{doc_id}: has a text file and an exemption -- drop the exemption")
    for ref_id, why in nodoc:
        if ref_id not in exempt:
            problems.append(f"{ref_id}: {why} -- exempt it with a reason (data/legislative_text/exemptions.json)")
    known = set(reqs) | {r for r, _ in nodoc}
    for eid, e in exempt.items():
        if eid not in known:
            problems.append(f"exemption {eid}: names no document or reference we hold")
        if not (e.get("reason") or "").strip():
            problems.append(f"exemption {eid}: no reason")
    for doc_id, t in sorted(texts.items()):
        missing = [k for k in TEXT_KEYS if k not in t]
        if missing:
            problems.append(f"{doc_id}: text file lacks {missing}")
            continue
        if t["document_id"] != doc_id:
            problems.append(f"{doc_id}: text file names {t['document_id']}")
        if not re.fullmatch(r"[0-9a-f]{64}", t["sha256"]):
            problems.append(f"{doc_id}: sha256 {t['sha256']!r} is not a sha256")
        if t["parser_version"] != pv:
            problems.append(f"{doc_id}: parsed by parser {t['parser_version']}, legislative_text.py is {pv} -- "
                            "re-parse (python scripts/fetch_legislative_text.py --reparse) and commit the diff")
        if doc_id not in reqs:
            problems.append(f"{doc_id}: text file for a document no bill or law stage names")
        if "api_key" in json.dumps(t["retrieved_from"]) or "api_key=" in t["source_url"]:
            problems.append(f"{doc_id}: a stored URL carries an api_key")
        if not any(p["amounts"] for p in t["paragraphs"]):
            problems.append(f"{doc_id}: no paragraph with a dollar amount -- not usable text; exempt it instead")
    return problems


# ---------------------------------------------------------------------------------------------- report

def load_texts(text_dir):
    out = {}
    if text_dir.exists():
        for p in sorted(text_dir.glob("*.json")):
            if p.name != "exemptions.json":
                out[p.stem] = json.loads(p.read_text())
    return out


def load_exempt(text_dir):
    p = text_dir / "exemptions.json"
    return {e["id"]: e for e in json.loads(p.read_text())["exemptions"]} if p.exists() else {}


def counts(outcomes):
    c = collections.Counter()
    for r in outcomes.values():
        c[r["result"]] += 1
    return c


ORDER = ("match", "mismatch", "ambiguous", "no paragraph", "exempt", "no text", "no reference")


def summary(outcomes, base=None, problems=(), moved=None, pending_added=None):
    c = counts(outcomes)
    lines = ["## Bill and law text check", ""]
    head = "| | " + " | ".join(ORDER) + " |"
    lines += [head, "|---" * (len(ORDER) + 1) + "|"]
    lines.append("| this branch | " + " | ".join(str(c[k]) for k in ORDER) + " |")
    if base is not None:
        b = counts(base)
        lines.append("| main | " + " | ".join(str(b[k]) for k in ORDER) + " |")
        lines.append("| change | " + " | ".join(f"{c[k] - b[k]:+d}" for k in ORDER) + " |")
    lines.append("")
    by = collections.Counter((r["subcommittee"], r["fiscal_year"], r["stage"], r["result"]) for r in outcomes.values())
    keys = sorted({k[:3] for k in by})
    lines += ["<details><summary>By subcommittee, fiscal year and stage</summary>", "",
              "| subcommittee | FY | stage | " + " | ".join(ORDER) + " |", "|---" * (len(ORDER) + 3) + "|"]
    for k in keys:
        lines.append(f"| {k[0]} | {k[1]} | {k[2]} | " + " | ".join(str(by[k + (r,)]) for r in ORDER) + " |")
    lines += ["", "</details>", ""]
    if moved is not None:
        lines.append(f"Records pending review added: {pending_added}. Status changes: "
                     + (", ".join(f"{a} -> {b}: {n}" for (a, b), n in sorted(moved.items())) or "none"))
        lines.append("")
    if problems:
        lines += ["**Failures**", ""] + [f"- {p}" for p in problems] + [""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write the results into data/staged.json")
    ap.add_argument("--ci", action="store_true", help="fail on gaps or stale records; job summary")
    ap.add_argument("--base-dir", type=Path, help="a checkout of the base branch, for the change since main")
    ap.add_argument("--report", type=Path, help="write every outcome as CSV here")
    a = ap.parse_args(argv)
    data = json.loads(L.STAGED.read_text())
    texts, exempt = load_texts(L.TEXT_DIR), load_exempt(L.TEXT_DIR)
    outcomes = check(data, texts, exempt)
    base = None
    if a.base_dir:
        try:
            bdata = json.loads((a.base_dir / "data" / "staged.json").read_text())
            bdir = a.base_dir / "data" / "legislative_text"
            base = check(bdata, load_texts(bdir), load_exempt(bdir))
        except Exception as e:                                       # noqa: BLE001 -- the base is for comparison only
            print(f"(no comparison with the base: {type(e).__name__}: {e})")
    problems = coverage(data, texts, exempt)
    for r in outcomes.values():
        if r["result"] in ("no text", "no reference"):
            problems.append(f"{r['observation_id']} ({r['subcommittee']} FY{r['fiscal_year']} {r['stage']}): {r['how']}")
    new_vals = merged(data, outcomes)
    old_ids = {v["validation_id"]: v for v in data["validations"]}
    pending_added = sum(1 for v in new_vals if v["validation_id"].startswith(PREFIX)
                        and v["human_review_status"] == "pending"
                        and old_ids.get(v["validation_id"], {}).get("human_review_status") != "pending")
    want_status = statuses(data, new_vals)
    moved = collections.Counter((o["verification_status"], want_status[o["observation_id"]]) for o in data["observations"]
                                if o["verification_status"] != want_status[o["observation_id"]])
    stale = new_vals != data["validations"] or bool(moved)
    if a.ci and stale:
        n = sum(1 for v in new_vals if old_ids.get(v["validation_id"]) != v)
        problems.append(f"data/staged.json is not up to date with this check ({n} records, "
                        f"{sum(moved.values())} statuses) -- run python scripts/check_legislative_text.py --write "
                        "and commit data/staged.json")
    text = summary(outcomes, base, problems, moved if a.write else None, pending_added if a.write else None)
    print(text)
    if a.ci and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
    if a.report:
        import csv
        cols = ["subcommittee", "fiscal_year", "stage", "observation_id", "account", "name", "result", "ours",
                "ours_at", "ours_where", "text_amount", "document", "text_where", "how"]
        with open(a.report, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            for r in sorted(outcomes.values(), key=lambda r: (r["subcommittee"], r["fiscal_year"], r["stage"],
                                                              r["account"], r["observation_id"])):
                w.writerow({**r, "text_where": where(r["paragraph"]) if r.get("paragraph") else ""})
    if a.write and stale:
        data["validations"] = new_vals
        for o in data["observations"]:
            o["verification_status"] = want_status[o["observation_id"]]
        L.STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(f"data/staged.json updated: {sum(1 for v in new_vals if old_ids.get(v['validation_id']) != v)} records")
    if a.ci and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
