# Congress.gov API: what else it can do for us

Investigation only (2026-10-07). This changes no production code and no data. The test scripts were throwaway and are not in the repo.
The NTU workbook was read as a reference input. It is not committed, and its rows are not copied here: the CSV holds only
our IDs, the fields compared, and whether they agree.

Files:
- `ntu_congress_crosscheck.csv`: item 7, all 12 subcommittees, FY2017–FY2026, plus the FY2027 House reports.
- `xml_vs_ours_fy2026_lhhs.csv`: item 4, the FY2026 Labor-HHS enrolled-bill XML compared with our Enacted figures.

## Rate limit

| | Limit (per hour) | Shared? |
|---|---|---|
| Congress.gov API | 20,000 (`X-RateLimit-Limit`) | **No.** Same api.data.gov key, separate counter |
| GovInfo API | 36,000 | Calls to one never moved the other's `X-RateLimit-Remaining` |

The whole investigation used about 1,700 Congress.gov requests. Responses took about 1–3 s each.

## 1. Stage tracking (bill actions → our stage): **works**

| Our stage | Action that dates it | Result on our real bills |
|---|---|---|
| House Reported | `type=Committee`, text "Committee on Appropriations reported … H. Rept. NNN-NNN". LoC code `5000` before the 119th Congress, `1010` in the 119th; House code `H12100` (original measure) | 128 House reports: report number 128/128, date 126/128 |
| Senate Reported | `type=Committee`, text "Committee on Appropriations. Original measure reported … With written report No. NNN-NNN" (LoC `14000`) | 76 Senate reports: number 76/76, date 75/76 |
| Passed House / Passed Senate | LoC `8000` / `17000` | present on every vehicle |
| Resolving differences | LoC `19500` (House) / `20500` (Senate) | |
| Enacted | LoC `36000` "Became Public Law No: NNN-NN" (also `E40000`) | 120/120 law numbers and dates agree with NTU |

**Ambiguities:**
- **Match on the text, not the code.** The LoC code for "reported" changed in the 119th Congress (`5000` → `1010`). A vehicle can also be "reported" by another committee: H.R. 2882 was reported by Natural Resources (H. Rept. 118-364), so require "Appropriations" in the text.
- **Action date vs. report date.** Three are off by a day or two: H. Rept. 115-234, H. Rept. 119-161 and S. Rept. 119-55 (reported 07-31, issued 08-01). Use the action date for "reported" and the report's `issueDate` as the print date.
- **Drafts never reported have no reported action, which is the right answer.** Our five draft rows have no Congress.gov report: CJS FY2021/22/23 Senate, CJS FY2024 House, Labor-HHS FY2023 Senate (marked `ours-only` in the CSV).
- **A committee bill can become law as something else.** H.R. 3055, the FY2020 House CJS bill, is P.L. 116-69, a *continuing resolution*. "This bill became law" must never be read as "this subcommittee's bill was enacted".
- **Omnibus vehicles** carry no subcommittee in their actions; the division comes from the enrolled XML (item 3).

## 2. Discovery: **works**

| Question | Answer |
|---|---|
| List Appropriations Committee reports | `committee/house/hsap00/reports` (513) and `committee/senate/ssap00/reports` (558) work. Each detail call gives title, `issueDate`, `associatedBill`, `isConferenceReport`. Congresses 114–119: 308 reports; 229 are one subcommittee bill. The rest are 302(b) suballocations and activity reports, which the title filters out |
| Conference reports | **Missing from the committee lists** (their `committees` is empty). Use `committee-report/{congress}/hrpt?conference=true`. This finds H. Rept. 115-929, 115-952, 116-9 (our CJS FY2019 Enacted report) and others |
| `fromDateTime` / `toDateTime` | Honoured by `committee-report`, `committee/{chamber}/{code}/reports`, `bill`, `crsreport`. Ignored by `law/{congress}` and `daily-congressional-record` (same count either way) |
| Sort by update date | `sort=updateDate desc` works on `bill` and `committee-report`. It is ignored on the committee reports list and on `crsreport`, whose default order is already newest first |
| "What's new since yesterday" | `committee-report?fromDateTime=<last run>` returned 16 reports for 2026-10-06/07. Filter to `hsap00`/`ssap00`/conference by detail call |

Labor-HHS reports, FY2017–FY2022 (every one Congress.gov lists):

