"""
The owner's decisions on the FY2022 backfill (PR #33, 2026-10-08). Appends to data/staged.json; an existing figure
changes only where the owner says so, by supersession (the old observation stays, marked superseded).

    python reference/review/lhhs/backfill/owner_decisions_fy2022.py           # what would change
    python reference/review/lhhs/backfill/owner_decisions_fy2022.py --write   # apply

1. HRSA 'Program Management' (FY2022 and earlier) is ACC-HHS-HRSA-PROGRAM-SUPPORT's headline: a Historical Name valid
   for those years only (from FY2023 'Program Management' is a line inside the account). Scope, checked with the
   FY2023 reports' FY2022 column: 'Total, HRSA-Wide Activities and Program Support' 1,213,196 = Program Management
   155,300 + Community Projects 1,057,896 (H.Rept. 117-403 p.809; the FY2023 Senate draft p.403) -- the same scope;
   the 340B (11,238) and Telehealth (35,050) administrative transfers are only in the 'with transfers' total.
   Congressionally directed spending / community project funding, where printed as its own line (FY2022 Enacted
   on), is recorded as a contained line of the account (the cell shows it as another line).
2. NIH Office of the Director, FY2022: derived as the 'Office of the Director' line + 'Gabriella Miller Kids First
   Research Act (Common Fund add)' 12,600 -- the FY2023-FY2026 'Subtotal, Office of the Director' is the lines
   printed under the heading (OD + Kids First in 21 of 22 printed columns; + an emergency line in the 22nd); Kids
   First is also recorded as a contained line. The FY2022 'Office of the Director' observations are superseded.
3. Diaper Grants (200,000, House FY2022, printed under SSBG): a separate line of ACC-HHS-ACF-SSBG (component
   chamber_proposal, added to nothing), with the note "House proposed Diaper Grants under this heading (not enacted)."
The short sums these lines explain are resolved with that reason (reviewer "owner rules (2026-10-08)").
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.argv, _argv = sys.argv[:1], sys.argv
import build_year as B  # noqa: E402
sys.argv = _argv
import validate_approps as V  # noqa: E402

STAGED = ROOT / "data" / "staged.json"
TODAY = "2026-10-08"
REVIEWER = "owner rules (2026-10-08)"
HRSA_PS, NIH_OD, SSBG = "ACC-HHS-HRSA-PROGRAM-SUPPORT", "ACC-HHS-NIH-OD", "ACC-HHS-ACF-SSBG"
COMPONENTS = [
    {"component_id": "congressionally_directed_spending", "label": "Congressionally directed spending",
     "kind": "contained", "description": "community project funding / congressionally directed spending printed as its "
                                         "own line inside the account's figure (already in the headline: never add it again)"},
    {"component_id": "kids_first", "label": "Gabriella Miller Kids First", "kind": "contained",
     "description": "the Gabriella Miller Kids First Research Act line printed under the NIH Office of the Director "
                    "(inside the Office of the Director's figure: never add it again)"},
    {"component_id": "chamber_proposal", "label": "Chamber proposal", "kind": "part",
     "description": "a one-off line a chamber proposed under the account's heading, not in the account's own line "
                    "(the cell shows it as a note)"},
]
HN = {"canonical_account_id": HRSA_PS, "former_name": "Program Management",
      "evidence": "HRSA's single program-management line through FY2022: H.Rept. 117-96 p.470 and the FY2022 Senate "
                  "chair's draft p.346 print 'Program Management' (including community project funding). The FY2023 "
                  "reports print the same FY2022 money as 'Total, HRSA-Wide Activities and Program Support' 1,213,196 = "
                  "Program Management 155,300 + Community Projects 1,057,896 (H.Rept. 117-403 p.809). Valid for FY2022 "
                  "and earlier; from FY2023 'Program Management' is a line inside this account, not its headline. "
                  "Approved by the owner 2026-10-08."}
# FY2022 stages: (stage, extraction package, column, source document)
STAGES = [("House Reported", "CRPT-117hrpt96", "Bill", "SRC-CRPT-117HRPT96"),
          ("President's Budget", "CRPT-117hrpt96", "FY 2022 Request", "SRC-CRPT-117HRPT96"),
          ("Senate Reported", "MANUAL-LHHS-FY2022-SenateReported-explanatory_statement-b033ae17",
           "Committee recommendation", "SRC-EXPL-LHHS-FY2022-SENATE"),
          ("Enacted", "CRPT-117hrpt403", "FY 2022 Enacted", "SRC-CRPT-117HRPT403")]
DOC_PKG = {"SRC-CRPT-117HRPT403": "CRPT-117hrpt403", "SRC-EXPL-LHHS-FY2023-SENATE":
           "MANUAL-LHHS-FY2023-SenateReported-explanatory_statement-88301550", "SRC-EXPL-LHHS-FY2024-HOUSE":
           "MANUAL-LHHS-FY2024-HouseReported-explanatory_statement-408e3388", "SRC-CRPT-118SRPT84": "CRPT-118srpt84",
           "SRC-CRPT-118SRPT207": "CRPT-118srpt207", "SRC-CRPT-118HRPT585": "CRPT-118hrpt585",
           "SRC-CRPT-119HRPT271": "CRPT-119hrpt271", "SRC-CRPT-119HRPT696": "CRPT-119hrpt696",
           "SRC-CRPT-119SRPT55": "CRPT-119srpt55"}
DIAPER_NOTE = "House proposed Diaper Grants under this heading (not enacted)."


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(STAGED.read_text())
    obs = data["observations"]
    by_id = {o["observation_id"]: o for o in obs}
    acct = {x["canonical_account_id"]: x for x in data["accounts"]}
    assert not any(c["component_id"] == "kids_first" for c in data["components"]), "already applied"
    ext = {}

    def rows(pkg, col, ag):
        if pkg not in ext:
            ext[pkg] = B.load_extraction(pkg)[1]
        return B.rows_for(ext[pkg], col, ag) if col in ext[pkg] else []

    def ids(prefix, field, rows_, width):
        n = max(int(r[field][len(prefix):]) for r in rows_ if r[field].startswith(prefix) and r[field][len(prefix):].isdigit())
        while True:
            n += 1
            yield f"{prefix}{n:0{width}d}"
    oids = ids("OBS-LHHS-", "observation_id", obs, 4)
    vids = ids("VAL-LHHS-", "validation_id", data["validations"], 5)
    new_obs, new_val, superseded, resolved = [], [], [], []

    def record(aid, stage, src, f, col, amount=None, component="", headline="", method=None, note="", label=None):
        o = {"observation_id": next(oids), "canonical_account_id": aid, "fiscal_year": 2022, "stage": stage,
             "chamber": {"House Reported": "House", "Senate Reported": "Senate"}.get(stage, "N/A"),
             "bill_id": None, "report_id": None, "amount": f["amount"] if amount is None else amount,
             "amount_type": "budget authority", "offsetting_collections": "FALSE", "transfer_link_account_id": "",
             "source_document_id": src, "source_page": str(f["source_page"]),
             "source_table_or_section": (f"Title II, {acct[aid]['agency']} -- "
                                         + (label or f"printed as {f['account_name_as_written'].strip()!r} [{col}]")
                                         + (f" -- Note: {note}" if note else "")),
             "extraction_method": method or f.get("extraction_method") or "AI-extracted",
             "confidence": f.get("extraction_confidence") or 0.95, "verification_status": "",
             "component": component, "headline_observation_id": headline}
        for r in f.get("_records", []):
            if r["rule_applied"] in ("source_text", "unit") and not method:
                new_val.append({"observation_id": o["observation_id"], "rule_applied": r["rule_applied"],
                                "expected_result": r["expected_result"], "observed_result": r["observed_result"],
                                "result": r["result"]})
        new_obs.append(o)
        return o

    def one(rs, pattern, path=""):
        hit = [f for f in rs if re.fullmatch(pattern, f["_clean"]) and path in f["account_path"] and not f.get("is_memo")]
        assert len(hit) == 1, (pattern, [(h["_clean"], h["amount"]) for h in hit])
        return hit[0]

    # 1. HRSA Program Management (FY2022 House, request, Senate: the old structure's single line)
    for stage, pkg, col, src in STAGES[:3]:
        assert not any(o["canonical_account_id"] == HRSA_PS and (o["fiscal_year"], o["stage"]) == (2022, stage) for o in obs)
        f = one(rows(pkg, col, "HRSA"), r"program management")
        o = record(HRSA_PS, stage, src, f, col)
        print("HRSA Program Support", stage, f"{o['amount'] // 1000:,}", o["observation_id"])
        if stage == "President's Budget":                # the other chamber's request column
            g = one(rows(STAGES[2][1], "Budget estimate", "HRSA"), r"program management")
            new_val.append({"observation_id": o["observation_id"], "rule_applied": "cross_document",
                            "expected_result": f"{STAGES[2][3]} p.{g['source_page']} prints {g['amount'] // 1000:,} "
                                               f"('{g['account_name_as_written'].strip()}' [Budget estimate])",
                            "observed_result": f"{o['amount'] // 1000:,} as recorded",
                            "result": "pass" if g["amount"] == o["amount"] else "flag"})
    # the earmark line where printed on its own, under every HRSA Program Support figure FY2022-FY2027
    for h in [o for o in obs if o["canonical_account_id"] == HRSA_PS and o["fiscal_year"] >= 2022 and not o["component"]
              and o["verification_status"] != "superseded" and o["source_document_id"] in DOC_PKG]:
        m = re.search(r"\[([^\]]+)\]$", B.PRINTED.sub("", h["source_table_or_section"]) or "") or \
            re.search(r"\[([^\]]+)\]", h["source_table_or_section"])
        if not m:
            continue
        col = m.group(1)
        hits = [f for f in rows(DOC_PKG[h["source_document_id"]], col, "HRSA")
                if re.search(r"community project|congressionally directed", f["_clean"]) and not f.get("is_memo")
                and f["amount"]]
        for f in hits:
            o = record(HRSA_PS, h["stage"], h["source_document_id"], f, col, component="congressionally_directed_spending",
                       headline=h["observation_id"])
            o["fiscal_year"], o["chamber"] = h["fiscal_year"], h["chamber"]
            print("  earmarks", h["fiscal_year"], h["stage"], f"{f['amount'] // 1000:,}", "under", h["observation_id"])

    # 2. NIH Office of the Director, FY2022: OD + Kids First
    for stage, pkg, col, src in STAGES:
        old = [o for o in obs if o["canonical_account_id"] == NIH_OD and (o["fiscal_year"], o["stage"]) == (2022, stage)
               and not o["component"] and o["verification_status"] != "superseded"]
        assert len(old) == 1, (stage, old)
        rs = rows(pkg, col, "NIH")
        od, kf = one(rs, r"office of the director"), one(rs, r"gabriella miller kids first research act.*")
        assert od["amount"] == old[0]["amount"], (stage, od["amount"], old[0]["amount"])
        total = od["amount"] + kf["amount"]
        label = (f"derived, not a printed line: 'Office of the Director' {od['amount'] // 1000:,} (p.{od['source_page']}) + "
                 f"'{B.clean_label(kf)}' {kf['amount'] // 1000:,} (p.{kf['source_page']}) = {total // 1000:,} (thousands); "
                 "the FY2023-FY2026 tables print 'Subtotal, Office of the Director' as the lines under the heading")
        h = record(NIH_OD, stage, src, dict(od, source_page=od["source_page"]), col, amount=total, method="derived",
                   label=label)
        new_val.append({"observation_id": h["observation_id"], "rule_applied": "structural",
                        "expected_result": f"headline = 'Office of the Director' + Gabriella Miller Kids First: "
                                           f"{od['amount'] // 1000:,} + {kf['amount'] // 1000:,} = {total // 1000:,} (thousands)",
                        "observed_result": f"{total // 1000:,} as recorded", "result": "pass"})
        record(NIH_OD, stage, src, kf, col, component="kids_first", headline=h["observation_id"])
        old[0]["verification_status"] = "superseded"
        old[0]["superseded_by_observation_id"] = h["observation_id"]
        superseded.append((old[0]["observation_id"], h["observation_id"]))
        print("NIH OD", stage, f"{old[0]['amount'] // 1000:,} ->", f"{total // 1000:,}", old[0]["observation_id"], "->",
              h["observation_id"])

    # 3. Diaper Grants
    stage, pkg, col, src = STAGES[0]
    f = one(rows(pkg, col, "ACF"), r"diaper grants", "Social Services Block Grant")
    o = record(SSBG, stage, src, f, col, component="chamber_proposal", note=DIAPER_NOTE)
    print("Diaper Grants", f"{o['amount'] // 1000:,}", o["observation_id"])

    # the short sums these explain
    reasons = {
        "ACC-HHS-HRSA-TOTAL": "The shortfall is HRSA's 'Program Management', the FY2022 heading of "
                              "ACC-HHS-HRSA-PROGRAM-SUPPORT (Historical Name, valid FY2022 and earlier; owner 2026-10-08), "
                              "now recorded: with it the sum equals the printed total.",
        "ACC-HHS-NIH-TOTAL": "The 12,600 is Gabriella Miller Kids First, now inside ACC-HHS-NIH-OD (derived as the Office of "
                             "the Director line + Kids First, the FY2023-FY2026 subtotal; owner 2026-10-08); any other "
                             "part of the shortfall is ARPA-H printed in the Office of the Secretary's section.",
        "ACC-HHS-ACF-TOTAL": "The shortfall is ACC-HHS-ACF-CHILD-SUPPORT's new advance (1,300,000, an advance line of the "
                             "account, not recorded) + Diaper Grants (200,000), now recorded as an SSBG chamber-proposal "
                             "line (owner 2026-10-08): with them the sum equals the printed total."}
    for v in data["validations"]:
        o = by_id.get(v["observation_id"])
        if (o and o["fiscal_year"] == 2022 and v["rule_applied"] == "table_total" and v["human_review_status"] == "pending"
                and o["canonical_account_id"] in reasons and "outside our accounts" in v["expected_result"]):
            v.update({"human_review_status": "resolved", "reviewer": REVIEWER, "resolution": reasons[o["canonical_account_id"]]})
            resolved.append((v["validation_id"], o["canonical_account_id"], o["stage"]))
    for vid, aid, st in resolved:
        print("resolved", vid, aid, st)

    for v in new_val:
        v.update({"validation_id": next(vids), "human_review_status": "", "reviewer": "", "resolution": ""})
    checks = collections.defaultdict(list)
    for v in data["validations"] + new_val:
        checks[v["observation_id"]].append((v["rule_applied"], v["result"], v["expected_result"],
                                            v["human_review_status"], v["resolution"]))
    for o in new_obs:
        o["verification_status"] = V.verification_status(o["confidence"], checks[o["observation_id"]])
    order = list(data["observations"][0]) + ["superseded_by_observation_id"]
    order = list(dict.fromkeys(order))
    vorder = list(data["validations"][0])
    print(f"{len(new_obs)} observations, {len(new_val)} validations, {len(superseded)} superseded, {len(resolved)} "
          f"sums resolved; statuses {collections.Counter(o['verification_status'] for o in new_obs)}")
    if a.write:
        data["components"] += COMPONENTS
        hn_ids = ids("HN-LHHS-", "historical_name_id", data["historical_names_tab"], 4)
        data["historical_names_tab"].append({"historical_name_id": next(hn_ids), **HN, "approved_date": TODAY,
                                             "confidence": 1.0, "human_reviewed": "TRUE"})
        data["observations"] += [{k: o.get(k, "") for k in order if k in o} for o in new_obs]
        data["validations"] += [{k: v[k] for k in vorder} for v in new_val]
        STAGED.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print("wrote", STAGED.relative_to(ROOT))


if __name__ == "__main__":
    main()
