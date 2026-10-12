"""
Write reference/review/accounting/pphf_table.csv: the Prevention and Public Health Fund allocation, Labor-HHS
FY2017-FY2026, which scripts/tag_components.py reads into transfer_in components.

Each bill's general provision (sec. 222) transfers the Fund "to the accounts specified, in the amounts specified,
and for the activities specified under the heading Prevention and Public Health Fund" in the report (or explanatory
statement), so the rows are that table's: the account (Senate) or agency (House, JES), the activity and the amount.
Where the table names only the agency, the activity's account is the one the same document's comparative table
prints the Fund's line under (ACCOUNT below), and the script checks that each account's rows add up to that line.
FY2024 Enacted: the FY2024 explanatory statement is not a document we hold, so the rows are the FY2025 Senate
report's comparative-table lines in its 2024 appropriation column (the same document as the printed view).

FY2017-FY2023 (the component backfill, owner 2026-10-12): House and Senate rows are read from each report's own
allocation table in the text layer (BACKFILL below; Senate tables print thousands). The FY2018 House report prints no
allocation table (H.R. 3358 sec. 225 points to one), so its rows are the comparative table's Fund lines in the
committee column. Enacted: the explanatory statements are not documents we hold, so, as for FY2024, the rows are the
Fund lines of the comparative table that prints the Enacted view, in its prior-year (enacted) column. FY2020 Enacted's
view prints in H.Rept. 116-450, whose table pages are images: its rows are the FY2021 Senate explanatory statement's
2020 column (text layer). Each cell's account sums are checked against the comparative table (or, for the FY2017 House
report, whose table pages are images, the narrative's 'in transfers from the PPH Fund' amount per account).

Run locally (document_store/ is gitignored; it needs the report PDFs and pdftotext):
    python reference/review/accounting/build_pphf_table.py
"""

import collections
import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reference" / "review" / "accounting" / "pphf_table.csv"

ACCOUNT = {"Immunization and Respiratory Diseases": "ACC-HHS-CDC-IMMUNIZATION",
         "Emerging and Zoonotic Infectious Diseases": "ACC-HHS-CDC-EMERGING-ZOONOTIC",
         "Chronic Disease Prevention and Health Promotion": "ACC-HHS-CDC-CHRONIC-DISEASE",
         "Public Health and Scientific Services": "ACC-HHS-CDC-SCIENTIFIC-SERVICES",
         "Public Health Scientific Services": "ACC-HHS-CDC-SCIENTIFIC-SERVICES",
         "Environmental Health": "ACC-HHS-CDC-ENVIRONMENTAL-HEALTH",
         "CDC-Wide Activities": "ACC-HHS-CDC-PROGRAM-SUPPORT",
         "Mental Health": "ACC-HHS-SAMHSA-MENTAL-HEALTH",
         "Aging and Disability Services Programs": ""}
ALD = "Aging and Disability Services Programs"
I, E, C, P, H, W, M = ("Immunization and Respiratory Diseases", "Emerging and Zoonotic Infectious Diseases",
    "Chronic Disease Prevention and Health Promotion", "Public Health Scientific Services", "Environmental Health",
    "CDC-Wide Activities", "Mental Health")
