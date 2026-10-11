# Account lifecycle rules

How the grid decides the years and stages an account exists in (`approps_store.lifecycle`, PR #41, owner
2026-10-09; follow-ups 2026-10-11). Everything here is derived from the data. The only hand-entered input is
the start-basis override (below), and it always carries a written reason.

## Evidence and life

- **Evidence** is the account's figures (observations not superseded) and its confirmed absences. An absence
  whose evidence says the account "did not exist" marks a year outside its life, not inside it.
- **Two stage groups**, each with the first and last fiscal year that has evidence:
  - proposed: the request, the House bill and the Senate bill;
  - Enacted.

  A group with no figure of its own has no life. A never-enacted account's Enacted absences show it was never
  enacted; they don't give it an Enacted life.
- **Start.** When the account's first evidence is in the first fiscal year on file (FY2017), its start is
  recorded as "before FY2017" for every group, not as a creation year.
- **End.** Open, unless the account ended or was only ever proposed.

## Types

| type | when |
|---|---|
| `ongoing` | first evidence in the first year on file, or a start-basis override (below) |
| `new` | first evidence later. Created in the year of its first Enacted figure; the predecessor comes from a `split_from`, `moved` or `merged_into` relationship |
| `proposed_only` | no Enacted figure, and the account is recorded as a proposal (`Account.status = proposed`) |
| `ended_eliminated` | last Enacted figure before the latest enacted year, no later proposal, no successor relationship |
| `ended_merged` / `renamed` / `split` / `moved` | the same, with a successor through `merged_into`, `renamed`, `split_from` (another account split from it) or `moved` |

An active account with no Enacted figure on file yet is treated as `ongoing` and its life is left open, so its
gaps stay missing. Its Enacted figures just haven't been collected.

## The grid

- A cell outside an account's life is blank: no token, never missing. The grid counts these cells separately
  (`outside_life_cells`).
- Inside a life, a gap keeps its real state, e.g. "?" for missing.
- A cell outside the life that has a figure, or a note of its own, stays visible.
- A row shows only where a column in view falls inside its life.
- The "Accounts shown" filters add back three kinds of row:
  - proposed-only accounts: on in the compare views, off in Enacted history;
  - ended accounts after their last year: off by default;
  - new accounts before their first year: off by default.
- Corner notes:
  - "Created FY<n>" on a new account's first Enacted year, linking its predecessor;
  - "Merged into <X> in FY<n>" or "Moved to <X>" on an ended account's last year;
  - "Proposed in the FY<n> request; never enacted" on a proposed-only account's first proposal.

## Start-basis override (`Account.start_basis`, 2026-10-11)

- `start_basis = "unidentified"`: the first year with evidence is **not** the account's start, because its
  earlier provision has not been identified yet.
- The lifecycle then never starts at that year. The account counts as `ongoing`, and every cell before its
  first evidence stays inside its life: those cells read "missing" ("?"), never blank.
- `start_basis_reason` is required.
  - The workbook build stops on a `start_basis` without a reason.
  - The store load refuses one too.
  - Neither accepts a reason without a `start_basis`.
- Applied to the Medicare Title XVIII limitation (`ACC-HHS-GP-MEDICARE-LIMITATION`), with the reason "FY2026
  provision not yet identified; earlier years unknown."
- **When the provision is identified, the override is removed and the normal lifecycle rule applies.**

## Relationship types

| type | use |
|---|---|
| `renamed` | the same program under a new name, as a separate canonical account |
| `merged_into` | enacted: the account folded into another (also an enacted consolidation) |
| `split_from` | the account was carved out of another (ASPR from PHSSEF, FY2024) |
| `moved` | enacted: a program moved from one account to another (the Strategic National Stockpile, FY2020) |
| `proposed_move` | proposed, not enacted: a single program's move (NIEHS into AHA) |
| `proposed_consolidation` | proposed, not enacted: one or more whole accounts or agencies folded into a new or existing body (HRSA and SAMHSA into AHA; NIDA and NIAAA into the proposed substance-use institute). Enacted, it is recorded as `merged_into` |

`same` and `uncertain` stay allowed in the schema; no relationship on file uses them. A relationship's ID is
never reused or renumbered when its type changes.
