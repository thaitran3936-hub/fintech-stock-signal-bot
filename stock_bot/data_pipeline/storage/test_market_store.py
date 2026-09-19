from market_store import MarketStore


store = MarketStore()


print("===== TEST MARKET STORE =====")


# Dữ liệu giả để kiểm tra hệ thống
store.save(
    symbol="FPT",
    price=100.5,
    volume=1200,
    bid=100.4,
    ask=100.6,
    data_type="test"
)

store.save(
    symbol="VCB",
    price=95.2,
    volume=800,
    bid=95.1,
    ask=95.3,
    data_type="test"
)


# Ghi queue xuống SQLite
count = store.flush()

print(f"Đã ghi {count} bản ghi vào SQLite")


# Kiểm tra dữ liệu mới nhất
print("\n===== DỮ LIỆU MỚI NHẤT =====")

print(
    "FPT:",
    store.get_latest("FPT")
)

print(
    "VCB:",
    store.get_latest("VCB")
)


# Đóng database
store.close()

print("\n===== TEST HOÀN TẤT =====")