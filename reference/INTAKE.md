# Intake (brief D-5)

How a new document gets into the data: the daily check, a committee draft, and what happens when a draft's
official version lands. Every step opens a draft PR for the owner to merge; nothing writes to main.

## The daily check

`.github/workflows/intake-watch.yml` runs `scripts/intake_watch.py` once a day (11:17 UTC; schedules run only
from main) and on demand (Actions > Intake check > Run workflow, with a look-back in days).

- It reads govinfo's BILLS, CRPT and PLAW collections modified in the look-back window (default 3 days, so a
  missed run is caught), with the `GOVINFO_API_KEY` secret sent as a request header. It is read-only
  (`permissions: contents: read`) and changes no file.
- It keeps a package when its title names a subcommittee in the data (or none, as a continuing resolution or an
  omnibus does) and the current or next fiscal year, and the data does not hold it (`new`), or holds it but govinfo
  modified it after our text file was retrieved (`changed`).
- Nothing new: one summary line, exit 0, no commit and no issue.
- Something new: the run summary lists each document, and `intake-plan.json` (the run's artifact, kept 30 days)
  holds one PR plan per document: branch `intake/<package>`, the (subcommittee, fiscal year, stage) cells it
  covers and the steps: register the source document, `scripts/fetch_legislative_text.py`, `scripts/intake.py
  --write` per cell and, where a committee draft we hold stands in for that stage, `scripts/reconcile_draft.py`.
- **Not yet:** opening those PRs. That needs D-4's engine (issue/PR workflow on `GITHUB_TOKEN`); until D-4 lands,
  a worker runs the plan's steps by hand on a branch.

Run it locally: `python scripts/intake_watch.py --since-days 30` (the key from `.env`), or with recorded answers:
`--recorded tests/fixtures/intake_watch/recorded_2026-10-16.json --today 2026-10-16 --since-days 30`.

## A committee draft

A committee draft is a document a draft stage's figures cite (Bill Report Reference `draft` TRUE) that is not
govinfo or Congress.gov text (`approps_store.committee_drafts`). Its figures are entered like any others (each with
its own page, verification `unverified`, never `human-verified`).

- **Not yet:** the `ops-add-committee-draft` issue form (needs D-4). Until then a worker adds the draft:
  `python govinfo_ingest.py ingest-local <pdf> --advance-copy --source-url <committee link> ...` stores it
  (`confirmation_status` "unconfirmed", its hash and public link; `ingest_local` refuses `--advance-copy` for an
  explanatory statement, which is stored without it), then the source document, the Bill Report Reference row with `draft`
  TRUE, and the figures go into `data/staged/`, then `scripts/intake.py --write`. A link and an attached file
  whose hashes differ stop the intake; neither is picked.

What the site publishes for it:
- `docs/data/index.json` `stage_status`: `{subcommittee: {fiscal year: {stage: "draft"}}}` for every stage whose
  current figures come from a committee draft;
- on each such figure (account files, subcommittee grids) `source_label` "Committee draft, unofficial" and
  `draft_release_date` (the document's `publication_date`; null where none is on file);
- `docs/data/ops.json` `intake`: every committee draft, `awaiting_official` (its fiscal year has not ended by
  `as_of` and its figures are current) and `days_waiting` since its release; the documents first added in the 30
  days up to `as_of`; `last_check` is null (the check commits nothing when nothing is new, so its last run is in
  the workflow's runs, not the data). `as_of` is the newest added date in the data, so both serving modes give the
  same answer with no clock.

## When the official version lands

1. Register the official document (govinfo or Congress.gov) in `source_docs` and fetch its text.
2. Put its figures for the stage, as its own pages print them, in a JSON list (`canonical_account_id`,
   `amount_type`, `component`, `amount`, `source_page`, `source_table_or_section`, `extraction_method`).
3. `python scripts/reconcile_draft.py --subcommittee CODE --fy N --stage "House Reported" --official <id>
   --rows rows.json` (what it would do), then `--write`:
   - same value: a new observation cites the official document and page; the draft's is superseded by it, and a
     passing cross_document record says the official version confirms the draft;
   - different value: the same supersession, with a cross_document record flagged for review (old and new
     figures, both documents and pages);
   - a line in only one version: a review item (a draft line the official version doesn't print stays, and is
     never a confirmed absence).
   Nothing is overwritten; new IDs come from `ids.Allocator`, new observations get today's added date.
4. `python scripts/intake.py --subcommittee CODE --fy N --stage ... --write` re-runs the checks; then the workbook
   fixture and the export as for any data change. The stage leaves `stage_status` once no current figure cites the
   draft.

The data holds no official reported version for any draft stage it holds, so the reconcile is proven on fixtures
(`tests/test_intake.py`), not on a past committee release.
