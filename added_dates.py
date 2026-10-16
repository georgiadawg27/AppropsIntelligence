"""
The date each figure and each document was first added to the record (brief D-5).

    python added_dates.py                 # check: every record dated, no date changed (exit 1 on a problem)
    python added_dates.py --stamp         # what --stamp --write would date; nothing written
    python added_dates.py --stamp --write # date every undated record today (UTC), in data/staged/
    python added_dates.py --from-git      # what main's history gives (the one-time backfill); nothing written
    python added_dates.py --from-git --write

Two fields in data/staged/:
  observations[].added_date   the date the figure (one observation ID) entered the record
  source_docs[].first_added   the date the document (one document ID) entered the record

A date is set once and never changed: a re-fetch, reparse, correction or supersession leaves it as it is (a
superseding figure is a new observation with its own date). Intake dates each new record the day it is written
(--stamp; scripts/intake.py --write, fetch_legislative_text.py --write and appendix_attributes.py --write call it).

The backfill (--from-git): the date of the first commit on main's first-parent history whose data/staged/ holds
the ID (a merged pull request: its merge commit's date, the day it reached main). data/staged.json entered the
repository on BASELINE (commit BASELINE_COMMIT, workbook v38) with records built earlier outside it; their real add
date is not on file, so they carry BASELINE and read as "on file by" that date, never as added that day.
"""

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402
import staged_data  # noqa: E402

# table -> (ID field, date field)
FIELDS = {"source_docs": ("document_id", "first_added"), "observations": ("observation_id", "added_date")}
BASELINE = "2026-10-07"
BASELINE_COMMIT = "7a42cfa"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def stamp(data, when=None):
    """Date every record that has no date yet with `when` (default today, UTC). A date already set is never changed.
    Returns {table: [IDs dated]}."""
    when = when or today()
    done = {}
    for table, (key, field) in FIELDS.items():
        done[table] = []
        for r in data.get(table, []):
            if not r.get(field):
                r[field] = when
                done[table].append(r[key])
    return done


def problems(data, base=None, when=None):
    """Every problem with the dates: a record with no date or not a YYYY-MM-DD date, a date before BASELINE or after
    `when` (default today, UTC), and (base: main's data) a date that differs from the one main holds for that ID."""
    when = when or today()
    out = []
    for table, (key, field) in FIELDS.items():
        held = {r[key]: r.get(field) for r in (base or {}).get(table, [])}
        for r in data.get(table, []):
            v = r.get(field)
            if not v:
                out.append(f"{r[key]}: no {field} (run python added_dates.py --stamp --write)")
            elif not isinstance(v, str) or not DATE.match(v):
                out.append(f"{r[key]}: {field} {v!r} is not a YYYY-MM-DD date")
            elif not BASELINE <= v <= when:
                out.append(f"{r[key]}: {field} {v} is outside {BASELINE}..{when}")
            elif held.get(r[key]) and held[r[key]] != v:
                out.append(f"{r[key]}: {field} changed from {held[r[key]]} (main) to {v}; a date is never changed")
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def from_git(ref="origin/main"):
    """{table: {ID: date}} from `ref`'s first-parent history: the commit date (UTC) of the first commit whose
    data (data/staged/, or data/staged.json before the split) holds each ID."""
    found = {table: {} for table in FIELDS}
    log = git("log", "--first-parent", "--reverse", "--format=%H %ct", ref, "--", *staged_data.GIT_PATHS).split("\n")
    for line in filter(None, log):
        sha, ts = line.split()
        day = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).date().isoformat()
        data = staged_data.load_git(sha, ROOT)
        if data is None:                                  # removed in this commit
            continue
        for table, (key, _) in FIELDS.items():
            for r in data.get(table, []):
                found[table].setdefault(r[key], day)
    return found


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stamp", action="store_true", help="date every undated record today (UTC)")
    p.add_argument("--from-git", action="store_true", help="the backfill: dates from main's history")
    p.add_argument("--ref", default="origin/main", help="--from-git: the history to read (default origin/main)")
    p.add_argument("--base", help="check: also hold every date to this ref's data (e.g. origin/main)")
    p.add_argument("--write", action="store_true", help="write data/staged/")
    a = p.parse_args(argv)
    staged = paths.staged()
    data = staged_data.load()

    if a.from_git:
        found = from_git(a.ref)
        for table, (key, field) in FIELDS.items():
            missing = [r[key] for r in data[table] if r[key] not in found[table]]
            for r in data[table]:
                if r[key] in found[table] and not r.get(field):
                    r[field] = found[table][r[key]]
            days = {}
            for r in data[table]:
                days[r.get(field) or "(none)"] = days.get(r.get(field) or "(none)", 0) + 1
            print(f"{table}: {len(data[table])} records; by {field}: "
                  + ", ".join(f"{d} {n}" for d, n in sorted(days.items())))
            if missing:
                print(f"  not on {a.ref} yet (left undated; --stamp dates them): {', '.join(missing)}")
    elif a.stamp:
        for table, ids in stamp(data).items():
            print(f"{table}: {len(ids)} dated {today()}" + (f": {', '.join(ids)}" if ids else ""))
    else:
        base = staged_data.load_git(a.base, ROOT) if a.base else None
        bad = problems(data, base)
        for line in bad:
            print(line)
        print(f"added dates: {len(bad)} problem(s)")
        return 1 if bad else 0

    if a.write:
        staged_data.save(data)
        print(f"wrote {staged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
