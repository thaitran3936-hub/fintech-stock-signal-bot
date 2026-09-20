"""
strategies/fa_strategy.py  —  Thành viên 4
Lớp 1 (FA: Growth & Quality) của chiến lược CANSLIM + Momentum.

Áp dụng ĐẦY ĐỦ các tiêu chí trong tài liệu BT3 (bản đầu + bản Hybrid CANSLIM/ATR), không bỏ tiêu chí nào.
Thiếu dữ liệu ở tiêu chí nào thì mã đó KHÔNG đạt tiêu chí đó, và fa_criteria_report() cho biết
đang thiếu dữ liệu gì, thiếu bao nhiêu mã -> dùng để yêu cầu bên Data bổ sung.

Luồng: dữ liệu tài chính (Data) -> tính tăng trưởng -> lọc FA -> gắn nhãn ngành ưu tiên
       -> ghi data/watch_list.json cho Thành viên 3.

Quy ước (theo data_naming_convention.pdf):
  * Cột DataFrame: snake_case. Phần trăm lưu dạng số thực: 26.2 nghĩa là 26.2%.
  * Hàm public có tiền tố fa_ . data/watch_list.json chỉ được ghi qua code.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# 1. Ngưỡng lọc (chỉnh ở đây, không sửa rải rác trong code)
# ----------------------------------------------------------------------------
MIN_ROE = 15.0                      # ROE > 15%
MAX_DEBT_EQUITY = 1.2               # D/E < 1.2 (tỷ lệ, 0.8 nghĩa là 0.8 lần vốn chủ)
MIN_QUARTER_GROWTH = 15.0           # LN ròng quý gần nhất tăng > 15%
QUARTER_GROWTH_BASIS = "yoy"        # "yoy": so với CÙNG KỲ năm trước (đúng bản BT3 đầu, cần 6 quý)
                                    # "qoq": so với quý liền trước (chỉ cần 3 quý, dễ nhiễu do mùa vụ)
REQUIRE_ACCELERATION = True         # tăng trưởng quý này > tăng trưởng quý trước (Delta Growth)
MIN_ANNUAL_PROFIT_GROWTH = 15.0     # LN ròng năm gần nhất tăng > 15%
MIN_ANNUAL_REVENUE_GROWTH = 15.0    # Doanh thu năm gần nhất tăng > 15%
REQUIRE_REVENUE_GROWTH = True       # bản BT3 đầu: "Doanh thu/Lợi nhuận năm > 15%" -> giữ cả doanh thu
REQUIRE_POSITIVE_3Y_PROFIT = True   # LN ròng dương liên tục 3 năm gần nhất
REQUIRE_POSITIVE_CFO = True         # tăng trưởng phải đi kèm dòng tiền HĐKD dương
MIN_FREE_FLOAT_PCT = 10.0           # loại cổ phiếu quá cô đặc (free float < 10%)
BANK_EXEMPT_DEBT_EQUITY = False     # False = áp D/E < 1.2 cho mọi mã (đúng văn bản). True = miễn cho ngân hàng
STRICT_PRIORITY_SECTOR = False      # False: ngành ưu tiên chỉ được xếp trên (văn bản ghi "Ưu tiên")

DEBT_EQUITY_IS_PERCENT = True       # financial_data.json ghi D/E theo % (60.2 = 0.602) -> tự chia 100. Đã là tỷ lệ thì đặt False
EXCLUDED_EXCHANGES = set()          # ví dụ {"UPCOM"}. Mặc định không loại sàn nào (lớp thanh khoản sẽ lọc sau)
EXCHANGE_NAMES = {"HOSE", "HNX", "UPCOM"}

# Ngành ưu tiên (so khớp theo từ khóa, không phân biệt hoa thường)
PRIORITY_SECTOR_KEYWORDS = {
    "Công nghệ / Bán dẫn / Hạ tầng": ["công nghệ", "cong nghe", "bán dẫn", "ban dan", "phần mềm", "phan mem",
                                       "hạ tầng", "ha tang", "viễn thông", "vien thong", "trung tâm dữ liệu",
                                       "logistics", "technology", "telecom", "software"],
    "Dầu khí / Năng lượng": ["dầu khí", "dau khi", "năng lượng", "nang luong", "oil", "gas", "energy"],
    "Ngân hàng": ["ngân hàng", "ngan hang", "bank"],
}
# Ngành bị hạ tỷ trọng (BT3 mục 2A): xuất khẩu hàng tiêu dùng truyền thống -> xếp cuối danh sách
DEPRIORITIZED_SECTOR_KEYWORDS = ["dệt may", "det may", "thủy sản", "thuy san", "gỗ", "textile", "seafood", "wood"]

# ----------------------------------------------------------------------------
# 2. Cột dữ liệu đầu vào
# ----------------------------------------------------------------------------
COLUMN_ALIASES = {
    "symbol": "ticker", "ma_ck": "ticker", "code": "ticker",
    "roe_pct": "roe", "de_ratio": "debt_equity", "d/e": "debt_equity",
    "nganh": "sector", "industry_vn": "sector", "freefloat": "free_float_pct",
}
# net_profit_q1..q6 : LN ròng 6 quý gần nhất, q1 = quý mới nhất (cần q5, q6 để so cùng kỳ)
# net_profit_y1..y3 : LN ròng 3 năm gần nhất, y1 = năm mới nhất
# revenue_y1..y3    : Doanh thu 3 năm gần nhất, y1 = năm mới nhất
# cfo               : dòng tiền từ hoạt động kinh doanh (TTM hoặc năm gần nhất)
NUMERIC_COLUMNS = (
    ["roe", "debt_equity", "free_float_pct", "cfo"]
    + [f"net_profit_q{i}" for i in range(1, 7)]
    + [f"net_profit_y{i}" for i in range(1, 4)]
    + [f"revenue_y{i}" for i in range(1, 4)]
)
REQUIRED_COLUMNS = ["ticker", "sector"] + NUMERIC_COLUMNS
OPTIONAL_CLOSE_COLUMNS = ["close_d1", "close_d2", "close_d3", "close_d4"]   # 4 giá đóng cửa gần nhất
OPTIONAL_FOREIGN_COLUMN = "foreign_net_buy_20d"  # (tùy chọn) mua bán ròng NĐTNN 20 phiên -> ưu tiên xếp trên (chữ I)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Cột nào thiếu thì tạo với giá trị NaN (không làm chương trình sập, báo cáo sẽ chỉ ra thiếu gì)."""
    out = df.copy()
    for col in NUMERIC_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    for col in ("ticker", "sector"):
        if col not in out.columns:
            out[col] = ""
    return out


