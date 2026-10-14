# Spec: anchored totals for the amount bases

Owner-approved 2026-10-14 (project thread "Make amount bases usable"). This replaces D-6's sum-first rule for
totals: ACCOUNTING_RULES.md 'Totals'. ACCOUNTING_RULES.md is updated by the brief that builds this (D-8).

## Problem (main after UI-8, #78)

A total's view on a basis is the sum of its accounts' views. One account with no figure, no budget class, or
unknown components blanks the total, and so does money the total prints that sits in no account we hold.
Across All appropriations, Program level, Discretionary and Discretionary incl. designated:

- Labor-HHS: Title II total 153 of 164 entries not computable, agency totals 511 of 1,540, accounts 580 of 12,636;
- CJS: totals 10 of 320, accounts 24 of 2,744.

The reports already print many of these totals ("Total, Centers for Disease Control, program level", title and
bill summary lines, the 302(b) comparison). Today they are used only as a fallback, which the page shows as one
unexplained `printed_total` lump. That's the FY2025 House CDC case: CDC-Wide Activities and Program Support has no
figure entered (H.R. 9029 appropriates $511,570,000), so the PPHF transfers show as +$1,186,200,000 in one line.

## Rule: build each total down from a printed anchor

Accounts stay as strict as today: no guessed figure, and untagged is not none tagged. Only totals change.

1. **Anchors.** For each total, basis and stage, the printed figure that basis must reach, in the reports' own
   words where they exist (labels follow the reports: "program level", "discretionary", "mandatory", "302(b)
   allocation"):
   - the committee table's own line for that total and basis: agency lines (program level, discretionary), title
     and bill summary lines (D-7), each in the stage's column. The President's Budget uses the request column of
     the House and Senate tables. Reconciling the request is the same work as reconciling a bill.
   - each committee report's comparison of the bill with its 302(b) allocation (budget authority and outlays,
     discretionary and mandatory), and the report's paragraph describing the breakdown, at bill level;
   - bill or law text, where it states a total.
   As printed's anchor is the printed total itself.
2. **Build-up.** Start from the total As printed (P), not the sum of accounts, so money printed in the total but
   held in no account stays in. Add the known adjustments of each account whose view is computable, grouped by
   type as today.
3. **Reconcile.** With an anchor A, the remainder is R = A − P − known adjustments.
   - R = 0: the total is A and matches the report. Every step is itemised.
   - R ≠ 0: the total is A. R is one named "not itemised" line listing the accounts whose components aren't
     known, cited to the anchor. It is never spread over accounts.

   With no anchor:
   - every line computable: P + adjustments;
   - otherwise not computable, with a `cause`.

   **Owner decision E:** strict; no account is counted as printed by assumption. Close these cells with data
   instead: hold the table lines that today sit in no account we hold (the D-6 review list), and fill the missing
   figures.
4. **Discretionary and mandatory.** The anchors are the tables' discretionary and mandatory lines and the 302(b)
   comparison. The pieces are mandatory accounts and mandatory portions. Between a bill's scored discretionary
   figure and the table's total, the reconciling items are named lines with their source: emergency amounts left
   out of the allocation, cap adjustments, changes in mandatory programs counted as discretionary, advances in the
   year available, and scorekeeping offsets.
5. **Published data (ui_contract v9).** Each total entry carries:
   - `anchor`: observation, label, document, page, kind;
   - `build_up`: grouped adjustments from P;
   - `remainder`: amount, accounts, why;
   - `status`: matches, computed or not_computable;
   - `cause` on every not computable entry: missing_figure, no_class, components_unknown, no_anchor,
     not_held.
   As printed + adjustments (the remainder included) = amount, exactly, still holds. The `printed_total` type is
   retired.
6. **Page.** One number per cell and no status marks: Σ goes, and a checked figure gets no marker (owner decision
   A). A total's chevron and source panel show the build-up, which reads like the report: "Total, CDC (as
   printed)", "+ Prevention and Public Health Fund", "= Total, Centers for Disease Control, program level (p.316),
   matches". A not computable cell says why in words, from `cause`.

## Scope (owner decision G)

Current scope first: Title II of Labor-HHS-Education and the CJS accounts held. The 302(b) comparison is
recorded and shown at bill level. Reconciling a whole bill from the 302(b) down to accounts, for each stage
including the request, needs every account of the bill held. That is a later phase; this spec's shape doesn't
change for it.

CBO status reports: not used for now (owner).

## Other owner decisions recorded

- B: agencies held only as their total (AHRQ, ACL, AHA) get budget class attributes from the Budget Appendix.
- C: request components come from the House and Senate tables' request columns (`table_line`, cited to the
  report page) where the Budget Appendix language is not found. An account whose table prints nothing beside it
  stays unknown.
- D: Non-defense becomes a Filter (named "Non-defense"), replacing the View tab's column. A combined Non-defense
  and excluding-earmarks set is published so both can be on together.

## Briefs, in order (one page and one data brief at a time)

| track | brief | needs |
|---|---|---|
| page | UI-9: Non-defense filter (exclusive with Exclude earmarks until D-8), Σ and markers removed, n/a says why, fallback line worded as the report's line | none |
| data | D-8: anchors, build-up, remainder, cause codes, the combined exclusion set; 302(b) comparison lines recorded at bill level | D-7 merged |
| page | UI-10: build-up in the total's chevron and panel; cause words; both exclusions together | D-8 |
| data | D-9: fills: CDC-Wide FY2025 House and the other 23 missing account figures; the Medicare limitation line's life (FY2026 on, P.L. 119-75 div. B sec. 241); class for AHRQ, ACL and AHA; request components from the request columns; hold the unheld table lines from review-D6 | D-8 |

D-7 (in flight) records the title and bill summary lines and the four CMS cells. It enters no account figures.
