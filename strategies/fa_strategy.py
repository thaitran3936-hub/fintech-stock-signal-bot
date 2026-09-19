"""
strategies/fa_strategy.py  —  Thành viên 4
Lớp 1 (FA: Growth & Quality) của chiến lược CANSLIM + Momentum.

Luồng: CSV chỉ số tài chính (bộ phận Data cào) -> tính tăng trưởng -> lọc FA
       -> gắn nhãn ngành ưu tiên -> ghi data/watch_list.json cho Thành viên 3.

Quy ước (theo data_naming_convention.pdf):
  * Cột DataFrame: snake_case (ticker, roe, debt_equity, eps_growth_qoq, ...).
  * Phần trăm lưu dạng số thực: 26.2 nghĩa là 26.2%, KHÔNG phải 0.262.
  * Hàm public có tiền tố fa_ để không trùng tên khi import chéo.
  * data/watch_list.json chỉ được ghi qua code (fa_export_watch_list).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# 1. Ngưỡng lọc (chỉnh ở đây, không sửa rải rác trong code)
# ----------------------------------------------------------------------------
MIN_ROE = 15.0                 # ROE > 15%
MAX_DEBT_EQUITY = 1.2          # D/E < 1.2
MIN_EPS_GROWTH_QOQ = 15.0      # LN ròng quý gần nhất tăng > 15%
MIN_EPS_GROWTH_YOY = 15.0      # LN ròng năm gần nhất tăng > 15%
MIN_REVENUE_GROWTH_YOY = 15.0  # Doanh thu năm gần nhất tăng > 15%
MIN_FREE_FLOAT_PCT = 0.0       # bản chiến lược mới không còn lọc free float (đặt 10.0 nếu muốn bật lại)

REQUIRE_REVENUE_GROWTH = False  # True: bắt buộc cả doanh thu > 15% (ngân hàng hay bị loại nếu bật)
REQUIRE_POSITIVE_CFO = True     # tăng trưởng phải đi kèm dòng tiền KD dương (nếu có cột cfo)
STRICT_PRIORITY_SECTOR = False  # True: chỉ giữ mã thuộc ngành ưu tiên; False: chỉ gắn nhãn + xếp trên
BANK_EXEMPT_DEBT_EQUITY = True  # ngân hàng luôn có D/E rất cao (đòn bẩy) -> miễn lọc D/E, nếu không sẽ loại hết ngân hàng

# Ngành ưu tiên theo bối cảnh 2026 (so khớp theo từ khóa, không phân biệt hoa thường)
PRIORITY_SECTOR_KEYWORDS = {
    "Công nghệ / Bán dẫn / Hạ tầng": ["công nghệ", "cong nghe", "bán dẫn", "ban dan",
                                       "hạ tầng", "ha tang", "viễn thông", "vien thong",
                                       "technology", "telecom"],
    "Dầu khí / Năng lượng": ["dầu khí", "dau khi", "năng lượng", "nang luong",
                              "oil", "gas", "energy"],
    "Ngân hàng": ["ngân hàng", "ngan hang", "bank"],
}

# ----------------------------------------------------------------------------
# 2. Cột đầu vào CSV
# ----------------------------------------------------------------------------
# Tên "sai chuẩn" -> tên chuẩn (lấy từ cột KHÔNG dùng trong file quy ước)
COLUMN_ALIASES = {
    "symbol": "ticker", "ma_ck": "ticker", "code": "ticker",
    "roe_pct": "roe", "de_ratio": "debt_equity", "d/e": "debt_equity",
    "nganh": "sector", "industry_vn": "sector", "freefloat": "free_float_pct",
    "growth": "eps_growth_qoq", "ln_growth": "eps_growth_qoq",
}

# Cột bắt buộc cho mỗi mã. Số liệu gốc (VNĐ hoặc tỷ VNĐ đều được, vì chỉ dùng tỷ lệ).
#   net_profit_q1..q4 : LN ròng 4 quý gần nhất, q1 = quý mới nhất
#   net_profit_y1..y3 : LN ròng 3 năm gần nhất, y1 = năm mới nhất
#   revenue_y1..y3    : Doanh thu 3 năm gần nhất, y1 = năm mới nhất
REQUIRED_COLUMNS = [
    "ticker", "sector", "roe", "debt_equity", "free_float_pct",
    "net_profit_q1", "net_profit_q2", "net_profit_q3", "net_profit_q4",
    "net_profit_y1", "net_profit_y2", "net_profit_y3",
    "revenue_y1", "revenue_y2", "revenue_y3",
]
# Cột tùy chọn: cfo (dòng tiền HĐKD), close_d1..close_d4 (4 giá đóng cửa gần nhất)
OPTIONAL_CLOSE_COLUMNS = ["close_d1", "close_d2", "close_d3", "close_d4"]


# ----------------------------------------------------------------------------
# 3. Đọc & chuẩn hóa dữ liệu
# ----------------------------------------------------------------------------
def fa_load_data(csv_path: str | Path) -> pd.DataFrame:
    """Đọc CSV, chuẩn hóa tên cột về snake_case, ép kiểu số, kiểm tra cột bắt buộc."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns=COLUMN_ALIASES)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV thiếu cột bắt buộc: {missing}")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["sector"] = df["sector"].fillna("").astype(str).str.strip()
    df = df.drop_duplicates(subset="ticker", keep="last")

    numeric_cols = [c for c in df.columns if c not in ("ticker", "sector")]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# 4. Tính các cột tăng trưởng