ROWS = [
 # FY2024 House (draft report; table names the agency only)
 (2024,"House Reported","SRC-EXPL-LHHS-FY2024-HOUSE",177,[
  ("ACL",ALD,"Alzheimer's Disease Program",14700000),("ACL",ALD,"Chronic Disease Self-Management",8000000),
  ("ACL",ALD,"Falls Prevention",5000000),("CDC",C,"Breastfeeding Grants (Hospitals Promoting Breastfeeding)",9750000),
  ("CDC",C,"Diabetes",155129000),("CDC",E,"Epidemiology and Laboratory Capacity Grants",40000000),
  ("CDC",E,"Healthcare Associated Infections",12000000),("CDC",C,"Heart Disease and Stroke Prevention Program",155105000),
  ("CDC",C,"Million Hearts Program",5000000),("CDC",W,"Preventive Health and Health Services Block Grant",160000000),
  ("CDC",I,"Section 317 Immunization Grants",593216000),("CDC",H,"Lead Poisoning Prevention",51000000),
  ("CDC",C,"Early Care Collaboratives",5000000),("SAMHSA",M,"Garrett Lee Smith—Youth Suicide",12000000)]),
 (2024,"Senate Reported","SRC-CRPT-118SRPT84",208,[
  (ALD,ALD,"Alzheimer's Disease Prevention Education and Outreach",14700000),(ALD,ALD,"Chronic Disease Self-Management",8000000),
  (ALD,ALD,"Falls Prevention",5000000),(I,I,"Section 317 Immunization Grants",702250000),
  (E,E,"Epidemiology and Laboratory Capacity Grants",40000000),(E,E,"Healthcare Associated Infections",12000000),
  (C,C,"Office of Smoking and Health (Tobacco Prevention/Media & Quit Lines)",125850000),
  (C,C,"Breast Feeding Grants (Hospitals Promoting Breastfeeding)",9750000),(C,C,"Million Hearts Program",5000000),
  (C,C,"Heart Disease & Stroke Prevention Program",57075000),(C,C,"Diabetes",52275000),(C,C,"Early Care Collaboratives",5000000),
  (H,H,"Lead Poisoning Prevention",17000000),(W,W,"Preventive Health and Health Services Block Grants",160000000),
  (M,M,"Suicide Prevention (Garrett Lee Smith)",12000000)]),
 (2025,"House Reported","SRC-CRPT-118HRPT585",211,[
  ("ACL",ALD,"Alzheimer's Disease Program",14700000),("ACL",ALD,"Chronic Disease Self-Management",8000000),
  ("ACL",ALD,"Falls Prevention",5000000),("CDC",C,"Hospitals Promoting Breastfeeding",9750000),
  ("CDC",C,"Diabetes",110758000),("CDC",E,"Epidemiology and Laboratory Capacity Grants",40000000),
  ("CDC",E,"Healthcare Associated Infections",12000000),("CDC",C,"Heart Disease and Stroke Prevention Program",110759000),
  ("CDC",C,"Million Hearts Program",5000000),("CDC",I,"Section 317 Immunization Grants",681933000),
  ("CDC",H,"Lead Poisoning Prevention",51000000),("CDC",P,"Public Health Data Modernization",160000000),
  ("CDC",C,"Early Care Collaboratives",5000000),("SAMHSA",M,"Garrett Lee Smith—Youth Suicide",12000000)]),
 (2025,"Senate Reported","SRC-CRPT-118SRPT207",242,[
  (ALD,ALD,"Alzheimer's Disease Prevention Education and Outreach",14700000),(ALD,ALD,"Chronic Disease Self-Management",8000000),
  (ALD,ALD,"Falls Prevention",5000000),(I,I,"Section 317 Immunization Grants",469350000),
  (E,E,"Epidemiology and Laboratory Capacity Grants",40000000),(E,E,"Healthcare Associated Infections",12000000),
  (C,C,"Office of Smoking and Health (Tobacco Prevention/Media & Quit Lines)",141850000),
  (C,C,"Breast Feeding Grants (Hospitals Promoting Breastfeeding)",9750000),(C,C,"Million Hearts Program",5000000),
  (C,C,"Heart Disease & Stroke Prevention Program",57075000),(C,C,"Diabetes",52275000),(C,C,"Early Care Collaboratives",5000000),
  ("Public Health and Scientific Services",P,"Data Modernization Initiative",182900000),
  (H,H,"Lead Poisoning Prevention",51000000),(W,W,"Preventive Health and Health Services Block Grants",160000000),
  (M,M,"Suicide Prevention (Garrett Lee Smith)",12000000)]),
 (2026,"House Reported","SRC-CRPT-119HRPT271",229,[
  ("ACL",ALD,"Alzheimer's Disease Program",14700000),("ACL",ALD,"Chronic Disease Self-Management",8000000),
  ("ACL",ALD,"Falls Prevention",5000000),("CDC",C,"Breast and Cervical Cancer",86182000),
  ("CDC",C,"Diabetes",163130000),("CDC",E,"Epidemiology and Laboratory Capacity Grants",45000000),
  ("CDC",C,"Heart Disease and Stroke Prevention Program",163130000),("CDC",I,"Immunization Grants",699933000),
  ("CDC",H,"Lead Poisoning Prevention",51000000),("CDC",P,"Public Health Data Modernization",185000000),
  ("CDC",C,"Early Care Collaboratives",5000000),("SAMHSA",M,"Garrett Lee Smith—Youth Suicide Prevention",12000000)]),
 (2026,"Senate Reported","SRC-CRPT-119SRPT55",265,[
  (ALD,ALD,"Alzheimer's Disease Program",14700000),(ALD,ALD,"Chronic Disease Self-Management",8000000),
  (ALD,ALD,"Elder Falls Prevention",5000000),(I,I,"Section 317 Immunization Program",554958000),
  (E,E,"Epidemiology and Laboratory Capacity Grants",40000000),(E,E,"Healthcare Associated Infections",12000000),
  (C,C,"Office of Smoking and Health",200000000),(C,C,"Breast Feeding Grants (Hospitals Promoting Breastfeeding)",9750000),
  (C,C,"Million Hearts Program",5000000),(C,C,"Heart Disease and Stroke",54255000),(C,C,"Diabetes",66412000),
  (C,C,"National Early Child Care Collaboratives",5000000),
  ("Public Health and Scientific Services",P,"Data Modernization Initiative",150000000),
  (H,H,"Childhood Lead Poisoning",51000000),(W,W,"Preventive Health and Health Services Block Grants",160000000),
  (W,W,"Public Health Data Modernization",90000000),(M,M,"Garrett Lee Smith-Youth Suicide Prevention",12000000)]),
 (2026,"Enacted","SRC-EXPL-LHHS-FY2026-ENACTED",245,[
  ("ACL",ALD,"Alzheimer's Disease Program",14700000),("ACL",ALD,"Chronic Disease Self-Management",8000000),
  ("ACL",ALD,"Elder Falls Prevention",5000000),("CDC",C,"Hospitals Promoting Breastfeeding",9750000),
  ("CDC",C,"Diabetes",118130000),("CDC",E,"Epidemiology and Laboratory Capacity",40000000),
  ("CDC",E,"Healthcare-Associated Infections",12000000),("CDC",C,"Heart Disease and Stroke",111105000),
  ("CDC",C,"Million Hearts Program",5000000),("CDC",C,"Tobacco",200000000),
  ("CDC",W,"Preventative Health and Health Services Block Grants",160000000),
  ("CDC",I,"Section 317 Immunization Program",596390000),("CDC",H,"Childhood Lead Poisoning",51000000),
  ("CDC",C,"National Early Child Care Collaboratives",5000000),("CDC",W,"Public Health Infrastructure and Capacity",90000000),
  ("SAMHSA",M,"Garrett Lee Smith–Youth Suicide Prevention",12000000)]),
]
# FY2024 Enacted: no allocation table for that year in a document we hold (its own is in the FY2024 explanatory
# statement); the FY2025 Senate report's comparative table prints each account's PPHF line in its 2024 column
FY2024_ENACTED = (2024,"Enacted","SRC-CRPT-118SRPT207",[
  (I,"Prevention and Public Health Fund",681933000),(E,"Prevention and Public Health Fund",52000000),
  (C,"Prevention and Public Health Fund",241267000),(H,"Prevention and Public Health Fund",51000000),
  (W,"Preventive Health and Health Services Block Grant (Prevention and Public Health Fund)",160000000),
  (M,"Prevention and Public Health Fund",12000000)])
