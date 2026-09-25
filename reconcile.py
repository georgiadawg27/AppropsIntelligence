"""
reconcile.py

When GPO's official version of a document arrives, reconcile any advance
copy ingested by hand for the same slot (bill + stage) against it.

  1. Same bytes (hash) -> the advance copy is confirmed; its observations,
     re-validated, lose the "provisional" cap. Nothing else to do.
  2. Different bytes -> GPO re-typesets and restamps every page, so bytes
     almost never match even when every number does. Extract both through
     the normal pipeline and compare observation by observation:
       - every value equal       -> confirmed, as for (1), plus one pass
                                    record noting the bytes differed;
       - any value differs, or a row exists in only one version
                                 -> superseded: GPO's observations are the
                                    current ones; each advance observation is
                                    marked superseded (pointing at its GPO
                                    counterpart), and every difference gets a
                                    Validation Record (rule
                                    advance_copy_reconciliation: old value, new
                                    value, both Source Document ids) flagged
                                    for human review -- resolved, but never
                                    silently.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import extract_approps as ex

RULE = "advance_copy_reconciliation"
CRPT_STAGE = {"h": "House Reported", "s": "Senate Reported"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def advance_copies_for(manifest, bill_package_id, official_package_id):
    """Unconfirmed advance copies in the manifest for the slot an official
    committee report fills: same bill (Congress + type + number, from the
    bill package it was found through) and the report's stage."""
    import govinfo_ingest as g
    bill = g.parse_bill_package_id(bill_package_id)
    m = re.match(r"CRPT-(\d+)([hs])rpt\d+", official_package_id)
    if not bill or not m:
        return []
    stage = CRPT_STAGE[m.group(2)]
    out = []
    for pid, e in manifest.items():
        if not (e.get("ingest_method") == "manual" and e.get("advance_copy")
                and e.get("confirmation_status") == "unconfirmed" and e.get("stage") == stage and e.get("bill_id")):
            continue
        try:
            spec = g.parse_bill_spec(e["bill_id"])
        except ValueError:
            continue
        if spec == bill[:3]:
            out.append(pid)
    return out


def observation_key(o):
    return (o["account_path"], o["column_header"], o.get("fiscal_year"), o.get("stage"))


def compare(advance_obs, official_obs):
    """-> (pairs, differing pairs, advance-only, official-only)."""
    off = {observation_key(o): o for o in official_obs}
    adv = {observation_key(o): o for o in advance_obs}
    pairs = [(adv[k], off[k]) for k in adv if k in off]
    diffs = [(a, o) for a, o in pairs if a["amount"] != o["amount"]]
    return pairs, diffs, [adv[k] for k in adv if k not in off], [off[k] for k in off if k not in adv]


def _record(observation_id, expected, observed, result):
    return {"validation_id": str(uuid.uuid4()), "observation_id": observation_id, "rule_applied": RULE,
            "expected_result": expected, "observed_result": observed, "result": result,
            "human_review_status": "pending" if result == "flag" else None, "reviewer": None, "resolution": None}


def reconcile(advance_id, official_id, manifest, store_dir, out_dir, cache_dir=ex.CACHE_DIR, offline=False,
              manifest_path=None):
    """Reconcile one advance copy against one official package, both already
    in store_dir / manifest. Writes the updated manifest, the re-extracted
    advance observations and a report to out_dir/reconciliation/."""
    store_dir, out_dir = Path(store_dir), Path(out_dir)
    manifest_path = Path(manifest_path or store_dir / "manifest.json")
    adv_e, off_e = manifest[advance_id], manifest[official_id]
    adv_pdf, off_pdf = Path(adv_e["stored_path"]), Path(off_e["stored_path"])
    adv_doc_id = str(uuid.uuid5(ex.OBS_NAMESPACE, f"{advance_id}|{adv_e['hash']}"))
    off_doc_id = str(uuid.uuid5(ex.OBS_NAMESPACE, f"{official_id}|{off_e['hash']}"))
    records, superseded = [], {}

    def extract(pdf):
        return ex.run(pdf, cache_dir=cache_dir, offline=offline, out_dir=out_dir, verbose=False,
                      manifest_path=manifest_path)

    if adv_e["hash"] == off_e["hash"]:
        outcome = "confirmed_identical_bytes"
    else:
        official = extract(off_pdf)
        advance = extract(adv_pdf)
        pairs, diffs, adv_only, off_only = compare(advance["observations"], official["observations"])
        if not diffs and not adv_only and not off_only:
            outcome = "confirmed_identical_values"
            records.append(_record(None, f"official {official_id} (Source Document {off_doc_id}) values",
                                   f"advance copy {advance_id} (Source Document {adv_doc_id}): bytes differ, "
                                   f"all {len(pairs)} values identical", "pass"))
        else:
            outcome = "superseded"
            for a, o in diffs:
                records.append(_record(a["observation_id"],
                                       f"{o['amount']} per official {official_id} (Source Document {off_doc_id})",
                                       f"{a['amount']} per advance copy {advance_id} (Source Document {adv_doc_id})",
                                       "flag"))
            for a in adv_only:
                records.append(_record(a["observation_id"], f"no counterpart in official {official_id} ({off_doc_id})",
                                       f"{a['amount']} per advance copy {advance_id} ({adv_doc_id})", "flag"))
            for o in off_only:
                records.append(_record(o["observation_id"], f"{o['amount']} per official {official_id} ({off_doc_id})",
                                       f"not in advance copy {advance_id} ({adv_doc_id})", "flag"))
            counterpart = {observation_key(a): o["observation_id"] for a, o in pairs}
            for a in advance["observations"]:
                superseded[a["observation_id"]] = counterpart.get(observation_key(a))

    adv_e["confirmation_status"] = "superseded" if outcome == "superseded" else "confirmed"
    adv_e["reconciled_with_document_id"] = official_id
    adv_e["reconciled_at"] = _now()
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Re-extract the advance copy under its new status: confirmed -> the
    # provisional cap comes off; superseded -> every row points at GPO's.
    advance = extract(adv_pdf)
    for o in advance["observations"]:
        if o["observation_id"] in superseded:
            o["verification_status"] = "superseded"
            o["superseded_by_observation_id"] = superseded[o["observation_id"]]
            o["verification_reason"] = f"superseded by the official version {official_id}"
    advance["validation_records"].extend(records)
    Path(advance["_paths"]["observations"]).write_text(json.dumps(
        {k: v for k, v in advance.items() if k != "_paths"}, indent=2))

    report = {"advance_package_id": advance_id, "official_package_id": official_id,
              "advance_source_document_id": adv_doc_id, "official_source_document_id": off_doc_id,
              "advance_sha256": adv_e["hash"], "official_sha256": off_e["hash"], "outcome": outcome,
              "reconciled_at": adv_e["reconciled_at"], "validation_records": records,
              "superseded": superseded}
    dest = out_dir / "reconciliation" / f"{advance_id}__{official_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2))
    report["_path"] = str(dest)
    report["advance_result"] = advance
    return report