# ----------------------------------------------------------------------------
# 3. Đọc dữ liệu: CSV phẳng
# ----------------------------------------------------------------------------
def fa_load_data(csv_path: str | Path) -> pd.DataFrame:
    """Đọc CSV, chuẩn hóa tên cột về snake_case, ép kiểu số. Cột thiếu -> NaN + cảnh báo."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns=COLUMN_ALIASES)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        print(f"[CẢNH BÁO] CSV thiếu cột: {missing}")
    df = _ensure_columns(df)

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["sector"] = df["sector"].fillna("").astype(str).str.strip()
    df = df.drop_duplicates(subset="ticker", keep="last")
    for col in [c for c in df.columns if c not in ("ticker", "sector")]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# 3b. Đọc dữ liệu: financial_data.json (mỗi mã có danh sách "history" gồm các kỳ quý / năm)
#     Trường đọc được ở cấp mã : industry (ngành ICB), exchange, free_float_pct,
#                                foreign_net_buy_20d, recent_closes [mới nhất -> cũ nhất]
#     Trường đọc được ở mỗi kỳ  : net_profit (hoặc eps), revenue, roe, roe_ttm, debt_equity, cfo, cfo_ttm
# ----------------------------------------------------------------------------
_Q_RE = re.compile(r"^\d{4}-Q[1-4]$")
_Y_RE = re.compile(r"^\d{4}-Năm$")


def _metric(row: dict):
    """Lợi nhuận của 1 kỳ: ưu tiên net_profit, chưa có thì tạm dùng EPS."""
    v = row.get("net_profit")
    return row.get("eps") if v is None else v


def _newest_first(rows: list, getter, n: int) -> list:
    vals = [getter(r) for r in rows][-n:][::-1]
    vals += [None] * (n - len(vals))
    return [np.nan if v is None else float(v) for v in vals]


def _latest_non_null(rows: list, field: str):
    for r in reversed(rows):
        if r.get(field) is not None:
            return float(r[field])
    return np.nan


def fa_load_financial_json(json_path: str | Path) -> pd.DataFrame:
    """Đọc financial_data.json -> DataFrame phẳng. Không đoán số liệu: thiếu thì để NaN."""
    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    records = []
    for ticker, info in raw.items():
        hist = info.get("history") or []
        q_rows = sorted((h for h in hist if _Q_RE.match(str(h.get("period", "")))), key=lambda h: h["period"])
        y_rows = sorted((h for h in hist if _Y_RE.match(str(h.get("period", "")))), key=lambda h: h["period"])

        # File cũ để tên sàn trong "sector" -> tách ra; ngành thật lấy từ "industry"
        sector = info.get("industry") or info.get("sector") or ""
        exchange = info.get("exchange") or (info.get("sector") if info.get("sector") in EXCHANGE_NAMES else "")
        if sector in EXCHANGE_NAMES:
            sector = ""

        # ROE: roe_ttm (quý mới nhất) > cộng ROE 4 quý (nếu đủ) > ROE năm gần nhất
        roe = np.nan
        if q_rows and q_rows[-1].get("roe_ttm") is not None:
            roe = float(q_rows[-1]["roe_ttm"])
        elif len(q_rows) >= 4 and all(r.get("roe") is not None for r in q_rows[-4:]):
            roe = float(sum(r["roe"] for r in q_rows[-4:]))
        elif y_rows:
            roe = _latest_non_null(y_rows, "roe")

        de = _latest_non_null(q_rows, "debt_equity")
        if pd.isna(de):
            de = _latest_non_null(y_rows, "debt_equity")
        if DEBT_EQUITY_IS_PERCENT and not pd.isna(de):
            de /= 100.0

        cfo = float(q_rows[-1]["cfo_ttm"]) if q_rows and q_rows[-1].get("cfo_ttm") is not None \
            else _latest_non_null(y_rows, "cfo")

        has_np = any(r.get("net_profit") is not None for r in q_rows + y_rows)
        ff = info.get("free_float_pct")
        rec = {"ticker": str(ticker).strip().upper(), "sector": sector, "exchange": exchange,
               "roe": roe, "debt_equity": de, "cfo": cfo,
               "free_float_pct": np.nan if ff is None else float(ff),
               "profit_source": "net_profit" if has_np else "eps",
               "latest_quarter": q_rows[-1]["period"] if q_rows else ""}
        fn = info.get(OPTIONAL_FOREIGN_COLUMN)
        if fn is not None:
            rec[OPTIONAL_FOREIGN_COLUMN] = float(fn)
        for i, v in enumerate(_newest_first(q_rows, _metric, 6), start=1):
            rec[f"net_profit_q{i}"] = v
        for i, v in enumerate(_newest_first(y_rows, _metric, 3), start=1):
            rec[f"net_profit_y{i}"] = v
        for i, v in enumerate(_newest_first(y_rows, lambda r: r.get("revenue"), 3), start=1):
            rec[f"revenue_y{i}"] = v
        closes = info.get("recent_closes") or []
        for i, c in enumerate(closes[:4], start=1):
            rec[f"close_d{i}"] = np.nan if c is None else float(c)
        records.append(rec)

    df = _ensure_columns(pd.DataFrame(records))
    if EXCLUDED_EXCHANGES:
        df = df[~df["exchange"].isin(EXCLUDED_EXCHANGES)]
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# 4. Tính các cột tăng trưởng
# ----------------------------------------------------------------------------
def _growth_pct(new: pd.Series, old: pd.Series) -> pd.Series:
    """% tăng trưởng. Kỳ gốc <= 0 thì không có nghĩa (vd từ lỗ sang lãi) -> NaN (bị loại)."""
    return ((new / old.where(old > 0)) - 1.0) * 100.0


def fa_compute_growth(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm: eps_growth_qoq (+_prev, theo QUARTER_GROWTH_BASIS), eps_growth_delta,
    eps_growth_yoy (LN năm), revenue_growth_yoy (doanh thu năm), profit_positive_3y."""
    out = _ensure_columns(df)
    if QUARTER_GROWTH_BASIS == "yoy":      # so với cùng kỳ năm trước
        now = _growth_pct(out["net_profit_q1"], out["net_profit_q5"])
        prev = _growth_pct(out["net_profit_q2"], out["net_profit_q6"])
    else:                                  # so với quý liền trước
        now = _growth_pct(out["net_profit_q1"], out["net_profit_q2"])
        prev = _growth_pct(out["net_profit_q2"], out["net_profit_q3"])
    out["eps_growth_qoq"] = now
    out["eps_growth_qoq_prev"] = prev
    out["eps_growth_delta"] = now - prev   # Delta Growth (điểm %)

    out["eps_growth_yoy"] = _growth_pct(out["net_profit_y1"], out["net_profit_y2"])
    out["revenue_growth_yoy"] = _growth_pct(out["revenue_y1"], out["revenue_y2"])
    out["profit_positive_3y"] = (out[["net_profit_y1", "net_profit_y2", "net_profit_y3"]] > 0).all(axis=1)
    return out