| FY | House | Senate |
|---|---|---|
| 2017 | H. Rept. 114-699 (H.R. 5926, 2016-07-22) | S. Rept. 114-274 (S. 3040, 2016-06-09) |
| 2018 | H. Rept. 115-244 (H.R. 3358, 2017-07-24) | S. Rept. 115-150 (S. 1771, 2017-09-07) |
| 2019 | H. Rept. 115-862 (H.R. 6470, 2018-07-23) | S. Rept. 115-289 (S. 3158, 2018-06-28) |
| 2020 | H. Rept. 116-62 (H.R. 2740, 2019-05-15) | none (not reported) |
| 2021 | H. Rept. 116-450 (H.R. 7614, 2020-07-15) | none (no Senate markups that year) |
| 2022 | H. Rept. 117-96 (H.R. 4502, 2021-07-19) | none (chair's draft only) |

FY2019 conference report: H. Rept. 115-952 (H.R. 6157, Defense + Labor-HHS).

## 3. Related bills and laws: **partly**

| Route | Result |
|---|---|
| Committee bill → `relatedbills` → enacted vehicle | 23 of 31 Labor-HHS/CJS committee bills reach it (all "Related bill", identified by CRS or House). **Fails whenever the law passed in the next Congress:** FY2017 (H.R. 244, 115th), FY2019 CJS (H.J.Res. 31, 116th), FY2025 (H.R. 1968, 119th). `relatedbills` never crosses a Congress |
| Law → subcommittee + division | Works from the other direction. `law/{congress}` → appropriations-titled laws (57 in Congresses 114–119) → enrolled-bill **Formatted XML** → `<division><enum>B</enum><header>…Appropriations Act, 2026</header>`. The bill's `titles` "portions as enacted" list is empty for every 119th-Congress law, so the XML is the reliable source |
| Compared with our Bill Report Reference (v37) | All 14 Enacted rows agree on `vehicle_bill_id`, law, `division` and `enactment_date`. `funding_type` follows: a full-year CR is the one "Full-Year Continuing Appropriations Act, 2025" division; omnibus/minibus is the number of regular divisions in the vehicle |

## 4. Bill text, FY2023–FY2026: **works**

Every version of the vehicles we checked has **Formatted XML**: introduced, engrossed, engrossed amendment, placed on calendar, and enrolled. The *Public Law* version has only PDF, text and USLM, so use the enrolled bill.

For FY2026 Labor-HHS (H.R. 7148 enrolled, Division B, Title II) we read 88 HHS headings and the first dollar amount after each. Compared with our 79 Enacted budget-authority figures:

| Result | Count | Why |
|---|---|---|
| Same amount | 62 | e.g. NCI $7,352,159,000; CDC lines; NIH institutes |
| Different | 11 | The bill's first dollar amount is not our headline: advance/prior-year amounts (Foster Care, Child Support), transfers counted in (NIGMS $3.27B in the bill vs $1.84B BA: PHS evaluation tap), trust-fund limitations (VICP), program level vs BA (GDM, ONC, SAMHSA program support, Rural Health, NIH Buildings) |
| No dollar amount in its paragraph | 2 | "such sums" / amounts under sub-headings (Environmental Health, Commissioned Officers) |
| No heading of that name | 4 | Our sub-accounts and general provisions (Head Start, Health Centers, Medicare limitation/operations) |

So the XML is a good **cross-check and heading source**. It is not a replacement for the explanatory-statement tables, which give the BA figure directly.

## 5. Congressional Record: **partly; better left to the human**

`daily-congressional-record/{vol}/{issue}/articles` lists articles with title, pages and PDF. It goes back at least to 2017. When you know the date, it finds the statement: 2026-01-22 H1353-H1909 (our Labor-HHS FY2026 explanatory statement, already stored), 2017-05-03 H3949, 2018-03-22 H2045, 2019-02-13 H1589 (conference report + explanatory material), 2020-12-21 H7879.

A search by date window around every floor action took about 50 issue calls per vehicle. It missed FY2020 (H.R. 1865) and FY2022 (H.R. 2471) and produced a false hit. I stopped it partway. FY2019 Labor-HHS's statement is in the conference report (H. Rept. 115-952), not in the Record. FY2025 (full-year CR) has none.

**Recommendation:** don't search the Record. When a law is enacted, a human gives the Record date and page (or the rules.house.gov / committee PDF). We ingest it manually as now, and the API only confirms that the article exists on that date.

## 6. CRS: **works**

The `crsreport` endpoint (14,167 items, PDF and HTML links, `relatedMaterials` → laws) includes the status series:

- Labor-HHS "Status of FY20xx … Appropriations: In Brief": R44478 (FY2017), R46457 (2021), R46853 (2022), R47233 (2023), R47622 (2024), R48109 (2025), R48616 (2026), R49344 (2027), plus IN10800/IN10751 (2018), IN10944 (2019), IN11114 (2020).
- CJS: the "Overview of FY20xx Appropriations for CJS" series, FY2020–FY2027 (R45702 … R48929).

There is no title search: the list has to be paged (57 pages of 250), or polled daily with `fromDateTime`. The Appropriations Status Table itself is a Congress.gov web page, not an API endpoint (R47240 describes it).

## 7. NTU cross-check: **works**

`ntu_congress_crosscheck.csv` has 329 rows:
- **120 Enacted rows** (12 subcommittees × FY2017–FY2026), all `both-agree`: vehicle, P.L., division and date. The only discrepancy during the run was my own classifier mistaking a surface-transportation extension (P.L. 119-103) for THUD; I fixed the filter. Conference reports are filled in where Congress.gov has one.
- **204 House/Senate Reported rows** from Congress.gov.
- **5 `ours-only` rows**: our five draft stages, which have no Congress.gov report.

The note column says where our Bill Report Reference agrees: 38 rows, no differences.

## What to build first for the daily automation

1. **Daily report watcher**, about 20 requests a day:
   - `committee-report?fromDateTime=<last run>` plus `?conference=true`;
   - keep the `hsap00`/`ssap00`/conference ones;
   - classify by title to subcommittee and FY;
   - propose new House/Senate Reported rows (bill, report, date) for review.
2. **Stage tracker for the bills we hold**: our committee bills and known vehicles, re-read when their `updateDate` changes. Map actions to stages by the text rules in item 1, so an "Enacted" event shows up the day it happens.
3. **Enactment filler**: on a new law, read the enrolled XML divisions to propose `vehicle_bill_id`, `division`, `enactment_date` and `funding_type` (they match all 120 NTU rows). The explanatory statement's location stays a **human input** (item 5).

Keep for later: CRS status-report polling (cheap; `fromDateTime`) and XML amount cross-checks.
