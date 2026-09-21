#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reversal.py
Kiểm chứng giả thuyết đảo nhãn kỳ năm của KBS trên 10 mã UPCoM ngẫu nhiên.
"""
import time
import pandas as pd
from vnstock import Finance

SAMPLE_TICKERS = ["GVT", "MGC", "SLD", "SWC", "ABI", "BSR", "MCH", "VGI", "ACV", "VEA"]

def safe_val(df, item_id, col):
    if df is None or df.empty or "item_id" not in df.columns or col not in df.columns:
        return None
    df_norm = df.copy()
    df_norm["item_id"] = df_norm["item_id"].astype(str).str.strip().str.lower()
    match = df_norm[df_norm["item_id"] == item_id.lower()]
    if not match.empty:
        try:
            return float(match.iloc[0][col])
        except:
            return None
    return None

def test_ticker(ticker):
    print(f"\n--- Đang kiểm tra mã {ticker} ---")
    try:
        fin = Finance(symbol=ticker, source="KBS")
        df_is = fin.income_statement(period="year")
        time.sleep(0.5)
        df_ratio = fin.ratio(period="year")
    except Exception as e:
        print(f"Lỗi gọi API {ticker}: {e}")
        return

    if df_is is None or df_ratio is None or df_is.empty or df_ratio.empty:
        print(f"{ticker}: Không đủ dữ liệu năm để kiểm chứng.")
        return

    # Lấy các cột năm chung
    cols = [c for c in df_is.columns if str(c).startswith("20") and c in df_ratio.columns]
    cols = sorted(cols)[-4:]
    if len(cols) < 3:
        print(f"{ticker}: Có quá ít năm ({len(cols)} năm).")
        return

    computed_margin = []
    ratio_margin = []

    for col in cols:
        rev = safe_val(df_is, "revenue", col)
        # Thử lấy lợi nhuận cổ đông công ty mẹ hoặc tổng lợi nhuận
        npat = safe_val(df_is, "profit_after_tax_for_shareholders_of_parent_company", col)
        if npat is None:
            npat = safe_val(df_is, "profit_after_tax", col)
        
        rm = safe_val(df_ratio, "net_margin", col)

        if rev and npat is not None:
            computed_margin.append(round(npat / rev * 100.0, 2))
        else:
            computed_margin.append(None)
        ratio_margin.append(rm)

    print(f"Các năm đối chiếu   : {cols}")
    print(f"Margin tự tính (IS) : {computed_margin}")
    print(f"Margin gốc (Ratio)  : {ratio_margin}")

    # Đếm số cặp khớp theo chiều xuôi
    direct_match = sum(
        1 for c, r in zip(computed_margin, ratio_margin)
        if c is not None and r is not None and abs(c - r) <= max(0.1, abs(r) * 0.05)
    )

    # Đếm số cặp khớp theo chiều ngược
    reversed_ratio = list(reversed(ratio_margin))
    reversed_match = sum(
        1 for c, r in zip(computed_margin, reversed_ratio)
        if c is not None and r is not None and abs(c - r) <= max(0.1, abs(r) * 0.05)
    )

    total_valid = sum(1 for c in computed_margin if c is not None)
    if total_valid >= 2:
        if direct_match >= total_valid * 0.7:
            print(f"👉 KẾT LUẬN {ticker}: DỮ LIỆU ĐÚNG CHIỀU XUÔI ({direct_match}/{total_valid})")
        elif reversed_match >= total_valid * 0.7:
            print(f"👉 KẾT LUẬN {ticker}: BỊ ĐẢO NGƯỢC 100% ({reversed_match}/{total_valid})")
        else:
            print(f"👉 KẾT LUẬN {ticker}: Không khớp cả 2 chiều (Lệch định nghĩa hoặc sai số).")

if __name__ == "__main__":
    for t in SAMPLE_TICKERS:
        test_ticker(t)
        time.sleep(1.0)