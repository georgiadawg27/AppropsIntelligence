"""
Intake for one cell -- (subcommittee, fiscal year, stage) -- of any subcommittee: extraction, checks, review items.
The generalized form of reference/review/lhhs/backfill/ (those per-year scripts stay as history).

    python scripts/intake.py --subcommittee CODE --fy N --stage S            # what it finds; nothing written
    python scripts/intake.py --subcommittee CODE --fy N --stage S --write    # also bring data/staged.json's checks
                                                                             # and components up to date

Stages: "President's Budget", "House Reported", "Senate Reported", "Enacted" (or PB, HOUSE, SENATE, ENACTED).
With APPROPS_DATA_ROOT (paths.py) it works on another data root.

It never adds or changes a figure: the cell's figures are entered first (data/staged.json, each with its source
page). Then, in order:

  0. inputs     the subcommittee code is one the code knows (subcommittees.py or a rule file) and usable in IDs
                (ids.py); the cell has a Bill Report Reference row; a bill or law stage has its committed text
                (data/legislative_text/) or an exemption; a President's Budget cell has the request language read
                (reference/review/accounting/request_language.py). A missing input is reported and the run stops.
  1. extraction each headline's paragraph in the stage's text (check_legislative_text.check) and its components
                (tag_components: the core's rules and the subcommittee's rule file)
  2. checks     --write runs, over the data root: check_legislative_text, tag_components, measure_reconcile
                (each --write), then the text check again (statuses)
  3. review     reference/review/<CODE>/intake/FY<N>-<STAGE>.csv under the data root: every thing the core and the
                rule files can't account for, named -- a text figure that differs or can't be placed, a paragraph
                no account reads, an account with no figure and no confirmed absence, a phrase no rule reads, a line
                of a kind no rule reads, a printed view that doesn't reconcile. What a rule file explains (hook
                'absence': e.g. a chamber's account the other chamber's bill doesn't carry by custom) is listed as
                info with the rule's reason, never as an absence or a blank.

Exit status: 0 when nothing needs review, 1 when a missing input stopped it, 2 when items await review.
"""

import argparse
import collections
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import check_legislative_text as CT  # noqa: E402
import ids as IDS  # noqa: E402
import legislative_text as L  # noqa: E402
import measure_reconcile as MR  # noqa: E402
import paths  # noqa: E402
import rules_registry as R  # noqa: E402
import subcommittees as SC  # noqa: E402
import tag_components as TC  # noqa: E402

STAGES = {"PB": "President's Budget", "HOUSE": "House Reported", "SENATE": "Senate Reported", "ENACTED": "Enacted"}
SLUG = {v: k for k, v in STAGES.items()}
COLUMNS = ["kind", "status", "observation_id", "account", "what", "found", "rule"]
CHECKS = ("check_legislative_text.py", "tag_components.py", "measure_reconcile.py", "check_legislative_text.py")


def item(kind, status, what, observation_id="", account="", found="", rule=""):
    return {"kind": kind, "status": status, "observation_id": observation_id, "account": account, "what": what,
            "found": found, "rule": rule}


def inputs(data, sc, fy, stage):
    """Step 0 -> the missing inputs (each a review item), [] when the cell can be read."""
    out = []
    try:
        IDS.check_code(sc)
    except IDS.IdError as e:
        out.append(item("missing input", "missing", str(e)))
    if sc not in SC.known():
        out.append(item("missing input", "missing", f"subcommittee {sc} is unknown: add reference/rules/{sc}.py "
                                                    "(reference/ADDING_A_SUBCOMMITTEE.md)"))
    if not any(a["subcommittee"] == sc for a in data["accounts"]):
        out.append(item("missing input", "missing", f"no account of {sc} in data/staged.json"))
    ref = next((r for r in data["bill_report_refs"] if r["subcommittee"] == sc and int(r["fiscal_year"]) == fy
                and r["stage"] == stage), None)
    if ref is None:
        out.append(item("missing input", "missing", f"no Bill Report Reference row for {sc} FY{fy} {stage}"))
        return out
    if stage in L.BILL_STAGES:
        reqs, nodoc = L.required_documents(data)
        exempt = CT.load_exempt(L.TEXT_DIR)
        doc = next((d for d, r in reqs.items() if ref["reference_id"] in r["references"]), None)
        if ref["reference_id"] in exempt or (doc and doc in exempt):
            return out
        if doc is None:
            out.append(item("missing input", "missing", f"{ref['reference_id']} names no bill or law -- exempt it "
                                                        "with a reason (data/legislative_text/exemptions.json)"))
        elif not L.text_path(doc).exists():
            out.append(item("missing input", "missing", f"{doc}: no committed text -- fetch it "
                                                        "(scripts/fetch_legislative_text.py) or exempt it"))
    elif stage == TC.REQUEST_STAGE:
        heads = [o for o in data["observations"] if int(o["fiscal_year"]) == fy and o["stage"] == stage
                 and not o.get("component") and o["amount_type"] == "budget authority"
                 and o["verification_status"] != "superseded" and account_sc(data).get(o["canonical_account_id"]) == sc]
        unread = sorted({o["canonical_account_id"] for o in heads
                         if (o["canonical_account_id"], fy) not in TC.request_language()})
        if unread:
            chapters = R.active().data("appendix", sc).get("chapters")
            out.append(item("missing input", "missing",
                            f"the request language of {len(unread)} account(s) is not read from the FY{fy} Budget "
                            "Appendix" + (f" ({', '.join(chapters)} chapters)" if chapters else
                                          " (no chapters named: add the rule file's 'appendix' rule)")
                            + " (reference/review/accounting/request_language.py): " + ", ".join(unread)))
    return out


