import json
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

symbol = "HPG"
url = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/finance-info/{symbol}"

for p in (1, 2):
    params = {
        "type": "KQKD",
        "termtype": 2,
        "termType": 2,
        "code": symbol,
        "page": p,
        "pageSize": 4,
    }
    res = requests.get(url, headers=HEADERS, params=params, timeout=10)
    if res.status_code == 200:
        data = res.json()
        print(f"\n--- THÔNG TIN HEAD PAGE {p} ---")
        for idx, h in enumerate(data.get("Head", []), start=1):
            print(f"Value{idx}: Year={h.get('YearPeriod')}, Term={h.get('TermCode')}, "
                  f"ReportTermID={h.get('ReportTermID')}, Period={h.get('PeriodBegin')}-{h.get('PeriodEnd')}, "
                  f"AuditStatus={h.get('AuditedStatus')}")