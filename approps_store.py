"""
approps_store.py

The relational store (SQLite, schema in store_schema.sql) and the thinnest
query surface on it.

    python approps_store.py load path/to/CJS_Title_III_Science_Pilot_Schema_Loaded_v10.xlsx [--db approps.db]
    python approps_store.py history "NASA Science" [--db approps.db] [--json]
    python approps_store.py resolve-relationship REL-0001 --reviewer NAME --resolution TEXT [--db approps.db]

load: builds a fresh database from the pilot workbook's 7 data tabs. Values
are converted strictly -- a value that doesn't fit its column stops the load
with the tab, row and column -- and the few known spelling variants are
mapped explicitly (VALUE_MAP) and counted in the load report, never silently.
Cross-tab rules the database can't express as a constraint are checked after
the insert (see check_*): they stop the load when the workbook contradicts
itself, or are reported as warnings when they're a data-quality question for
a human.

history: resolves an account name with the same fuzzy rule extraction uses
(accounts.best_match: letters-only edit distance, unambiguous, agency-scoped,
canonical plus reviewed former names in one pool) and prints the account's
observations by fiscal year and stage with each value's source document.
A query may lead with its agency ("NASA Science", "National Science
Foundation Office of Inspector General"): the agency is matched by full name
or its initials and scopes the account match, as a table's agency heading
does during extraction.
"""

import argparse
from collections import Counter
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

import accounts as A

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "store_schema.sql"
DEFAULT_DB = ROOT / "approps.db"

STAGE_ORDER = ["President's Budget", "House Reported", "Senate Reported", "Enacted", "House Passed", "Senate Passed"]

# (column, workbook value) -> stored value. Only unambiguous spelling variants
# of a Data Dictionary enum value; each use is counted in the load report.
VALUE_MAP = {("extraction_method", "human_entered"): "human-entered"}


class LoadError(Exception):
    pass


# ---------------------------------------------------------------------------
# Strict value conversion
# ---------------------------------------------------------------------------

def blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def to_bool(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str) and v.strip().upper() in ("TRUE", "FALSE"):
        return int(v.strip().upper() == "TRUE")
    raise ValueError(f"not a boolean: {v!r}")


def to_int(v):
    if isinstance(v, bool):
        raise ValueError(f"not an integer: {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    raise ValueError(f"not an integer: {v!r}")


def to_real(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"not a number: {v!r}")
    return float(v)


def to_date(v):
    """A date, from a date, a midnight datetime, 'YYYY-MM-DD' or 'YYYY-MM-DD 00:00:00'."""
    if isinstance(v, dt.datetime):
        if v.time() != dt.time():
            raise ValueError(f"a date with a time of day: {v!r}")
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T]00:00(?::00)?)?", str(v).strip())
    if m:
        dt.date.fromisoformat(m.group(1))
        return m.group(1)
    raise ValueError(f"not a date: {v!r}")


def to_timestamp(v):
    """'YYYY-MM-DD' stays date-only (the precision the workbook has);
    anything with a time becomes 'YYYY-MM-DDTHH:MM:SS'."""
    if isinstance(v, dt.datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.date.fromisoformat(s).isoformat()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2})?", s)
    if m:
        return dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{m.group(3) or ':00'}").isoformat(timespec="seconds")
    raise ValueError(f"not a timestamp: {v!r}")


def to_text(v):
    if isinstance(v, (dt.date, dt.datetime, bool)):
        raise ValueError(f"not text: {v!r}")
    return str(v).strip() if isinstance(v, str) else str(v)


# tab -> table, [(column, converter, required)]
TABS = [
    ("Account", "account", [
        ("canonical_account_id", to_text, True), ("canonical_name", to_text, True), ("agency", to_text, True),
        ("bureau", to_text, False), ("treasury_account_symbol", to_text, False), ("status", to_text, True),
        ("fund_type", to_text, True), ("effective_start", to_date, True), ("effective_end", to_date, False),
        ("historical_names", to_text, False), ("historical_identifiers", to_text, False),
        ("subcommittee", to_text, True), ("notes", to_text, False)]),
    ("Historical Name", "historical_name", [
        ("historical_name_id", to_text, True), ("canonical_account_id", to_text, True), ("former_name", to_text, True),
        ("evidence", to_text, True), ("approved_date", to_date, False), ("confidence", to_real, True),
        ("human_reviewed", to_bool, True)]),
    ("Source Document", "source_document", [
        ("document_id", to_text, True), ("source_agency", to_text, True), ("url_or_identifier", to_text, True),
        ("document_type", to_text, True), ("congress_session", to_text, False), ("fiscal_year", to_int, True),
        ("publication_date", to_date, False), ("stage", to_text, True), ("retrieval_timestamp", to_timestamp, False),
        ("source_page", to_text, False), ("also_covers", to_text, False)]),
    ("Bill Report Reference", "bill_report_reference", [
        ("reference_id", to_text, True), ("subcommittee", to_text, True), ("fiscal_year", to_int, True),
        ("stage", to_text, True), ("bill_id", to_text, False), ("report_id", to_text, False),
        ("bill_url", to_text, False), ("report_jes_url", to_text, False), ("lookup_key", to_text, True)]),
    ("Appropriations Observation", "appropriations_observation", [
        ("observation_id", to_text, True), ("canonical_account_id", to_text, True), ("fiscal_year", to_int, True),
        ("stage", to_text, True), ("chamber", to_text, False), ("bill_id", to_text, False),
        ("report_id", to_text, False), ("amount", to_int, True), ("amount_type", to_text, True),
        ("component", to_text, False),
        ("offsetting_collections", to_bool, True), ("transfer_link_account_id", to_text, False),
        ("source_document_id", to_text, True), ("source_page", to_text, False),
        ("source_table_or_section", to_text, False), ("extraction_method", to_text, True),
        ("confidence", to_real, True), ("verification_status", to_text, True)]),
    ("Account Relationship", "account_relationship", [
        ("relationship_id", to_text, True), ("from_account_id", to_text, True), ("to_account_id", to_text, True),
        ("relationship_type", to_text, True), ("effective_fiscal_year", to_int, False), ("evidence", to_text, True),
        ("confidence", to_real, True), ("human_reviewed", to_bool, True)]),
    ("Validation Record", "validation_record", [
        ("validation_id", to_text, True), ("observation_id", to_text, True), ("rule_applied", to_text, True),
        ("expected_result", to_text, False), ("observed_result", to_text, False), ("result", to_text, True),
        ("human_review_status", to_text, False), ("reviewer", to_text, False), ("resolution", to_text, False)]),
]