def account_sc(data):
    return {a["canonical_account_id"]: a["subcommittee"] for a in data["accounts"]}


def run_checks():
    for script in CHECKS:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--write"], capture_output=True,
                           text=True, env=os.environ.copy())
        if r.returncode != 0:
            raise SystemExit(f"{script} --write failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")


def review(data, sc, fy, stage):
    """Step 3 -> the cell's items."""
    out = []
    acc = {a["canonical_account_id"]: a for a in data["accounts"]}
    mine = {a for a, x in acc.items() if x["subcommittee"] == sc}
    cell_obs = {o["observation_id"]: o for o in data["observations"] if o["canonical_account_id"] in mine
                and int(o["fiscal_year"]) == fy and o["stage"] == stage and o["verification_status"] != "superseded"}
    heads = {o["canonical_account_id"]: o for o in cell_obs.values()
             if not o.get("component") and o["amount_type"] == "budget authority"}
    # the text check
    texts, exempt = CT.load_texts(L.TEXT_DIR), CT.load_exempt(L.TEXT_DIR)
    outcomes = CT.check(data, texts, exempt)
    placed, docs = set(), set()
    for oid, res in sorted(outcomes.items()):
        if oid not in cell_obs:
            continue
        if res.get("document"):
            docs.add(res["document"])
        if res.get("paragraph"):
            placed |= set(res["paragraph"]["paragraph_id"].split("+"))
        if res["result"] in ("mismatch", "ambiguous", "no paragraph"):
            out.append(item(f"text check: {res['result']}", "review", res.get("how", ""), oid, res["account"],
                            f"${res['text_amount']:,}" if res.get("text_amount") is not None else ""))
    refs = [r for r in data["bill_report_refs"] if r["subcommittee"] == sc and int(r["fiscal_year"]) == fy
            and r["stage"] == stage]
    division = (refs[0].get("division") or "").strip() if refs else ""
    for doc in sorted(docs):
        for p in texts[doc]["paragraphs"]:
            if p["amounts"] and p["paragraph_id"] not in placed and (not division or not p["division"]
                                                                     or p["division"] == division):
                out.append(item("paragraph no account reads", "review",
                                f"{CT.where(p)}: {len(p['amounts'])} amount(s), first ${p['amounts'][0]['amount']:,}"))
    # missing figures
    absences = {(c["canonical_account_id"], int(c["fiscal_year"]), c["stage"]) for c in data["confirmed_absences"]}
    for a in sorted(mine):
        if acc[a].get("total_scope") or a in heads or (a, fy, stage) in absences:
            continue
        ctx = TC.ctx_for(sc, a, fy, stage)
        hit = R.active().first("absence", f"{a} FY{fy} {stage}", ctx, a, fy, stage)
        if hit:
            out.append(item("explained by a rule", "info", hit[1], account=a, rule=hit[0].id))
        else:
            out.append(item("no figure", "review", f"{acc[a]['canonical_name']}: no {stage} figure and no confirmed "
                                                   "absence citing bill or law text", account=a))
    # the tagger
    _, _, rep = TC.build(data)
    for u in rep["untagged"]:
        if u["observation_id"] in cell_obs:
            out.append(item("phrase no rule reads", "review", f"{u['where']}: '{u['phrase']}'", u["observation_id"],
                            cell_obs[u["observation_id"]]["canonical_account_id"], f"${u['amount']:,}"))
    for k, kind in (("pilot_no_paragraph", "no paragraph read for components"),
                    ("auto_default", "in_headline by default, not by arithmetic"),
                    ("unknown_table_line", "a table line of a kind no rule reads"),
                    ("retag_no_headline", "a line with no headline in its cell")):
        for x in rep[k]:
            oid = x.split(" ", 1)[0]
            if oid in cell_obs:
                out.append(item(kind, "review", x, oid, cell_obs[oid]["canonical_account_id"]))
    # measure_reconcile
    _, mr = MR.run(data)
    for o in mr:
        if o["observation_id"] in cell_obs and o["result"] == "fail":
            out.append(item(f"measure_reconcile: {o['kind']}", "review", o.get("detail", ""), o["observation_id"],
                            o.get("account", ""), rule=o.get("cause") or ""))
    return out


def write(items, sc, fy, stage):
    path = paths.data_root() / "reference" / "review" / sc / "intake" / f"FY{fy}-{SLUG.get(stage, stage)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(items)
    return path


def intake(sc, fy, stage, write_checks=False):
    """-> (items, path of the review file, exit status)"""
    stage = STAGES.get(stage.upper(), stage)
    data = json.loads(paths.staged().read_text())
    missing = inputs(data, sc, fy, stage)
    if missing:
        return missing, write(missing, sc, fy, stage), 1
    if write_checks:
        run_checks()
        data = json.loads(paths.staged().read_text())
    items = review(data, sc, fy, stage)
    return items, write(items, sc, fy, stage), 2 if any(i["status"] == "review" for i in items) else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--subcommittee", required=True)
    ap.add_argument("--fy", type=int, required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    items, path, status = intake(a.subcommittee, a.fy, a.stage, a.write)
    c = collections.Counter((i["status"], i["kind"]) for i in items)
    print(f"intake {a.subcommittee} FY{a.fy} {a.stage}: {len(items)} item(s) -> {path}")
    for (st, kind), n in sorted(c.items()):
        print(f"  {st:8} {kind}: {n}")
    if status == 1:
        print("stopped: a missing input (above) -- nothing was read or written")
    return status


if __name__ == "__main__":
    sys.exit(main())