# ----------------------------------------------------------------------------
def _growth_pct(new: pd.Series, old: pd.Series) -> pd.Series:
    """% tăng trưởng. Nếu kỳ gốc <= 0 thì không có nghĩa -> NaN (sẽ bị loại)."""
    return ((new / old.where(old > 0)) - 1.0) * 100.0


def fa_compute_growth(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm: eps_growth_qoq, eps_growth_qoq_prev, eps_growth_delta,
    eps_growth_yoy, revenue_growth_yoy, profit_positive_3y."""
    out = df.copy()
    out["eps_growth_qoq"] = _growth_pct(out["net_profit_q1"], out["net_profit_q2"])
    out["eps_growth_qoq_prev"] = _growth_pct(out["net_profit_q2"], out["net_profit_q3"])
    # Tốc độ tăng tốc quý (Delta Growth): tăng trưởng quý này - tăng trưởng quý trước (điểm %)
    out["eps_growth_delta"] = out["eps_growth_qoq"] - out["eps_growth_qoq_prev"]

    out["eps_growth_yoy"] = _growth_pct(out["net_profit_y1"], out["net_profit_y2"])
    out["revenue_growth_yoy"] = _growth_pct(out["revenue_y1"], out["revenue_y2"])
    out["profit_positive_3y"] = (out[["net_profit_y1", "net_profit_y2", "net_profit_y3"]] > 0).all(axis=1)
    return out


# ----------------------------------------------------------------------------
# 5. Các bộ lọc (mỗi hàm trả về mask boolean để dễ test/debug từng tiêu chí)
# ----------------------------------------------------------------------------
def fa_filter_roe(df: pd.DataFrame, min_roe: float = MIN_ROE) -> pd.Series:
    return df["roe"] > min_roe


def fa_filter_debt_equity(df: pd.DataFrame, max_de: float = MAX_DEBT_EQUITY) -> pd.Series:
    ok = df["debt_equity"] < max_de
    if BANK_EXEMPT_DEBT_EQUITY and "priority_sector" in df.columns:
        ok |= df["priority_sector"] == "Ngân hàng"
    return ok


def fa_filter_growth_quarter(df: pd.DataFrame, min_growth: float = MIN_EPS_GROWTH_QOQ) -> pd.Series:
    mask = df["eps_growth_qoq"] > min_growth
    if REQUIRE_POSITIVE_CFO and "cfo" in df.columns:
        mask &= df["cfo"] > 0
    return mask


def fa_filter_acceleration(df: pd.DataFrame) -> pd.Series:
    """Chữ C: tăng trưởng LN quý này phải LỚN HƠN tăng trưởng quý trước (tăng tốc)."""
    return df["eps_growth_qoq"] > df["eps_growth_qoq_prev"]


def fa_filter_growth_long_term(df: pd.DataFrame) -> pd.Series:
    mask = (df["eps_growth_yoy"] > MIN_EPS_GROWTH_YOY) & df["profit_positive_3y"]
    if REQUIRE_REVENUE_GROWTH:
        mask &= df["revenue_growth_yoy"] > MIN_REVENUE_GROWTH_YOY
    return mask


def fa_filter_free_float(df: pd.DataFrame, min_ff: float = MIN_FREE_FLOAT_PCT) -> pd.Series:
    if min_ff <= 0:  # không lọc
        return pd.Series(True, index=df.index)
    return df["free_float_pct"] >= min_ff


def fa_tag_sector(df: pd.DataFrame) -> pd.DataFrame:
    """Sector Tagging: thêm cột priority_sector (tên nhóm ưu tiên hoặc '')."""
    def _tag(sector: str) -> str:
        s = (sector or "").lower()
        for group, keywords in PRIORITY_SECTOR_KEYWORDS.items():
            if any(k in s for k in keywords):
                return group
        return ""

    out = df.copy()
    out["priority_sector"] = out["sector"].map(_tag)
    return out


# ----------------------------------------------------------------------------
# 6. Chạy toàn bộ bộ lọc
# ----------------------------------------------------------------------------
def fa_run_screen(df: pd.DataFrame) -> pd.DataFrame:
    """Nhận DataFrame đã qua fa_load_data, trả về danh sách mã đạt chuẩn FA."""
    df = fa_tag_sector(fa_compute_growth(df))

    mask = (
        fa_filter_roe(df)
        & fa_filter_debt_equity(df)
        & fa_filter_growth_quarter(df)
        & fa_filter_acceleration(df)
        & fa_filter_growth_long_term(df)
        & fa_filter_free_float(df)
    )
    if STRICT_PRIORITY_SECTOR:
        mask &= df["priority_sector"] != ""

    passed = df[mask.fillna(False)].copy()
    # Ngành ưu tiên lên trước, sau đó theo tốc độ tăng trưởng quý giảm dần
    passed["_is_priority"] = passed["priority_sector"] != ""
    passed = passed.sort_values(["_is_priority", "eps_growth_qoq"], ascending=[False, False])
    return passed.drop(columns="_is_priority").reset_index(drop=True)


# ----------------------------------------------------------------------------
# 7. Xuất data/watch_list.json (Thành viên 4 ghi, Thành viên 3 chỉ đọc)
# ----------------------------------------------------------------------------
def _num(value, ndigits: int = 1):
    """NaN/None -> None; còn lại làm tròn, ép về float Python để json.dump được."""
    if value is None or pd.isna(value):
        return None
    return round(float(value), ndigits)


def fa_export_watch_list(passed: pd.DataFrame,
                         output_path: str | Path = "data/watch_list.json") -> Path:
    """Ghi watch_list.json theo chuẩn chung. Ghi qua file tạm rồi replace để
    Thành viên 3 không bao giờ đọc phải file ghi dở."""
    today = date.today().isoformat()
    records = []
    for row in passed.to_dict(orient="records"):
        rec = {
            "ticker": row["ticker"],
            "sector": row["sector"],
            "roe": _num(row["roe"]),
            "debt_equity": _num(row["debt_equity"], 2),
            "eps_growth_qoq": _num(row["eps_growth_qoq"]),
            "eps_growth_qoq_prev": _num(row["eps_growth_qoq_prev"]),
            "eps_growth_yoy": _num(row["eps_growth_yoy"]),
            "free_float_pct": _num(row["free_float_pct"]),
            # --- trường mở rộng (cần báo Thành viên 3 biết) ---
            "revenue_growth_yoy": _num(row["revenue_growth_yoy"]),
            "eps_growth_delta": _num(row["eps_growth_delta"]),
            "priority_sector": row["priority_sector"],
            "updated_at": today,
        }
        closes = [_num(row.get(c), 2) for c in OPTIONAL_CLOSE_COLUMNS if c in row]
        if closes and any(c is not None for c in closes):
            rec["recent_closes"] = closes  # [mới nhất, ..., cũ nhất]
        records.append(rec)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, output_path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
    return output_path


# ----------------------------------------------------------------------------
# 8. Chạy thử: python strategies/fa_strategy.py data/fundamentals.csv [file_xuat.json]
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    csv_file = sys.argv[1] if len(sys.argv) > 1 else "data/fundamentals.csv"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "data/watch_list.json"
    raw = fa_load_data(csv_file)
    result = fa_run_screen(raw)
    path = fa_export_watch_list(result, out_file)
    print(f"Đọc {len(raw)} mã -> đạt chuẩn FA: {len(result)} mã -> {path}")
    if not result.empty:
        cols = ["ticker", "sector", "roe", "debt_equity", "eps_growth_qoq", "eps_growth_yoy"]
        print(result[cols].to_string(index=False))