# Workbook columns that exist only as lookups into Bill Report Reference; they
# are checked against it (check_bill_report_lookups), not stored twice.
DERIVED_COLUMNS = {"Appropriations Observation": {"bill_url", "report_jes_url"}}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def read_tabs(workbook):
    import openpyxl                                   # only needed to load
    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    out = {}
    for tab, _, cols in TABS:
        if tab not in wb.sheetnames:
            raise LoadError(f"workbook has no {tab!r} tab")
        rows = list(wb[tab].iter_rows(values_only=True))
        head = [h.strip() if isinstance(h, str) else h for h in rows[0]]
        expected = {c for c, _, _ in cols} | DERIVED_COLUMNS.get(tab, set())
        missing, extra = expected - set(head), {h for h in head if h is not None} - expected
        if missing or extra:
            raise LoadError(f"{tab}: columns differ from the schema -- missing {sorted(missing)}, "
                            f"unexpected {sorted(extra)}")
        out[tab] = [(i, dict(zip(head, r))) for i, r in enumerate(rows[1:], start=2)
                    if any(not blank(v) for v in r)]
    return out


def convert_rows(tabs, report):
    converted = {}
    for tab, table, cols in TABS:
        out = []
        for rownum, raw in tabs[tab]:
            rec = {}
            for col, conv, required in cols:
                v = raw.get(col)
                if blank(v):
                    if required:
                        raise LoadError(f"{tab} row {rownum}: {col} is blank")
                    rec[col] = None
                    continue
                try:
                    rec[col] = conv(v)
                except ValueError as e:
                    raise LoadError(f"{tab} row {rownum}: {col}: {e}") from None
                mapped = VALUE_MAP.get((col, rec[col]))
                if mapped is not None:
                    key = f"{tab}.{col}: {rec[col]!r} -> {mapped!r}"
                    report["value_map"][key] = report["value_map"].get(key, 0) + 1
                    rec[col] = mapped
            rec["_row"] = rownum
            out.append(rec)
        converted[tab] = out
    return converted


def insert(conn, table, rows, extra=None):
    for r in rows:
        rec = {k: v for k, v in r.items() if not k.startswith("_")}
        rec.update(extra(r) if extra else {})
        cols = list(rec)
        try:
            conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                         [rec[c] for c in cols])
        except sqlite3.IntegrityError as e:
            raise LoadError(f"{table} (workbook row {r['_row']}): {e}") from None


def bill_report_key(subcommittee, fiscal_year, stage):
    return f"{subcommittee}-{fiscal_year}-{stage}"


def check_historical_names(rows):
    """Account.historical_names (display list) must equal the Historical Name
    rows for that account, in order -- the same rule build_accounts.py
    enforces."""
    problems = []
    for a in rows["Account"]:
        display = [n.strip() for n in (a["historical_names"] or "").split(";") if n.strip()]
        tab = [h["former_name"] for h in rows["Historical Name"]
               if h["canonical_account_id"] == a["canonical_account_id"]]
        if display != tab:
            problems.append(f"{a['canonical_account_id']}: Account.historical_names {display} != Historical Name {tab}")
    return problems


def check_bill_report_lookups(tabs, rows):
    """The workbook's bill_id / report_id / bill_url / report_jes_url on each
    observation are formulas looking up Bill Report Reference; their cached
    values must equal that lookup, or the workbook was saved without
    recalculating (or the formula range stopped short of a new row)."""
    subcommittee = {a["canonical_account_id"]: a["subcommittee"] for a in rows["Account"]}
    brr = {b["lookup_key"]: b for b in rows["Bill Report Reference"]}
    problems = []
    for (rownum, raw), o in zip(tabs["Appropriations Observation"], rows["Appropriations Observation"]):
        ref = brr.get(bill_report_key(subcommittee.get(o["canonical_account_id"]), o["fiscal_year"], o["stage"]))
        for col in ("bill_id", "report_id", "bill_url", "report_jes_url"):
            want = (ref or {}).get(col)
            got = None if blank(raw.get(col)) else str(raw[col]).strip()
            if want != got:
                problems.append(f"Appropriations Observation row {rownum} {o['observation_id']}: {col} is {got!r}, "
                                f"Bill Report Reference says {want!r}")
    return problems


