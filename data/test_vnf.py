import requests

url = "https://fiin-fundamental.ssi.com.vn/StockInfor/StockOverview/PNJ"
headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://iboard.ssi.com.vn/",
}
r = requests.get(url, headers=headers, timeout=10)
print(r.status_code)
print(r.text[:3000])  # in ra để xem cấu trúc thật