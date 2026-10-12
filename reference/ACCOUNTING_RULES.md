# Accounting rules (accounting views, phase 1)

Every figure keeps its **As printed** headline: the Appropriations Observation with no component, budget
authority, exactly as its source prints it. Nothing here changes a figure. Phase 1 adds the records that later
views are computed from, and a check that the records add up.

Code: `accounting.py` (schema, checks, views), `scripts/tag_components.py` (writes the component records),
`scripts/measure_reconcile.py` (the check), `scripts/check_legislative_text.py` (like with like).

## Two new tables

### Account Attribute (`account_attributes` in data/staged.json)

One value per account, fiscal year and attribute, each with its evidence (source document, location and the
words that show it). Filled in phase 2 from the Budget Appendix; empty now.

| attribute | values |
|---|---|
| `budget_class` | `discretionary`, `mandatory` |
| `budget_subfunction` | three digits (e.g. `550`); equal to the last three digits of the account's `account_id_code` |
| `fund_type` | `general`, `trust`, `special`, `revolving` |
| `account_id_code` | the Budget Appendix identification code, e.g. `075-0849-0-1-550` |

IDs: `AAT-NNNN` (CJS), `AAT-LHHS-NNNN` (Labor-HHS). FY2017 to FY2027.

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

Amounts are signed as their effect on budget authority: `rescission`, `transfer_out` and `fees_offsets` are
negative; every other type is positive. IDs: `CMP-NNNN` (CJS), `CMP-LHHS-NNNN` (Labor-HHS). An ID is never
reused or renumbered: a record no longer produced is retired (`observation_components_retired`) and its ID
stays retired. `tag_method` says where a record came from: `retagged`, `table_line`, `auto_text`,
`carried_forward` or `cr_anomaly`. `within_component_id` names the component this one is part of (HCFAC's cap
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

Discretionary needs each account's `budget_class` (phase 2) and is not computed yet.

## Defaults (the owner's decisions)

1. **Advances count when available (CBO)** by default.
2. **FY2025 full-year CR** (P.L. 119-4): components continue from FY2024. Where an anomaly names the account,
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

## Tagging rules

**Re-tagged** (lines we already hold, IDs kept; the component names the line in `source_observation_id`):
supplemental and emergency lines, defense lines (NSF), the Medicaid advance lines, the program-level lines
(ONC's evaluation tap), the 12 HRSA congressionally directed spending lines, the MCH FY2017 P.L. 114-254 line
(`other_law`), and the CJS FY2027 transfers and offsetting collections (USPTO, NOAA ORF).

**Table lines**: the Vaccine Injury Compensation Trust Fund's "HRSA Administrative expenses" line, read from the
headline's own table (`reference/review/accounting/table_lines.csv`), is its `trust_fund_limitation`.

**Prevention and Public Health Fund** (Labor-HHS FY2024-FY2026): each bill's sec. 222 transfers the Fund "to the
accounts specified, in the amounts specified" under the heading Prevention and Public Health Fund in the report or
explanatory statement. Each row of that table is a `transfer_in` beside the receiving account's headline
(`reference/review/accounting/pphf_table.csv`, written by `build_pphf_table.py`). Where the table names only the
agency (House reports, the FY2026 explanatory statement), the activity's account is the one the same document's
comparative table prints the Fund's line under, and each account's rows add up to that line. FY2024 Enacted: its
own table is in the FY2024 explanatory statement, which we don't hold, so the rows are the FY2025 Senate report's
comparative-table lines (2024 appropriation column). ACL's rows are reported, not recorded (we don't hold ACL's
accounts).

**ONC** (FY2024 House and Senate, FY2025 Senate and Enacted, FY2026 House and Senate): the headline is $0 budget
authority as the bill text prints it (no new budget authority), and the paragraph's whole amount is its section 241
`transfer_in`, beside the headline. FY2025 Enacted cites P.L. 119-4 sec. 1101, continuing P.L. 118-47 div. D.
Each headline has a `cross_document` check from the report's comparative table, which prints ONC's section 241
line in parentheses (a non-add) and no budget-authority line; FY2025 Enacted uses the FY 2025 column of the FY2026
House report's table.

**Auto-tagged** from the committed bill and law text. Pilot: Labor-HHS FY2024-FY2026 and CJS FY2026. Each
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
| "an additional amount" in a general provision under the account's heading | a component of that account |
| Community Project Funding, Congressionally Directed Spending, "projects specified in the table", HRSA "projects financing the construction and renovation" | `earmark` |

**Not a component** (never tagged, never reported as a phrase no rule tagged): set-asides ("shall be for Indian
tribes", "amounts required to be reserved"); sub-allocations ("not less than $X for", "of such amount: (1)",
"from amounts made available under this heading"); program tables ("for the purposes and in the amounts specified
in the table"); the 251(b)(2)(C)(ii) base ("is provided to meet the terms of section 251(b)(2)(C)(ii)"); and the
headline's own appropriating amounts, such as a second "In addition" paragraph already counted in the headline.

When the paragraph's main amount is itself a component (HCFAC: all of it transferred from the trust funds), each
of-which component on the same side of the headline is within it.

A component's amount is always printed in the text or table, never fitted. Only `in_headline` is decided by
arithmetic for an auto-tagged component: the paragraph's main amount, less the of-which parts left out, plus the
separate parts counted, must give the headline. Where no single assignment does, the default holds (an
of-which inside, an "in addition" amount beside) and the record's note says so.

## measure_reconcile

1. **Base**: for each headline with components, `base = headline - included components` must be at least zero. A
   negative base is a `fail`, pending review, on the headline.
2. **Printed views**: a printed parallel total that a computed view stands for (appropriated in this bill,
   available this fiscal year, program level, excluding emergencies) must equal the computed view exactly. An
   agency total's view adds what the view changes on each of its accounts. A difference is a `flag`, pending
   review: the tagging may be incomplete, and the figure is not in question. Differences are grouped by cause,
   with one review item per cause (`VAL-MRC-<cause>`, listing every cell) and an info record on each printed view
   line naming its cause:

   | cause | meaning |
   |---|---|
   | `pphf_general_provision_table` | Prevention and Public Health Fund transfers made through the general provisions' table (CDC, SAMHSA); none since phase 1b |
   | `onc_no_headline` | ONC has no headline in the cell, so its section 241 transfer has nothing to sit on (none since phase 1b) |
   | `cr_anomaly_dropped` | a component a P.L. 119-4 anomaly changed is missing from FY2025 and explains the difference exactly |
   | `unexplained` | anything else |

   `account_not_held` (the money is in an account we don't hold: ACL program level) is not a failure; the view
   counts as not checked.

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
