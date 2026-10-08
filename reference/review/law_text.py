"""
The law_text check: an Enacted figure against the enacted law itself. For each enacted year we hold
(Bill Report Reference, stage Enacted: the vehicle and its division), read the enrolled bill's Formatted
XML (govinfo BILLS-<congress><type><number>enr; the method of reference/review/congress_api), and for each
account's headline Enacted figure (budget authority, no component) find the appropriation heading of the
same name in that division, under the account's agency, and the first dollar amount in its paragraph.

    python reference/review/law_text.py            # compare; writes law_text_comparison.csv
    python reference/review/law_text.py --write    # also append the law_text validation records

  pass  -- the figure equals that first dollar amount
  info  -- it differs, with the reason (program level, advances, transfers and trust-fund limitations differ
           from the headline by design); never a fail
  (none) -- no heading of that name in the division, or the year is a full-year CR (no amounts by heading)

law_text is a confirming check (validate_approps.CONFIRMING_RULES); info neither fails nor confirms.
The XML files live in document_store/enrolled_xml (not committed); their sha256 are in law_text_sources.csv.
"""

import argparse
import collections
import csv
import hashlib
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
XML_DIR = ROOT / "document_store" / "enrolled_xml"
OUT = HERE / "law_text_comparison.csv"
SOURCES = HERE / "law_text_sources.csv"
YEARS = {"LHHS": range(2022, 2027), "CJS": range(2017, 2027)}

# the agency each account's heading sits under, as words of the enclosing headers (title, major, intermediate)
AGENCY_WORDS = {
    "National Aeronautics and Space Administration": "aeronautics space administration",
    "National Science Foundation": "national science foundation",
    "Department of Justice": "justice",
    "Department of Commerce": "",                    # bureau decides (below)
    "Executive Office of the President": "science",  # Title III -- Science
}
BUREAU_WORDS = {"U.S. Patent and Trademark Office": "patent trademark",
                "National Oceanic and Atmospheric Administration": "oceanic atmospheric"}


def norm(s):
    s = html.unescape(s or "").lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in ("the",))


def text(x):
    return html.unescape(re.sub(r"<[^>]+>", " ", x))


def vehicle_file(ref):
    """'H.R. 6938' + its law link -> BILLS-119hr6938enr."""
    m = re.match(r"(H\.R\.|S\.|H\.J\.Res\.|S\.J\.Res\.)\s*(\d+)", ref["vehicle_bill_id"])
    kind = {"H.R.": "hr", "S.": "s", "H.J.Res.": "hjres", "S.J.Res.": "sjres"}[m.group(1)]
    congress = re.search(r"(?:PLAW-|/)(1\d\d)(?:publ|/plaws)", ref["bill_url"]).group(1)
    return f"BILLS-{congress}{kind}{m.group(2)}enr"


def fetch(name):
    path = XML_DIR / f"{name}.xml"
    if not path.exists():
        XML_DIR.mkdir(parents=True, exist_ok=True)
        url = f"https://www.govinfo.gov/content/pkg/{name}/xml/{name}.xml"
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    return path


LIMIT = re.compile(r"of which\s*$", re.I)
ALLOWANCE = re.compile(r"^[^$]{0,30}(official )?reception and representation", re.I)


def amounts(para):
    """The paragraph's dollar amounts in order, as (amount, is_limit): a limit is a set-aside ('of which $X')
    or a reception and representation allowance ('not to exceed $2,250 for official reception ...'), printed
    ahead of the appropriation in some paragraphs -- not the appropriation itself."""
    out = []
    for m in re.finditer(r"\$([\d,]+)", para):
        before = para[max(0, m.start() - 40):m.start()]
        after = para[m.end():m.end() + 80]
        out.append((int(m.group(1).replace(",", "")), bool(LIMIT.search(before) or ALLOWANCE.search(after))))
    return out


def first_amount(para):
    """The first dollar amount that is not a set-aside or reception allowance (the method's 'first dollar amount')."""
    return next((a for a, lim in amounts(para) if not lim), None)


