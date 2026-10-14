# Accounting rules (accounting views, phases 1 and 2)

Every figure keeps its **As printed** headline: the Appropriations Observation with no component, budget
authority, exactly as its source prints it. Nothing here changes a figure. Phase 1 adds the records that later
views are computed from, and a check that the records add up.

Code: `accounting.py` (schema, checks, views), `scripts/tag_components.py` (writes the component records),
`scripts/measure_reconcile.py` (the check), `scripts/check_legislative_text.py` (like with like).

## Two new tables

### Account Attribute (`account_attributes` in data/staged.json)

One value per account, fiscal year, attribute and `stage_basis`, each with its evidence (source document, location
and the words that show it). Filled in phase 2 from the Budget Appendix (`scripts/appendix_attributes.py`).

| attribute | values |
|---|---|
| `budget_class` | `discretionary`, `mandatory`, `mixed` (both in the same year; the split is carried by `mandatory_portion` components) |
| `budget_subfunction` | three digits (e.g. `550`); equal to the last three digits of the account's `account_id_code` |
| `fund_type` | `general`, `trust`, `special`, `revolving` |
| `account_id_code` | the Budget Appendix identification code, e.g. `075-0849-0-1-550` |

IDs: `AAT-NNNN` (CJS), `AAT-LHHS-NNNN` (Labor-HHS). FY2017 to FY2027. A record no longer produced is retired
(`account_attributes_retired`).

**Where each value comes from (phase 2).** Each account maps to its Appendix account (and, where the Appendix
account holds several of ours, a program activity line) in `reference/review/accounting/appendix_map.csv`. The
Program and Financing schedules of the HHS, Commerce, Justice, NASA, NSF and EOP chapters, FY2017 to FY2027, are
read from the text layer (no page images) into `appendix_schedules.json`.

- `stage_basis` `enacted` serves the Enacted, House and Senate stages. `account_id_code`, `fund_type` and
  `budget_subfunction` come from FY N's own Appendix volume (the account as it stood when FY N was appropriated;
  the FY N+2 volume where FY N's own prints no schedule, with a note). `budget_class` comes from the FY N+2
  Appendix's actual column. FY2026 (no FY2028 volume yet) and FY2027 take their class from the FY2027 Appendix's
  2026 est. column, the latest enacted level (the 2027 column is the request); those classes are marked
  `provisional`, to be re-checked when the FY2028 and FY2029 volumes are published (owner, 2026-10-12).
- `stage_basis` `request` serves the President's Budget: FY N from FY N's own Appendix, the request's own account
  first (a proposed or moved account, e.g. the Administration for a Healthy America). It is recorded only where it
  differs from the enacted value; the President's Budget takes the enacted value otherwise
  (`accounting.attribute_value`). An activity the request leaves unfunded in an unchanged account keeps the
  account's code; its class follows enacted.
- `account_id_code` is the schedule's identification code; `budget_subfunction` its last three digits (`999`: the
  account spans several subfunctions); `fund_type` its fund digit (1 general, 2 special, 3/4 revolving, 7/8
  trust).
- `budget_class` from lines 4000 (discretionary budget authority, gross) and 4090 (mandatory): both nonzero is
  `mixed`. With neither printed (a rescission-only account), the budget-authority lines 11xx/13xx/15xx/17xx
  (discretionary) and 12xx/14xx/16xx/18xx (mandatory). Where a mixed schedule labels its mandatory program
  activities ('(Mandatory)', 'ACA'), the class is that of the activity lines the account maps to. Where it does not
  (CDC, NIH, SAMHSA), the class stays `mixed`, and the evidence says the schedule does not split by activity.
- Activity-level class from the report tables (owner, 2026-10-12): where the Appendix leaves an account-year
  `mixed` and its held lines in the House report tables carry a D or M mark (H.Rept. 115-244, 115-862, 116-62;
  `class_marks.csv`, written by `class_crosscheck.py`), the class is the mark's, cited to the table page, with the
  Appendix's reading kept in the evidence. Enacted, House and Senate lines serve the enacted basis; President's
  Budget lines the request (a request the Appendix shows mixed like the enacted one, with no marked request line,
  follows the enacted class). Lines marked both D and M keep `mixed`. `mixed` stays only where no mark exists and
  the Appendix cannot split the account.