# ----------------------------------------------------------------------------
# 5. Các bộ lọc (mỗi hàm trả về mask boolean; thiếu dữ liệu -> False)
# ----------------------------------------------------------------------------
def _all_true(df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=df.index)


def fa_filter_roe(df: pd.DataFrame) -> pd.Series:
    return df["roe"] > MIN_ROE


def fa_filter_debt_equity(df: pd.DataFrame) -> pd.Series:
    ok = df["debt_equity"] < MAX_DEBT_EQUITY
    if BANK_EXEMPT_DEBT_EQUITY and "priority_sector" in df.columns:
        ok |= df["priority_sector"] == "Ngân hàng"
    return ok


def fa_filter_growth_quarter(df: pd.DataFrame) -> pd.Series:
    return df["eps_growth_qoq"] > MIN_QUARTER_GROWTH


def fa_filter_acceleration(df: pd.DataFrame) -> pd.Series:
    if not REQUIRE_ACCELERATION:
        return _all_true(df)
    return df["eps_growth_qoq"] > df["eps_growth_qoq_prev"]


def fa_filter_growth_annual(df: pd.DataFrame) -> pd.Series:
    return df["eps_growth_yoy"] > MIN_ANNUAL_PROFIT_GROWTH


def fa_filter_profit_positive_3y(df: pd.DataFrame) -> pd.Series:
    return df["profit_positive_3y"] if REQUIRE_POSITIVE_3Y_PROFIT else _all_true(df)