def division_headings(path, letter):
    """The division's appropriation headings in order: (heading, first $ amount or None, paragraph text, context)."""
    s = Path(path).read_text(encoding="utf-8")
    divs = [(m.start(), text(m.group(1)).strip()) for m in
            re.finditer(r"<division\b[^>]*>\s*<enum>(.*?)</enum>", s, re.S)]
    starts = [p for p, e in divs if e.strip().rstrip(".") == letter]
    if not starts:
        raise LookupError(f"{path.name}: no division {letter}")
    lo = starts[0]
    hi = min([p for p, _ in divs if p > lo] + [len(s)])
    body = s[lo:hi]
    titles = [(m.start(), text(m.group(1)).strip()) for m in
              re.finditer(r"<title\b[^>]*>\s*<enum>[^<]*</enum>\s*<header\b[^>]*>(.*?)</header>", body, re.S)]
    out, major, inter = [], "", ""
    for m in re.finditer(r"<appropriations-(major|intermediate|small)\b[^>]*>\s*<header\b[^>]*>(.*?)</header>(.*?)"
                         r"(?=<appropriations-(?:major|intermediate|small)\b|</title>|</division>|$)", body, re.S):
        level, hdr = m.group(1), " ".join(text(m.group(2)).split())
        para = " ".join(text(m.group(3)).split())
        # each <text> paragraph's own first amount (a heading may carry several appropriating paragraphs)
        leads = [first_amount(" ".join(text(t).split())) for t in re.findall(r"<text\b[^>]*>(.*?)</text>", m.group(3), re.S)]
        title = ([t for p, t in titles if p <= m.start()] or [""])[-1]
        if level == "major":
            major, inter = hdr, ""
        elif level == "intermediate" and not hdr.startswith("("):
            inter = hdr
        if hdr.startswith("(") and out:                # "(including transfer of funds)": part of the heading above
            prev = out[-1]
            prev["paragraph"] = (prev["paragraph"] + " " + para).strip()
            if prev["amount"] is None:
                prev["amount"] = first_amount(para)
            continue
        out.append({"heading": hdr, "level": level, "amount": first_amount(para), "leads": [a for a in leads if a],
                    "paragraph": para, "context": " | ".join(x for x in (title, major, inter) if x)})
    return out


def names(acc, obs_list, hist):
    """Every name the account's heading may carry: canonical, former names, printed labels."""
    out = {norm(acc["canonical_name"])}
    for h in hist.get(acc["canonical_account_id"], []):
        out.add(norm(h))
    for x in (acc.get("historical_names") or "").replace(" formerly ", ";").split(";"):
        if x.strip():
            out.add(norm(x))
    for o in obs_list:
        m = re.search(r"printed as '([^']+)'", o["source_table_or_section"] or "")
        if m:
            out.add(norm(m.group(1)))
    return {n for n in out if n}


def in_context(acc, ctx):
    c = norm(ctx)
    words = BUREAU_WORDS.get(acc["bureau"]) or AGENCY_WORDS.get(acc["agency"])
    if words is None:                                   # LHHS: the agency's own name
        words = norm(acc["agency"])
    return all(w in c.split() for w in words.split())


def reason(o, h, others):
    """Why the law's first amount is not the headline (the known categories), or 'unexplained'."""
    law, ours, p = h["amount"], o["amount"], h["paragraph"].lower()
    rel = [x for x in others if x["amount"] and x is not o]
    for x in rel:
        if x["amount"] == law:
            what = x["component"] or x["amount_type"]
            return f"the law's first amount is this account's {what} line ({x['observation_id']})"
    for x in rel:
        for y in rel:
            if x is not y and ours + x["amount"] == law:
                return f"the law's first amount is the headline plus {x['component'] or x['amount_type']} ({x['observation_id']})"
    if any(ours + x["amount"] == law for x in rel):
        x = next(x for x in rel if ours + x["amount"] == law)
        return f"the law's first amount is the headline plus {x['component'] or x['amount_type']} ({x['observation_id']})"
    leads = h.get("leads") or []
    if len(leads) > 1 and sum(leads) == ours:
        return None                                       # a match: the sum of the heading's paragraphs (reason_match)
    import itertools
    rest = [x for x, lim in amounts(h["paragraph"]) if x != law][:12]
    for k in (1, 2):
        for combo in itertools.combinations(rest, k):
            if law + sum(combo) == ours:
                return ("the headline is the first amount plus " + " + ".join(f"${x:,}" for x in combo)
                        + " printed later in the same paragraph")
    if re.search(r"section 241 of the phs act|section 241 of the public health service act", p[:900]) and law > ours:
        return "the first amount is made available from the PHS evaluation set-aside (section 241), not appropriated budget authority (by design)"
    if re.search(r"first quarter of fiscal year|which shall become available on october 1|advance appropriation", p[:600]):
        return "advance appropriation: the first amount is for a later fiscal year"
    if re.search(r"trust fund|limitation|derived from|offsetting collections|fees", p[:700]):
        return "trust-fund limitation, fees or collections in the first amount (by design)"
    if re.search(r"transfer", p[:700]):
        return "the first amount counts transfers or amounts made available by transfer (by design)"
    if law > ours:
        return "the law's first amount is larger than the headline (program level or amounts made available elsewhere)"
    return ("the headline is larger than the law's first amount: it counts amounts provided outside this paragraph "
            "(another paragraph or division of the act) -- worth a look")