- Carried from the last mark (owner, component backfill, 2026-10-12): an account-year that is `mixed` only because
  none of its held lines carries a D or M mark takes the class of the account's latest earlier marked year, when
  the Appendix account (agency and account, e.g. `075-0943`) is the same and no `mandatory_portion` component splits
  that year's money. It cites the marked year's table page, and the evidence begins "carried from FY____ mark".
- The Account tab's own `fund_type` follows the Appendix (owner, 2026-10-12): AFF, CVF, USTSF, NIH CURES and NOAA
  PDF are special funds, DOJ WCF a revolving fund, CMS Program Management and Medicare Operations general-fund
  accounts; each correction is noted on the account. A general-fund account with `trust_fund_limitation`
  components (CMS Program Management, Medicare Hearings and Appeals: funded by transfers from the trust funds) is
  consistent, not a disagreement.
- Totals and part lines carry no attributes; an account with no schedule found is left empty with a note
  (`account_attribute_notes`, `reference/review/accounting/appendix_coverage.csv`). Nothing is guessed. The
  defense split stays on the `defense_portion` lines; no subfunction implies one.

### Observation Component (`observation_components`)

A component is part of a headline (`in_headline` TRUE) or money beside it (`in_headline` FALSE), with its
source, location and the quoted phrase that justifies it.

| type | what it is |
|---|---|
| `advance_enacted` | an advance appropriated in this bill for a later year |
| `advance_available` | an advance from an earlier bill that becomes available this year |
| `designated` | an amount with a designation: `emergency`, `disaster`, `cap_adjustment` or `other` |
| `transfer_in` | money derived by transfer, with `transfer_from` (e.g. section 241 of the PHS Act) |
| `transfer_out` | money transferred to another account (negative) |
| `trust_fund_limitation` | "not to exceed $X ... from the ... Trust Fund" |
| `fees_offsets` | fees and offsetting collections (negative) |
| `rescission` | a rescission (negative) |
| `defense_portion` | the defense (050) part of an account |
| `other_law` | an amount from another law printed inside the figure |
| `earmark` | Community Project Funding / Congressionally Directed Spending |
| `mandatory_portion` | the mandatory part of a mixed account printed inside its headline (phase 2): PSSF's section 436 amount, the VICTF claims line, EEOICP |

