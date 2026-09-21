import json
import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
symbol = "GVT"
url = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/profile/{symbol}"

res = requests.get(url, headers=HEADERS, timeout=5)
if res.status_code == 200:
    data = res.json()
    print("Toàn bộ các trường trong Profile của KBS:")
    for k, v in data.items():
        print(f"{k:<10}: {v}")