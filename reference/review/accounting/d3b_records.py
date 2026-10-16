"""
D-3b (follow-ups from #72): records changed in data/staged.json. Supersede, never overwrite; no ID reused.

Part 1, CA-LHHS-0037 (CDC-Wide Activities and Program Support, FY2023 President's Budget): the absence rested on the
House report's table (H.Rept. 117-403 p.813), which a confirmed absence may not. The FY2023 Budget Appendix's
proposed language for the heading 'CDC-WIDE ACTIVITIES AND PROGRAM SUPPORT' (HHS chapter pp.437-438, read with
request_language.chapter_paragraphs) appropriates $9,620,961,000 "to carry out titles II, III, IV, VII, XI, XV, XVII,
XIX, XXI, XXIII, XXVI, and XXVIII of the PHS Act ..." -- one appropriation for all of CDC (the report's CDC total for
the request, 9,676,319 thousand, less EEOICP's mandatory 55,358 thousand, is the same 9,620,961 thousand), not the
account's scope. It neither prints a request amount for the account nor confirms none, so the absence is retired
(the brief's third outcome): the cell becomes missing, and the request column's $160,000,000 Fund line under the
account (pphf_table.csv) stays reported, not recorded (tag_components' 'PPHF rows with no headline'), for review
(reference/review/accounting/review-D3b.md).

Part 3, NSF Research and Related Activities, President's Budget FY2017-FY2026 (owner-approved, the rule of D-3
part 1 extended to the request language): the headline is the amount the Budget Appendix's proposed language
appropriates for the paragraph (nondefense + defense, less an amount the paragraph designates an emergency
requirement, which stays beside), with the report table's 'Defense function' line re-recorded inside it as
defense_inside. Each figure is read from request_language.json (the Appendix text layer) and must equal the old
headline + the defense line (REQUEST below); otherwise the script stops. FY2019 and FY2024 are left as they are
(LEFT): their Appendix paragraph prints a total that is not the table's nondefense + defense.

    python reference/review/accounting/d3b_records.py           # what would change
    python reference/review/accounting/d3b_records.py --write   # write it (idempotent)
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import staged_data  # noqa: E402
STAGED = ROOT / "data" / "staged"
RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
REQUEST_LANGUAGE = ROOT / "reference" / "review" / "accounting" / "request_language.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import tag_components as TC  # noqa: E402

RETIRE_ABSENCES = {
    "CA-LHHS-0037": "CDC-Wide Activities FY2023 President's Budget: rested on a table's silence (H.Rept. 117-403 "
                    "p.813); the FY2023 Budget Appendix's language under the heading (HHS chapter pp.437-438) "
                    "appropriates $9,620,961,000 for all of CDC, not the account's scope: it neither prints the "
                    "account's request nor confirms none, so the cell is missing (D-3b, 2026-10-14)",
}


def retire_absences(data, retired):
    gone = [c for c in data["confirmed_absences"] if c["confirmed_absence_id"] in RETIRE_ABSENCES]
    for c in gone:
        print(f"retire {c['confirmed_absence_id']}: {RETIRE_ABSENCES[c['confirmed_absence_id']]}")
    data["confirmed_absences"] = [c for c in data["confirmed_absences"]
                                  if c["confirmed_absence_id"] not in RETIRE_ABSENCES]
    retired["confirmed_absences"].update(RETIRE_ABSENCES)
    return bool(gone)


ACCOUNT = "ACC-NSF-RRA"
STAGE = "President's Budget"
RULING = ("NSF R&RA: the paragraph's total, defense inside (D-3, owner 2026-10-13), extended to the request's Budget "
          "Appendix language (D-3b, owner-approved 2026-10-14)")
LINE_RULING = "D-3b, owner-approved 2026-10-14"
# (fy, old headline, new headline: the Appendix paragraph's amount less what it designates an emergency requirement)
REQUEST = [
    (2017, "OBS-0727", 6079430000), (2018, "OBS-0723", 5361650000), (2020, "OBS-0715", 5662960000),
    (2021, "OBS-0711", 6213020000), (2022, "OBS-0707", 8139710000), (2023, "OBS-0703", 8425987000),
    (2025, "OBS-0695", 7625320000), (2026, "OBS-0691", 3276150000),
]
# left as they are, with the reason (listed for the owner)
LEFT = {
    2019: "the FY2019 Appendix paragraph (p.1091) appropriates $4,230,510,000; H.Rept. 115-704's request column "
          "prints 6,079,680 + 71,000 = 6,150,680 thousand: not the same "
          "figure, so not recorded",
    2024: "the FY2024 Appendix paragraph (p.1141) appropriates $9,029,900,000; S.Rept. 118-62's request column "
          "prints 8,915,900 + 102,000 = 9,017,900 thousand, $12,000,000 less: not explained, so not recorded",
}


def appendix_paragraphs():
    out = {}
    for a in json.loads(REQUEST_LANGUAGE.read_text())["accounts"]:
        if a["account"] == ACCOUNT and a["status"] == "read":
            out[a["fiscal_year"]] = a
    return out


def paragraph_total(para):
    main = para["amounts"][0]
    emergency = [x["amount"] for x in para["amounts"][1:] if TC.EMERGENCY.search(x["phrase"].split("]", 1)[-1])]
    return main["amount"] - sum(emergency), main["amount"], emergency


def nsf_request(data):
    """-> [(old, new)] observations to supersede (old -> new); prints the old -> new table."""
    obs = {o["observation_id"]: o for o in data["observations"]}
    n = max(int(o["observation_id"].split("-")[1]) for o in data["observations"]
            if re.match(r"OBS-\d+$", o["observation_id"]))
    order = list(data["observations"][0])
    paras = appendix_paragraphs()
    for fy, why in LEFT.items():
        para = paras[fy]["paragraph"]
        total = paragraph_total(para)[0]
        old = next(o for o in data["observations"] if o["canonical_account_id"] == ACCOUNT and o["fiscal_year"] == fy
                   and o["stage"] == STAGE and not o["component"] and o["amount_type"] == "budget authority"
                   and o["verification_status"] != "superseded")
        line = next(o for o in data["observations"] if o["canonical_account_id"] == ACCOUNT and o["fiscal_year"] == fy
                    and o["stage"] == STAGE and o["component"] == "defense")
        if total == old["amount"] + line["amount"]:
            raise SystemExit(f"STOP: FY{fy} now matches ({total:,}): take it out of LEFT")
        print(f"FY{fy} left as is: {why}")
    rows, added = [], []
    for fy, old_id, expected in REQUEST:
        old = obs[old_id]
        assert (old["canonical_account_id"], old["fiscal_year"], old["stage"]) == (ACCOUNT, fy, STAGE), old_id
        line = next(o for o in data["observations"] if o["canonical_account_id"] == ACCOUNT and o["fiscal_year"] == fy
                    and o["stage"] == STAGE and o["component"] == "defense"
                    and o["source_document_id"] == old["source_document_id"])
        rec = paras[fy]
        para, doc = rec["paragraph"], rec["document_id"]
        page = para["location"]["pages"].replace("pp.", "").replace("p.", "")
        total, main_amount, emergency = paragraph_total(para)
        if not total == expected == old["amount"] + line["amount"]:
            raise SystemExit(f"STOP: FY{fy}: the Appendix paragraph gives ${total:,}, expected ${expected:,}, "
                             f"{old_id} + {line['observation_id']} ${old['amount'] + line['amount']:,}")
        rows.append((fy, old, line, total, doc, page))
        if old["verification_status"] == "superseded":
            continue                                          # done already (idempotent)
        tdoc = old["source_document_id"]
        m = re.match(r"SRC-CRPT-(\d+)(H|S)RPT(\d+)", tdoc)
        tname = (f"{'H.Rept.' if m.group(2) == 'H' else 'S.Rept.'} {m.group(1)}-{m.group(3)}" if m
                 else "the explanatory statement")
        note = (f"{tname} ({tdoc} p.{old['source_page']}) prints the request's nondefense line "
                f"{old['amount'] // 1000:,} thousand ({old_id}) and the 'Defense function' line "
                f"{line['amount'] // 1000:,} thousand ({line['observation_id']}); the paragraph's amount counts both: "
                f"{old['amount'] // 1000:,} + {line['amount'] // 1000:,} = {total // 1000:,} thousand")
        less = (f" (${main_amount:,} less ${sum(emergency):,} the paragraph designates an emergency requirement, "
                f"recorded beside)" if emergency else "")
        section = (f"National Science Foundation -- the FY{fy} Budget Appendix's proposed appropriations language under "
                   f"'Research and Related Activities' (p.{page}): ${total:,}{less}")
        n += 1
        new_head = {k: "" for k in order}
        new_head.update({"observation_id": f"OBS-{n:04d}", "canonical_account_id": ACCOUNT, "fiscal_year": fy,
                         "stage": STAGE, "chamber": old["chamber"], "amount": total, "amount_type": "budget authority",
                         "offsetting_collections": "FALSE", "source_document_id": doc, "source_page": page,
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
    new_of = {o["observation_id"]: x for o, x in added}
    print("| FY | Old headline | Defense line | New headline | Source, page | Old figure's source | "
          "Defense line old -> new |")
    print("|---|---|---|---|---|---|---|")
    for fy, old, line, total, doc, page in rows:
        nh = new_of.get(old["observation_id"], {}).get("observation_id") or old.get("superseded_by_observation_id")
        nl = new_of.get(line["observation_id"], {}).get("observation_id") or line.get("superseded_by_observation_id")
        print(f"| {fy} | {old['observation_id']} ${old['amount']:,} | ${line['amount']:,} | {nh} ${total:,} | "
              f"{doc} p.{page} | {old['source_document_id']} p.{old['source_page']} | {line['observation_id']} -> {nl} |")
    print(f"{len(rows)} cells checked against the Appendix text; {len(added) // 2} to supersede")
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    data = staged_data.load()
    retired = json.loads(RETIRED.read_text())
    changed = retire_absences(data, retired)
    added = nsf_request(data)
    for old, new in added:
        old["verification_status"] = "superseded"
        old["superseded_by_observation_id"] = new["observation_id"]
        data["observations"].append(new)
    changed = changed or bool(added)
    if not changed:
        print("nothing to change")
    if args.write and changed:
        staged_data.save(data)
        RETIRED.write_text(json.dumps(retired, indent=1) + "\n")
        print("data/staged.json and retired_ids.json updated")


if __name__ == "__main__":
    sys.exit(main())
