# Decisions for the owner: D-2 (2026-10-13)

One section per open item, as in decisions-D1.md. Nothing here is applied: each option says what it would change.
Amounts are dollars; report figures print in thousands.

## NSF Research and Related Activities: is the headline the paragraph's total? (D-2 item 7)

**Found.** The headline is recorded as the report table's nondefense line (FY2026 Enacted 7,057,700,000), with the
defense function line (FY2026 Enacted 118,800,000) beside it as a `defense_portion` (retagged). In every bill and law
we hold, the R&RA paragraph appropriates one amount that is exactly the headline plus the defense line (plus, in FY2024
and FY2025 Senate, nothing else: their emergency lines are separate, beside). The paragraph never splits defense from
nondefense; the 050 split is the report table's. So the paragraph does appropriate the total. "Bill text beats the
table" (CLAUDE.md, Data) would make each headline the paragraph's total, with the defense line a `defense_portion`
**inside** it.

Bill and law cells (each read from the committed text by check_legislative_text, result "match" today because the
check already counts the defense line; pages from the govinfo PDFs, D-2):

| FY | Stage | Headline (old) | Defense line | Paragraph appropriates | New headline | Source of new figure, page | Old figure's source |
|---|---|---|---|---|---|---|---|
| 2017 | House Reported | OBS-0726 $6,011,910,000 | CMP-0050 $67,520,000 | $6,079,430,000 | $6,079,430,000 | SRC-BILLS-114HR5393RH, p.67 | SRC-CRPT-114HRPT605 p.114-115 |
| 2017 | Senate Reported | OBS-0725 $5,966,125,000 | CMP-0049 $67,520,000 | $6,033,645,000 | $6,033,645,000 | SRC-BILLS-114S2837PCS, p.77 | SRC-CRPT-114SRPT239 p.139-140 |
| 2017 | Enacted | OBS-0724 $5,966,125,000 | CMP-0048 $67,520,000 | $6,033,645,000 | $6,033,645,000 | SRC-PLAW-115PUBL31, 131 Stat. 215 | SRC-CRPT-115HRPT231 p.120-121 |
| 2018 | House Reported | OBS-0722 $5,962,645,000 | CMP-0046 $71,000,000 | $6,033,645,000 | $6,033,645,000 | SRC-BILLS-115HR3267RH, p.68 | SRC-CRPT-115HRPT231 p.120-121 |
| 2018 | Senate Reported | OBS-0721 $5,846,803,000 | CMP-0045 $71,000,000 | $5,917,803,000 | $5,917,803,000 | SRC-BILLS-115S1662PCS, p.78 | SRC-CRPT-115SRPT139 p.140-141 |
| 2018 | Enacted | OBS-0720 $6,263,476,000 | CMP-0044 $71,000,000 | $6,334,476,000 | $6,334,476,000 | SRC-PLAW-115PUBL141, 132 Stat. 432 | SRC-CRPT-115HRPT704 p.128-129 |
| 2019 | House Reported | OBS-0718 $6,580,500,000 | CMP-0042 $71,000,000 | $6,651,500,000 | $6,651,500,000 | SRC-BILLS-115HR5952RH, p.72 | SRC-CRPT-115HRPT704 p.128-129 |
| 2019 | Senate Reported | OBS-0717 $6,485,183,000 | CMP-0041 $71,000,000 | $6,556,183,000 | $6,556,183,000 | SRC-BILLS-115S3072PCS, p.81 | SRC-CRPT-115SRPT275 p.157-158 |
| 2019 | Enacted | OBS-0716 $6,449,000,000 | CMP-0040 $71,000,000 | $6,520,000,000 | $6,520,000,000 | SRC-PLAW-116PUBL6, 133 Stat. 124 | SRC-CRPT-116HRPT101 p.148-149 |
| 2020 | House Reported | OBS-0714 $7,035,301,000 | CMP-0038 $71,000,000 | $7,106,301,000 | $7,106,301,000 | SRC-BILLS-116HR3055RH, p.79 | SRC-CRPT-116HRPT101 p.148-149 |
| 2020 | Senate Reported | OBS-0713 $6,698,670,000 | CMP-0037 $71,000,000 | $6,769,670,000 | $6,769,670,000 | SRC-BILLS-116S2584RS, p.82 | SRC-CRPT-116SRPT127 p.191-192 |
| 2020 | Enacted | OBS-0712 $6,666,200,000 | CMP-0036 $71,000,000 | $6,737,200,000 | $6,737,200,000 | SRC-PLAW-116PUBL93, 133 Stat. 2420 | SRC-CRPT-116HRPT455 p.187-188 |
| 2021 | House Reported | OBS-0710 $6,896,123,000 | CMP-0034 $71,000,000 | $6,967,123,000 | $6,967,123,000 | SRC-BILLS-116HR7667RH, p.96 | SRC-CRPT-116HRPT455 p.187-188 |
| 2021 | Senate Reported | OBS-0709 $6,836,400,000 | CMP-0033 $71,000,000 | $6,907,400,000 | $6,907,400,000 | SRC-BILL-CJS-FY2021-SENATE, not fetched (committee draft host unreachable); P0073 | SRC-EXPL-FY2021-SENATE p.197-199 |
| 2021 | Enacted | OBS-0708 $6,838,769,000 | CMP-0032 $71,000,000 | $6,909,769,000 | $6,909,769,000 | SRC-PLAW-116PUBL260, 134 Stat. 1271 | SRC-CRPT-117HRPT97 p.213-214 |
| 2022 | House Reported | OBS-0706 $7,618,729,000 | CMP-0030 $77,000,000 | $7,695,729,000 | $7,695,729,000 | SRC-BILLS-117HR4505RH, p.106 | SRC-CRPT-117HRPT97 p.213-214 |
| 2022 | Senate Reported | OBS-0705 $7,590,099,000 | CMP-0029 $77,000,000 | $7,667,099,000 | $7,667,099,000 | SRC-BILLS-117S3042IS, p.92 | SRC-EXPL-FY2022-SENATE p.207-208 |
| 2022 | Enacted | OBS-0704 $7,082,400,000 | CMP-0028 $77,000,000 | $7,159,400,000 | $7,159,400,000 | SRC-PLAW-117PUBL103, 136 Stat. 139 | SRC-CRPT-117HRPT395 p.219-220 |
| 2023 | House Reported | OBS-0702 $7,615,530,000 | CMP-0026 $90,000,000 | $7,705,530,000 | $7,705,530,000 | SRC-BILLS-117HR8256RH, p.95 | SRC-CRPT-117HRPT395 p.219-220 |
| 2023 | Senate Reported | OBS-0701 $8,231,907,000 | CMP-0025 $90,000,000 | $8,321,907,000 | $8,321,907,000 | SRC-BILLS-117S4664IS, p.96 | SRC-EXPL-FY2023-SENATE p.215-216 |
| 2023 | Enacted | OBS-0700 $6,931,136,000 | CMP-0024 $90,000,000 | $7,021,136,000 | $7,021,136,000 | SRC-PLAW-117PUBL328, 136 Stat. 4549 | SRC-CRPT-118SRPT62 p.219-220 |
| 2024 | House Reported | OBS-0698 $7,776,762,000 | CMP-0022 $89,884,000 | $7,866,646,000 | $7,866,646,000 | SRC-BILLS-118HR5893IH, p.86 | SRC-EXPL-FY2024-HOUSE p.170-171 |
| 2024 | Senate Reported | OBS-0697 $7,086,287,000 | CMP-0021 $102,000,000 | $7,608,287,000 (+ emergency CMP-0054 $420,000,000, beside) | $7,188,287,000 | SRC-BILLS-118S2321RS, p.101 | SRC-CRPT-118SRPT62 p.219-220 |
| 2024 | Enacted | OBS-0696 $7,067,190,000 | CMP-0020 $109,310,000 | $7,176,500,000 | $7,176,500,000 | SRC-PLAW-118PUBL42, 138 Stat. 161 | SRC-CRPT-118HRPT582 p.247-248 |
| 2025 | House Reported | OBS-0694 $7,437,320,000 | CMP-0018 $109,310,000 | $7,546,630,000 | $7,546,630,000 | SRC-BILLS-118HR9026RH, p.90 | SRC-CRPT-118HRPT582 p.247-248 |
| 2025 | Senate Reported | OBS-0693 $6,449,280,000 | CMP-0017 $106,000,000 | $7,528,280,000 (+ emergency CMP-0052 $973,000,000, beside) | $6,555,280,000 | SRC-BILLS-118S4795RS, p.99 | SRC-CRPT-118SRPT198 p.227-228 |
| 2025 | Enacted | OBS-0692 $7,067,190,000 | CMP-0016 $109,310,000 | (full-year CR; no paragraph) | $7,176,500,000 (continues FY2024's) | P.L. 119-4 div. A sec. 1101 | SRC-CRPT-119HRPT272 p.256-257 |
| 2026 | House Reported | OBS-0690 $6,263,690,000 | CMP-0014 $109,310,000 | $6,373,000,000 | $6,373,000,000 | SRC-BILLS-119HR5342RH, p.87 | SRC-CRPT-119HRPT272 p.256-257 |
| 2026 | Senate Reported | OBS-0689 $7,067,190,000 | CMP-0013 $109,310,000 | $7,176,500,000 | $7,176,500,000 | SRC-BILLS-119S2354RS, p.100 | SRC-CRPT-119SRPT44 p.217-219 |
| 2026 | Enacted | OBS-0688 $7,057,700,000 | CMP-0012 $118,800,000 | $7,176,500,000 | $7,176,500,000 | SRC-PLAW-119PUBL74, 140 Stat. 45 | SRC-EXPL-FY2026-PB p.129 |

President's Budget (the request's proposed language in FY N's Budget Appendix, D-2 step 1; NSF chapter):

| FY | Request headline (old) | Defense line | Request language appropriates (FY N Appendix) | New headline | Note |
|---|---|---|---|---|---|
| 2017 | OBS-0727 $6,011,910,000 | $67,520,000 | $6,079,430,000 (p.1195) | $6,079,430,000 | |
| 2018 | OBS-0723 $5,290,650,000 | $71,000,000 | $5,361,650,000 (p.1083) | $5,361,650,000 | |
| 2019 | OBS-0719 $6,079,680,000 | $71,000,000 | $4,230,510,000 (p.1091) | not determined | the report's request column follows the amended request, not the Appendix's language |
| 2020 | OBS-0715 $5,591,960,000 | $71,000,000 | $5,662,960,000 (p.1107) | $5,662,960,000 | |
| 2021 | OBS-0711 $6,142,020,000 | $71,000,000 | $6,213,020,000 (p.1187) | $6,213,020,000 | |
| 2022 | OBS-0707 $8,062,710,000 | $77,000,000 | $8,139,710,000 (p.1205) | $8,139,710,000 | |
| 2023 | OBS-0703 $8,335,987,000 | $90,000,000 | $8,425,987,000 (p.1173) | $8,425,987,000 | |
| 2024 | OBS-0699 $8,915,900,000 | $102,000,000 | $9,029,900,000 (p.1141) | not determined | the language is $12,000,000 more than headline + defense; not explained |
| 2025 | OBS-0695 $7,519,320,000 | $106,000,000 | $8,045,320,000 (p.1083) | $7,625,320,000 | the rest, $420,000,000, is the emergency piece (OBS-0771), beside |
| 2026 | OBS-0691 $3,166,840,000 | $109,310,000 | $3,276,150,000 (p.1007) | $3,276,150,000 | |

**What it would change.**
- Headlines: 30 bill and law headlines (FY2017-FY2026, House, Senate, Enacted; FY2025 Enacted continues FY2024's under
  the CR) rise by their defense line, old -> new as in the table; 8 request headlines too if the rule is extended to
  the request language (FY2019 and FY2024 are not determined).
- Components: each `defense_portion` (CMP-0017 ... CMP-0050) moves from beside to inside (in_headline FALSE -> TRUE);
  amounts unchanged; no new IDs.
- Views (site): for each changed cell, every basis but nondefense rises by the defense amount (as printed included);
  `views_nondefense` is unchanged (headline less the inside defense portion = today's headline); excluding-earmarks
  views rise with their basis. The CJS title and bill totals are printed figures and do not change; no measure_reconcile
  printed view involves R&RA, so its counts do not move.
- The text check stays "match" (the paragraph's amount equals the new headline, with nothing beside it to add).

**Options.**
- A. Adopt: headline = the paragraph's total, defense inside (30 bill/law headlines; needs your approval as a headline
  change; list above).
- B. Keep the report's nondefense line as the headline (today), and record the paragraph's total as a note on each
  cell.

**Recommendation: A** for the bill and law stages (the rule the data already follows elsewhere); the request rows only
if you extend the rule to the Appendix's language.

---

## President's Budget totals: the Fund's allocation (`pphf_general_provision_table`, 10 cells)

**Found.** With the request language read (step 1), CDC's and SAMHSA's President's Budget program levels are checked
for the first time. Each differs by Prevention and Public Health Fund money: the request allocates the Fund outside
its appropriations language (no phrase in the Appendix names it), and no President's Budget row is in
`pphf_table.csv`. CDC FY2017, FY2018, FY2020, FY2021, FY2022, FY2025 and SAMHSA FY2017, FY2022, FY2023, FY2025 (the
amounts are in measure_reconcile's summary; SAMHSA's are the $12,000,000 Garrett Lee Smith line each year but FY2017,
$27,830,000).

- A. Read each report's comparative-table Prevention and Public Health Fund lines in the request column as President's
  Budget `transfer_in` rows (`build_pphf_table.py`, as FY2024 Enacted's rows come from a comparative table). Expected:
  the 10 cells pass. No headline changes.
- B. Leave flagged.

**Recommendation: A** (a later PR; the House report tables are images for some years, so page images may be needed).

## President's Budget totals: unexplained (5 cells)

| Cell | Difference (printed - computed) | Note |
|---|---|---|
| ACF FY2025 [excluding emergencies] | -$2,914,179,000 | the FY2025 Appendix's Refugee and Entrant Assistance language (p.439) designates it: 'of the amounts made available under this heading, $2,914,179,000, along with any amounts made available pursuant to the preceding proviso, are designated by the Congress as being for an emergency requirement'. The amount's phrase window (legislative_text.amounts_in, 100 characters after) ends at 'are designated by the Con', so the emergency rule never sees it. A follow-up: widen the window or read the designation to the clause's end (a parser change: every text file is reparsed) |
| AHRQ FY2024 [program level] | $45,000,000 | |
| OS FY2023 [program level] | $103,614,000 | |
| NIH FY2019 [program level] | $728,400,000 | the FY2019 report's request column follows the amended request, not the Appendix |
| ACF FY2017 [program level] | $15,000,000 | |

Recommendation: review each against the report's request column; no change in this PR.

## A default that would leave a negative base (rule added in D-2; confirmed, owner 2026-10-13)

CMS Program Management FY2026 request (OBS-LHHS-0209, 3,464,391 thousand, the report's request column): the
Appendix language's trust-fund limitation is $3,518,440,000. No assignment turns the language into the headline, and
the default (inside) left a negative base. D-2 adds: a default never puts inside more than the headline holds, so the
limitation (CMP-LHHS-1175) is beside, with a note. No figure changes; it fired only here.

- A. Keep the rule (ACCOUNTING_RULES, Arithmetic).
- B. Keep the component inside and let the base check fail for review.

**Recommendation: A** (the base is defined as never negative).

## Notes for the record (no decision needed unless you want one)

- **Appendix misprints.** The FY2022 NASA (p.1201, '$46,00,0000'), FY2024 HHS (p.433, '$552,500,0000') and FY2027
  HHS (p.477, '$3,527,479,00') chapters print amounts with misplaced commas; they are listed in
  `request_language.json` and not read. Each was checked on the page image.

## NEI FY2027 request: '$833,000' (probable misprint; for review)

The FY2027 HHS chapter prints NEI's proposed appropriation as '$833,000' (p.454; checked on the page image), against
the report's request column, 833,000 thousand ($833,000,000; OBS for ACC-HHS-NIH-NEI FY2027 President's Budget).
Owner, D-2: treat it as a probable misprint like the three above. It is listed under `misprinted_amounts`
(`request_language.PROBABLE_MISPRINTS`) and not read, so no component or view uses it. NEI's FY2027 language holds no
other amount and no component phrase, so the account stays "read, no match" and its views are the report's headline.
Nothing changes. For review: confirm against the FY2027 Congressional Justification, or a corrected Appendix if
one is published.

## Owner confirmations (2026-10-13, on this PR)

- The never-negative default rule (above): confirmed, A.
- Retiring CA-LHHS-0038 and -0039: confirmed.
- Decisions 1-3 (NSF R&RA; President's Budget PPHF rows; the 5 unexplained President's Budget cells, including the
  phrase-window follow-up) go to a follow-up PR, D-3.

- **OBS-LHHS-1711** (the brief's "HSPS FY2026 House 35,395,000") is the FY2026 President's Budget line in H.Rept.
  119-271's request column, not the House figure; the FY2026 House line is OBS-LHHS-1710 (129,895,000). Neither is an
  earmark-rule headline (the table prints no CDS line for HSPS in any FY2026 column), so neither needs an earmark.
- **Confirmed absences retired** (confirmed, owner 2026-10-13). CA-LHHS-0038 and -0039 (CDC-Wide FY2023 House and Senate) rested on the tables'
  silence; the bill-text headlines of decision 4 A replace them (`reference/review/lhhs/retired_ids.json`).
- **Not held.** The FY2023 Senate explanatory statement (appropriations.senate.gov) could not be fetched in D-2, so the
  Birth Defects, EEOICP and ONC FY2023 Senate headlines carry no table figure as a note; CDC-Wide's cites the held
  'Subtotal' (VAL-LHHS-02215).