def covered_cells(doc):
    """(fiscal_year, stage) cells a document supplies: its own plus the
    'FY2025 Enacted; FY2024 President's Budget' entries of also_covers.
    -> (cells, entries that aren't a fiscal year + stage)."""
    cells, unparsed = {(doc["fiscal_year"], doc["stage"])}, []
    for entry in (doc["also_covers"] or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        m = re.fullmatch(r"FY(\d{4}) (.+)", entry)
        if m and m.group(2) in STAGE_ORDER:
            cells.add((int(m.group(1)), m.group(2)))
        else:
            unparsed.append(entry)
    return cells, unparsed


# The stages a document of each type can itself be. (An explanatory
# statement accompanies an enacted bill or, before FY2024, a Senate bill
# posted without a report.)
DOCUMENT_TYPE_STAGES = {
    "committee_report": ("House Reported", "Senate Reported"),
    "explanatory_statement": ("House Reported", "Senate Reported", "Enacted"),
    "public_law": ("Enacted",),
    "presidents_budget": ("President's Budget",),
    "budget_appendix": ("President's Budget",),
}


def data_quality_warnings(conn):
    """Questions for a human, not load failures."""
    warnings = []
    docs = {r["document_id"]: dict(r) for r in conn.execute("SELECT * FROM source_document")}
    for d in docs.values():
        _, unparsed = covered_cells(d)
        for e in unparsed:
            warnings.append(f"source_document {d['document_id']}: also_covers entry is not a fiscal year + stage: {e!r}")
    for o in conn.execute("SELECT observation_id, fiscal_year, stage, source_document_id, source_page "
                          "FROM appropriations_observation"):
        d = docs[o["source_document_id"]]
        if (o["fiscal_year"], o["stage"]) not in covered_cells(d)[0]:
            warnings.append(f"observation {o['observation_id']} (FY{o['fiscal_year']} {o['stage']}) cites "
                            f"{d['document_id']}, which neither is nor also_covers that fiscal year + stage")
        if o["source_page"] != d["source_page"]:
            warnings.append(f"observation {o['observation_id']}: source_page {o['source_page']!r} is outside "
                            f"{d['document_id']}'s table pages {d['source_page']!r}")
    for r in conn.execute("SELECT o.observation_id, o.chamber, o.stage FROM appropriations_observation o"):
        want = {"House Reported": "House", "House Passed": "House", "Senate Reported": "Senate",
                "Senate Passed": "Senate"}.get(r["stage"], "N/A")
        if r["chamber"] != want:
            warnings.append(f"observation {r['observation_id']}: chamber {r['chamber']!r} for stage {r['stage']!r}")
    for d in docs.values():
        allowed = DOCUMENT_TYPE_STAGES.get(d["document_type"])
        if allowed and d["stage"] not in allowed:
            warnings.append(f"source_document {d['document_id']}: a {d['document_type']} recorded at stage "
                            f"{d['stage']!r} (FY{d['fiscal_year']}) -- a {d['document_type']} is a "
                            f"{' / '.join(allowed)} document; the row may describe the column taken from it, "
                            f"not the document")
    for r in conn.execute("SELECT * FROM bill_report_reference ORDER BY reference_id"):
        empty = [c for c in ("bill_id", "report_id", "bill_url", "report_jes_url") if r[c] is None]
        if empty:
            warnings.append(f"bill_report_reference {r['reference_id']}: {', '.join(empty)} blank")
    warnings += stale_resolution_warnings(conn)
    return warnings


def stale_resolution_warnings(conn):
    """An account-level review (account_identity) resolved on one observation
    while other observations of the same account are still flagged with no
    resolved account_identity record of their own -- the resolution didn't
    reach every observation it decides. Reported, not repaired: the workbook
    is the record."""
    out = []
    for v in conn.execute(
            "SELECT v.validation_id, o.canonical_account_id FROM validation_record v "
            "JOIN appropriations_observation o USING (observation_id) "
            "WHERE v.rule_applied = 'account_identity' AND v.human_review_status = 'resolved' "
            "ORDER BY v.validation_id"):
        stale = [r[0] for r in conn.execute(
            "SELECT o.observation_id FROM appropriations_observation o "
            "WHERE o.canonical_account_id = ? AND o.verification_status = 'flagged' AND NOT EXISTS ("
            "  SELECT 1 FROM validation_record w WHERE w.observation_id = o.observation_id "
            "  AND w.rule_applied = 'account_identity' AND w.human_review_status = 'resolved') "
            "ORDER BY o.observation_id", (v["canonical_account_id"],))]
        still_flagged = [r[0] for r in conn.execute(
            "SELECT o.observation_id FROM appropriations_observation o JOIN validation_record w USING (observation_id) "
            "WHERE w.validation_id = ? AND o.verification_status = 'flagged'", (v["validation_id"],))]
        if stale or still_flagged:
            out.append(f"validation_record {v['validation_id']} resolves {v['canonical_account_id']}'s identity, but "
                       + "; ".join(filter(None, [
                           f"its own observation is still flagged ({', '.join(still_flagged)})" if still_flagged else "",
                           f"{len(stale)} other observation(s) of the account are still flagged with no resolved "
                           f"record: {', '.join(stale)}" if stale else ""])))
    return out


# ---------------------------------------------------------------------------
# Pipeline boundary: an extraction observation as a store row
# ---------------------------------------------------------------------------

def observation_row(o, source_document_id):
    """
    One extract_approps observation -> an appropriations_observation row, or
    None for a row that isn't an account's funding figure (a rollup other
    than an agency total, a memo line). Raises ValueError for an account row
    the store can't take as is -- one with no canonical account (unmatched or
    ambiguous: it goes to a person first) or an amount that isn't whole
    dollars.
    """
    if "account_match" not in o:
        return None
    if not o.get("canonical_account_id"):
        raise ValueError(f"{o['observation_id']}: {o['account_name_as_written']!r} has no canonical account "
                         f"({o['account_match']})")
    if not isinstance(o["amount"], int):
        raise ValueError(f"{o['observation_id']}: amount {o['amount']!r} is not whole dollars")
    # component: an emergency line is already told apart by amount_type
    # (supplemental), as the workbook does; a sub-line inheriting its parent's
    # account ("Defense function" under R&RA) is mapped to the canonical
    # component vocabulary (accounts.match_component) -- or kept as printed
    # when nothing matches, which add_observations then holds for review
    component = None
    if o.get("account_match") == "inherited":
        component, _ = A.match_component(o["account_name_as_written"])
    return {"observation_id": o["observation_id"], "canonical_account_id": o["canonical_account_id"],
            "fiscal_year": o["fiscal_year"], "stage": o["stage"], "chamber": o["chamber"],
            "bill_id": o.get("bill_id"), "report_id": o.get("report_id"), "amount": o["amount"],
            "amount_type": o["amount_type"], "component": component,
            "offsetting_collections": int(bool(o["offsetting_collections"])),
            "transfer_link_account_id": o.get("transfer_link_account_id"),
            "source_document_id": source_document_id, "source_page": o["source_page"],
            "source_table_or_section": o["source_table_or_section"], "extraction_method": o["extraction_method"],
            "confidence": o["extraction_confidence"], "verification_status": o["verification_status"]}


def fact_key(o):
    """What makes two observations the same real-world fact -- the store's
    observation_fact index. Section text, source and page are description."""
    return (o["canonical_account_id"], o["fiscal_year"], o["stage"], o["amount_type"],
            o.get("component"), o.get("transfer_link_account_id"))


def add_observations(conn, rows):
    """
    Add observations (e.g. observation_row() output from an extraction) to a
    store that may already hold the same facts from another source. Same
    mechanism as advance-copy reconciliation (reconcile.compare), with the
    store's fact key:

      - same fact, same amount   -> not stored twice: a cross_document pass
                                    record on the stored observation cites the
                                    second source
      - same fact, other amount  -> not stored twice: the stored observation is
                                    flagged, and a pending cross_document flag
                                    record carries the incoming amount and
                                    source for a person to decide
      - a new fact               -> stored
      - a new fact with a component the store has never used for that
        account (e.g. 'Defense function' where every stored year says
        'defense')             -> held, not stored: a pending flag record on
                                    the stored row(s) it may be another name
                                    for (same amount type and cell if there
                                    are any, else the latest year's)
    One transaction. -> summary dict of observation ids per outcome.
    """
    from reconcile import compare, _record          # the advance-copy mechanism
    dup = [k for k, n in Counter(fact_key(r) for r in rows).items() if n > 1]
    if dup:
        raise ValueError(f"incoming rows repeat a fact: {dup[:5]}")
    existing = [dict(r) for r in conn.execute("SELECT * FROM appropriations_observation")]
    pairs, diffs, new_only, _ = compare(rows, existing, key=fact_key)
    differing = {id(n) for n, _ in diffs}
    vocabulary = {}
    for o in existing:
        vocabulary.setdefault(o["canonical_account_id"], set()).add(o["component"])

    def may_duplicate(new):
        """Stored rows a held observation may be another name for."""
        same = [o for o in existing if o["canonical_account_id"] == new["canonical_account_id"]
                and o["amount_type"] == new["amount_type"]]
        named = [o for o in same if o["component"] is not None] or same
        cell = [o for o in named if (o["fiscal_year"], o["stage"]) == (new["fiscal_year"], new["stage"])]
        if cell:
            return cell
        latest = max((o["fiscal_year"] for o in named), default=None)
        return [o for o in named if o["fiscal_year"] == latest]

    def src(o):
        return f"{o['source_document_id']} p.{o['source_page']}"

    def add_record(rec):
        conn.execute(f"INSERT INTO validation_record ({', '.join(rec)}) VALUES ({', '.join('?' for _ in rec)})",
                     list(rec.values()))

    out = {"confirmed": [], "conflicting": [], "added": [], "held": []}
    with conn:
        for new, old in pairs:
            if id(new) in differing:
                add_record(_record(old["observation_id"], f"{old['amount']:,} per {src(old)}",
                                   f"{new['amount']:,} per {src(new)} (incoming {new['observation_id']})",
                                   "flag", rule="cross_document"))
                conn.execute("UPDATE appropriations_observation SET verification_status = 'flagged' "
                             "WHERE observation_id = ?", (old["observation_id"],))
                out["conflicting"].append(old["observation_id"])
            else:
                add_record(_record(old["observation_id"], f"{old['amount']:,} per {src(old)}",
                                   f"{new['amount']:,} per {src(new)} (incoming {new['observation_id']})",
                                   "pass", rule="cross_document"))
                out["confirmed"].append(old["observation_id"])
        for new in new_only:
            known = vocabulary.get(new["canonical_account_id"])
            if known and new["component"] not in known:
                for old in may_duplicate(new):
                    add_record(_record(old["observation_id"],
                                       f"component {old['component']!r}: {old['amount']:,} per {src(old)}",
                                       f"incoming {new['observation_id']} has component {new['component']!r} "
                                       f"({new['amount']:,} per {src(new)}, FY{new['fiscal_year']} {new['stage']}), "
                                       f"a component this account has never used -- another name for this one, "
                                       f"or a new line? Not stored.", "flag", rule="cross_document"))
                out["held"].append(new["observation_id"])
                continue
            conn.execute(f"INSERT INTO appropriations_observation ({', '.join(new)}) "
                         f"VALUES ({', '.join('?' for _ in new)})", list(new.values()))
            out["added"].append(new["observation_id"])
    return out


# ---------------------------------------------------------------------------
# Review: a resolved relationship reaches every observation it decides
# ---------------------------------------------------------------------------

def open_findings(conn, observation_id, excluding_rule=None):
    """Validation records on an observation that are not a pass and not resolved."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM validation_record WHERE observation_id = ? AND result <> 'pass' "
        "AND (human_review_status IS NULL OR human_review_status <> 'resolved') "
        "AND (? IS NULL OR rule_applied <> ?)", (observation_id, excluding_rule, excluding_rule))]


# A person confirming a relationship resolves the account-identity doubt a low
# confidence was about: one step removed from a directly approved Historical
# Name (1.0), level with other human-confirmed judgment calls.
RESOLVED_IDENTITY_CONFIDENCE = 0.95


def resolve_relationship(conn, relationship_id, reviewer, resolution, verification_status="human-verified"):
    """
    Record a person's resolution of an Account Relationship and carry it to
    every observation it decides: the observations of either of its accounts
    that the open identity question is holding back -- flagged, or carrying
    an unresolved account_identity finding. (Not every observation of the
    accounts: a relationship's direction doesn't say which side was in
    doubt, and already-verified rows aren't waiting on it.)

      - the relationship: human_reviewed = 1
      - each of those observations: its account_identity Validation Record is
        marked resolved (reviewer, resolution) -- one is created for an
        observation that had none, so no observation is left resting on
        another's record
      - an observation that is 'flagged' becomes verification_status and its
        confidence is raised to RESOLVED_IDENTITY_CONFIDENCE (never lowered),
        unless it still has another open finding (a failed total, say), which
        the relationship doesn't decide -- then both stay as they are
    One transaction. -> summary dict.
    """
    rel = conn.execute("SELECT * FROM account_relationship WHERE relationship_id = ?", (relationship_id,)).fetchone()
    if rel is None:
        raise LookupError(f"no account relationship {relationship_id!r}")
    if not reviewer or not resolution:
        raise ValueError("a resolution needs a reviewer and the resolution text")
    summary = {"relationship_id": relationship_id, "accounts": [rel["from_account_id"], rel["to_account_id"]],
               "observations": [],
               "records_updated": [], "records_created": [], "status_changed": [], "still_flagged": []}
    with conn:
        conn.execute("UPDATE account_relationship SET human_reviewed = 1 WHERE relationship_id = ?", (relationship_id,))
        for o in conn.execute(
                "SELECT o.observation_id, o.verification_status FROM appropriations_observation o "
                "WHERE o.canonical_account_id IN (?, ?) AND (o.verification_status = 'flagged' OR EXISTS ("
                "  SELECT 1 FROM validation_record v WHERE v.observation_id = o.observation_id "
                "  AND v.rule_applied = 'account_identity' AND v.result <> 'pass' "
                "  AND (v.human_review_status IS NULL OR v.human_review_status <> 'resolved'))) "
                "ORDER BY o.observation_id", (rel["from_account_id"], rel["to_account_id"])).fetchall():
            oid = o["observation_id"]
            summary["observations"].append(oid)
            recs = [r["validation_id"] for r in conn.execute(
                "SELECT validation_id FROM validation_record WHERE observation_id = ? AND rule_applied = 'account_identity'",
                (oid,))]
            if recs:
                conn.executemany("UPDATE validation_record SET human_review_status = 'resolved', reviewer = ?, "
                                 "resolution = ? WHERE validation_id = ?", [(reviewer, resolution, v) for v in recs])
                summary["records_updated"] += recs
            else:
                vid = f"VAL-{relationship_id}-{oid}"
                conn.execute(
                    "INSERT INTO validation_record (validation_id, observation_id, rule_applied, expected_result, "
                    "observed_result, result, human_review_status, reviewer, resolution) "
                    "VALUES (?, ?, 'account_identity', ?, ?, 'flag', 'resolved', ?, ?)",
                    (vid, oid, f"account identity decided by Account Relationship {relationship_id}",
                     f"{rel['from_account_id']} {rel['relationship_type']} {rel['to_account_id']}", reviewer, resolution))
                summary["records_created"].append(vid)
            if o["verification_status"] == "flagged":
                if open_findings(conn, oid, excluding_rule="account_identity"):
                    summary["still_flagged"].append(oid)
                else:
                    conn.execute("UPDATE appropriations_observation SET verification_status = ?, "
                                 "confidence = max(confidence, ?) WHERE observation_id = ?",
                                 (verification_status, RESOLVED_IDENTITY_CONFIDENCE, oid))
                    summary["status_changed"].append(oid)
    return summary


# Cross-tab checks a person may knowingly waive for one load (e.g. while a
# fixed workbook is on its way). A waived check still runs; its problems are
# listed in the load report under "waived".
WAIVABLE = {"historical_names_display": check_historical_names}


def load(workbook, db_path, waive=()):
    """Build a fresh database at db_path from the workbook. -> load report."""
    unknown = set(waive) - set(WAIVABLE)
    if unknown:
        raise ValueError(f"not a waivable check: {sorted(unknown)} (waivable: {sorted(WAIVABLE)})")
    db_path = Path(db_path)
    tmp = db_path.with_name(db_path.name + ".building")
    tmp.unlink(missing_ok=True)
    report = {"workbook": str(workbook), "value_map": {}, "rows": {}, "warnings": [], "waived": {}}
    tabs = read_tabs(workbook)
    rows = convert_rows(tabs, report)
    problems = check_bill_report_lookups(tabs, rows)
    for name, check in WAIVABLE.items():
        found = check(rows)
        if name in waive:
            report["waived"][name] = found
        else:
            problems = found + problems
    if problems:
        raise LoadError("workbook contradicts itself:\n  " + "\n  ".join(problems[:20])
                        + (f"\n  ... {len(problems) - 20} more" if len(problems) > 20 else ""))
    conn = connect(tmp)
    try:
        conn.executescript(SCHEMA.read_text())
        subcommittee = {a["canonical_account_id"]: a["subcommittee"] for a in rows["Account"]}
        brr_id = {b["lookup_key"]: b["reference_id"] for b in rows["Bill Report Reference"]}
        with conn:
            for tab, table, _ in TABS:
                extra = None
                if table == "appropriations_observation":
                    extra = lambda o: {"bill_report_reference_id": brr_id.get(bill_report_key(
                        subcommittee.get(o["canonical_account_id"]), o["fiscal_year"], o["stage"]))}
                insert(conn, table, rows[tab], extra)
                report["rows"][table] = len(rows[tab])
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise LoadError(f"foreign key violations: {[tuple(r) for r in fk]}")
        report["warnings"] = data_quality_warnings(conn)
    finally:
        conn.close()
    tmp.replace(db_path)
    return report


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

STOPWORDS = {"and", "of", "the", "for", "on", "in"}


def agency_aliases(agency):
    """Full name plus its initials, with and without small words:
    National Aeronautics and Space Administration -> NASA, NAASA;
    Department of Justice -> DJ, DOJ."""
    words = re.findall(r"[A-Za-z]+", agency)
    return {A.norm(agency), "".join(w[0] for w in words if w.lower() not in STOPWORDS).lower(),
            "".join(w[0] for w in words).lower()}


def accounts_for_matching(conn):
    """The matching pool from the store: canonical names plus reviewed former
    names (Historical Name rows with human_reviewed = 1)."""
    out = []
    for a in conn.execute("SELECT * FROM account ORDER BY canonical_account_id"):
        names = [r["former_name"] for r in conn.execute(
            "SELECT former_name FROM historical_name WHERE canonical_account_id = ? AND human_reviewed = 1 "
            "ORDER BY historical_name_id", (a["canonical_account_id"],))]
        out.append({**dict(a), "historical_names": names})
    return out


def split_agency(text, agencies):
    """Longest leading run of words that names an agency (full name or
    initials, letters only, exact) -> (agency, rest) or (None, text)."""
    words = text.split()
    for k in range(len(words) - 1, 0, -1):
        head = A.norm(" ".join(words[:k]))
        hits = [ag for ag in agencies if head in agency_aliases(ag)]
        if len(hits) == 1:
            return hits[0], " ".join(words[k:])
    return None, text


def resolve(conn, text, agency=None):
    """-> {"match", "account" (dict or None), "distance", "matched_name", "via",
    "agency", "query", "candidates"}. Same rule as extraction (accounts.py)."""
    pool = accounts_for_matching(conn)
    agencies = sorted({a["agency"] for a in pool})
    name = text
    if agency is None:
        agency, name = split_agency(text, agencies)
    elif agency not in agencies:
        hits = [ag for ag in agencies if A.norm(agency) in agency_aliases(ag)]
        agency = hits[0] if len(hits) == 1 else agency
    whole = [ag for ag in agencies if A.norm(text) in agency_aliases(ag)]
    if len(whole) == 1:
        # the query is an agency by itself ("NASA"): its agency-total account
        totals = [a for a in pool if a["agency"] == whole[0] and A.AGENCY_TOTAL_RE.search(a["canonical_name"])]
        if len(totals) == 1:
            return {"query": text, "agency": whole[0], "name": text, "match": "exact", "distance": 0,
                    "matched_name": whole[0], "account": totals[0], "via": "agency_total", "candidates": []}
    scoped = [a for a in pool if agency is None or a["agency"] == agency]
    kind, acct, d, matched = A.best_match(name, scoped, lambda a: [a["canonical_name"]] + a["historical_names"])
    out = {"query": text, "agency": agency, "name": name, "match": kind, "distance": d, "matched_name": matched,
           "account": acct, "via": None, "candidates": []}
    if acct:
        out["via"] = "canonical" if matched == acct["canonical_name"] else "historical_name"
    else:
        # what a human would pick from: the accounts that tied or came close
        # (ambiguous), or the nearest few (unmatched) -- shown, never chosen
        t = A.norm(name)
        scored = [(min((A.distance(t, A.norm(n)), n) for n in [a["canonical_name"]] + a["historical_names"]), a)
                  for a in scoped]
        top = min((s[0][0] for s in scored), default=0)
        for best, a in scored:
            if best[0] <= top + A.AMBIGUITY_MARGIN:
                out["candidates"].append({"canonical_account_id": a["canonical_account_id"],
                                          "canonical_name": a["canonical_name"], "agency": a["agency"],
                                          "distance": best[0], "name": best[1]})
        out["candidates"].sort(key=lambda c: (c["distance"], c["canonical_account_id"]))
    return out


def history(conn, account_id):
    """An account's full record: the account, its former names, its account
    relationships (each way), and every observation with its source document
    and validation records, by fiscal year and stage."""
    acct = dict(conn.execute("SELECT * FROM account WHERE canonical_account_id = ?", (account_id,)).fetchone())
    former = [dict(r) for r in conn.execute(
        "SELECT * FROM historical_name WHERE canonical_account_id = ? ORDER BY historical_name_id", (account_id,))]
    rels = []
    for r in conn.execute("SELECT * FROM account_relationship WHERE from_account_id = ? OR to_account_id = ? "
                          "ORDER BY relationship_id", (account_id, account_id)):
        other = r["to_account_id"] if r["from_account_id"] == account_id else r["from_account_id"]
        o = conn.execute("SELECT canonical_name, status FROM account WHERE canonical_account_id = ?", (other,)).fetchone()
        n = conn.execute("SELECT count(*), min(fiscal_year), max(fiscal_year) FROM appropriations_observation "
                         "WHERE canonical_account_id = ?", (other,)).fetchone()
        rels.append({**dict(r), "other_account_id": other, "other_name": o["canonical_name"],
                     "other_status": o["status"], "other_observations": n[0], "other_fiscal_years": [n[1], n[2]]})
    stage_rank = {s: i for i, s in enumerate(STAGE_ORDER)}
    obs = []
    for r in conn.execute(
            "SELECT o.*, d.document_type, d.source_agency, d.url_or_identifier, d.publication_date, "
            "       d.fiscal_year AS document_fiscal_year, d.stage AS document_stage, "
            "       b.bill_url, b.report_jes_url "
            "FROM appropriations_observation o "
            "JOIN source_document d ON d.document_id = o.source_document_id "
            "LEFT JOIN bill_report_reference b ON b.reference_id = o.bill_report_reference_id "
            "WHERE o.canonical_account_id = ?", (account_id,)):
        rec = dict(r)
        rec["validation"] = [dict(v) for v in conn.execute(
            "SELECT validation_id, rule_applied, result, human_review_status FROM validation_record "
            "WHERE observation_id = ? ORDER BY validation_id", (r["observation_id"],))]
        obs.append(rec)
    obs.sort(key=lambda o: (o["fiscal_year"], stage_rank[o["stage"]], o["amount_type"] != "budget authority",
                            o["amount_type"], o["component"] is not None, o["component"] or "", o["observation_id"]))
    # gaps in the four-stage series, from the account's first fiscal year to
    # the latest one the store holds for any account -- a year nobody entered
    # for this account shows as missing, never as zero
    first = min((o["fiscal_year"] for o in obs), default=None)
    last = conn.execute("SELECT max(fiscal_year) FROM appropriations_observation").fetchone()[0]
    have = {(o["fiscal_year"], o["stage"]) for o in obs}
    missing = [(y, s) for y in range(first, last + 1) for s in STAGE_ORDER[:4]
               if (y, s) not in have] if first is not None else []
    return {"account": acct, "historical_names": former, "relationships": rels, "observations": obs,
            "missing_cells": missing}


def fmt_amount(v):
    return f"{v:,}"


def print_history(res, h, out=sys.stdout):
    a = h["account"]
    w = out.write
    how = {"exact": "exact", "ocr_corrected": f"fuzzy, {res['distance']} edit(s)"}[res["match"]]
    w(f"{a['canonical_name']}  [{a['canonical_account_id']}]\n")
    w(f"  {a['agency']} / {a['bureau']}  status={a['status']}  fund_type={a['fund_type']}\n")
    w(f"  query {res['query']!r}"
      + (f" -> agency {res['agency']!r}, name {res['name']!r}" if res["agency"] else "")
      + f" matched {res['matched_name']!r} ({how}, via {res['via']})\n")
    for n in h["historical_names"]:
        w(f"  former name {n['former_name']!r} [{n['historical_name_id']}] "
          f"reviewed={bool(n['human_reviewed'])} confidence={n['confidence']}: {n['evidence']}\n")
    for r in h["relationships"]:
        arrow = f"{r['from_account_id']} {r['relationship_type']} {r['to_account_id']}"
        w(f"  relationship {r['relationship_id']}: {arrow} (FY{r['effective_fiscal_year']}, confidence "
          f"{r['confidence']}, human_reviewed={bool(r['human_reviewed'])}) -- related account "
          f"{r['other_name']!r} ({r['other_status']}) has {r['other_observations']} observation(s), "
          f"FY{r['other_fiscal_years'][0]}-FY{r['other_fiscal_years'][1]}, not included below\n")
    w("\n")
    head = ("FY", "Stage", "Amount type", "Component", "Amount ($)", "Status", "Conf", "Source document", "Pages",
            "Section")
    rows = [(str(o["fiscal_year"]), o["stage"], o["amount_type"], o["component"] or "", fmt_amount(o["amount"]),
             o["verification_status"],
             f"{o['confidence']:g}", o["source_document_id"], o["source_page"] or "",
             (o["source_table_or_section"] or "")
             + "".join(f"  [{v['rule_applied']}:{v['result']}]" for v in o["validation"]))
            for o in h["observations"]]
    widths = [max(len(x) for x in col) for col in zip(head, *rows)] if rows else [len(x) for x in head]
    for i, r in enumerate([head] + rows):
        w("  ".join(c.rjust(widths[j]) if j == 4 else c.ljust(widths[j]) for j, c in enumerate(r)).rstrip() + "\n")
        if i == 0:
            w("  ".join("-" * x for x in widths) + "\n")
    w(f"\n{len(rows)} observation(s)")
    if h["missing_cells"]:
        w(f"; no observation for {len(h['missing_cells'])} fiscal year/stage cell(s): "
          + ", ".join(f"FY{y} {s}" for y, s in h["missing_cells"]))
    w("\n\nSources\n")
    seen = {}
    for o in h["observations"]:
        seen.setdefault(o["source_document_id"], o)
    for doc_id, o in seen.items():
        w(f"  {doc_id}: {o['document_type']}, {o['source_agency']}, published {o['publication_date']}, "
          f"recorded as FY{o['document_fiscal_year']} {o['document_stage']} -- {o['url_or_identifier']}\n")


def cmd_history(args):
    if not Path(args.db).exists():
        raise SystemExit(f"{args.db} not found -- run: python approps_store.py load <workbook>")
    conn = connect(args.db)
    res = resolve(conn, args.name, args.agency)
    if res["account"] is None:
        why = {"ambiguous": "matches more than one account", "unmatched": "matches no account"}[res["match"]]
        msg = [f"{args.name!r} {why}" + (f" in {res['agency']}" if res["agency"] else "") + " -- not guessing."]
        msg += [f"  {c['canonical_account_id']}: {c['canonical_name']} ({c['agency']}), distance {c['distance']}"
                + (f" via {c['name']!r}" if c["name"] != c["canonical_name"] else "") for c in res["candidates"]]
        if res["match"] == "ambiguous":
            msg.append("  Lead with the agency (e.g. 'NSF Office of Inspector General') or pass --agency.")
        print("\n".join(msg), file=sys.stderr)
        return 2 if res["match"] == "ambiguous" else 1
    h = history(conn, res["account"]["canonical_account_id"])
    if args.json:
        json.dump({"resolved": {k: v for k, v in res.items() if k != "account"}, **h}, sys.stdout, indent=2,
                  default=str)
        print()
    else:
        print_history(res, h)
    return 0


def cmd_load(args):
    try:
        report = load(args.workbook, args.db, waive=args.waive)
    except LoadError as e:
        raise SystemExit(f"load failed, nothing written: {e}")
    print(f"{args.db}: " + ", ".join(f"{t} {n}" for t, n in report["rows"].items()))
    for name, found in report["waived"].items():
        print(f"  WAIVED {name}: {len(found)} problem(s)")
        for msg in found:
            print(f"    {msg}")
    for k, n in report["value_map"].items():
        print(f"  mapped {k} ({n} rows)")
    if report["warnings"]:
        print(f"  {len(report['warnings'])} warning(s) for review:")
        for wmsg in report["warnings"]:
            print(f"    {wmsg}")
    return 0


def cmd_resolve_relationship(args):
    conn = connect(args.db)
    try:
        s = resolve_relationship(conn, args.relationship_id, args.reviewer, args.resolution)
    except (LookupError, ValueError) as e:
        raise SystemExit(str(e))
    print(f"{s['relationship_id']} resolved ({' / '.join(s['accounts'])}): {len(s['observations'])} observation(s); "
          f"{len(s['records_updated'])} record(s) updated, {len(s['records_created'])} created; "
          f"{len(s['status_changed'])} no longer flagged"
          + (f"; still flagged for another open finding: {', '.join(s['still_flagged'])}" if s["still_flagged"] else ""))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("load", help="build the database from the pilot workbook")
    pl.add_argument("workbook")
    pl.add_argument("--db", default=str(DEFAULT_DB))
    pl.add_argument("--waive", action="append", default=[], choices=sorted(WAIVABLE),
                    help="load despite this cross-tab check's problems (listed in the report)")
    pl.set_defaults(func=cmd_load)
    ph = sub.add_parser("history", help="an account's observation history")
    ph.add_argument("name", help="account name, optionally led by its agency ('NASA Science')")
    ph.add_argument("--agency", help="agency name or initials, if not in the name")
    ph.add_argument("--db", default=str(DEFAULT_DB))
    ph.add_argument("--json", action="store_true")
    ph.set_defaults(func=cmd_history)
    pr = sub.add_parser("resolve-relationship",
                        help="record a person's resolution of an Account Relationship on every observation it decides")
    pr.add_argument("relationship_id")
    pr.add_argument("--reviewer", required=True)
    pr.add_argument("--resolution", required=True)
    pr.add_argument("--db", default=str(DEFAULT_DB))
    pr.set_defaults(func=cmd_resolve_relationship)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