def fa_filter_revenue_growth(df: pd.DataFrame) -> pd.Series:
    if not REQUIRE_REVENUE_GROWTH:
        return _all_true(df)
    return df["revenue_growth_yoy"] > MIN_ANNUAL_REVENUE_GROWTH


def fa_filter_cfo(df: pd.DataFrame) -> pd.Series:
    return df["cfo"] > 0 if REQUIRE_POSITIVE_CFO else _all_true(df)


def fa_filter_free_float(df: pd.DataFrame) -> pd.Series:
    return df["free_float_pct"] >= MIN_FREE_FLOAT_PCT if MIN_FREE_FLOAT_PCT > 0 else _all_true(df)


def fa_tag_sector(df: pd.DataFrame) -> pd.DataFrame:
    """Sector Tagging: priority_sector (nhóm ưu tiên hoặc '') và deprioritized (ngành bị hạ tỷ trọng)."""
    def _tag(sector: str) -> str:
        s = (sector or "").lower()
        for group, keywords in PRIORITY_SECTOR_KEYWORDS.items():
            if any(k in s for k in keywords):
                return group
        return ""

    out = df.copy()
    out["priority_sector"] = out["sector"].map(_tag)
    out["deprioritized"] = out["sector"].map(
        lambda s: any(k in (s or "").lower() for k in DEPRIORITIZED_SECTOR_KEYWORDS))
    return out


def _q_cols() -> list:
    """Dữ liệu gốc cần có để tính tăng trưởng quý gần nhất."""
    return ["net_profit_q1", "net_profit_q5"] if QUARTER_GROWTH_BASIS == "yoy" else ["net_profit_q1", "net_profit_q2"]


def _q_cols_accel() -> list:
    """Dữ liệu gốc cần có để so tăng trưởng quý này với quý trước."""
    if QUARTER_GROWTH_BASIS == "yoy":
        return ["net_profit_q1", "net_profit_q2", "net_profit_q5", "net_profit_q6"]
    return ["net_profit_q1", "net_profit_q2", "net_profit_q3"]


# Danh sách tiêu chí: (tên hiển thị, các cột DỮ LIỆU GỐC phải có, hàm lọc)
FA_CRITERIA = [
    ("ROE > 15%", ["roe"], fa_filter_roe),
    ("D/E < 1.2", ["debt_equity"], fa_filter_debt_equity),
    ("LN quý gần nhất > 15%", _q_cols, fa_filter_growth_quarter),
    ("Tăng tốc so với quý trước", _q_cols_accel, fa_filter_acceleration),
    ("LN năm gần nhất > 15%", ["net_profit_y1", "net_profit_y2"], fa_filter_growth_annual),
    ("LN dương liên tục 3 năm", ["net_profit_y1", "net_profit_y2", "net_profit_y3"], fa_filter_profit_positive_3y),
    ("Doanh thu năm > 15%", ["revenue_y1", "revenue_y2"], fa_filter_revenue_growth),
    ("Dòng tiền HĐKD > 0", ["cfo"], fa_filter_cfo),
    ("Free float >= 10%", ["free_float_pct"], fa_filter_free_float),
]


