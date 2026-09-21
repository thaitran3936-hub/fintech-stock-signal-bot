from vnstock import Finance

# Tạo đối tượng Finance cho một mã để kiểm tra dữ liệu KBS
fa = Finance(symbol="PNJ", source="KBS")

# Lấy dữ liệu chỉ số tài chính theo quý
df = fa.ratio(period="quarter")

# In tên tất cả các cột mà KBS trả về
print("COLUMNS:")
print(df.columns.tolist())

# In toàn bộ các dòng để kiểm tra item_id và giá trị thực tế
print("\nFULL DATA:")
print(df.to_string())