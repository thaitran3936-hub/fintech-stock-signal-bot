from vnstock import Finance

# Tạo đối tượng Finance cho mã PNJ từ nguồn KBS
fa = Finance(symbol="PNJ", source="KBS")

# Lấy các chỉ số tài chính theo quý
df_ratio = fa.ratio(period="quarter")

# Lấy báo cáo kết quả kinh doanh theo quý
df_income = fa.income_statement(period="quarter")

# In EPS cơ bản theo từng quý từ báo cáo KQKD
print("\n=== EPS THEO QUÝ ===")
print(
    df_income[
        df_income["item_id"] == "earnings_per_share_vnd"
    ].to_string(index=False)
)

# In doanh thu và lợi nhuận sau thuế cổ đông công ty mẹ
print("\n=== REVENUE + PROFIT ===")
print(
    df_income[
        df_income["item_id"].isin([
            "revenue",
            "profit_after_tax_for_shareholders_of_parent_company"
        ])
    ].to_string(index=False)
)

# In các chỉ số ratio mà module FA cần dùng
print("\n=== RATIO ===")
print(
    df_ratio[
        df_ratio["item_id"].isin([
            "pe_ratio",
            "pb_ratio",
            "roe",
            "roe_trailling",
            "debt_to_equity",
            "net_margin"
        ])
    ].to_string(index=False)
)