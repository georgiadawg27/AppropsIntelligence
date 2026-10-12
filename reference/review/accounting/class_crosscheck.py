"""
Cross-checks of the account attributes (accounting views, phase 2) against what the reports print. Failures go to
review (class_crosscheck.csv); nothing is adjusted.

  marks   The older House report tables mark each line D (discretionary), M (mandatory), TF (trust fund) or NA
          (H.Rept. 115-244, 115-862, 116-62: on the text layer; H.Rept. 114-699 and 116-450 print their tables
          as images and are skipped). Each held line of those tables is found on its own page by its amount in
          its own column, and its mark compared with the account's budget_class (and, for TF, with a trust fund
          link: fund_type 'trust' or a trust_fund_limitation component).
  hrsa    The newer reports print 'Total, Health Resources and Services Administration' with a 'Discretionary'
          line under it. Each column's discretionary line is compared with the HRSA total's discretionary view
          computed from its accounts' budget_class and mandatory_portion components (measure_reconcile).
  trust   fund_type 'trust' (from the Appendix fund digit) against trust_fund_limitation components, account by
          account; and the Account tab's own fund_type against the Appendix's.

    python reference/review/accounting/class_crosscheck.py --cache document_store

Needs pdftotext (poppler) and the report PDFs (downloaded when missing).
"""

import argparse
import collections
import csv
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import accounting as AC  # noqa: E402
import measure_reconcile as MR  # noqa: E402
import tag_components as TC  # noqa: E402

OUT = Path(__file__).resolve().parent / "class_crosscheck.csv"
MARK_DOCS = ("SRC-CRPT-115HRPT244", "SRC-CRPT-115HRPT862", "SRC-CRPT-116HRPT62")
IMAGE_ONLY = ("SRC-CRPT-114HRPT699", "SRC-CRPT-116HRPT450")
MARK = re.compile(r"(?:\.|\s)\s*\b(D|M|TF|NA)\b\s+[\d(]")
HRSA_TOTAL = re.compile(r"Total, Health Resources and Services Administration\b")
DISC = re.compile(r"^\s*Discretionary\s*\.{3}")
NUM = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])|(?<![\d,])\d{1,3}(?![\d,])")
COLUMNS = ["check", "result", "canonical_account_id", "fiscal_year", "stage", "source_document_id", "page",
           "printed", "attribute", "note"]


def fetch(url, dest):
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (approps-intelligence crosscheck)"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dest.write_bytes(r.read())
    return dest


def page_text(pdf, page):
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True).stdout


def pages(pdf):
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout.split("\f")


def numbers(line):
    m = re.search(r"\.{3,}", line)
    start = m.end() if m else 0
    return [(int(x.group().replace(",", "")), x.end()) for x in NUM.finditer(line, start)]


def trust_link(data, account, comps_by_acc):
    fund = {r["value"] for r in data["account_attributes"]
            if r["canonical_account_id"] == account and r["attribute"] == "fund_type"}
    return "trust" in fund or account in comps_by_acc


def marks(data, cache, docs, attrs, out):
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    tfl = {o_acc for o_acc in (c["_account"] for c in data["_components"]
                               if c["component_type"] == "trust_fund_limitation")}
    texts = {}
    for o in sorted(data["observations"], key=lambda o: o["observation_id"]):
        doc = o["source_document_id"]
        if (doc not in MARK_DOCS or o.get("component") or o["amount_type"] != "budget authority"
                or o["verification_status"] == "superseded" or acc[o["canonical_account_id"]].get("total_scope")):
            continue
        page = re.match(r"\d+", o.get("source_page") or "")
        if not page:
            continue
        page = int(page.group())
        if (doc, page) not in texts:
            pdf = fetch(docs[doc]["url_or_identifier"], cache / f"{doc}.pdf")
            texts[(doc, page)] = page_text(pdf, page).splitlines()
        amt = o["amount"] // 1000
        words = {w.lower() for w in re.findall(r"[A-Za-z]{5,}", TC.printed(o))}
        found = set()
        for line in texts[(doc, page)]:
            m = MARK.search(line)
            # the line's amount in a column, and a word of the line's own label (an amount alone can repeat)
            if (m and any(v == amt for v, _ in numbers(line[m.start():]))
                    and words & {w.lower() for w in re.findall(r"[A-Za-z]{5,}", line[:m.start()])}):
                found.add((m.group(1), " ".join(line.split())[:120]))
        mk = {f[0] for f in found}
        base = {"check": "marks", "canonical_account_id": o["canonical_account_id"], "fiscal_year": o["fiscal_year"],
                "stage": o["stage"], "source_document_id": doc, "page": page}
        if len(mk) != 1:
            continue                                   # the line is not on the text layer, or its amount is ambiguous
        mark, line = mk.pop(), next(iter(found))[1]
        rec = AC.attribute_value(attrs, o["canonical_account_id"], int(o["fiscal_year"]), "budget_class", o["stage"])
        cls = rec["value"] if rec else ""
        if mark == "NA":
            result, note = "not compared", "NA: no class printed"
        elif not cls:
            result, note = "not compared", "no budget_class on file"
        elif mark == "TF":
            linked = trust_link(data, o["canonical_account_id"], tfl)
            ok = cls in ("discretionary", "mixed") and linked
            result = "agree" if ok else "disagree"
            note = ("" if ok else "TF line, but " + ("the account's class is mandatory" if cls == "mandatory" else
                                                    "no trust fund link (fund_type trust or trust_fund_limitation)"))
        else:
            want = {"D": "discretionary", "M": "mandatory"}[mark]
            result = "agree" if cls == want else "consistent (mixed)" if cls == "mixed" else "disagree"
            note = "" if result == "agree" else f"{mark} line; budget_class {cls}"
        out.append(dict(base, result=result, printed=f"{mark}: {line}", attribute=cls, note=note))