COMPARATIVE = {  # per-account PPHF lines printed in the same document's comparative table (bill column), for the check
 (2024,"House Reported"): {I:593216000,E:52000000,C:329984000,H:51000000,W:160000000},
 (2024,"Senate Reported"): {I:702250000,E:52000000,C:254950000,H:17000000,W:160000000},
 (2025,"House Reported"): {I:681933000,E:52000000,C:241267000,P:160000000,H:51000000},
 (2025,"Senate Reported"): {I:469350000,E:52000000,C:270950000,P:182900000,H:51000000,W:160000000},
 (2026,"House Reported"): {I:699933000,E:45000000,C:417442000,P:185000000,H:51000000},
 (2026,"Senate Reported"): {I:554958000,E:52000000,C:340417000,P:150000000,H:51000000,W:250000000},
 (2026,"Enacted"): {I:596390000,E:52000000,C:448985000,H:51000000,W:250000000},
}
BILL = {(2024,"House Reported"):"SRC-BILLS-118HR5894IH",(2024,"Senate Reported"):"SRC-BILLS-118S2624RS",
        (2024,"Enacted"):"SRC-PLAW-118PUBL47",(2025,"House Reported"):"SRC-BILLS-118HR9029RH",
        (2025,"Senate Reported"):"SRC-BILLS-118S4942RS",(2026,"House Reported"):"SRC-BILLS-119HR5304RH",
        (2026,"Senate Reported"):"SRC-BILLS-119S2587RS",(2026,"Enacted"):"SRC-PLAW-119PUBL75"}


