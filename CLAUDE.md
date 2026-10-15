# CLAUDE.md

## Standing constraints

These apply to every session and every brief, whatever the task says.

Security and settings
- Never change repo visibility, GitHub Pages, branch protection or secrets.
- Never print a secret value, and never add steps that echo environment
  variables. GOVINFO_API_KEY stays a repository secret, used only by the
  manual fetch workflow.
- The scoping doc is read-only: never commit it, quote it or copy it into the
  repo. Never commit the NTU workbook or copy its rows (terms unconfirmed).
- Ask before deleting data or changing what is public (IP review pending).

Pull requests
- One draft PR per thread. Never merge; the owner merges.
- Judge a PR by its GitHub checks, which run the full suite. Locally, run only
  the tests that touch your change; never run the full suite.
- Never commit `docs/Approps_Pilot_Schema_Loaded.xlsx`. The export job on main
  is its only writer; PRs update `tests/fixtures/workbook_values.json` instead.
- Never skip, disable or loosen a check to get green. Route failures to review.
- No headline figure changes unless the owner approves; list any change old ->
  new with source and page.

Data
- Record each stage as its own documents print it. Enacted follows the law.
- Bill/law text beats the report table when scopes match; keep the table
  figure as a note.
- Supersede, never overwrite. Never reuse or renumber IDs; retired IDs go on
  the retired lists.
- Blank is not zero. A confirmed absence cites bill/law text, never a table's
  silence.
- Missing input: say what is missing and stop. Never guess a figure.
- Never adjust a figure silently.

## Token discipline

- Never read full bill text, statute text, or other large source documents into
  the conversation. Write a script that extracts or compares what's needed and
  read only its output.
- Read files by line range when you only need part of them. Use grep/rg to
  locate before reading.
- Pipe test, build, and search output through tail/grep/head so no single tool
  result exceeds ~200 lines.
- For any task that requires reading many files or large documents, use a
  subagent and have it return only a concise summary (findings, file paths,
  line numbers), not file contents.
- Default subagents to Sonnet; use Opus only for planning, architecture, and
  reviewing tricky logic.
- At the end of each phase, write decisions and open items to
  `reference/handoff.md` (not docs/, which is the published site and must match the export exactly) so the next session can start fresh instead of continuing
  a long thread.
- Wait for CI with one check-in timed for when it should finish, not repeated
  polling. Report the checks once, when all have finished on the final commit.
- Don't report on runs or checks for commits you've since replaced.
