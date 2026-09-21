import json
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# 1. TEST LẤY FREE FLOAT TỪ PROFILE / OVERVIEW
print("=== 1. TEST LẤY FREE FLOAT TRỰC TIẾP TỪ KBS ===")
symbol = "HPG"
test_urls = [
    f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/profile/{symbol}",
    f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/overview/{symbol}",
    f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/company/{symbol}",
]

for url in test_urls:
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            print(f"✅ Thành công với URL: {url}")
            # In ra các key xem có KLCPNY không
            content = data.get("data", data)
            print("Dữ liệu trả về mẫu:")
            print(json.dumps(content, ensure_ascii=False, indent=2)[:500])
            break
        else:
            print(f"❌ {url} -> Status {res.status_code}")
    except Exception as e:
        print(f"Lỗi: {e}")

# 2. TEST LẤY BCTC 8 KỲ (PAGE 1 VÀ PAGE 2)
print("\n=== 2. TEST LẤY BCTC (KQKD) VỚI PAGE=1 VÀ PAGE=2 ===")
# Endpoint KQKD từ tài liệu
bctc_url = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/financial/report"
params_p1 = {"symbol": symbol, "type": "KQKD", "period": "Q", "p": 1}
params_p2 = {"symbol": symbol, "type": "KQKD", "period": "Q", "p": 2}

try:
    r1 = requests.get(bctc_url, headers=HEADERS, params=params_p1, timeout=5)
    r2 = requests.get(bctc_url, headers=HEADERS, params=params_p2, timeout=5)
    print(f"Page 1 status: {r1.status_code}")
    print(f"Page 2 status: {r2.status_code}")
    if r1.status_code == 200:
        print("Mẫu dữ liệu Page 1:")
        print(json.dumps(r1.json(), ensure_ascii=False, indent=2)[:300])
except Exception as e:
    print(f"Lỗi test BCTC: {e}")