def page(doc, p):
    return subprocess.run(["pdftotext","-f",str(p),"-l",str(p),"-layout", str(ROOT / "document_store" / f"{doc}.pdf"),"-"],
                          capture_output=True, text=True).stdout
def fmt(n, thousands=False):
    return f"{n//1000:,}" if thousands else f"{n:,}"
def find(doc, p, words, amount, tok, thousands=False):
    for q in (p, p-1, p+1):
        for line in page(doc, q).splitlines():
            if fmt(amount, thousands) in line and words.split()[0].strip("()'’") in line:
                if doc.startswith("SRC-EXPL") and tok in line:
                    line = line[line.find(tok):line.find(fmt(amount)) + len(fmt(amount))]
                return q, re.sub(r"\.{3,}", " ... ", re.sub(r"\s{2,}", "  ", line.strip()))
    raise SystemExit(f"not found: {doc} p{p} {words} {amount}")
out = []
cdc = collections.Counter(); acct = collections.defaultdict(collections.Counter)
for fy, st, doc, p, rows in ROWS:
    for printed_acct, acc_name, activity, amt in rows:
        q, line = find(doc, p, activity.replace("Alzheimer's","Alzheimer"), amt, printed_acct)
        acc = ACCOUNT[acc_name]
        out.append(dict(fiscal_year=fy, stage=st, source_document_id=doc, page=q, table="allocation",
                        account_printed=printed_acct, activity=activity, amount=amt, account_id=acc,
                        bill_document_id=BILL[(fy,st)], bill_section="sec. 222", printed_line=line, read_from="text layer"))
        if acc: acct[(fy,st)][acc_name] += amt
fy, st, doc, rows = FY2024_ENACTED
for acc_name, label, amt in rows:
    for p in range(430, 445):
        hit = [l for l in page(doc, p).splitlines() if label.split()[0] in l and f"({amt//1000:,})" in l]
        if hit: break
    else: raise SystemExit(f"not found {acc_name}")
    line = re.sub(r"\.{3,}", " ... ", re.sub(r"\s{2,}", "  ", hit[0].strip()))
    out.append(dict(fiscal_year=fy, stage=st, source_document_id=doc, page=p, table="comparative, 2024 appropriation column",
                    account_printed=acc_name, activity=label, amount=amt, account_id=ACCOUNT[acc_name],
                    bill_document_id=BILL[(fy,st)], bill_section="div. D sec. 222", printed_line=line, read_from="text layer"))
bad = 0

# ---- FY2017-FY2023 backfill ---------------------------------------------------------------------------------------
# House tables name the agency only: the activity's account, by the activity (checked against COMPARATIVE_BF)
ACTIVITY = [(r"Breast ?[Ff]eeding|Diabetes|Heart Disease|Million Hearts|Tobacco|Smoking|Early Care|Racial and Ethnic", C),
            (r"Epidemiology|Healthcare Associated", E), (r"317|Immunization", I), (r"Lead Poisoning", H),
            (r"Block Grant", W), (r"Garrett Lee Smith|Suicide", M), (r"Alzheimer|Self[- ]Management|Falls", ALD)]
SENATE_ACCOUNT = [("Immunization", I), ("Emerging", E), ("Chronic", C), ("Environmental", H), ("CDC", W), ("Mental", M),
                  ("Aging", ALD)]
