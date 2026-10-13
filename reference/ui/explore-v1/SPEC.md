# Explore v1: UI spec (approved design, 2026-10-12)

This is the design the owner approved for the Explore workspace (the subcommittee grid). Every UI pull request that touches Explore builds to this spec. Anything a PR does differently is listed in the PR under "Departures from SPEC.md", with the reason; nothing is changed silently.

- `screenshots/` holds the reference images. Compare your build against them state by state.
- `design/` holds the design source (Design Component files from the owner's design canvas). It is reference only: it needs a runtime this repo does not ship, so don't copy it into `web/`. Build in `web/index.html` (vanilla JS), the same as today.
- The figures in the design are real FY2026 Labor-HHS figures from `docs/data` at main @ #55. `design/sources.json` is a copy of the citation fields for the cells the design shows.
- The screenshots were rendered with Inter standing in for IBM Plex Sans, so letter widths differ slightly from production.

## 1. Rules that override everything else

1. **The page never shows a figure the data doesn't hold.** It may show the difference between two figures it holds (change columns, % change, largest movers). It never computes totals, accounting views, classifications or anything else the build hasn't published. If a feature needs a field that isn't in `docs/data/`, the feature stays hidden (section 9).
2. **Blank is not zero.** Each non-value cell shows its state (section 4) and is never used in a sum, difference or percentage. A **printed zero is a figure**: it counts in dollar differences (AHRQ's FY2026 request of 0 is −369,000 vs FY2025 enacted). A percentage from a zero base shows "new" (or "—" when both are zero). A confirmed absence with no printed figure is not used.
3. **Every figure is one click from its source** (section 5.1).
4. **Colour has one meaning each** (section 8): blue fill = hover only; yellow = selection only; green/orange = direction of change only.
5. Keep the standing behaviour: a window-height scroll box, sticky header rows, a sticky first column, an always-visible bottom scrollbar.

## 2. Screenshots (the states every PR reproduces)

| File | State |
|---|---|
| `01-first-screen.png` | Default: Labor-HHS, FY2026, Stages; agencies collapsed; overview in the side panel |
| `02-figure-source.png` | NIH expanded; NCI FY2026 Enacted clicked; panel shows that figure's source |
| `03-account-panel.png` | NIH and CMS expanded; NCI account open in the panel |
| `04-filter-panel.png` | Filter panel open with Advanced expanded; "Agency: NIH" chip applied |
| `05-hover-expanded-agency.png` | NIH expanded; pointer on NHLBI FY2026 Senate: row, column and header highlighted |
| `06-phone-list.png` | 390 × 844: one stage at a time, NIH expanded |
| `07-phone-account-sheet.png` | 390 × 844: NCI account sheet open |

Note: `04` shows the Amount basis and Account type controls enabled. That is the target once the accounting data is published (UI-0). Until then they are hidden (section 9).

## 3. Layout (desktop, ≥ 640 px)

Top to bottom:

1. **Header bar** (navy): product name; navigation Explore · Find changes · Sources. No sign-in link.
2. **Control row** (white band): Search (grows), Subcommittee, Fiscal year (**default: the current fiscal year**, FY2027 as of Oct 1, 2026; owner decision 2026-10-12; the screenshots use FY2026 only because it shows every stage), Compare (three joined buttons: Stages · Two years · Enacted history), Filter button with a count badge. Nothing else is always visible.
3. **Applied chips** (only when something is applied): `Applied: [Agency: National Institutes of Health ×] … Clear all`.
4. **Title row**: eyebrow `LABOR-HHS-EDUCATION · TITLE II · FY2026` (condensed caps); heading = department name, or the agency name when an agency filter is applied. Right side: a units-and-basis label (`$ thousands · as printed`), then View ▾, Share, Export.
5. **Workspace**: the grid (flex 999 1 600px) and the docked side panel (flex 1 1 340px) side by side, same height. Below ~980 px the panel wraps under the grid. The panel never overlays the grid.
6. **Footer legend** (one line): units · "click an account name for details · click a figure for its source" · `—` not funded · `missing` · blank = outside the account's life.

Adopted in UI-2b (owner decision, 2026-10-13): the grid box (and the docked panel beside it) fills the window from below the title row to the window's bottom edge, and follows window resizes; the sticky header rows, sticky first column and the box's own bottom scrollbar are kept. The legend, definitions and provenance sit below it, reached by scrolling the page.

There are no tiles above the grid: the headline figures live in the overview panel.

## 4. The grid

**Columns (Stages mode).** Account | FY2025 Enacted (CR) | FY2026 Request | House | Senate | Enacted | Change vs FY2025 enacted. "Change vs FY2026 request" is an extra column under View › Columns, off by default. Two header rows: years (FY2026 spans its four stages; the change header reads "Change vs"), then stage names. Column template: `minmax(250px, 2.2fr) repeat(5, minmax(98px, 1fr)) minmax(112px, 1fr)`.

**Default rows** (collapsed):
1. Total band: `Total, Title II (printed)`. With an agency filter it reads `Total, Title II (printed, all accounts)` and stays as printed; filtering never re-sums.
2. Section label `AGENCIES · n`.
3. One row per agency total, collapsed, each with a caret and a display abbreviation after the name (HRSA, CDC, NIH …).
4. Section label `GENERAL PROVISIONS`, then one collapsible row `Title II general provisions (n lines)`; expanded, its note reads "No printed total · each line counts in the Title II total".
5. Section label `PROPOSED IN THE FY2026 REQUEST, NOT ENACTED`, then proposed-only accounts (e.g. Administration for a Healthy America) with a `proposed` tag.

**Expanding an agency** (caret, or Expand all in View):
- The agency row keeps a white background and gains: a 4 px navy bar on the left edge of its name cell, a 1 px rule and 6 px gap above it, bold figures, and the note "Agency total · sum of the N lines below". N is the number of the row's `rollup_members` that are inside their life in the selected fiscal year, which is the number of lines actually listed. For NIH in FY2026 that is 32: the data holds 33 members, and National Institute of Substance Use and Addiction Research is outside its life in FY2026, so it is not listed.
- Its lines follow, indented, each on a white row with a 2 px rail (`#b9c6dc`) in the name column joining them to the total. Line names are 13 px regular; a name that opens the account panel uses the link style (navy `#16325f`, light underline offset 3 px; owner, 2026-10-12, #59), otherwise `#3a4150`; line figures 13 px regular `#4a5160`. They must look visibly subordinate to the bold total they add up to.
- Sub-groups inside an agency (e.g. "Proposed in the FY2026 request", "Appropriated mandatory") are small caps labels on the rail. "Appropriated mandatory" depends on `budget_class` (section 9).
- **Class tags** (`budget_class`; owner, 2026-10-12, #61). A line's class for the year is its enacted-basis record, else its request-basis record (a proposed account). A line whose class is mandatory sits under "Appropriated mandatory" in its agency (a line outside an agency gets a "mandatory" tag). A mixed line gets "incl. mandatory" only when a `mandatory_portion` component is tagged on its figure at the lead stage; a mixed line with none tagged counts as discretionary (ACCOUNTING_RULES) and gets no tag. A mixed line's mandatory portion is read at the lead stage (the stage the grid leads with for the year in view), not at other stages of the year. An agency gets "incl. mandatory" when it holds such a line in view. FY2026 Labor-HHS: HRSA, CDC, CMS, ACF and OS. The hover names each line behind the tag and its mandatory amount, e.g. "Vaccine Injury Compensation Program Trust Fund: mandatory portion 272,062" (a mandatory account: its whole printed figure); portions are listed, never added. A provisional class has a dashed tag outline (a mandatory line under the sub-group gets a "provisional" tag), and the footer legend says "dashed tag = provisional budget class".
- **Defense portion column** (View › Columns, `defense_split`): the lead stage's `defense_portion` amount where one is tagged, a button to the figure's source; empty where none is tagged (untagged is not zero). Nondefense is a view the data doesn't publish yet; it stays hidden behind the `nondefense` flag (owner, #61).

**Cell states** (from the data; no new ones):

| State | Shows | Hover text |
|---|---|---|
| `value` | the figure | "Click for its source: <doc>, p. <n>" |
| `not_funded`, printed zero | `—` muted, clickable to its source | "Not funded: printed zero". Counts as 0 in dollar differences |
| `not_funded`, confirmed absence | `—` muted | "Not funded: confirmed absent in the bill or law text". Not used in any difference |
| `missing` | `missing` small muted italic | "Document on file; figure not yet entered" |
| `not_collected` / `not_enacted` / `not_reported` | `n/c` / `n/e` / `n/r` small muted | the state's meaning |
| `no_printed_total` / `no_figure` | `—` muted | the state's meaning |
| `outside_life` | blank | "Outside the account's life" |

**Under an Amount basis other than As printed** (section 5.6; owner, #61), a cell holding a printed figure shows its published view (`views.<basis>`, the timing's entry): the amount when checked or computed; `excl.` when the basis leaves the account out; `n/a` for an account and `not checked` for a total when not computable. The reason is on hover, each is a button to the figure's source, and a legend line explains the three. Only checked or computed amounts feed a difference. Cells without a printed figure keep their state tokens.

**Note markers** (owner decision, 2026-10-13, UI-2b). The default view draws no corner triangles. A figure's notes (its cell note, and the other lines on file for the cell, listed and never added) show in its source panel under "Notes" (section 5.1); a cell with no figure keeps its note on hover. The triangles, and their legend line, appear only in Reviewer mode.

**Emphasis.** The FY2026 Enacted column has a warm wash (`#f7f5ef`) on every row except the total band, and a bold header. The change column shows `±amount` over `±%`: green when up, orange when down, figures only (never the fill).

**Weights.** Total band 14/700. Collapsed agency 14/500 (name and figures). Expanded agency 14/600. Lines 13/400. Change column follows its row (600 total and expanded, 500 collapsed, 400 lines).

**Density.** Comfortable (default) rows have 12 px vertical padding; Compact has 6 px.

**Years without an Enacted stage** (the default year is usually one of these):
- The **lead stage** is the furthest stage, in the order Enacted › Senate › House › Request, for which the title total holds a printed value. It is chosen once per subcommittee and year, never row by row. For FY2027 Labor-HHS at main @ #55 that is House: the request and House stages hold figures, Senate is not collected, and nothing is enacted.
- The stage columns are unchanged. Stages with no figures yet show their state tokens (`n/c`, `n/e`), never blanks or zeros.
- The change column becomes "**<lead stage> vs FY<n−1> enacted**", e.g. "House vs FY2026 enacted". A row with no value at the lead stage shows an empty change cell. View › Columns adds "Request vs FY<n−1> enacted".
- The lead-stage column takes the warm wash instead of Enacted.
- The units-and-basis label adds the status: "$ thousands · as printed · FY2027 not yet enacted · latest: House".
- "Still moving" (the Status filter) = nothing enacted for that year yet, and at least one earlier stage of that year holds a figure.
- If no stage has a title total, there is no lead stage. The change column shows nothing, and the overview says "No printed totals yet for FY<n>".
- A subcommittee that prints no title total at all (CJS today) takes as its lead stage the furthest stage any of its rows holds a figure for. This only chooses which stage the change column compares. The overview still says "No printed title or bill total on file. The page never adds accounts into one." (owner, 2026-10-12, #58)

## 5. Interactions

### 5.1 Click a figure → its source
- Every cell whose data holds an observation is a button. It has a faint dotted underline (`#b9c3d3`, offset 4 px), and its hover text names the document and page. Printed zeros (`—`) are clickable too.
- Clicking it gives the cell a yellow fill (`#fff1c2`) with a 2 px `#d9a400` inset outline, and switches the side panel to **Source for this figure**:
  - account name and stage (e.g. "FY2026 enacted")
  - the exact value in $ thousands, its short form, and "as printed"
  - verification badge: Auto-validated / Not yet confirmed by a second document / Flagged for review (`verification_status`)
  - Document (`source_document_id` as its short name, linked to `url_or_identifier`) and Page (`source_page`)
  - "Printed as" box (`source_table_or_section`)
  - Bill or law (`bill_id`, linked to `bill_url`, when present)
  - Record (`observation_id`)
  - primary button "Open page N of the document" → `url_or_identifier#page=N`
  - when the row is an account with its own page: "See every stage for this account"
- Clicking another figure moves the selection.

Adopted in UI-2b (owner decision, 2026-10-13); this replaces the panel list above where they differ:
- No panel mode shows the "or click outside · Esc" hint, the account panel included (click-away and Esc still work; section 5.3's hint no longer applies).
- The big figure is in full dollars ("$7,352,159,000"), with "printed as 7,352,159 in $ thousands" under it.
- One block headed "Source document": the document's short name (plain text, not a link), its page, and the "Printed as" line, then one primary button "Open source document at page N" (`url_or_identifier#page=N`). The document name is no longer a separate link to page 1.
- The bill or law is a small secondary link, "Bill / law: P.L. 119-75, Div. B", visibly separate from the source document. For a request figure it reads "Budget document: FY2026 Budget Appendix".
- "Notes" when the figure has any (section 4, Note markers).

### 5.2 Click an account name → account view in the panel
The panel shows: the path (Title II › agency); the name; the headline in short form ($7.35B) with the exact $K and "FY2026 enacted, as printed"; the change vs the prior year when both are values; "FY2026 by stage, with sources" (each stage: amount, then document · page · bill link); "Enacted, FY2017–FY2026" bars (FY2025 CR bar lighter, gaps for non-values, never zero bars; the bars start at zero); Open account page; Watch (browser-only, section 9). The selected account row gets a light yellow fill (`#fff6dc`).

Adopted in UI-2b (owner decision, 2026-10-13); this replaces the stage list above: the header is the path, the name, the short headline ($7.35B, captioned "FY2026 enacted, as printed") and the change vs the prior year. Then a three-column table, Stage | Amount ($K) | Source, where Source is one link, "H.Rept. 119-271, p. 343", to that page. The bill and law links sit on one line under the table: the prior year's law first, labelled with its year and "(full-year CR)" when it was one, then the year's bills and law ("Bill text: FY2025: P.L. 119-4 (full-year CR) · H.R. 5304 · S. 2587 · P.L. 119-75"). The Budget Appendix is not bill text and is not listed. The per-row bill links are gone. History bars as before. The phone sheet (section 6) shares this layout.

### 5.3 Panel modes and returning to the overview
Precedence: figure source > account > overview. Each non-overview mode shows "‹ Title II overview" (or "‹ NIH overview" with an agency filter) and the hint "or click outside · Esc". The overview returns when you:
- click the back link;
- click anywhere outside the grid, the panel and any open menu (page background, title area, around the controls). Clicks on controls do their own job and don't clear;
- press Esc. The first press closes an open menu; the next clears the selection.

Clicks inside the grid or the panel never clear the selection.

### 5.4 The overview panel
"Overview", the scope title ("Title II, Department of Health and Human Services · FY2026", or the filtered agency), the hint "Select an account in the grid to see its stages and sources here", three stacked figures (enacted; vs FY2025 enacted (full-year CR); vs FY2026 request), "FY2026 by stage (printed totals)", "Largest changes vs FY2025 enacted" (top three up, top three down, among the rows in scope with both values), and "Enacted in" (law with link, date signed, funding type, vehicle, from `enactments`), plus the CR note when the prior year was a full-year CR.

In a year without an Enacted stage, the three figures become: the lead stage's total ("Title II, House, FY2027"); the lead stage vs FY<n−1> enacted; and the lead stage vs the request (when the lead stage isn't the request). "Largest changes" uses the lead stage. "Enacted in" becomes "**Status**": which stages are on file, with their documents, and "Not yet enacted". For FY2027 Labor-HHS that is the House total, 1,606,033,805, +52,771,448 (+3.4%) vs the FY2026 enacted total of 1,553,262,357.

### 5.5 Hover crosshair
Hovering a figure cell tints its whole row `#ecf1f9` (including the sticky name cell) and its whole column `#f1f5fb`; the crossing cell is `#d6e2f5`; the column's header turns `#dfe8f6`, bold, with a 2 px `#1f4e8c` underline. Hovering a name tints the row only. Section labels and header-only rows don't highlight. Leaving the grid clears it. Selection fills are drawn over hover.

### 5.6 Filter panel (the Filter button)
A dialog under the button: Agency (select), Account type (All · Discretionary · Mandatory · Mixed), Status in FY<n> (Any · Enacted · Still moving · Proposed only), then an **Advanced** disclosure whose one-line summary reads e.g. "As printed · advances when available · earmarks included". Advanced holds Amount basis (five options with one-line definitions from `reference/ACCOUNTING_RULES.md`), "Advance appropriations count" (When available · When enacted), Change of at least (%) or at least ($K), and checkboxes for Exclude earmarks, Include committee drafts (on by default; draft columns are labelled "(draft)"; owner, 2026-10-12, #58), Show ended and reorganized accounts, and Only figures pending review or flagged. Footer: Reset · Done. Each non-default choice becomes a chip. The badge on the Filter button counts the chips.

Adopted in UI-3 (owner, 2026-10-12, #61):
- The basis definitions follow `reference/ACCOUNTING_RULES.md` as `accounting.BASES` applies it; Program level adds transfers in and trust-fund limitations only (no fees, no transfers out; owner decision on #57).
- Exclude earmarks is hidden behind its own flag, `exclude_earmarks` (false until UI-0b publishes a view that leaves earmarks out); the page never computes one.
- Under a basis other than As printed, the units-and-basis label adds the timing: "$ thousands · all appropriations · advances when available".
- Under such a basis, the overview shows a total only where the data marks it checked, otherwise "not checked (n accounts)"; the figure-source panel adds the view, the printed headline, each component (type, amount, source) and the arithmetic (the printed figure, the components the view used, "=" the published amount).

### 5.7 View menu, Share, Export
- **View**: Units ($ thousands · $ millions), Density (Comfortable · Compact), Columns (Change vs FY2026 request), Expand all / Collapse all, Reviewer mode.
- **Share**: copies the URL. All state (filters, compare mode, FY, units, density, columns, expanded rows, selected figure) round-trips through the query string, and the button briefly reads "Link copied".
- **Export**: brief UI-4.

## 6. Phone (< 640 px)

There is no grid on a phone.
- Header with a menu button.
- One row: a "Labor-HHS · FY2026" picker, plus Search and Filter icon buttons (44 × 44).
- Summary card: the headline enacted figure, change vs prior year, change vs request.
- A stage switch (Request · House · Senate · Enacted, default Enacted) that stays pinned while scrolling, with the note "Showing FY2026 Enacted · % change vs FY2025 enacted · tap a line for sources".
- The list: each agency shows a short figure ($47.29B) and % change; expanding keeps the parent/line formatting (bold total plus "sum of the N lines below"; lighter lines on a rail). Lines are sorted largest first, the first 6 show, then "Show N more … lines".
- Tapping a line opens a bottom sheet: the exact $K, every stage with document · page · bill link, the history bars, Open account page, Watch. The sheet closes with × or Esc.
- Last on the page: the link "Open the full table (best on a larger screen)".

Compare modes, the View menu and Export are not offered on phones.

Adopted on the phone (owner, 2026-10-12, #60):
- The picker reads the data's subcommittee name, "Labor-HHS-Education · FY2026", on one line; if it doesn't fit it is cut with an ellipsis, never wrapped.
- Lines are sorted strictly largest first at the stage shown (a line inside another line stays right after it).
- Every line name that opens the sheet carries the link style (as on desktop, #59).
- Tapping an agency that has lines expands it; the sheet opens from a line, or from a row with no lines below it.
- The summary card's changes read "vs FY<n−1> enacted" and "vs request".
- A % change from a zero base shows "new" ("—" when both are zero), as section 1 rule 2.

Adopted in UI-2b (owner decision, 2026-10-13): the account sheet follows section 5.2's UI-2b layout (header, Stage | Amount ($K) | Source table with one source link per stage, "Bill text:" line, history bars).

## 7. Typography

IBM Plex Sans (self-hosted under `web/`, SIL OFL; fall back to system-ui), base 14 px / 1.4; all figures `font-variant-numeric: tabular-nums`. IBM Plex Sans Condensed 12 px caps with 0.08em tracking for eyebrows and section labels. Heading 24/600. Panel title 19/600. Panel headline figure 30/600.

## 8. Colour tokens and meanings

| Token | Value | Use |
|---|---|---|
| header | `#16213a` | header bar; active nav underline `#8fb3ec`, inactive nav text `#c9d2e3` |
| page | `#f6f5f1` | page background, section label rows |
| surface | `#ffffff` | grid rows, panel, cards |
| ink / ink-2 / muted | `#1b1f27` / `#3a4150` / `#5b6372` | text levels. State tokens (`—`, `missing`, `n/c` …), abbreviations, hints and captions all use muted. Don't go lighter: `#5b6372` is the lightest grey that keeps 4.5:1 on every fill here, down to the hover crossing (4.6:1). |
| rule / rule-strong / input | `#e8eaee` / `#cfd3da` / `#c9ced8` | row lines / expanded-agency and total rules / control borders |
| accent | `#1f4e8c` (text on light: `#16325f`) | primary buttons, pressed toggles, expanded-agency bar, links |
| chip | bg `#e9eff9`, border `#c3d3ee` | applied filters |
| up / down | `#1d6b3a` / `#b3471b` | change direction only |
| total band | `#efede7` | Title total row |
| enacted wash | `#f7f5ef` | FY2026 Enacted column |
| hover row / column / cross / header | `#ecf1f9` / `#f1f5fb` / `#d6e2f5` / `#dfe8f6` | hover only |
| selected figure | `#fff1c2` + inset `#d9a400` | selection only |
| selected account row | `#fff6dc` | selection only |
| rail | `#b9c6dc` | lines under an expanded agency |
| note marks | note `#B7791F`, other lines `#8A93A3` (corner triangles) | Reviewer mode only (owner decision, 2026-10-13, UI-2b) |
| tag: incl. mandatory | bg `#eeeaf8`, text `#4b3f72`; provisional class: 1 px dashed outline `#8a7fb0` (owner, #61) | class tags (UI-0) |
| tag: proposed | bg `#fdf1d8`, text `#7a4b00` | proposed-only accounts |
| badges | auto-validated `#e3f1e7`/`#1d6b3a`; unverified `#eef0f3`/`#3a4150`; flagged `#fde8dc`/`#9a3412`; human-verified → Verified by a person (green, as auto-validated) | verification status |

Contrast: every text/background pair meets 4.5:1. Every control is a real `<button>`, `<a>`, `<input>` or `<select>`, keyboard-reachable with a visible focus ring; dialogs and the sheet close on Esc and return focus.

## 9. Data readiness (what may ship now)

The page reads a `features` object from `docs/data/index.json` (written by `export_static.py`) and shows a feature only when its flag is true. `web/ui_contract.json` lists, for each feature, the data fields it reads; `tests/test_static.py` fails if a feature is on while any of its fields is missing from `docs/data/`.

| Feature | Needs | Status at main @ #55 |
|---|---|---|
| Grid, states, compare modes, expand/collapse, sections | `rows`, `cells`, `rollup_members`, `member_of`, `lifecycle`, `enactments` | ready |
| Click figure → source panel | `source_document_id`, `source_page`, `source_table_or_section`, `url_or_identifier`, `bill_id`, `bill_url`, `verification_status`, `observation_id` | ready |
| Account panel, history bars | the above, plus enacted history per account | ready |
| Overview panel, largest movers | printed totals and agency rows (differences only) | ready |
| Search, agency filter, status filter | names, former names, `lifecycle`, cell states | ready |
| Hover, click-away, Esc, Share/URL state | none | ready |
| Abbreviations | display-only label map in `web/`, keyed by `canonical_account_id` (not data) | ready |
| Export CSV | the shown rows | ready (UI-4) |
| Phone list and sheet | same as grid and account panel | ready |
| Filter › Advanced: change threshold, Include committee drafts, Show ended and reorganized accounts, Only figures pending review or flagged | cell states, `lifecycle`, verification status | ready (the Advanced section shows these now; the basis items join it when their flag is on) |
| Amount basis, timing | published views per cell (UI-0) | ready (UI-0 #57, built in UI-3 #61) |
| Account type filter, "incl. mandatory" tags, "Appropriated mandatory" sub-group | `budget_class` per account-year (UI-0) | ready (UI-0 #57, built in UI-3 #61) |
| Defense portion column | `defense_portion` components (UI-0) | ready (UI-0 #57, built in UI-3 #61) |
| Exclude earmarks (`exclude_earmarks`) | a published view that leaves earmarks out | data ready (UI-0b #62); page controls in UI-3b |
| Nondefense (`nondefense`) | a published nondefense figure per cell | data ready (UI-0b #62); page controls in UI-3b |
| Watch | browser storage only; no alerts or email | later (UI-6) |

## 10. Required tests (Playwright, in `tests/test_compare.py` or a new `tests/test_explore_v1.py`)

1. `first_screen_defaults`: the four controls plus Filter; agencies collapsed; three section labels; no tiles above the grid; the overview panel is visible.
2. `figure_opens_source`: click NCI FY2026 Enacted → the panel shows document, page 285, "Printed as", P.L. 119-75, the record ID and "Open page 285 of the document" with `#page=285`; the cell is marked selected.
3. `every_value_cell_is_clickable`: each value cell in the default view has an accessible name "Source for <account>, <stage>".
4. `click_away_clears`: with a figure selected, a click on the page background restores the overview; a click inside the grid does not.
5. `escape_closes_then_clears`: open the Filter panel, Esc closes it; Esc again clears the selected figure.
6. `expand_agency_formatting`: expand NIH in FY2026 → the agency row has the left bar and the note "sum of the 32 lines below" (members inside their life that year), and exactly 32 lines are listed; line figures are 13 px weight 400; the agency figures are weight 600.
7. `hover_crosshair`: hover a line cell → that row, that column and that header get the hover colours; the crossing cell differs from both; leaving the grid clears all three.
8. `selection_beats_hover`: a selected cell keeps its yellow fill while hovered.
9. `filter_chip_and_total_label`: apply Agency: NIH → chip appears, badge shows 1, the total reads "(printed, all accounts)" and its figures are unchanged.
10. `url_round_trip`: every control and the selected figure survive a reload from the copied URL.
11. `blank_vs_zero`: a printed zero shows `—`, is clickable, and counts in the dollar change (AHRQ FY2026 Request vs FY2025 enacted = −369,000) with its % shown as "new"/"—" rules; a confirmed absence and an outside-life cell never feed a change or %; an outside-life cell is blank and not clickable.
12. `feature_flags_hide_unready`: with `features.accounting_views` false, no Amount basis, Account type or defense controls exist in the DOM.
13. `phone_layout`: at 390 × 844 there is no grid; the stage switch is present; tapping NCI opens the sheet with five stages and their sources.
14. `keyboard_and_focus`: every control is reachable by Tab; dialogs and the sheet return focus on close.
15. `default_year_and_lead_stage`: with no `fy` in the URL the page opens on FY2027. For Labor-HHS the change header reads "House vs FY2026 enacted"; the Senate and Enacted columns show `n/c` and `n/e`; the overview's first figure is the House total 1,606,033,805 and its change is +52,771,448 (+3.4%). Switching to FY2026 restores the Enacted layout.

## 11. Screenshot comparison in every UI PR

Each UI PR adds `screenshots/<thread>/` with the seven states above, rendered from the built page at the same sizes, each placed side by side with the reference image ("Ours · Design reference", as in `screenshots/grid-v2/`). The PR description lists every visible difference under "Departures from SPEC.md".