Amounts are signed as their effect on budget authority: `rescission`, `transfer_out` and `fees_offsets` are
negative; every other type is positive. IDs: `CMP-NNNN` (CJS), `CMP-LHHS-NNNN` (Labor-HHS). An ID is never
reused or renumbered: a record no longer produced is retired (`observation_components_retired`) and its ID
stays retired. `tag_method` says where a record came from: `retagged`, `table_line`, `auto_text`,
`carried_forward`, `cr_anomaly`, `appendix_text` (the request's proposed language in the Budget Appendix) or
`report_text` (a report's own statement, e.g. that EEOICP's funds are mandatory). `within_component_id` names the component this one is part of (HCFAC's cap
adjustment inside the amount transferred from the trust funds): the base subtracts only the outer one. Views add
top-level components only; a nested component is used only when a view splits its parent (picks it without the
parent, as a view leaving out the cap adjustment but keeping the trust-fund amount would), so no view ever counts a
nested component and its parent together.

The workbook build (`scripts/build_workbook.py`) stops on any record that breaks these rules, and the store
loader (`approps_store.load`) refuses it.

## Arithmetic

    headline = base + the components the headline includes (in_headline TRUE)
    base     = what is left; never negative
    a view   = headline - the included components the view leaves out + the components beside it the view counts

| view | leaves out | adds |
|---|---|---|
| As printed | nothing | nothing |
| advances when available (default) | `advance_enacted` | `advance_available` |
| advances when enacted (appropriated in this bill) | `advance_available` | `advance_enacted` |
| program level | nothing | `transfer_in`, `trust_fund_limitation` |
| program level, advances when available | `advance_enacted` | `advance_available`, `transfer_in`, `trust_fund_limitation` |
| excluding emergencies | `designated`/`emergency` | nothing |
| excluding earmarks | `earmark` | nothing |
| nondefense | `defense_portion` | nothing |

**Discretionary** (phase 2) is computed for agency and title totals from each account's `budget_class` for the
year and stage: a discretionary account counts its headline; a mandatory account counts nothing (its amount as the
bill appropriates it, advances when enacted, comes off); a mixed account counts its headline less its
`mandatory_portion` components (`accounting.discretionary_adjustment`). An account with no class on file is
counted as printed and named in the check.

### Structural rules (owner, D-6, 2026-10-14)

Two facts about where a component can exist, applied to every account cell of the published views
(`site_accounting.Layer.structural`), whether or not the cell's text was read for components. Neither fills in a
basis view that is itself not computable or excluded: the exclusion view copies the cell's view entry for entry.

1. **No earmarks in a request.** Earmarks (Congressionally Directed Spending, Community Project Funding) are added
   by Congress, so every President's Budget cell's `views_excluding_earmarks.<basis>` equals its `views.<basis>`.
   Reason: "a request carries no earmarks".
2. **No defense outside function 050.** An account whose `budget_subfunction` for the year, read from FY N's own
   Budget Appendix volume (the attribute's source is `SRC-BUDGET-APP-FY<N>...`), is not 05x, with no defense line
   printed in the cell (`defense`, `defense_inside`) and no `defense_portion` tagged, has `views_nondefense.<basis>`
   equal to `views.<basis>`. Reason: "not a defense function (subfunction NNN)". Subfunction 999 (the account spans
   more than one function), a subfunction read from a later volume, or none on file doesn't qualify: those cells
   keep the tagging rule (untagged is not none tagged).

### Totals (owner, D-6, 2026-10-14)

A total's view, on every basis, timing and exclusion, is the sum of its lines' published views: an agency total's
accounts; a title or bill total's accounts plus each agency held only as its total. Lines outside their life are
not behind it; a confirmed absence counts as nothing; accounts a basis leaves out (excluded) are not summed and are
counted in the reason. checked where a printed total measure_reconcile matched equals the sum; otherwise computed,
with the printed total's difference and its `VAL-MRC-<cause>` where measure_reconcile flagged it. Not computable
where a line is (named, with its reason), where a line has no figure, or where the lines as printed don't add up to
the printed total -- the money is in no account held, so their sum is not the total
(`reference/review/accounting/total_gaps.csv`). As printed is always the printed total.

### Adjustments (D-6)

Every published view entry with an amount lists `adjustments`: what turns the As printed figure into it. An
account's: one item per component the view takes out (sign -) or adds (sign +), with its id, type, label, amount
and source document and page. A total's: grouped by component type and sign, with the accounts behind each, plus
the accounts the basis leaves out (their As printed figure, sign -). As printed + the adjustments = the amount,
exactly (tests/test_site_data.py checks every entry). An entry equal to As printed has an empty list.

## Defaults (the owner's decisions)

1. **Advances count when available (CBO)** by default.
2. **FY2025 full-year CR** (P.L. 119-4): components continue from FY2024 (Labor-HHS; CJS from D-2, whose anomalies
   naming a held account, div. A sec. 1301(3) NOAA Operations, Research and Facilities and 1301(6) NASA Safety, Security
   and Mission Services, set the level only and change no component). Where an anomaly names the account,
   only what its text changes is affected; every other component of the account continues (owner, 2026-10-11).
   Where the anomaly states the new amount, a new FY2025 component is recorded from its text (`cr_anomaly`, citing
   the section); a changed component is dropped with nothing in its place only when the anomaly ends the item:

   | account | section | FY2024 component | FY2025 |
   |---|---|---|---|
   | Medicaid | 1109(b)(2) | advance for Q1 FY2025, $245,580,414,000 | advance for Q1 FY2026, $261,063,820,000 (already held from the table line) |
   | Child Support | 1109(b)(3) | advance $1,400,000,000 | advance $1,600,000,000 |
   | Foster Care | 1109(b)(4) | advance $3,400,000,000 | advance $3,600,000,000 |
   | HCFAC | 1902(b) | trust funds $915,000,000; cap adjustment $604,000,000 within it | level $941,000,000; cap adjustment $630,000,000 within it |
   | NIH Innovation Account (CURES) | 1905 | $407,000,000 (`designated`/`other`) | $127,000,000 |
   | HRSA Program Support, SAMHSA Health Surveillance and Program Support, ACF Children and Families Services Programs | 1908(2), (3), (4): CDS to $0 | `earmark` | dropped |
   | HRSA Health Systems | 1904: OPTN registration fees | `fees_offsets` (none held for FY2024) | - |

   Earmarks do not continue anywhere (sec. 1111: FY2024 earmark language has no legal effect). The Prevention and
   Public Health Fund allocation continues under sec. 1906 (the FY2024 amounts of P.L. 118-47 div. D sec. 222(a)).
   `scripts/tag_components.py` prints every FY2024 Enacted component's outcome: dropped, replaced (old -> new) or
   carried forward.
3. **Draft committee stages** are tagged like reported stages.
4. **Earmarks are included in every view** by default. An earmark counts inside the account whose paragraph
   appropriates it.
5. **NSF Research and Related Activities: the paragraph's total, defense inside** (D-3, owner 2026-10-13/14). In
   every bill and law cell the headline is the amount the R&RA paragraph appropriates (the report's nondefense line
   plus its 'Defense function' line; an amount the paragraph designates an emergency stays beside), citing the bill
   or law page, with the table's nondefense figure as the note. The table's defense line is re-recorded under
   `defense_inside` (kind `contained`, never added to the account's figure), naming the new headline, and is tagged
   a `defense_portion` inside it. The President's Budget takes the same rule from the Budget Appendix's proposed
   language (D-3b, owner-approved 2026-10-14; FY2017-FY2018, FY2020-FY2023, FY2025-FY2026). FY2019 and FY2024 keep the
   table's nondefense headline with its `defense` line (a part) beside: their Appendix paragraph total is not the
   table's nondefense + defense (for review). Store exemption, for this case only (`approps_store.headline_errors` /
   `superseded_errors`; tests `test_store.ReRecordedLineExemption`):
   - a contained line may name a headline from another document only when that headline superseded the line's own
     document's headline in the same cell (bill or law text over the report table);
   - a replacement may change component only when it re-records the same printed line: same document, page and
     amount.
   Any other contained line from another document, or component change, still fails to load.
   Re-ingesting a table that prints a superseded figure (`approps_store.add_observations`: same fact, same
   amount as a superseded record) confirms that record; it is never a conflict with the replacement and never
   stored again (so the table's defense line can't come back as a part).

## Tagging rules

**Re-tagged** (lines we already hold, IDs kept; the component names the line in `source_observation_id`):
supplemental and emergency lines, defense lines (NSF), the Medicaid advance lines, the program-level lines
(ONC's evaluation tap), the 12 HRSA congressionally directed spending lines, the MCH FY2017 P.L. 114-254 line
(`other_law`), and the CJS FY2027 transfers and offsetting collections (USPTO, NOAA ORF).

**Table lines**: the Vaccine Injury Compensation Trust Fund's "HRSA Administrative expenses" line, read from the
headline's own table (`reference/review/accounting/table_lines.csv`), is its `trust_fund_limitation`; its
"Post-FY 1988 Claims" line (phase 2) is its `mandatory_portion`. A "Congressionally Directed Spending" line beside the
account line of an earmark-rule headline is its `earmark`, inside (D-2: SAMHSA HSPS FY2026 Senate, S.Rept. 119-55
p.436).

**Mandatory portions** (phase 2, every year; `reference/review/accounting/mandatory_lines.csv`, written by
`mandatory_lines.py`): Promoting Safe and Stable Families' section 436 amount, read from the bill or law text
("For carrying out ... section 436 of the Social Security Act, $X", `auto_text`) and, for the President's Budget,
from the request's proposed language in FY N's Appendix (`appendix_text`); and EEOICP's whole line, where the
headline's own report states the program's funds are mandatory (`report_text`). FY2025 Enacted continues FY2024's
under the CR. Each is a `mandatory_portion` inside the headline.

**Prevention and Public Health Fund** (Labor-HHS FY2017-FY2026): each bill's general provision (sec. 219 to 225) transfers the Fund "to the
accounts specified, in the amounts specified" under the heading Prevention and Public Health Fund in the report or
explanatory statement. Each row of that table is a `transfer_in` beside the receiving account's headline
(`reference/review/accounting/pphf_table.csv`, written by `build_pphf_table.py`). Where the table names only the
agency (House reports, the FY2026 explanatory statement), the activity's account is the one the same document's
comparative table prints the Fund's line under, and each account's rows add up to that line. FY2024 Enacted: its
own table is in the FY2024 explanatory statement, which we don't hold, so the rows are the FY2025 Senate report's
comparative-table lines (2024 appropriation column). ACL's rows are reported, not recorded (we don't hold ACL's
accounts). FY2017-FY2023 (the component backfill): House and Senate rows from each report's own allocation table;
the FY2018 House report prints none, so its rows are the comparative table's Fund lines (committee column). Enacted
rows are the Fund lines of the comparative table that prints the Enacted view, in its enacted column (FY2020: the
FY2021 Senate explanatory statement's 2020 column, since H.Rept. 116-450's table pages are images).

**Advances** (Child Support, Foster Care, Medicaid; every year and stage; component backfill): the request's
advance for the next year's first quarter, read from FY N's Appendix language (`advance_lines.csv`,
`appendix_text`), is the President's Budget headline's `advance_enacted` (Child Support and Foster Care; Medicaid's
are held from the report tables). Every headline also carries the advance an earlier law appropriated for this
year's first quarter as `advance_available`: FY N-1's law text (the advance phrase rule on its paragraph; FY2026
from the CR's anomaly), and FY2017 from the FY2017 Appendix's bracketed enacted text (FY2016's law is not held).
It is inside the headline only when the headline less what is inside it is the current-year amount plus the
advance; otherwise beside.

**ONC** (FY2024 House and Senate, FY2025 Senate and Enacted, FY2026 House and Senate): the headline is $0 budget
authority as the bill text prints it (no new budget authority), and the paragraph's whole amount is its section 241
`transfer_in`, beside the headline. FY2025 Enacted cites P.L. 119-4 sec. 1101, continuing P.L. 118-47 div. D.
Each headline has a `cross_document` check from the report's comparative table, which prints ONC's section 241
line in parentheses (a non-add) and no budget-authority line; FY2025 Enacted uses the FY 2025 column of the FY2026
House report's table.

**Auto-tagged** from the committed bill and law text. Pilot: Labor-HHS FY2024-FY2026 and CJS FY2026; the component
backfill adds Labor-HHS and CJS FY2017-FY2023 (House, Senate, Enacted); the D-1 follow-up adds FY2027 House
(H.R. 9260 Labor-HHS, H.R. 8845 CJS; FY2027 Senate and Enacted hold no figures); D-2 adds CJS FY2024 and FY2025
(House, Senate, Enacted; FY2025 Enacted is the full-year CR, below). An agency held only as its total (AHRQ, owner
D-1; ACL, owner D-2) is read from the agency's one appropriation paragraph, as an account would be (the Administration
for a Healthy America is also held only as a total and is not read). Each
amount in the headline's paragraph is read by its own clause:

| phrase | component |
|---|---|
| "designated by the Congress as being for an emergency requirement" | `designated` / `emergency` |
| "for the first quarter of fiscal year" / "shall become available on October 1" | `advance_enacted` |
| "derived by transfer" / "available under section 241" | `transfer_in` |
| "the following amounts shall be available under section 241: (1) $X ...; and (2) $Y" | `transfer_in`, each item |
| "not to exceed $X ... from the ... Trust Fund" | `trust_fund_limitation` |
| "transferred (in appropriate part) from the ... Trust Fund(s)" (HCFAC, Medicare hearings and appeals) | `trust_fund_limitation` |
| "additional new budget authority specified for purposes of section 251(b)(2)(C)" | `designated` / `cap_adjustment` |
| "section 1001(b)(4) of the 21st Century Cures Act" (NIH Innovation Account) | `designated` / `other` |
| "from the 10-year Pediatric Research Initiative Fund" | `transfer_in` (from PRIF) |
| "transferred to and merged with the appropriation for the Office of Inspector General" | `transfer_out` |
| "shall be available ... from amounts made available under" (PPHF or section 241) | `transfer_in` |
| "in addition to amounts provided herein, $X is (shall be) available to this appropriation ... from amounts provided" (an evaluation tap; D-1) | `transfer_in` (section 241 where the held phrase prints '241'; else "not named in the phrase") |
| "$X shall be available for ... from amounts made available under section 241 of the PHS Act", the source printed past the phrase window (D-3b: only this wording is read on into the paragraph's text; CDC Injury Prevention, FY2025 request) | `transfer_in` (section 241; never PPHF) |
| "an additional amount" in a general provision under the account's heading | a component of that account |
| Community Project Funding, Congressionally Directed Spending, "projects specified in the table", HRSA "projects financing the construction and renovation" | `earmark` |

**President's Budget** (D-2): each held account's proposed appropriations language in FY N's Budget Appendix
(HHS chapter for Labor-HHS; Commerce, Justice, NASA, NSF and EOP for CJS), FY2017-FY2027, is read from the text layer
by `reference/review/accounting/request_language.py` into `request_language.json` (chapter, printed pages, the text as
printed and the proposed text with its [bracketed] deletions and the cited act dropped). An account's heading is
matched by its names under its agency, as the bill check matches a paragraph; failing that, by its Appendix account
(`appendix_map.csv`) where one heading's language precedes that account's schedule (and the map's activity pattern
names the heading where the account shares it), and never where two held accounts reach the same heading that way.
The same phrase rules then read each amount (`appendix_text`); the advances of Child Support, Foster Care and Medicaid
stay with the advance records. General provisions are not read for the request. Coverage is account by account
(`tag_components.covered`): an account whose language was read is read for components like an auto-tagged cell, even
where no phrase matches ("read, no match"), so its views compute; one whose language was not found (no heading of
its own, or the request deletes it: all its language in brackets) stays not computable, with the reason. A
President's Budget agency total is checked by measure_reconcile where every account behind it was read. An amount
the Appendix prints with misplaced commas ('$46,00,0000'), or one the owner treats as a probable misprint (NEI
FY2027's '$833,000', D-2), is listed as a misprint and not read, so no component or view uses it.

**Lines printed inside a headline** (D-2): a supplemental '(emergency)' line or another act's line that the
headline's own table prints inside the account's total (ACF FY2023 'Formula Grants (emergency)' inside 'Total,
LIHEAP'; SAMHSA Mental Health FY2023's 'CR Funding—Public Law 117–180 Suicide Lifeline' inside 'Subtotal, Mental
Health') is named in `inside_lines.csv` with the printed arithmetic, and its component is inside the headline. A
`supplemental_act` line the table labels '(emergency)' is `designated`/`emergency`; other `supplemental_act` lines stay
`other_law`. An agency total's view that leaves out emergency designations also leaves out a retagged emergency line
the total's own table prints after an account's total (beside the account, inside the agency total).

**Not a component** (never tagged, never reported as a phrase no rule tagged): set-asides ("shall be for Indian
tribes", "amounts required to be reserved"); sub-allocations ("not less than $X for", "of such amount: (1)",
"from amounts made available under this heading"); program tables ("for the purposes and in the amounts specified
in the table"); the 251(b)(2)(C)(ii) base ("is provided to meet the terms of section 251(b)(2)(C)(ii)"); and the
headline's own appropriating amounts, such as a second "In addition" paragraph already counted in the headline, or a
general provision's additional amount under the account's heading where the paragraph's amount plus it is the
headline (OS-GDM FY2023 Senate: $565,394,000 and sec. 243's $350,000,000 = $915,394,000; D-1), which the in_headline
arithmetic counts with the paragraph's amount (owner, D-1); an availability clause on the heading's own money ("of the
amounts made available under this heading, up to $1,000,000 from amounts made available for Quarantine
appropriations shall remain available until expended", CDC Emerging and Zoonotic; owner, D-1). A section 241
sub-allocation ("of the amounts provided under this heading from amounts available under section 241 of the PHS Act,
$X shall be available to carry out evaluations") is part of the heading's section 241 amount, already tagged with it
(D-1).

When the paragraph's main amount is itself a component (HCFAC: all of it transferred from the trust funds), each
of-which component on the same side of the headline is within it.

A component's amount is always printed in the text or table, never fitted. Only `in_headline` is decided by
arithmetic for an auto-tagged component: the paragraph's main amount, less the of-which parts left out, plus the
separate parts counted, must give the headline. Where no single assignment does, the default holds (an
of-which inside, an "in addition" amount beside) and the record's note says so; but a default never puts inside
more than the headline holds (the base is never negative): largest first, such a component is beside, with a note
(D-2: CMS Program Management FY2026 request, whose Appendix limitation exceeds the report's request figure). An amount larger than the
paragraph's main amount cannot be part of it, so it is read as beside (unless it is the headline itself).

## measure_reconcile

1. **Base**: for each headline with components, `base = headline - included components` must be at least zero. A
   negative base is a `fail`, pending review, on the headline.
2. **Printed views**: a printed parallel total that a computed view stands for (appropriated in this bill,
   available this fiscal year, program level, excluding emergencies) must equal the computed view exactly. A view
whose named headline the bill text superseded is computed from the replacement. An
   agency total's view adds what the view changes on each of its accounts. A difference is a `flag`, pending
   review: the tagging may be incomplete, and the figure is not in question. Differences are grouped by cause,
   with one review item per cause (`VAL-MRC-<cause>`, listing every cell) and an info record on each printed view
   line naming its cause. A printed `discretionary` agency or title total (phase 2) is checked in every cell
   against the discretionary view above:

   | cause | meaning |
   |---|---|
   | `pphf_general_provision_table` | Prevention and Public Health Fund transfers made through the general provisions' table (CDC, SAMHSA); since D-2 only President's Budget totals, whose request allocates the Fund outside the Appendix language |
   | `onc_no_headline` | ONC has no headline in the cell, so its section 241 transfer has nothing to sit on (none since D-2) |
   | `cr_anomaly_dropped` | a component a P.L. 119-4 anomaly changed is missing from FY2025 and explains the difference exactly |
   | `mandatory_advance_untagged` | (discretionary) a mandatory account's headline is printed as available in this bill and its advances are not tagged, so the advance the total counts is not taken off (none since the component backfill) |
   | `member_no_headline` | an account of the agency has no headline in the cell, so its components are missing (none since D-2) |
   | `headline_superseded` | the view counts the report's headline, which the bill text superseded with a figure that differs by the report's rounding |
   | `nef_transfer_untagged` | Nonrecurring Expenses Fund money (a general provision, or a proviso naming the Fund apart from the amount) that no phrase rule tags |
   | `pandemic_flu_balances` | the FY2017 program level counts 'Pandemic Flu balances (Public Law 111-32)', which no phrase rule tags |
   | `evaluation_tap_phrase_untagged` | an evaluation tap worded 'is available to this appropriation ... from amounts provided pursuant to section 241'; none since D-1 (the phrase rule above tags it) |
   | `agency_total_only` | the agency is held only as its total, whose paragraph is not read for components (AHRQ) |
   | `prif_outside_printed_program_level` | the report's program level leaves out the PRIF transfer the bill makes in addition (NIH FY2023) |
   | `onc_evaluation_tap_line` | the printed line is ONC's 'Evaluation Tap Funding' ($0), not a program-level total; none since D-1 (the line is checked as below) |
   | `emergency_lines_not_held` | the total counts emergency appropriations from the law's other divisions, which we don't hold as lines (none since D-2) |
   | `request_stage_class` | (discretionary) a President's Budget total where an account's request-stage class differs from how the report's total treats it |
   | `unexplained` | anything else |

   `account_not_held` (the money is in an account we don't hold: ACL program level) is not a failure; the view
   counts as not checked.

   ONC's 'Evaluation Tap Funding' line (recorded as `program_level`; the retag rule reads it as ONC's section 241
   `transfer_in`) is not a program-level total: it is checked against the sum of the cell's section 241
   `transfer_in` components (D-1). A $0 line where ONC's paragraph appropriates budget authority with no section 241
   amount (FY2017 House, FY2019 Enacted) passes.

3. **Earmark rule** (D-2, permanent): where a headline set by the earmark rule (the account line plus its
   congressionally directed spending / community project funding, owner 2026-10-11) differs from the table's account
   line it replaced, the earmark components inside it must equal the difference (`VAL-MRE-<obs>`). A difference fails,
   pending review; nothing is adjusted.

The check never adjusts a figure and never confirms one: it is not a confirming rule, and a flag counts neither
way in the verification status.

## Text check: like with like

The bill and law text check uses the components recorded independently of the text (`retagged`, `table_line`,
`carried_forward`; never `auto_text`, which was fitted to the same paragraph):

- the paragraph's main amount equals the headline plus the components beside it that the paragraph prints, less
  the included ones it prints beside its main amount: a `match`. ONC: no new budget authority, and the
  paragraph's whole amount is the section 241 transfer.
- with no main amount, the paragraph's trust-fund limitation equals the `trust_fund_limitation` component
  inside the headline: `component match`, an `info` record. The text confirms the component, not the headline
  (the Vaccine Injury claims are "such sums as may be necessary").