# (fy, stage, report, first page of the allocation table, amounts in thousands, bill, section)
BACKFILL = [
 (2017,"House Reported","SRC-CRPT-114HRPT699",116,False,"SRC-BILLS-114HR5926RH","sec. 221"),
 (2017,"Senate Reported","SRC-CRPT-114SRPT274",162,True,"SRC-BILLS-114S3040PCS","sec. 221"),
 (2018,"Senate Reported","SRC-CRPT-115SRPT150",159,True,"SRC-BILLS-115S1771PCS","sec. 219"),
 (2019,"House Reported","SRC-CRPT-115HRPT862",128,False,"SRC-BILLS-115HR6470RH","sec. 222"),
 (2019,"Senate Reported","SRC-CRPT-115SRPT289",175,True,"SRC-BILLS-115S3158PCS","sec. 224"),
 (2020,"House Reported","SRC-CRPT-116HRPT62",179,False,"SRC-BILLS-116HR2740RH","sec. 222"),
 (2021,"House Reported","SRC-CRPT-116HRPT450",233,False,"SRC-BILLS-116HR7614RH","sec. 222"),
 (2021,"Senate Reported","SRC-EXPL-LHHS-FY2021-SENATE",180,True,"SRC-BILL-LHHS-FY2021-SENATE","sec. 222"),
 (2022,"House Reported","SRC-CRPT-117HRPT96",259,False,"SRC-BILLS-117HR4502RH","sec. 221"),
 (2022,"Senate Reported","SRC-EXPL-LHHS-FY2022-SENATE",231,True,"SRC-BILLS-117S3062IS","sec. 221"),
 (2023,"House Reported","SRC-CRPT-117HRPT403",248,False,"SRC-BILLS-117HR8295RH","sec. 221"),
 (2023,"Senate Reported","SRC-EXPL-LHHS-FY2023-SENATE",234,True,"SRC-BILLS-117S4659IS","sec. 221"),
]
# comparative-table Fund lines (thousands) read as rows: (fy, stage, document, column, bill, section, [(account, amount, page)])
COMPARATIVE_ROWS = [
 (2018,"House Reported","SRC-CRPT-115HRPT244","committee recommendation","SRC-BILLS-115HR3358RH","sec. 225",
  [(I,273650,211),(E,52000,211),(C,337950,211),(H,17000,212),(W,160000,213)]),
 (2017,"Enacted","SRC-CRPT-115HRPT244","2017 enacted","SRC-PLAW-115PUBL31","",
  [(I,324350,211),(E,52000,211),(C,337950,211),(H,17000,212),(W,160000,213),(M,12000,217)]),
 (2018,"Enacted","SRC-CRPT-115HRPT862","2018 enacted","SRC-PLAW-115PUBL141","div. H sec. 224",
  [(I,324350,361),(E,52000,361),(C,247550,362),(H,17000,362),(W,160000,363),(M,12000,367)]),
 (2019,"Enacted","SRC-CRPT-116HRPT62","2019 enacted","SRC-PLAW-115PUBL245","div. B sec. 222",
  [(I,320550,333),(E,52000,333),(C,254950,333),(H,17000,334),(W,160000,335),(M,12000,339)]),
 (2020,"Enacted","SRC-EXPL-LHHS-FY2021-SENATE","2020 appropriation","SRC-PLAW-116PUBL94","div. A sec. 222",
  [(I,370300,247),(E,52000,247),(C,254950,247),(H,17000,247),(W,160000,248),(M,12000,252)]),
 (2021,"Enacted","SRC-CRPT-117HRPT96","2021 enacted","SRC-PLAW-116PUBL260","div. H sec. 222",
  [(I,372200,472),(E,52000,472),(C,254950,472),(H,17000,473),(W,160000,474),(M,12000,478)]),
 (2022,"Enacted","SRC-CRPT-117HRPT403","2022 enacted","SRC-PLAW-117PUBL103","div. H sec. 222",
  [(I,419350,811),(E,52000,811),(C,254950,811),(H,17000,812),(W,160000,813),(M,12000,817)]),
 (2023,"Enacted","SRC-CRPT-118SRPT84","2023 appropriation","SRC-PLAW-117PUBL328","div. H sec. 222",
  [(I,419350,376),(E,52000,376),(C,254950,377),(H,17000,377),(W,160000,377),(M,12000,379)]),
]
# the check: per-account Fund lines in the same document's comparative table (committee column), thousands; the
# FY2017 House report's table pages are images, so its narrative's 'in transfers from the PPH Fund' amounts
COMPARATIVE_BF = {
 (2017,"House Reported"): {I:337300,E:52000,C:201500,H:17500,W:300000},
 (2017,"Senate Reported"): {I:324350,E:52000,C:337950,H:17000,W:160000},
 (2018,"Senate Reported"): {I:324350,E:52000,C:247550,H:17000,W:160000},
 (2019,"House Reported"): {I:324350,E:52000,C:294650,H:17000,W:160000},
 (2019,"Senate Reported"): {I:324350,E:52000,C:254950,H:17000,W:160000},
 (2020,"House Reported"): {I:347800,E:52000,C:277450,H:17000,W:160000},
 (2021,"Senate Reported"): {I:360200,E:64000,C:254950,H:17000,W:160000},
 (2022,"House Reported"): {I:419350,E:52000,C:254950,H:17000,W:160000},
 (2022,"Senate Reported"): {I:419350,E:52000,C:254950,H:17000,W:160000},
 (2023,"House Reported"): {I:419350,E:52000,C:254950,H:17000,W:160000},
 (2023,"Senate Reported"): {I:419350,E:52000,C:254950,H:17000,W:160000},
}
NO_CHECK = {(2021,"House Reported"): "H.Rept. 116-450's comparative-table pages are images; the agency sum is checked by "
                                     "measure_reconcile against the printed program level"}


