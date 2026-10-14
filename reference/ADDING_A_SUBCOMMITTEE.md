# Adding a subcommittee

A third subcommittee needs data and a rules file, not code edits (G-1). The core -- `scripts/tag_components.py`,
`scripts/check_legislative_text.py`, `scripts/measure_reconcile.py`, `scripts/intake.py`, the workbook, the
export and the page -- holds only the general rules and reads the subcommittee list from the data. What is true
of one subcommittee's documents or accounts lives in `reference/rules/<CODE>.py`.

The worked example is the synthetic subcommittee in `tests/fixtures/any_subcommittee/` (SYNLB, modelled on
Legislative Branch), run end to end by `tests/test_any_subcommittee.py`.

## 1. Pick the code

- Upper-case letters and digits, 2-10 characters, starting with a letter (`ids.CODE`), and not a reserved word
  (`ids.RESERVED`: TXT, MR, MRV, MRC, MRE and the record types). It is stored on every account and Bill Report
  Reference row and goes into every ID: `OBS-<CODE>-0001`, `VAL-<CODE>-00001`, `CMP-<CODE>-0001`.
- In `subcommittees.py`: map the words its bills' titles use to the code (`SUBCOMMITTEES`; several entries there
  today hold a long name such as "Legislative Branch", which can't go into an ID -- replace it with the short
  code) and give it a display name (`DISPLAY_NAMES`). The page, the export and the workbook show the display name;
  only the code is stored.

## 2. Create the rule file

`reference/rules/<CODE>.py`, with `CODE = "<CODE>"` and `RULES = []` to start. Copy the header of
`tests/fixtures/any_subcommittee/rules/SYNLB.py`. An empty rule file is enough for the code to know the
subcommittee; add rules only when step 6 shows a need.

## 3. Enter the data

In `data/staged.json` (each figure with its document and page; blank is not zero; never a guessed figure):

