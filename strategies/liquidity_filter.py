"""
strategies/liquidity_filter.py
LỚP 2 — LIQUIDITY FILTER

Luồng xử lý:

Lớp 1 - Fundamental Analysis (FA)
        ↓
data/watch_list.json
        ↓
Lớp 2 - Liquidity Filter
        ↓
Đọc data/market_data.db
        ↓
Tính:
    - Turnover trung bình 20 phiên
    - Volume MA20
    - Trading Coverage
    - Số phiên dữ liệu
        ↓
Lọc:
    Turnover TB20 >= 5 tỷ VNĐ/phiên
        ↓
GHI ĐÈ
data/watch_list.json
        ↓
Lớp 3 - Technical Analysis (TA)

Lưu ý:
- Lớp 2 không tạo file output riêng.
- Volume MA20 không dùng để loại mã ở Lớp 2.
- Volume MA20 được giữ lại trong watch_list.json để TA sử dụng.
- Dữ liệu phải được cập nhật trong market_data.db trước khi chạy.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================================
# 1. NGƯỠNG LỌC THANH KHOẢN
# ============================================================================

# Turnover trung bình 20 phiên gần nhất
# phải >= 5 tỷ VNĐ/phiên.
MIN_TURNOVER_VND = 5_000_000_000

# Số phiên gần nhất dùng để tính thanh khoản.
LOOKBACK_DAYS = 20

# ---------------------------------------------------------------------------
# Các điều kiện phụ — hiện tại KHÔNG bật
# ---------------------------------------------------------------------------

# Tỷ lệ phiên có giao dịch trong 20 phiên gần nhất.
REQUIRE_TRADING_COVERAGE = False

MIN_TRADING_COVERAGE_PCT = 80.0

# Yêu cầu số phiên lịch sử tối thiểu.
REQUIRE_MIN_LISTING_HISTORY = False

MIN_LISTING_SESSIONS = 70

# ---------------------------------------------------------------------------
# Đơn vị giá
# ---------------------------------------------------------------------------

# Dữ liệu hiện tại của bạn:
# close = 127.98
# volume = 3,172,800
#
# Nếu close đang được lưu theo đơn vị "nghìn đồng":
# Turnover = close × volume × 1,000
#
# Ví dụ:
# 127.98 × 3,172,800 × 1,000
# ≈ 406 tỷ VNĐ
#
PRICE_UNIT_MULTIPLIER = 1000


# ============================================================================
# 2. ĐƯỜNG DẪN DỮ LIỆU
# ============================================================================

DEFAULT_WATCH_LIST_PATH = "data/watch_list.json"

DEFAULT_DB_PATH = "data/market_data.db"


# ============================================================================
# 3. CÁC TRƯỜNG THANH KHOẢN ĐƯỢC LƯU LẠI
# ============================================================================

LIQUIDITY_FIELDS = [
    "avg_turnover_vnd",
    "volume_ma20",
    "trading_coverage_pct",
    "sessions_available",
    "liquidity_latest_date",
    "liquidity_checked_at",
]


# ============================================================================
# 4. HÀM HỖ TRỢ
# ============================================================================

def _all_true(df: pd.DataFrame) -> pd.Series:
    """
    Trả về Series toàn True.
    Dùng cho các điều kiện đang tắt.
    """
    return pd.Series(True, index=df.index)


# ============================================================================
# 5. ĐỌC WATCH LIST TỪ LỚP 1
# ============================================================================

def liq_load_watch_list(
    path: str = DEFAULT_WATCH_LIST_PATH,
) -> list[dict]:
    """
    Đọc danh sách mã do Lớp 1 FA xuất ra.

    File:
        data/watch_list.json
    """

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Không tìm thấy file watch list: {path}"
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Trường hợp JSON là list trực tiếp
    if isinstance(data, list):
        return data

    # Trường hợp JSON có wrapper
    if isinstance(data, dict):

        for key in [
            "stocks",
            "watch_list",
            "data",
            "symbols",
        ]:

            if (
                key in data
                and isinstance(data[key], list)
            ):
                return data[key]

    raise ValueError(
        "Cấu trúc data/watch_list.json "
        "không phải danh sách mã hợp lệ."
    )


# ============================================================================
# 6. LẤY DANH SÁCH BẢNG TRONG DATABASE
# ============================================================================

def liq_check_database(
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Kiểm tra database có tồn tại và có bảng historical_ohlcv.
    """

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Không tìm thấy database: {db_path}"
        )

    conn = sqlite3.connect(db_path)

    try:

        tables = pd.read_sql_query(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            """,
            conn,
        )

    finally:
        conn.close()

    if "historical_ohlcv" not in tables["name"].tolist():

        raise ValueError(
            "Database không có bảng "
            "'historical_ohlcv'."
        )


# ============================================================================
# 7. KIỂM TRA NGÀY DỮ LIỆU MỚI NHẤT
# ============================================================================

def liq_get_latest_database_date(
    db_path: str = DEFAULT_DB_PATH,
) -> str | None:
    """
    Lấy ngày dữ liệu mới nhất trong database.

    Dùng để kiểm tra xem database có được cập nhật
    gần đây hay không.
    """

    conn = sqlite3.connect(db_path)

    try:

        result = pd.read_sql_query(
            """
            SELECT MAX(date) AS latest_date
            FROM historical_ohlcv
            """,
            conn,
        )

    finally:
        conn.close()

    if result.empty:
        return None

    latest = result.loc[
        0,
        "latest_date",
    ]

    if pd.isna(latest):
        return None

    return str(latest)


# ============================================================================
# 8. ĐỌC LỊCH SỬ GIÁ CỦA MỘT MÃ
# ============================================================================

def liq_load_price_history(
    conn: sqlite3.Connection,
    symbol: str,
) -> pd.DataFrame:
    """
    Đọc dữ liệu OHLCV của một mã.

    Database thực tế của project:
        symbol
        date
        open
        high
        low
        close
        volume
    """

    query = """
        SELECT
            symbol,
            date,
            open,
            high,
            low,
            close,
            volume
        FROM historical_ohlcv
        WHERE symbol = ?
        ORDER BY date ASC
    """

    df = pd.read_sql_query(
        query,
        conn,
        params=(symbol,),
    )

    if df.empty:
        return df

    # Chuẩn hóa date
    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce",
    )

    # Chuyển numeric
    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "date",
            "close",
            "volume",
        ]
    )

    return df.sort_values(
        "date"
    ).reset_index(drop=True)


# ============================================================================
# 9. TÍNH CÁC CHỈ SỐ THANH KHOẢN
# ============================================================================

def liq_compute_metrics(
    df: pd.DataFrame,
) -> dict:
    """
    Tính các chỉ số từ LOOKBACK_DAYS phiên gần nhất.

    Kết quả:
        avg_turnover_vnd
        volume_ma20
        trading_coverage_pct
        sessions_available
        liquidity_latest_date
    """

    empty_result = {
        "avg_turnover_vnd": np.nan,
        "volume_ma20": np.nan,
        "trading_coverage_pct": np.nan,
        "sessions_available": 0,
        "liquidity_latest_date": None,
    }

    if df is None or df.empty:
        return empty_result

    required_columns = {
        "date",
        "close",
        "volume",
    }

    if not required_columns.issubset(
        df.columns
    ):
        return empty_result

    df = df.copy()

    # -----------------------------------------------------------------------
    # Chỉ lấy 20 phiên gần nhất
    # -----------------------------------------------------------------------

    df = df.sort_values(
        "date"
    )

    recent = df.tail(
        LOOKBACK_DAYS
    ).copy()

    if recent.empty:
        return empty_result

    # -----------------------------------------------------------------------
    # TURNOVER
    # -----------------------------------------------------------------------

    # Turnover = Giá × Volume × hệ số đơn vị
    recent["turnover_vnd"] = (
        recent["close"]
        * recent["volume"]
        * PRICE_UNIT_MULTIPLIER
    )

    avg_turnover_vnd = (
        recent["turnover_vnd"].mean()
    )

    # -----------------------------------------------------------------------
    # VOLUME MA20
    # -----------------------------------------------------------------------

    volume_ma20 = (
        recent["volume"].mean()
    )

    # -----------------------------------------------------------------------
    # TRADING COVERAGE
    # -----------------------------------------------------------------------

    trading_days = (
        recent["volume"] > 0
    ).sum()

    trading_coverage_pct = (
        trading_days
        / len(recent)
        * 100
    )

    # -----------------------------------------------------------------------
    # SỐ PHIÊN
    # -----------------------------------------------------------------------

    sessions_available = len(
        recent
    )

    # -----------------------------------------------------------------------
    # NGÀY DỮ LIỆU MỚI NHẤT CỦA MÃ
    # -----------------------------------------------------------------------

    latest_date = recent[
        "date"
    ].max()

    if pd.notna(latest_date):

        latest_date = (
            latest_date.strftime(
                "%Y-%m-%d"
            )
        )

    else:
        latest_date = None

    return {
        "avg_turnover_vnd": float(
            avg_turnover_vnd
        ),

        "volume_ma20": float(
            volume_ma20
        ),

        "trading_coverage_pct": float(
            trading_coverage_pct
        ),

        "sessions_available": int(
            sessions_available
        ),

        "liquidity_latest_date": latest_date,
    }


# ============================================================================
# 10. TÍNH THANH KHOẢN CHO TOÀN BỘ WATCH LIST
# ============================================================================

def liq_attach_metrics(
    watch_list: list[dict],
    db_path: str = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """
    Tính chỉ số thanh khoản cho từng mã trong watch list.
    """

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Không tìm thấy database: {db_path}"
        )

    rows = []

    conn = sqlite3.connect(
        db_path
    )

    try:

        for item in watch_list:

            # ----------------------------------------------------------------
            # Lấy symbol
            # ----------------------------------------------------------------

            symbol = (
                item.get("symbol")
                or item.get("ticker")
                or item.get("code")
            )

            if not symbol:
                continue

            symbol = str(
                symbol
            ).upper().strip()

            # ----------------------------------------------------------------
            # Đọc dữ liệu lịch sử
            # ----------------------------------------------------------------

            price_df = liq_load_price_history(
                conn,
                symbol,
            )

            # ----------------------------------------------------------------
            # Tính metrics
            # ----------------------------------------------------------------

            metrics = liq_compute_metrics(
                price_df
            )

            row = dict(item)

            row["symbol"] = symbol

            # Giữ ticker nếu dữ liệu FA đang dùng
            if "ticker" not in row:
                row["ticker"] = symbol

            row.update(metrics)

            row[
                "liquidity_checked_at"
            ] = str(date.today())

            rows.append(row)

    finally:

        conn.close()

    return pd.DataFrame(
        rows
    )


# ============================================================================
# 11. BỘ LỌC TURNOVER
# ============================================================================

def liq_filter_turnover(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Điều kiện chính của Lớp 2:

        Turnover TB20 >= 5 tỷ VNĐ/phiên
    """

    return (
        df["avg_turnover_vnd"]
        >= MIN_TURNOVER_VND
    )


# ============================================================================
# 12. BỘ LỌC TRADING COVERAGE
# ============================================================================

def liq_filter_trading_coverage(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Nếu REQUIRE_TRADING_COVERAGE = False
    thì điều kiện này không loại mã.
    """

    if not REQUIRE_TRADING_COVERAGE:

        return _all_true(df)

    return (
        df["trading_coverage_pct"]
        >= MIN_TRADING_COVERAGE_PCT
    )


# ============================================================================
# 13. BỘ LỌC LỊCH SỬ
# ============================================================================

def liq_filter_listing_history(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Nếu REQUIRE_MIN_LISTING_HISTORY = False
    thì điều kiện này không loại mã.
    """

    if not REQUIRE_MIN_LISTING_HISTORY:

        return _all_true(df)

    return (
        df["sessions_available"]
        >= MIN_LISTING_SESSIONS
    )


# ============================================================================
# 14. DANH SÁCH TIÊU CHÍ
# ============================================================================

LIQ_CRITERIA = [

    (
        "Turnover TB20 >= 5 tỷ VNĐ/phiên",
        ["avg_turnover_vnd"],
        liq_filter_turnover,
    ),

    (
        "Tỷ lệ phiên có giao dịch",
        ["trading_coverage_pct"],
        liq_filter_trading_coverage,
    ),

    (
        "Đủ lịch sử giao dịch",
        ["sessions_available"],
        liq_filter_listing_history,
    ),
]


# ============================================================================
# 15. BÁO CÁO TIÊU CHÍ
# ============================================================================

def liq_criteria_report(
    df: pd.DataFrame,
) -> None:
    """
    In số lượng mã đạt từng tiêu chí.
    """

    if df.empty:

        print(
            "[Liquidity] "
            "Không có dữ liệu để báo cáo."
        )

        return

    print()
    print("=" * 70)
    print("LIQUIDITY CRITERIA REPORT")
    print("=" * 70)

    for (
        name,
        _,
        filter_func,
    ) in LIQ_CRITERIA:

        mask = filter_func(
            df
        ).fillna(False)

        count = int(
            mask.sum()
        )

        print(
            f"{name}: "
            f"{count}/{len(df)} mã"
        )

    print("=" * 70)


# ============================================================================
# 16. GHI ĐÈ WATCH LIST
# ============================================================================

def liq_export_watch_list(
    df: pd.DataFrame,
    output_path: str = DEFAULT_WATCH_LIST_PATH,
) -> None:
    """
    Ghi đè kết quả Lớp 2 vào:

        data/watch_list.json

    Không tạo file output riêng.
    """

    output_file = Path(
        output_path
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ------------------------------------------------------------------------
    # Không export các cột nội bộ dùng để báo cáo tiêu chí.
    # ------------------------------------------------------------------------

    export_df = df.copy()

    # Nếu có cột boolean tiêu chí thì xóa
    criterion_names = [
        name
        for name, _, _ in LIQ_CRITERIA
    ]

    export_df = export_df.drop(
        columns=criterion_names,
        errors="ignore",
    )

    # ------------------------------------------------------------------------
    # Chuyển NaN → None để JSON hợp lệ
    # ------------------------------------------------------------------------

    export_df = export_df.replace(
        {
            np.nan: None
        }
    )

    records = export_df.to_dict(
        orient="records"
    )

    # ------------------------------------------------------------------------
    # Ghi file tạm trước
    # ------------------------------------------------------------------------

    fd, temp_path = tempfile.mkstemp(
        dir=str(
            output_file.parent
        ),
        suffix=".tmp",
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                records,
                f,
                ensure_ascii=False,
                indent=2,
            )

        # Ghi đè file chính
        os.replace(
            temp_path,
            output_file,
        )

    except Exception:

        if os.path.exists(
            temp_path
        ):
            os.remove(
                temp_path
            )

        raise


# ============================================================================
# 17. CHẠY LỚP 2
# ============================================================================

def liq_run_screen(
    watch_list_path: str = DEFAULT_WATCH_LIST_PATH,
    db_path: str = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """
    Chạy toàn bộ Liquidity Filter.

    INPUT:
        data/watch_list.json

    DATABASE:
        data/market_data.db

    OUTPUT:
        Ghi đè data/watch_list.json
    """

    print()
    print("=" * 70)
    print("LỚP 2 — LIQUIDITY FILTER")
    print("=" * 70)

    # ------------------------------------------------------------------------
    # Kiểm tra database
    # ------------------------------------------------------------------------

    liq_check_database(
        db_path
    )

    # ------------------------------------------------------------------------
    # Kiểm tra ngày dữ liệu mới nhất
    # ------------------------------------------------------------------------

    latest_db_date = (
        liq_get_latest_database_date(
            db_path
        )
    )

    print(
        f"[Liquidity] "
        f"Ngày dữ liệu mới nhất trong DB: "
        f"{latest_db_date}"
    )

    # ------------------------------------------------------------------------
    # Đọc output của FA
    # ------------------------------------------------------------------------

    watch_list = liq_load_watch_list(
        watch_list_path
    )

    total_input = len(
        watch_list
    )

    print(
        f"[Liquidity] "
        f"Tổng số mã đầu vào: "
        f"{total_input}"
    )

    if total_input == 0:

        print(
            "[Liquidity] "
            "Watch list đang rỗng."
        )

        return pd.DataFrame()

    # ------------------------------------------------------------------------
    # Tính metrics
    # ------------------------------------------------------------------------

    df = liq_attach_metrics(
        watch_list,
        db_path,
    )

    if df.empty:

        print(
            "[Liquidity] "
            "Không tính được dữ liệu."
        )

        return pd.DataFrame()

    # ------------------------------------------------------------------------
    # Kiểm tra số mã có dữ liệu
    # ------------------------------------------------------------------------

    valid_data_mask = (
        df["avg_turnover_vnd"]
        .notna()
    )

    valid_count = int(
        valid_data_mask.sum()
    )

    missing_count = (
        len(df) - valid_count
    )

    print(
        f"[Liquidity] "
        f"Có dữ liệu thanh khoản: "
        f"{valid_count}/{len(df)} mã"
    )

    if missing_count > 0:

        print(
            f"[Liquidity] "
            f"Không có dữ liệu: "
            f"{missing_count} mã"
        )

    # ------------------------------------------------------------------------
    # Báo cáo phân phối Turnover
    # ------------------------------------------------------------------------

    valid_turnover = (
        df.loc[
            valid_data_mask,
            "avg_turnover_vnd"
        ]
    )

    if not valid_turnover.empty:

        print()
        print(
            "[Liquidity] "
            "Phân phối Turnover TB20:"
        )

        print(
            f"  - Min    : "
            f"{valid_turnover.min():,.0f} VNĐ"
        )

        print(
            f"  - Median : "
            f"{valid_turnover.median():,.0f} VNĐ"
        )

        print(
            f"  - Max    : "
            f"{valid_turnover.max():,.0f} VNĐ"
        )

    # ------------------------------------------------------------------------
    # Áp dụng tất cả điều kiện
    # ------------------------------------------------------------------------

    final_mask = pd.Series(
        True,
        index=df.index,
    )

    for (
        name,
        _,
        filter_func,
    ) in LIQ_CRITERIA:

        mask = (
            filter_func(df)
            .fillna(False)
        )

        final_mask &= mask

    # ------------------------------------------------------------------------
    # Kết quả
    # ------------------------------------------------------------------------

    passed_df = df.loc[
        final_mask
    ].copy()

    rejected_count = (
        len(df)
        - len(passed_df)
    )

    print()
    print(
        f"[Liquidity] "
        f"Đạt điều kiện thanh khoản: "
        f"{len(passed_df)} mã"
    )

    print(
        f"[Liquidity] "
        f"Bị loại: "
        f"{rejected_count} mã"
    )

    # ------------------------------------------------------------------------
    # Báo cáo tiêu chí
    # ------------------------------------------------------------------------

    liq_criteria_report(
        df
    )

    # ------------------------------------------------------------------------
    # Ghi đè data/watch_list.json
    # ------------------------------------------------------------------------

    liq_export_watch_list(
        passed_df,
        watch_list_path,
    )

    print()
    print(
        f"[Liquidity] "
        f"Đã ghi đè: "
        f"{watch_list_path}"
    )

    print(
        f"[Liquidity] "
        f"Watch list mới: "
        f"{len(passed_df)} mã"
    )

    # ------------------------------------------------------------------------
    # In danh sách mã đạt
    # ------------------------------------------------------------------------

    if not passed_df.empty:

        print()
        print(
            "Các mã đạt Liquidity:"
        )

        for symbol in passed_df[
            "symbol"
        ].tolist():

            print(
                f"  - {symbol}"
            )

    else:

        print()
        print(
            "[Liquidity] "
            "Không có mã nào đạt."
        )

    print()
    print("=" * 70)

    return passed_df


# ============================================================================
# 18. MAIN
# ============================================================================

if __name__ == "__main__":

    liq_run_screen()