"""
A committee draft's official version arrives (brief D-5 part 3): reconcile the stage's draft figures against it.

    python scripts/reconcile_draft.py --subcommittee CODE --fy N --stage "House Reported" \\
        --official SRC-CRPT-119HRPT652 --rows official_rows.json            # what it would do; nothing written
    ... --write                                                            # write data/staged.json

Inputs (a missing one is said and the run stops; nothing is guessed):
  - a draft stage: its Bill Report Reference row has draft TRUE, and its current figures cite a committee draft
    (approps_store.committee_drafts: not govinfo or Congress.gov text);
  - the official document, already registered in data/staged.json source_docs, and official text (govinfo or
    Congress.gov);
  - --rows: the official version's figures for the stage, as its own pages print them (JSON list of
    {canonical_account_id, amount_type, component, amount, source_page, source_table_or_section,
    extraction_method}). The official figures are never entered beside the draft's by hand: two current figures
    for one fact can't both stand (the store's observation_fact index), so this script enters them.

For each figure (account, amount type, component, transfer link):
  same value in both   -> confirmed: a new observation cites the official document and page; the draft's is
                          superseded by it (superseded_by_observation_id); a passing cross_document record says so.
  different value      -> the official figure supersedes the draft's the same way (new observation, old one linked)
                          and a cross_document record flags the difference for review: old and new figures, both
                          documents and pages.
  only in the draft    -> the draft's figure stays; a review item says the official version does not print it
                          (never a confirmed absence: a table's silence is not one).
  only in the official -> a new observation, with a review item saying the draft did not print it.
Nothing is overwritten and nothing is silent. New IDs come from ids.Allocator (live and retired IDs counted); new
observations get today's added date (added_dates.py). Afterwards run the intake for the cell (scripts/intake.py
--write), which re-runs the text check, the tagger and measure_reconcile over the new figures.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import added_dates  # noqa: E402
import approps_store as S  # noqa: E402
import ids as IDS  # noqa: E402
import intake_watch as W  # noqa: E402
import paths  # noqa: E402

RETIRED = ROOT / "reference" / "review" / "lhhs" / "retired_ids.json"
COPIED = ("canonical_account_id", "fiscal_year", "stage", "chamber", "amount_type", "component",
          "offsetting_collections", "transfer_link_account_id")


class MissingInput(Exception):
    pass


def key(o):
    return (o["canonical_account_id"], o["amount_type"], o.get("component") or "", o.get("transfer_link_account_id") or "")


def money(n):
    return f"{n:,}"


def cite(doc, page):
    return f"{doc} p.{page}" if page else doc


def reconcile(data, sc, fy, stage, official_id, rows, retired=(), when=None):
    """Reconcile in place. Returns {"confirmed": [...], "superseded": [...], "draft_only": [...],
    "official_only": [...], "review": [validation ids]}; raises MissingInput naming what is missing."""
    docs = {d["document_id"]: d for d in data["source_docs"]}
    if official_id not in docs:
        raise MissingInput(f"{official_id} is not a source document in data/staged.json: register it first")
    if not S.is_official_text(docs[official_id]["url_or_identifier"]):
        raise MissingInput(f"{official_id} is not govinfo or Congress.gov text, so it can't be the official version")
    drafts = W.drafts_by_cell(data).get((sc, fy, stage))
    if not drafts:
        raise MissingInput(f"{sc} FY{fy} {stage} has no committee draft figures to reconcile (Bill Report Reference "
                           "draft TRUE and current figures from a committee draft)")
    if not rows:
        raise MissingInput(f"no official figures given (--rows) for {sc} FY{fy} {stage} from {official_id}")
    acc = {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}
    for r in rows:
        if acc.get(r.get("canonical_account_id")) != sc:
            raise MissingInput(f"official row {r!r}: not an account of {sc}")
        if not isinstance(r.get("amount"), int):
            raise MissingInput(f"official row {r!r}: amount must be whole dollars")
        if not r.get("source_page"):
            raise MissingInput(f"official row {r!r}: no source_page (every figure cites its page)")
    draft_obs = {key(o): o for o in data["observations"]
                 if acc.get(o["canonical_account_id"]) == sc and o["fiscal_year"] == fy and o["stage"] == stage
                 and o["source_document_id"] in drafts and o.get("verification_status") != "superseded"}
    official = {}
    for r in rows:
        k = key(r)
        if k in official:
            raise MissingInput(f"official rows give {k} twice")
        official[k] = r
    used = ([o["observation_id"] for o in data["observations"]] + [v["validation_id"] for v in data["validations"]]
            + list(retired))
    alloc = IDS.Allocator(used)
    out = {"confirmed": [], "superseded": [], "draft_only": [], "official_only": [], "review": []}
    new_of = {}

    def record(obs_id, expected, observed, result):
        vid = alloc.next("VAL", sc)
        data["validations"].append({"validation_id": vid, "observation_id": obs_id, "rule_applied": "cross_document",
                                    "expected_result": expected, "observed_result": observed, "result": result,
                                    "human_review_status": "pending" if result != "pass" else "", "reviewer": "",
                                    "resolution": ""})
        if result != "pass":
            out["review"].append(vid)

    def enter(r, like):
        oid = alloc.next("OBS", sc)
        o = {k: like.get(k, "") for k in COPIED} | {
            "observation_id": oid, "bill_id": "", "report_id": "", "amount": r["amount"],
            "component": r.get("component") or "", "transfer_link_account_id": r.get("transfer_link_account_id") or "",
            "headline_observation_id": "", "source_document_id": official_id, "source_page": str(r["source_page"]),
            "source_table_or_section": r.get("source_table_or_section") or "",
            "extraction_method": r.get("extraction_method") or "human-entered", "confidence": 1.0,
            "verification_status": "unverified"}
        data["observations"].append(o)
        return o

    for k in sorted(set(draft_obs) | set(official)):
        d, r = draft_obs.get(k), official.get(k)
        if d and r:
            o = enter(r, d)
            new_of[d["observation_id"]] = o
            d["verification_status"] = "superseded"
            d["superseded_by_observation_id"] = o["observation_id"]
            was = f"{money(d['amount'])} per {cite(d['source_document_id'], d.get('source_page'))} (committee draft)"
            now = f"{money(r['amount'])} per {cite(official_id, r['source_page'])}"
            if d["amount"] == r["amount"]:
                record(o["observation_id"], was, f"{now}: the official version confirms the draft", "pass")
                out["confirmed"].append(o["observation_id"])
            else:
                record(o["observation_id"], was, f"{now}: the official version differs; it supersedes the draft's "
                       f"{d['observation_id']}", "flag")
                out["superseded"].append(o["observation_id"])
        elif d:
            record(d["observation_id"], f"{money(d['amount'])} per {cite(d['source_document_id'], d.get('source_page'))}"
                   " (committee draft)", f"{official_id} prints no such line: the draft's figure stays; not a "
                   "confirmed absence", "flag")
            out["draft_only"].append(d["observation_id"])
        else:
            like = {"canonical_account_id": r["canonical_account_id"], "fiscal_year": fy, "stage": stage,
                    "chamber": {"House Reported": "House", "Senate Reported": "Senate"}.get(stage, "N/A"),
                    "amount_type": r["amount_type"], "offsetting_collections": "FALSE"}
            o = enter(r, like)
            record(o["observation_id"], f"the committee draft ({', '.join(drafts)}) prints no such line",
                   f"{money(r['amount'])} per {cite(official_id, r['source_page'])}: only in the official version",
                   "flag")
            out["official_only"].append(o["observation_id"])
    # a component line inside a superseded headline points at the headline that replaced it
    for d_id, o in new_of.items():
        d = next(x for x in data["observations"] if x["observation_id"] == d_id)
        if d.get("headline_observation_id"):
            h = new_of.get(d["headline_observation_id"])
            o["headline_observation_id"] = h["observation_id"] if h else d["headline_observation_id"]
    added_dates.stamp(data, when)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--subcommittee", required=True)
    p.add_argument("--fy", type=int, required=True)
    p.add_argument("--stage", required=True, choices=["House Reported", "Senate Reported"])
    p.add_argument("--official", required=True, help="the official document's id (registered in source_docs)")
    p.add_argument("--rows", required=True, help="the official version's figures (JSON list)")
    p.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    data = json.loads(paths.staged().read_text())
    retired = json.loads(RETIRED.read_text()) if RETIRED.exists() else {}
    try:
        out = reconcile(data, a.subcommittee, a.fy, a.stage, a.official, json.loads(Path(a.rows).read_text()),
                        list(retired.get("observations", {})) + list(retired.get("validations", {})))
    except MissingInput as e:
        print(f"missing input: {e}")
        return 1
    for k, v in out.items():
        print(f"{k}: {len(v)}" + (f" ({', '.join(v)})" if v else ""))
    if a.write:
        paths.staged().write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(f"wrote {paths.staged()}; next: python scripts/intake.py --subcommittee {a.subcommittee} --fy {a.fy} "
              f"--stage {a.stage!r} --write")
    return 2 if out["review"] else 0


if __name__ == "__main__":
    sys.exit(main())