def hrsa(data, cache, docs, attrs, out):
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    total = acc["ACC-HHS-HRSA-TOTAL"]
    comps = AC.components_by_observation(data)
    heads = TC.headline_of(data)
    held = collections.defaultdict(list)
    for o in data["observations"]:
        if (o["canonical_account_id"] == total["canonical_account_id"] and not o.get("component")
                and o["verification_status"] != "superseded"):
            held[o["source_document_id"]].append(o)
    for doc in sorted(held):
        pdf = fetch(docs[doc]["url_or_identifier"], cache / f"{doc}.pdf")
        for i, t in enumerate(pages(pdf)):
            lines = t.splitlines()
            for j, line in enumerate(lines):
                if not HRSA_TOTAL.search(line):
                    continue
                dline = next((lines[k] for k in range(j + 1, min(j + 5, len(lines))) if DISC.search(lines[k])), None)
                if dline is None:
                    continue
                tot, dis = numbers(line), numbers(dline)
                for o in held[doc]:
                    col = [end for v, end in tot if v * 1000 == o["amount"]]
                    if len(col) != 1:
                        continue
                    d = [v for v, end in dis if abs(end - col[0]) <= 3]
                    base = {"check": "hrsa", "canonical_account_id": total["canonical_account_id"],
                            "fiscal_year": o["fiscal_year"], "stage": o["stage"], "source_document_id": doc,
                            "page": i + 1}
                    if len(d) != 1:
                        continue
                    got = MR.discretionary_view(data, total, o, o, heads, comps, attrs)
                    if isinstance(got, str):
                        out.append(dict(base, result="not compared", printed=f"{d[0] * 1000:,}", attribute="",
                                        note=got))
                        continue
                    computed, how, _ = got
                    ok = computed == d[0] * 1000
                    out.append(dict(base, result="agree" if ok else "disagree",
                                    printed=f"Discretionary {d[0] * 1000:,} (total {o['amount']:,})",
                                    attribute=f"computed {computed:,}",
                                    note="" if ok else f"printed - computed = {d[0] * 1000 - computed:,}; {how}"))


def trust(data, out):
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    fund = collections.defaultdict(set)
    for r in data["account_attributes"]:
        if r["attribute"] == "fund_type":
            fund[r["canonical_account_id"]].add(r["value"])
    tfl = collections.Counter(c["_account"] for c in data["_components"]
                              if c["component_type"] == "trust_fund_limitation")
    for a in sorted(set(fund) | set(tfl)):
        is_trust = "trust" in fund[a]
        if is_trust == bool(tfl[a]):
            out.append({"check": "trust", "result": "agree", "canonical_account_id": a,
                        "printed": f"{tfl[a]} trust_fund_limitation components", "attribute": "/".join(sorted(fund[a])),
                        "note": ""})
        else:
            out.append({"check": "trust", "result": "disagree", "canonical_account_id": a,
                        "printed": f"{tfl[a]} trust_fund_limitation components",
                        "attribute": "/".join(sorted(fund[a])) or "(none on file)",
                        "note": ("fund_type trust with no trust_fund_limitation component" if is_trust else
                                 "trust_fund_limitation components, but the Appendix account is not a trust fund")})
    for a, vals in sorted(fund.items()):
        own = acc[a].get("fund_type") or ""
        if own and own not in vals:
            out.append({"check": "trust", "result": "disagree", "canonical_account_id": a,
                        "printed": f"Account tab fund_type {own}", "attribute": "/".join(sorted(vals)),
                        "note": "the Account tab's fund_type differs from the Appendix fund digit"})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / "document_store")
    a = ap.parse_args(argv)
    data = json.loads((ROOT / "data" / "staged.json").read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    data["_components"] = [dict(c, _account=obs[c["observation_id"]]["canonical_account_id"])
                           for c in data["observation_components"]]
    docs = {d["document_id"]: d for d in data["source_docs"]}
    attrs = AC.attribute_index(data)
    out = []
    marks(data, a.cache, docs, attrs, out)
    hrsa(data, a.cache, docs, attrs, out)
    trust(data, out)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in out:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    c = collections.Counter((r["check"], r["result"]) for r in out)
    for k, n in sorted(c.items()):
        print(k, n)
    print(f"skipped (tables printed as images): {', '.join(IMAGE_ONLY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