def clean(line):
    return re.sub(r"\.{3,}", " ... ", re.sub(r"\s{2,}", "  ", line.strip()))


def allocation_rows(doc, first, thousands):
    """The allocation table's rows from the text layer: dot-leadered (or column-set, where the activity wraps) lines
    ending in an amount, from the table's first page to the next general provision ('Section 2..')."""
    rows = []
    for q in (first, first + 1):
        for line in page(doc, q).splitlines():
            if re.match(r"\s*(Section|Sec\.) ?\d", line) and rows:
                return rows
            m = re.search(r"\$?([\d,]{4,})\s*$", line)
            if not m or not (re.search(r"\.{3,}", line) or len(re.findall(r"\S\s{3,}(?=\S)", line.strip())) >= 2):
                continue                # a row is dot-leadered or set in columns; prose is neither
            cells = [c.strip(" .$") for c in re.split(r"\s*\.{2,}\s*|\s{3,}", line.strip()) if c.strip(" .$")]
            amount = int(m.group(1).replace(",", "")) * (1000 if thousands else 1)
            rows.append((q, cells[:-1], amount, clean(line)))
    return rows


def account_of(cells):
    if len(cells) >= 3 or (len(cells) == 2 and cells[0] not in ("ACL", "CDC", "SAMHSA")):
        printed = cells[-2]
        return printed, next(a for k, a in SENATE_ACCOUNT if printed.startswith(k) or k in printed.split()[0])
    activity = cells[-1]
    return cells[0], next(a for rx, a in ACTIVITY if re.search(rx, activity))


for fy, st, doc, first, thousands, bill, sec in BACKFILL:
    for q, cells, amt, line in allocation_rows(doc, first, thousands):
        printed_acct, acc_name = account_of(cells)
        acc = ACCOUNT[acc_name]
        out.append(dict(fiscal_year=fy, stage=st, source_document_id=doc, page=q, table="allocation",
                        account_printed=printed_acct, activity=cells[-1], amount=amt, account_id=acc,
                        bill_document_id=bill, bill_section=sec, printed_line=line, read_from="text layer"))
        if acc: acct[(fy, st)][acc_name] += amt // 1000
for fy, st, doc, column, bill, sec, rows in COMPARATIVE_ROWS:
    for acc_name, amt, p in rows:
        hit = [l for l in page(doc, p).splitlines() if re.search(r"(?i)prevention and public health fund", l)
               and str(amt) in re.sub(r"[^\d]", "", l)]
        if not hit:
            raise SystemExit(f"not found: {doc} p{p} {acc_name} {amt}")
        out.append(dict(fiscal_year=fy, stage=st, source_document_id=doc, page=p, table=f"comparative, {column} column",
                        account_printed=acc_name, activity="Prevention and Public Health Fund", amount=amt * 1000,
                        account_id=ACCOUNT[acc_name], bill_document_id=bill, bill_section=sec,
                        printed_line=clean(hit[0]), read_from="text layer"))
for k, want in COMPARATIVE_BF.items():
    got = {a: v for a, v in acct[k].items() if a != M and a != ALD}
    if got != want: print("MISMATCH", k, got, want); bad += 0 if k in NO_CHECK else 1
for k, why in NO_CHECK.items():
    print("not checked by account:", k, why)
for k, want in COMPARATIVE.items():
    got = {a: v for a, v in acct[k].items() if a != M and a != ALD}
    if got != want: print("MISMATCH", k, got, want); bad += 1
for r in out:
    if r["account_id"]: cdc[(r["fiscal_year"], r["stage"], r["account_id"].split("-")[2])] += r["amount"]
for k in sorted(cdc): print(k, f"{cdc[k]:,}")
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(len(out), "rows;", bad, "mismatches")