# ----------------------------------------------------------------------------
# 6. Chạy toàn bộ bộ lọc + báo cáo dữ liệu thiếu
# ----------------------------------------------------------------------------
def fa_run_screen(df: pd.DataFrame) -> pd.DataFrame:
    """Trả về danh sách mã đạt TẤT CẢ tiêu chí FA."""
    df = fa_tag_sector(fa_compute_growth(df))
    mask = _all_true(df)
    for _, _, fn in FA_CRITERIA:
        mask &= fn(df)
    if STRICT_PRIORITY_SECTOR:
        mask &= df["priority_sector"] != ""

    passed = df[mask.fillna(False)].copy()
    # Thứ tự: ngành ưu tiên lên trước, ngành hạ tỷ trọng xuống cuối, có dòng tiền ngoại ròng dương ưu tiên,
    # sau đó tăng trưởng quý giảm dần
    passed["_prio"] = passed["priority_sector"] != ""
    passed["_foreign"] = passed[OPTIONAL_FOREIGN_COLUMN] > 0 if OPTIONAL_FOREIGN_COLUMN in passed.columns else False
    passed = passed.sort_values(["deprioritized", "_prio", "_foreign", "eps_growth_qoq"],
                                ascending=[True, False, False, False])
    return passed.drop(columns=["_prio", "_foreign"]).reset_index(drop=True)


def fa_criteria_report(df: pd.DataFrame) -> str:
    """Bảng: mỗi tiêu chí có bao nhiêu mã CÓ dữ liệu để xét và bao nhiêu mã ĐẠT.
    'Có dữ liệu' thấp = đó là thứ cần yêu cầu bên Data bổ sung."""
    d = fa_tag_sector(fa_compute_growth(df))
    rows = []
    for name, cols, fn in FA_CRITERIA:
        has = d[cols() if callable(cols) else cols].notna().all(axis=1)
        rows.append({"Tiêu chí": name, "Có dữ liệu": int(has.sum()), "Đạt": int((fn(d) & has).sum())})
    rows.append({"Tiêu chí": "Ngành ICB (gắn nhãn ưu tiên)", "Có dữ liệu": int((d["sector"] != "").sum()),
                 "Đạt": int((d["priority_sector"] != "").sum())})
    text = f"Tổng số mã: {len(d)} | So sánh tăng trưởng quý theo: {QUARTER_GROWTH_BASIS}\n"
    text += pd.DataFrame(rows).to_string(index=False)
    if "profit_source" in d.columns:
        text += f"\nNguồn số liệu lợi nhuận: {d['profit_source'].value_counts().to_dict()} (eps = tạm dùng EPS thay LN ròng)"
    return text


# ----------------------------------------------------------------------------
# 7. Xuất data/watch_list.json (Thành viên 4 ghi, Thành viên 3 chỉ đọc)
# ----------------------------------------------------------------------------
def _num(value, ndigits: int = 1):
    if value is None or pd.isna(value):
        return None
    return round(float(value), ndigits)


def fa_export_watch_list(passed: pd.DataFrame,
                         output_path: str | Path = "data/watch_list.json") -> Path:
    """Ghi watch_list.json. Ghi qua file tạm rồi replace để Thành viên 3 không đọc phải file ghi dở."""
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
            "quarter_growth_basis": QUARTER_GROWTH_BASIS,
            "revenue_growth_yoy": _num(row["revenue_growth_yoy"]),
            "eps_growth_delta": _num(row["eps_growth_delta"]),
            "cfo": _num(row["cfo"], 0),
            "priority_sector": row["priority_sector"],
            "updated_at": today,
        }
        if OPTIONAL_FOREIGN_COLUMN in row and not pd.isna(row[OPTIONAL_FOREIGN_COLUMN]):
            rec[OPTIONAL_FOREIGN_COLUMN] = _num(row[OPTIONAL_FOREIGN_COLUMN], 0)
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
# 8. Chạy thử:
#    python strategies/fa_strategy.py data/financial_data.json data/watch_list.test.json
#    python strategies/fa_strategy.py data/sample_fundamentals.csv data/watch_list.sample.json
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    in_file = sys.argv[1] if len(sys.argv) > 1 else "data/financial_data.json"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "data/watch_list.json"
    raw = fa_load_financial_json(in_file) if in_file.lower().endswith(".json") else fa_load_data(in_file)
    print(fa_criteria_report(raw))
    result = fa_run_screen(raw)
    path = fa_export_watch_list(result, out_file)
    print(f"\nĐọc {len(raw)} mã -> đạt TẤT CẢ tiêu chí FA: {len(result)} mã -> {path}")
    if not result.empty:
        cols = ["ticker", "sector", "roe", "debt_equity", "eps_growth_qoq", "eps_growth_qoq_prev", "eps_growth_yoy"]
        print(result[cols].to_string(index=False))