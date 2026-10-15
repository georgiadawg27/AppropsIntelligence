"""
D-3 decision 1 (NSF Research and Related Activities, owner 2026-10-13; the defense line's form, owner 2026-10-14):
every bill and law cell held, FY2017-FY2026 (House, Senate, Enacted), takes the amount the R&RA paragraph appropriates
as its headline (nondefense + defense, less an amount the paragraph designates an emergency requirement, which stays
beside), with the report table's 'Defense function' line inside it. Supersede, never overwrite (data/staged.json):

  - the table's nondefense headline -> superseded by a new headline citing the bill or law page; the table figure is
    kept as the new headline's note, and the old observation links to the new one
  - the table's defense line (component 'defense', a part the store adds) -> superseded by a new observation of the
    same printed line under component 'defense_inside' (contained: the store never adds it), headline_observation_id
    the new headline; tag_components reads it as a defense_portion inside the headline

FY2025 Enacted (the full-year CR, P.L. 119-4 div. A sec. 1101, no anomaly names the account) continues FY2024's
paragraph (P.L. 118-42). Every figure is read from the committed text (data/legislative_text/) and must equal both
the old headline + the defense line and the list in decisions-D2.md (EXPECTED); otherwise the script stops.

    python reference/review/accounting/d3_records.py           # what would change
    python reference/review/accounting/d3_records.py --write   # write it (idempotent: a cell already done is skipped)
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import legislative_text as L  # noqa: E402
import tag_components as TC  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
ACCOUNT = "ACC-NSF-RRA"
RULING = "NSF R&RA: the paragraph's total, defense inside (D-3, owner 2026-10-13)"
LINE_RULING = "D-3, owner 2026-10-14"
COMPONENT = {"component_id": "defense_inside", "label": "Defense function (inside the paragraph's total)",
             "kind": "contained",
             "description": "the NSF Research and Related Activities 'Defense function' line, inside the headline: the "
                            "bill or law paragraph's amount counts it (never add it again)"}

# (fy, stage, old headline, new headline as decisions-D2.md lists it, text document, page, where (Stat. page))
# pages: the govinfo PDF page the paragraph prints on (a bill's PDF page is its printed page); the FY2021 Senate
# chair's draft: the committed text's PDF page
EXPECTED = [
    (2017, "House Reported", "OBS-0726", 6079430000, "SRC-BILLS-114HR5393RH", "67", ""),
    (2017, "Senate Reported", "OBS-0725", 6033645000, "SRC-BILLS-114S2837PCS", "77", ""),
    (2017, "Enacted", "OBS-0724", 6033645000, "SRC-PLAW-115PUBL31", "81", "131 Stat. 215"),
    (2018, "House Reported", "OBS-0722", 6033645000, "SRC-BILLS-115HR3267RH", "68", ""),
    (2018, "Senate Reported", "OBS-0721", 5917803000, "SRC-BILLS-115S1662PCS", "78", ""),
    (2018, "Enacted", "OBS-0720", 6334476000, "SRC-PLAW-115PUBL141", "86", "132 Stat. 432"),
    (2019, "House Reported", "OBS-0718", 6651500000, "SRC-BILLS-115HR5952RH", "72", ""),
    (2019, "Senate Reported", "OBS-0717", 6556183000, "SRC-BILLS-115S3072PCS", "81", ""),
    (2019, "Enacted", "OBS-0716", 6520000000, "SRC-PLAW-116PUBL6", "112", "133 Stat. 124"),
    (2020, "House Reported", "OBS-0714", 7106301000, "SRC-BILLS-116HR3055RH", "79", ""),
    (2020, "Senate Reported", "OBS-0713", 6769670000, "SRC-BILLS-116S2584RS", "82", ""),
    (2020, "Enacted", "OBS-0712", 6737200000, "SRC-PLAW-116PUBL93", "104", "133 Stat. 2420"),
    (2021, "House Reported", "OBS-0710", 6967123000, "SRC-BILLS-116HR7667RH", "96", ""),
    (2021, "Senate Reported", "OBS-0709", 6907400000, "SRC-BILL-CJS-FY2021-SENATE", "88", ""),
    (2021, "Enacted", "OBS-0708", 6909769000, "SRC-PLAW-116PUBL260", "91", "134 Stat. 1271"),
    (2022, "House Reported", "OBS-0706", 7695729000, "SRC-BILLS-117HR4505RH", "106", ""),
    (2022, "Senate Reported", "OBS-0705", 7667099000, "SRC-BILLS-117S3042IS", "92", ""),
    (2022, "Enacted", "OBS-0704", 7159400000, "SRC-PLAW-117PUBL103", "91", "136 Stat. 139"),
    (2023, "House Reported", "OBS-0702", 7705530000, "SRC-BILLS-117HR8256RH", "95", ""),
    (2023, "Senate Reported", "OBS-0701", 8321907000, "SRC-BILLS-117S4664IS", "96", ""),
    (2023, "Enacted", "OBS-0700", 7021136000, "SRC-PLAW-117PUBL328", "91", "136 Stat. 4549"),
    (2024, "House Reported", "OBS-0698", 7866646000, "SRC-BILLS-118HR5893IH", "86", ""),
    (2024, "Senate Reported", "OBS-0697", 7188287000, "SRC-BILLS-118S2321RS", "101", ""),
    (2024, "Enacted", "OBS-0696", 7176500000, "SRC-PLAW-118PUBL42", "137", "138 Stat. 161"),
    (2025, "House Reported", "OBS-0694", 7546630000, "SRC-BILLS-118HR9026RH", "90", ""),
    (2025, "Senate Reported", "OBS-0693", 6555280000, "SRC-BILLS-118S4795RS", "99", ""),
    (2025, "Enacted", "OBS-0692", 7176500000, "SRC-PLAW-118PUBL42", "137", "138 Stat. 161"),
    (2026, "House Reported", "OBS-0690", 6373000000, "SRC-BILLS-119HR5342RH", "87", ""),
    (2026, "Senate Reported", "OBS-0689", 7176500000, "SRC-BILLS-119S2354RS", "100", ""),
    (2026, "Enacted", "OBS-0688", 7176500000, "SRC-PLAW-119PUBL74", "41", "140 Stat. 45"),
]
CR = ("SRC-PLAW-119PUBL4", "2")      # FY2025 Enacted cites the CR (as OBS-LHHS-4122 does)
PAGE_NOTE = " Page citations are PDF page numbers."


def name_of(doc, para=None):
    m = re.match(r"SRC-BILLS-\d+(HR|S)(\d+)", doc)
    if m:
        return ("H.R. " if m.group(1) == "HR" else "S. ") + m.group(2)
    m = re.match(r"SRC-PLAW-(\d+)PUBL(\d+)", doc)
    if m:
        return f"P.L. {m.group(1)}-{m.group(2)}" + (f" div. {para['division']}" if para and para.get("division") else "")
    if doc == "SRC-BILL-CJS-FY2021-SENATE":
        return "the FY2021 Senate chair's draft"
    return doc


def table_name(doc, kind):
    m = re.match(r"SRC-CRPT-(\d+)(H|S)RPT(\d+)", doc)
    if m:
        return f"{'H.Rept.' if m.group(2) == 'H' else 'S.Rept.'} {m.group(1)}-{m.group(3)}"
    return "the explanatory statement" if kind == "explanatory_statement" else doc


def paragraph(doc):
    t = L.load_text(doc)
    hits = [p for p in t["paragraphs"] if "SCIENCE FOUNDATION" in " > ".join(p["heading_path"]).upper()
            and p["heading_path"][-1].upper() == "RESEARCH AND RELATED ACTIVITIES"]
    if len(hits) != 1:
        raise SystemExit(f"STOP: {doc}: {len(hits)} R&RA paragraphs")
    return hits[0]


def paragraph_total(para):
    """The paragraph's main amount less what it designates an emergency requirement (beside the headline)."""
    main = para["amounts"][0]
    emergency = [a["amount"] for a in para["amounts"][1:] if TC.EMERGENCY.search(a["phrase"].split("]", 1)[-1])]
    return main["amount"] - sum(emergency), main["amount"], emergency


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = {o["observation_id"]: o for o in data["observations"]}
    docs = {d["document_id"]: d for d in data["source_docs"]}
    n = max(int(o["observation_id"].split("-")[1]) for o in data["observations"] if re.match(r"OBS-\d+$", o["observation_id"]))
    order = list(data["observations"][0])
    rows, added, doc_pages = [], [], {}
    for fy, stage, old_id, expected, doc, page, stat in EXPECTED:
        old = obs[old_id]
        assert (old["canonical_account_id"], old["fiscal_year"], old["stage"]) == (ACCOUNT, fy, stage), old_id
        lines = [o for o in data["observations"] if o["canonical_account_id"] == ACCOUNT and o["fiscal_year"] == fy
                 and o["stage"] == stage and o.get("component") in ("defense", "defense_inside")
                 and o["amount_type"] == "budget authority"]
        line = next(o for o in lines if o["component"] == "defense" and o["source_document_id"] == old["source_document_id"])
        para = paragraph(doc)
        total, main_amount, emergency = paragraph_total(para)
        if not (total == expected == old["amount"] + line["amount"]):
            raise SystemExit(f"STOP: FY{fy} {stage}: the paragraph gives ${total:,}, decisions-D2.md ${expected:,}, "
                             f"{old_id} + {line['observation_id']} ${old['amount'] + line['amount']:,}")
        rows.append((fy, stage, old, line, total, doc, page, stat, para, emergency))
        if old["verification_status"] == "superseded":
            continue                                          # done already (idempotent)
        src_doc, src_page = (CR if (fy, stage) == (2025, "Enacted") else (doc, page))
        kind = docs[old["source_document_id"]]["document_type"]
        tname = table_name(old["source_document_id"], kind)
        note = (f"{tname} ({old['source_document_id']} p.{old['source_page']}) prints the nondefense line "
                f"{old['amount'] // 1000:,} thousand ({old_id}) and the 'Defense function' line "
                f"{line['amount'] // 1000:,} thousand ({line['observation_id']}); the paragraph's amount counts both: "
                f"{old['amount'] // 1000:,} + {line['amount'] // 1000:,} = {total // 1000:,} thousand")
        less = (f" (${main_amount:,} less ${sum(emergency):,} the paragraph designates an emergency requirement, "
                f"recorded beside)" if emergency else "")
        where = f" ({stat})" if stat else ""
        if (fy, stage) == (2025, "Enacted"):
            section = (f"P.L. 119-4 div. A sec. 1101 (the FY2025 full-year CR) continues {name_of(doc, para)}, "
                       f"'Research and Related Activities'{where}: ${total:,}; no anomaly names the account")
        else:
            section = (f"Title III, National Science Foundation -- printed in {name_of(doc, para)} under 'Research and "
                       f"Related Activities': ${total:,}{less}{where}")
        n += 1
        new_head = {k: "" for k in order}
        new_head.update({"observation_id": f"OBS-{n:04d}", "canonical_account_id": ACCOUNT, "fiscal_year": fy,
                         "stage": stage, "chamber": old["chamber"], "amount": total, "amount_type": "budget authority",
                         "offsetting_collections": "FALSE", "source_document_id": src_doc, "source_page": src_page,
                         "source_table_or_section": f"{section}; bill or law text controls over the report table for "
                                                    f"the same scope (owner 2026-10-11; {RULING}) -- Note: {note}",
                         "extraction_method": "text-extracted", "confidence": 0.95, "verification_status": "unverified",
                         "component": "", "headline_observation_id": ""})
        n += 1
        new_line = dict(line, observation_id=f"OBS-{n:04d}", component="defense_inside",
                        headline_observation_id=new_head["observation_id"],
                        source_table_or_section=f"{line['source_table_or_section']} -- inside the paragraph's total "
                                                f"({new_head['observation_id']}); re-recorded from "
                                                f"{line['observation_id']} ({LINE_RULING})")
        new_line.pop("superseded_by_observation_id", None)
        added += [(old, new_head), (line, new_line)]
        if src_doc != "SRC-PLAW-119PUBL4":
            doc_pages.setdefault(src_doc, set()).add(src_page)
    print("| FY | Stage | Old headline | Defense line | New headline | Source, page | Old figure's source | "
          "Defense line old -> new |")
    print("|---|---|---|---|---|---|---|---|")
    new_of = {o["observation_id"]: x for o, x in added}
    for fy, stage, old, line, total, doc, page, stat, para, emergency in rows:
        nh = new_of.get(old["observation_id"], {}).get("observation_id") or old.get("superseded_by_observation_id")
        nl = new_of.get(line["observation_id"], {}).get("observation_id") or line.get("superseded_by_observation_id")
        src = "P.L. 119-4 div. A sec. 1101 (continues P.L. 118-42, 138 Stat. 161)" if (fy, stage) == (2025, "Enacted") \
            else f"{doc} p.{page}" + (f", {stat}" if stat else "")
        print(f"| {fy} | {stage} | {old['observation_id']} ${old['amount']:,} | ${line['amount']:,} | {nh} ${total:,} "
              f"| {src} | {old['source_document_id']} p.{old['source_page']} | {line['observation_id']} -> {nl} |")
    print(f"{len(rows)} cells checked against decisions-D2.md and the text; {len(added) // 2} to supersede")
    changed_docs = []
    for d, pages in sorted(doc_pages.items()):
        x = docs[d]
        cur = x["source_page"] or ""
        miss = [p for p in sorted(pages, key=int) if not any(
            re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", s) and int(re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", s).group(1))
            <= int(p) <= int(re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", s).group(2) or
                             re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", s).group(1))
            for s in cur.split(";") if s.strip())]
        if miss:
            x["source_page"] = "; ".join([s.strip() for s in cur.split(";") if s.strip()] + miss)
            if PAGE_NOTE.strip() not in x["notes"]:
                x["notes"] = (x["notes"] + PAGE_NOTE).strip()
            changed_docs.append(f"{d} {cur!r} -> {x['source_page']!r}")
    print("source documents given their cited pages:", "; ".join(changed_docs) or "none")
    if not any(c["component_id"] == COMPONENT["component_id"] for c in data["components"]):
        data["components"].append(COMPONENT)
        print("component vocabulary: + defense_inside (contained)")
    if args.write:
        for old, new in added:
            old["verification_status"] = "superseded"
            old["superseded_by_observation_id"] = new["observation_id"]
            data["observations"].append(new)
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("data/staged.json updated")


if __name__ == "__main__":
    sys.exit(main())
