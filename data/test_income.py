from vnstock import Finance

# Tạo đối tượng Finance cho mã PNJ từ nguồn KBS
fa = Finance(symbol="PNJ", source="KBS")

# Lấy báo cáo kết quả kinh doanh theo quý
df = fa.income_statement(period="quarter")

# In tên các cột mà KBS trả về
print("COLUMNS:")
print(df.columns.tolist())

# In toàn bộ dữ liệu để kiểm tra các chỉ tiêu doanh thu và lợi nhuận
print("\nFULL DATA:")
print(df.to_string())