# Differences read further than one heading's paragraph (2026-10-08): the record's result and wording,
# kept here so a rerun reports them the same way
ACROSS = {
    "OBS-LHHS-0014": ("pass",
                      "H.R. 2617 (enrolled) div. H, 'LOW INCOME HOME ENERGY ASSISTANCE': $1,500,000,000; div. N "
                      "(Disaster Relief Supplemental Appropriations Act, 2023), same heading, second paragraph: $2,500,000,000",
                      "match (combined across divisions: H + N): $1,500,000,000 + $2,500,000,000 = 4,000,000,000 as recorded "
                      "(div. H names that second paragraph in its allocation proviso; div. N's first paragraph, "
                      "$1,000,000,000, is not in this figure). Includes $2,500,000,000 from Division N (Disaster Relief "
                      "Supplemental Appropriations Act, 2023) of P.L. 117-328."),
    "OBS-LHHS-1664": ("info",
                      "H.R. 2617 (enrolled) div. H, 'MENTAL HEALTH': first amount $2,693,507,000",
                      "2,755,507,000 as recorded = $2,693,507,000 (P.L. 117-328 div. H) + $62,000,000 for 988 Suicide "
                      "Lifeline activities appropriated by P.L. 117-180 (H.R. 6833, the FY2023 continuing resolution, "
                      "div. A sec. 145); S.Rept. 118-84 p.380 prints that row as 'CR Funding--Public Law 117-180 Suicide "
                      "Lifeline 62,000' inside the 2023 appropriation (another law, by design)"),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    acc = {x["canonical_account_id"]: x for x in data["accounts"]}
    hist = collections.defaultdict(list)
    for h in data.get("historical_names_tab", []):
        hist[h.get("canonical_account_id")].append(h.get("former_name") or h.get("historical_name") or "")
    by_acc_fy = collections.defaultdict(list)
    for o in data["observations"]:
        if o["stage"] == "Enacted" and o["verification_status"] != "superseded":
            by_acc_fy[(o["canonical_account_id"], o["fiscal_year"])].append(o)
    refs = {(r["subcommittee"], int(r["fiscal_year"])): r for r in data["bill_report_refs"] if r["stage"] == "Enacted"}
    # an observation already compared with the enrolled law (an other-law note is a law_text record too, not a comparison)
    have = {(v["observation_id"], v["rule_applied"]) for v in data["validations"] if "(enrolled)" in (v["expected_result"] or "")}
    # the other-law notes (build_year.other_law_notes): amounts from other laws printed within a figure
    other_law = {v["observation_id"]: ([int(x.replace(",", "")) for x in re.findall(r"Includes \$([\d,]+)|and \$([\d,]+)",
                                                                                      v["observed_result"]) for x in x if x],
                                       v["validation_id"])
                 for v in data["validations"] if "prints amounts from other laws" in (v["expected_result"] or "")}
    rows, recs, sources, updates = [], [], {}, []
    for sc, years in YEARS.items():
        for fy in years:
            ref = refs[(sc, fy)]
            if ref.get("funding_type") == "full_year_cr":
                rows.append({"subcommittee": sc, "fiscal_year": fy, "observation_id": "", "result": "",
                             "reason": f"{ref['vehicle_bill_id']} is a full-year CR: no amounts by heading"})
                continue
            name = vehicle_file(ref)
            path = fetch(name)
            sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            heads = division_headings(path, ref["division"])
            for (aid, ofy), obs_list in sorted(by_acc_fy.items()):
                if ofy != fy or acc[aid]["subcommittee"] != sc or acc[aid].get("total_scope"):
                    continue
                for o in obs_list:
                    if o["amount_type"] != "budget authority" or o["component"]:
                        continue
                    want = names(acc[aid], obs_list, hist)
                    hits = [h for h in heads if norm(h["heading"]) in want and in_context(acc[aid], h["context"])]
                    row = {"subcommittee": sc, "fiscal_year": fy, "observation_id": o["observation_id"],
                           "canonical_account_id": aid, "ours": o["amount"], "law": "", "heading": "",
                           "vehicle": f"{ref['vehicle_bill_id']} div. {ref['division']} ({name})", "result": "", "reason": ""}
                    if not hits:
                        row["reason"] = "no heading of that name under the account's agency in the division"
                        rows.append(row)
                        continue
                    h = hits[0]
                    row.update(law=h["amount"] if h["amount"] is not None else "", heading=h["heading"])
                    if h["amount"] is None:
                        res, why = "info", "no dollar amount in the heading's paragraph (such sums / amounts under sub-headings)"
                    elif h["amount"] == o["amount"]:
                        res, why = "pass", ""
                    else:
                        res, why = "info", reason(o, h, obs_list)
                        if why is None:                  # the heading's paragraphs sum to the figure
                            res, why = "pass", ("match (the sum of the heading's " + str(len(h["leads"])) + " paragraphs): "
                                                + " + ".join(f"${a:,}" for a in h["leads"]) + f" = {o['amount']:,} as recorded")
                    extra, note_id = other_law.get(o["observation_id"], ([], ""))
                    if res == "info" and extra and h["amount"] is not None and h["amount"] + sum(extra) == o["amount"]:
                        why = ("the headline is the first amount plus " + " + ".join(f"${x:,}" for x in extra)
                               + f" from other laws printed within the figure (another law, by design; {note_id})")
                    if o["observation_id"] in ACROSS:
                        res, _, why = ACROSS[o["observation_id"]]
                    row.update(result=res, reason=why)
                    rows.append(row)
                    if (o["observation_id"], "law_text") in have:
                        # an earlier record the improved check now matches (the heading's paragraphs summed): updated
                        # in place, recording the paragraphs (owner, 2026-10-08) -- a check result, not a figure
                        if res == "pass" and why.startswith("match (the sum of the heading's"):
                            law_amt = " + ".join(f"${x:,}" for x in h["leads"])
                            updates.append((o["observation_id"],
                                            f"{ref['vehicle_bill_id']} (enrolled) div. {ref['division']}, '{h['heading']}': "
                                            f"its {len(h['leads'])} paragraphs {law_amt}",
                                            f"{o['amount']:,} as recorded; {why}"))
                        continue
                    law_amt = f"${h['amount']:,}" if h["amount"] is not None else "no dollar amount"
                    recs.append({"observation_id": o["observation_id"], "rule_applied": "law_text",
                                 "expected_result": f"{ref['vehicle_bill_id']} (enrolled) div. {ref['division']}, "
                                                    f"'{h['heading']}': first amount {law_amt}",
                                 "observed_result": f"{o['amount']:,} as recorded" + (f"; {why}" if why else ""),
                                 "result": res})
    with open(OUT, "w", newline="") as f:
        cols = ["subcommittee", "fiscal_year", "observation_id", "canonical_account_id", "ours", "law", "heading",
                "vehicle", "result", "reason"]
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(SOURCES, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["package", "url", "sha256"])
        for k, v in sorted(sources.items()):
            w.writerow([k, f"https://www.govinfo.gov/content/pkg/{k}/xml/{k}.xml", v])
    c = collections.Counter((r["subcommittee"], r["result"] or "none") for r in rows if r["observation_id"])
    print(dict(c))
    print(collections.Counter(r["reason"] for r in rows if r["result"] == "info"))
    if a.write:
        write(data, recs, updates)


def write(data, recs, updates=()):
    def nxt(prefix, width):
        n = max(int(v["validation_id"][len(prefix):]) for v in data["validations"]
                if re.fullmatch(re.escape(prefix) + r"\d+", v["validation_id"]))
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    ids = {"LHHS": nxt("VAL-LHHS-", 5), "CJS": nxt("VAL-", 4)}
    obs = {o["observation_id"]: o for o in data["observations"]}
    order = ["validation_id", "observation_id", "rule_applied", "expected_result", "observed_result", "result",
             "human_review_status", "reviewer", "resolution"]
    changed = []
    for oid, exp, obsd in updates:
        v = next(v for v in data["validations"] if v["observation_id"] == oid and v["rule_applied"] == "law_text"
                 and "(enrolled)" in (v["expected_result"] or ""))
        if (v["result"], v["expected_result"], v["observed_result"]) != ("pass", exp, obsd):
            changed.append((v["validation_id"], oid, v["result"]))
            v.update({"result": "pass", "expected_result": exp, "observed_result": obsd})
    for vid, oid, was in changed:
        print(f"updated {vid} ({oid}): {was} -> pass")
    for r in recs:
        sc = "LHHS" if r["observation_id"].startswith("OBS-LHHS-") else "CJS"
        r.update({"validation_id": next(ids[sc]), "human_review_status": "", "reviewer": "", "resolution": ""})
        data["validations"].append({k: r[k] for k in order})
    # the standard status rule, on every observation it sets
    by_obs = collections.defaultdict(list)
    for v in data["validations"]:
        by_obs[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    moved = collections.Counter()
    for o in data["observations"]:
        if o["verification_status"] in ("human-verified", "provisional", "superseded"):
            continue
        new = V.verification_status(o["confidence"], by_obs[o["observation_id"]])
        if new != o["verification_status"]:
            moved[(o["canonical_account_id"].split("-")[1] if o["observation_id"].startswith("OBS-LHHS") else "CJS",
                   o["fiscal_year"], o["verification_status"], new)] += 1
            o["verification_status"] = new
    STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"{len(recs)} law_text records appended; status changes: {dict(moved)}")


if __name__ == "__main__":
    main()