- accounts (`subcommittee` = the code; `ACC-...` IDs), with any agency or title totals (`total_scope`);
- a Bill Report Reference row per fiscal year and stage (`BR-<CODE>-FY<N>-PB|HOUSE|SENATE|ENACTED`; an Enacted
  row's `funding_type` -- `full_year_cr` makes the cell a CR cell, its components carried from the year before);
- the source documents and the observations. New IDs come from one allocator:

      import ids
      alloc = ids.Allocator(every_id_already_used_including_retired)
      alloc.next("OBS", "<CODE>")      # OBS-<CODE>-0001; also VAL, CA, REL, HN, CMP, AAT

  (CJS keeps its code-less pattern, `OBS-####`; no ID is ever renumbered or reused.)

## 4. Commit the text

For every House, Senate and Enacted stage: `python scripts/fetch_legislative_text.py` (the manual fetch workflow
holds the API key), or an exemption with a written reason in `data/legislative_text/exemptions.json`. A cell
whose bill or law text is committed is read for components automatically (`tag_components.coverage()`); there is
no list of cells to edit. For the President's Budget, the request language is read from the Appendix chapters the
rule file names (hook `appendix`) by `reference/review/accounting/request_language.py`.

## 5. Run the intake, cell by cell

    python scripts/intake.py --subcommittee <CODE> --fy <N> --stage PB|HOUSE|SENATE|ENACTED [--write]

It stops on a missing input and says what is missing (no Bill Report Reference row, no text and no exemption, no
request language, a code it doesn't know). Otherwise it reads each headline's paragraph and components, runs the
checks (with `--write`: the text check, the tagger and measure_reconcile write their records into
`data/staged.json`) and writes `reference/review/<CODE>/intake/FY<N>-<STAGE>.csv`. Exit 0: nothing to review;
2: items to review; 1: stopped.

## 6. Work the review items

Each item names what was found and where. For each one, decide:

- **A figure question** (a text figure that differs, an account with no figure and no confirmed absence): go to the
  documents. A confirmed absence cites bill or law text, never a table's silence.
- **A quirk of this subcommittee** (a phrase no rule reads, a line of a kind no rule reads, a paragraph no account
  reads, a missing line that is this subcommittee's custom): write a rule in the rule file, at the hook where the
  quirk lives (`rules_registry.HOOKS`):

  | hook | what it can change |
  |---|---|
  | `display` | the display name, when `subcommittees.DISPLAY_NAMES` has none |
  | `layout` | how the bills print an agency (words over its paragraphs, other names): which paragraph is an account's |
  | `appendix` | the Budget Appendix chapters the request language is read from |
  | `extract` | an agency held only as its total: its headline read from the agency's one paragraph |
  | `list_item` | an amount that is an item of a list the rule reads (beside the main amount) |
  | `phrase` | one amount of a paragraph read as a component, or vetoed (not a component) |
  | `past_window` | an amount nothing classifies, read on past the phrase window |
  | `transfer_source` | the source a transfer names |
  | `not_component` | a phrase that looks like a component but is not (counted, never tagged) |
  | `line` | a held table line re-tagged as a component of its headline |
  | `table_line` | a row of `table_lines.csv` the core's CDS rule doesn't read |
  | `components` | components from the rule's own source file (e.g. Labor-HHS's PPHF table) |
  | `advances` | an account that appropriates the next year's first-quarter advance |
  | `carry_forward` | a full-year CR's anomaly that changes a carried component |
  | `checks` | measure_reconcile's known causes, and a printed line checked its own way |
  | `absence` | a missing figure that is neither an absence nor a blank (e.g. "not carried by custom") |

  Every rule states its scope -- subcommittee, accounts, fiscal years, stages (`ANY` only where it truly holds) --
  and fires only inside it. Add two tests in `tests/test_any_subcommittee.py` (or the subcommittee's own test
  module): it fires on its real case and changes what it should; it does not fire outside its scope. Then rerun
  the intake: the item is gone, and nothing else moved.

Never stretch a rule to cover a case it wasn't written for. Two rules that act on the same line stop the run
(`RuleConflict`): narrow their scopes. A rule that acts where a core rule acts must name that core rule in
`overrides` (`tag_components.CORE_PHRASE`), or that stops the run too.

## 7. When a rule belongs in the core

A rule that two or more subcommittees need is a general rule. The loader refuses the same rule id in two rule
files. To move one:

1. Write it once in the core, at the hook's call site (the function `rules_registry.HOOKS` names), with no
   subcommittee in it -- e.g. a phrase rule joins `tag_components.core_classify` (name it in `CORE_PHRASE`), a
   law's CR provision joins `tag_components.CR_LAWS`.
2. Delete it from every rule file; move its tests to the core's tests (`tests/test_accounting.py`).
3. Prove nothing moved: `tag_components.py --ci`, `measure_reconcile.py --ci`, `check_legislative_text.py --ci`
   pass with `data/staged.json` unchanged, and the export's `docs/data` is byte-identical.
4. List it in `reference/handoff.md`: rule id, old file -> core function.

## 8. Check the whole

    python scripts/check_legislative_text.py --ci
    python scripts/tag_components.py --ci
    python scripts/measure_reconcile.py --ci
    python scripts/build_workbook.py   # the Read Me lists one line per subcommittee, from the data
    python export_static.py --out /tmp/x && diff -r docs/data /tmp/x/data

The export writes the subcommittee's grid (`docs/data/subcommittees/<CODE>.json`) and lists it in
`subcommittees.json` with its display name; the page offers it in the Subcommittee menu. The existing
subcommittees' files must not change (a subcommittee's grid spans its own fiscal years only). Regenerate
`tests/fixtures/workbook_values.json` (`scripts/workbook_digest.py`) only with the owner's approval of the
figures it holds; a pull request never commits `docs/Approps_Pilot_Schema_Loaded.xlsx`.

## What still needs code

- A new cell state on the page (e.g. "not carried by custom" shown as its own state instead of an empty cell):
  the `absence` hook explains the cell in the intake's review file, but the page has no state for it
  (`web/index.html` `cell_state`/`NONFIG`; `approps_store.cell_state`).
- A new full-year CR law: `tag_components.CR_LAWS` (its continuing and earmark sections), shared by every
  subcommittee the law covers.
- Report-table extraction for a new title (`extract_approps.py`, the per-year `reference/review/lhhs/backfill/`
  scripts) is still per-subcommittee work; the intake starts after the figures are entered.
