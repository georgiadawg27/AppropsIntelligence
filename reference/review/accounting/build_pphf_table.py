"""
Write reference/review/accounting/pphf_table.csv: the Prevention and Public Health Fund allocation, Labor-HHS
FY2024-FY2026, which scripts/tag_components.py reads into transfer_in components.

Each bill's general provision (sec. 222) transfers the Fund "to the accounts specified, in the amounts specified,
and for the activities specified under the heading Prevention and Public Health Fund" in the report (or explanatory
statement), so the rows are that table's: the account (Senate) or agency (House, JES), the activity and the amount.
Where the table names only the agency, the activity's account is the one the same document's comparative table
prints the Fund's line under (ACCOUNT below), and the script checks that each account's rows add up to that line.
FY2024 Enacted: the FY2024 explanatory statement is not a document we hold, so the rows are the FY2025 Senate
report's comparative-table lines in its 2024 appropriation column (the same document as the printed view).

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
for k, want in COMPARATIVE.items():
    got = {a: v for a, v in acct[k].items() if a != M and a != ALD}
    if got != want: print("MISMATCH", k, got, want); bad += 1
for r in out:
    if r["account_id"]: cdc[(r["fiscal_year"], r["stage"], r["account_id"].split("-")[2])] += r["amount"]
for k in sorted(cdc): print(k, f"{cdc[k]:,}")
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(len(out), "rows;", bad, "mismatches")
