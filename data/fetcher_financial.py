#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetcher_financial.py
====================
Thu thập dữ liệu FA từ Vnstock KBS:
- Tự động Retry khi mạng giật ([Errno 11001] / Connection Error), không nhảy sai sang BCTC Năm.
- Lọc sạch mã rác cổ xưa (UEM 2012-2015).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

try:
    from vnstock import Finance
except ImportError:  # pragma: no cover
    Finance = None


SOURCE = "KBS"
LIMIT = None
MAX_PERIODS = 4
ALLOW_SUFFIXED_COLUMNS = False
DEFAULT_SECTOR = "Chưa phân loại"

RATIO_ROW_IDS: Dict[str, Tuple[str, ...]] = {
    "pe": ("pe_ratio",),
    "pb": ("pb_ratio",),
    "roe": ("roe",),
    "debt_equity": ("debt_to_equity", "debtperequity"),
    "net_margin": ("net_margin",),
}

RATIO_YEAR_EPS_IDS: Tuple[str, ...] = (
    "earning_per_share",
    "eps",
    "trailing_eps",
)

INCOME_EPS_IDS: Tuple[str, ...] = (
    "earnings_per_share_vnd",
    "earning_per_share",
    "eps",
)

INCOME_REVENUE_ID = "revenue"

REVENUE_LABEL_PREFERENCE: Tuple[str, ...] = (
    "doanh thu thuần",
    "net revenue",
    "doanh thu thuan",
)

METADATA_COLUMNS = {
    "item", "item_en", "item_id", "ticker", "symbol", "index", "yearreport", "lengthreport",
}

QUARTER_COL_RE = re.compile(r"^(\d{4})-Q([1-4])$")
YEAR_COL_RE = re.compile(r"^(\d{4})(?:-Năm|-Nam|-Year)?$")
SUFFIX_RE = re.compile(r"^(?P<base>.+?)_(?P<n>\d+)$")

logger = logging.getLogger("fa_fetcher")


def setup_logging(log_path: str = "data/fa_fetch.log") -> None:
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    directory = os.path.dirname(log_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)


class Period:
    __slots__ = ("kind", "year", "quarter")

    def __init__(self, kind: str, year: int, quarter: Optional[int] = None):
        self.kind = kind
        self.year = year
        self.quarter = quarter

    @staticmethod
    def parse(text: str, kind: str) -> Optional["Period"]:
        text = str(text).strip()
        if kind == "quarter":
            match = QUARTER_COL_RE.match(text)
            if not match:
                return None
            return Period("quarter", int(match.group(1)), int(match.group(2)))

        match = YEAR_COL_RE.match(text)
        if not match:
            return None
        return Period("year", int(match.group(1)))

    def key(self) -> Tuple[int, int]:
        return (self.year, self.quarter or 0)

    def label(self) -> str:
        if self.kind == "quarter":
            return f"{self.year}-Q{self.quarter}"
        return f"{self.year}-Năm"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Period) and self.key() == other.key() and self.kind == other.kind

    def __hash__(self) -> int:
        return hash((self.kind, self.year, self.quarter))

    def previous_quarter(self) -> Optional["Period"]:
        if self.kind != "quarter" or self.quarter is None:
            return None
        if self.quarter > 1:
            return Period("quarter", self.year, self.quarter - 1)
        return Period("quarter", self.year - 1, 4)

    def previous_year_same_quarter(self) -> Optional["Period"]:
        if self.kind == "quarter":
            return Period("quarter", self.year - 1, self.quarter)
        return Period("year", self.year - 1)


class Diagnostics:
    def __init__(self) -> None:
        self._items: Dict[str, List[Dict[str, Any]]] = {}

    def add(self, ticker: str, code: str, message: str, period: Optional[str] = None, level: str = "WARNING") -> None:
        self._items.setdefault(ticker, []).append({
            "level": level, "code": code, "period": period, "message": message,
        })
        logger.debug(f"[{level}] {ticker} {period or ''}: {message}")

    def as_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        return self._items

    def merge_into(self, existing: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(existing)
        merged.update(self._items)
        return merged


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [
            "_".join(str(part) for part in col if str(part) != "nan").strip("_")
            for col in df.columns
        ]
    return df


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "N/A", "n/a", "nan", "NaN", "None", "null"):
        return None
    if text.endswith("%"):
        text = text[:-1].strip()

    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def round_or_none(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        result = round(float(value), digits)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def calc_growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def empty_history_row(period_label: str) -> Dict[str, Any]:
    return {
        "period": period_label,
        "eps": None, "pe": None, "pb": None, "roe": None,
        "debt_equity": None, "net_margin": None,
        "eps_growth_qoq": None, "eps_growth_yoy": None, "revenue_growth_qoq": None,
    }


HISTORY_FIELDS = tuple(empty_history_row("x").keys())


class PeriodResolver:
    def __init__(self, diagnostics: Diagnostics, ticker: str):
        self.diag = diagnostics
        self.ticker = ticker

    def resolve(self, df: pd.DataFrame, kind: str) -> Dict[Period, str]:
        if df is None or df.empty:
            return {}

        exact: Dict[Period, str] = {}
        for raw_col in df.columns:
            col = str(raw_col).strip()
            if col.lower() in METADATA_COLUMNS:
                continue

            suffix_match = SUFFIX_RE.match(col)
            base = suffix_match.group("base") if suffix_match else col
            period = Period.parse(base, kind)
            if period is None:
                continue

            # Lọc chặn các kỳ rác quá cũ
            if kind == "quarter" and period.year < 2024:
                continue
            if kind == "year" and period.year < 2021:
                continue

            if not suffix_match and period not in exact:
                exact[period] = col

        return exact


class RowResolver:
    def __init__(self, diagnostics: Diagnostics, ticker: str):
        self.diag = diagnostics
        self.ticker = ticker

    def find_all_by_item_id(self, df: pd.DataFrame, item_id: str) -> List[Any]:
        if df is None or df.empty or "item_id" not in df.columns:
            return []
        normalized = df["item_id"].astype(str).str.strip().str.lower()
        return df.index[normalized == item_id.strip().lower()].tolist()

    def find_one(self, df: pd.DataFrame, item_ids: Sequence[str]) -> Optional[Any]:
        if df is None or df.empty or "item_id" not in df.columns:
            return None
        for item_id in item_ids:
            matches = self.find_all_by_item_id(df, item_id)
            if matches:
                return matches[0]
        return None

    def find_revenue_row(self, df: pd.DataFrame) -> Optional[Any]:
        if df is None or df.empty or "item_id" not in df.columns:
            return None
        matches = self.find_all_by_item_id(df, INCOME_REVENUE_ID)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        if "item" in df.columns:
            for keyword in REVENUE_LABEL_PREFERENCE:
                for index in matches:
                    label = str(df.loc[index, "item"]).strip().lower()
                    if keyword in label:
                        return index
        return None


def get_cell(df: pd.DataFrame, row_index: Optional[Any], column: Optional[str]) -> Optional[float]:
    if df is None or df.empty or row_index is None or column is None or column not in df.columns:
        return None
    try:
        value = df.loc[row_index, column]
    except Exception:
        return None
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    return safe_float(value)


class FinancialDataFetcher:
    def __init__(self, request_delay: float = 2.0, rate_limit_sleep: float = 25.0):
        self.source = SOURCE
        self.request_delay = request_delay
        self.rate_limit_sleep = rate_limit_sleep
        self.diag = Diagnostics()

    def _call(self, finance: Any, method: str, ticker: str, **kwargs) -> Optional[pd.DataFrame]:
        """Tự động Retry khi mạng rớt hoặc gặp Rate Limit, kiên trì thử lại 3 lần."""
        for attempt in range(1, 4):
            try:
                df = getattr(finance, method)(**kwargs)
                df = flatten_columns(df)
                if df is not None and not df.empty:
                    return df
                return None
            except Exception as exc:
                msg = str(exc).lower()
                # Xử lý Rate limit
                if any(k in msg for k in ("rate limit", "exceeded", "too many", "giới hạn", "429")):
                    logger.info(f"  [Rate limit] {ticker} - Chờ {self.rate_limit_sleep:.0f}s để hồi phục API...")
                    time.sleep(self.rate_limit_sleep)
                    continue
                # Xử lý mạng rớt / DNS lag [Errno 11001]
                if any(k in msg for k in ("nameresolutionerror", "11001", "getaddrinfo failed", "connectionpool", "connection")):
                    logger.info(f"  [Mạng chập chờn] {ticker} - Đang thử kết nối lại lần {attempt}/3...")
                    time.sleep(3.0)
                    continue

                return None
        return None

    def _extract_ratio(self, df_ratio: Optional[pd.DataFrame], row_map: Dict[str, Optional[Any]], column: Optional[str]) -> Dict[str, Optional[float]]:
        result: Dict[str, Optional[float]] = {}
        for metric in RATIO_ROW_IDS:
            result[metric] = get_cell(df_ratio, row_map.get(metric), column)
        return result

    def _build_quarter_history(self, ticker: str, df_income: pd.DataFrame, df_ratio: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        resolver = PeriodResolver(self.diag, ticker)
        rows = RowResolver(self.diag, ticker)

        income_cols = resolver.resolve(df_income, "quarter")
        ratio_cols = resolver.resolve(df_ratio, "quarter") if df_ratio is not None else {}

        if not income_cols:
            return []

        eps_row = rows.find_one(df_income, INCOME_EPS_IDS)
        revenue_row = rows.find_revenue_row(df_income)
        
        ratio_row_map = {}
        if df_ratio is not None:
            for metric, item_ids in RATIO_ROW_IDS.items():
                ratio_row_map[metric] = rows.find_one(df_ratio, item_ids)

        eps_map: Dict[Period, Optional[float]] = {}
        revenue_map: Dict[Period, Optional[float]] = {}

        for period, column in income_cols.items():
            eps_map[period] = get_cell(df_income, eps_row, column)
            revenue_map[period] = get_cell(df_income, revenue_row, column)

        all_periods = sorted(income_cols.keys(), key=lambda p: p.key())
        selected = all_periods[-MAX_PERIODS:]

        history: List[Dict[str, Any]] = []
        for period in selected:
            label = period.label()
            row = empty_history_row(label)
            eps = eps_map.get(period)
            row["eps"] = round_or_none(eps)

            ratio_column = ratio_cols.get(period)
            if ratio_column is not None:
                ratio_values = self._extract_ratio(df_ratio, ratio_row_map, ratio_column)
                for metric, value in ratio_values.items():
                    row[metric] = round_or_none(value)

            previous_quarter = period.previous_quarter()
            if previous_quarter in eps_map:
                row["eps_growth_qoq"] = round_or_none(calc_growth(eps, eps_map[previous_quarter]))

            if previous_quarter in revenue_map:
                row["revenue_growth_qoq"] = round_or_none(calc_growth(revenue_map.get(period), revenue_map[previous_quarter]))

            same_quarter_last_year = period.previous_year_same_quarter()
            if same_quarter_last_year in eps_map:
                row["eps_growth_yoy"] = round_or_none(calc_growth(eps, eps_map[same_quarter_last_year]))

            history.append(row)

        return history

    def _build_year_history(self, ticker: str, df_ratio_year: Optional[pd.DataFrame], df_income_year: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        resolver = PeriodResolver(self.diag, ticker)
        rows = RowResolver(self.diag, ticker)

        ratio_cols = resolver.resolve(df_ratio_year, "year") if df_ratio_year is not None else {}
        income_cols = resolver.resolve(df_income_year, "year") if df_income_year is not None else {}

        eps_from_ratio: Dict[Period, Optional[float]] = {}
        if ratio_cols:
            eps_row = rows.find_one(df_ratio_year, RATIO_YEAR_EPS_IDS)
            if eps_row is not None:
                for period, column in ratio_cols.items():
                    eps_from_ratio[period] = get_cell(df_ratio_year, eps_row, column)

        eps_map = eps_from_ratio
        all_periods = sorted(set(ratio_cols.keys()) | set(eps_map.keys()), key=lambda p: p.key())
        if not all_periods:
            return []

        selected = all_periods[-MAX_PERIODS:]
        ratio_row_map = {}
        if df_ratio_year is not None:
            for metric, item_ids in RATIO_ROW_IDS.items():
                ratio_row_map[metric] = rows.find_one(df_ratio_year, item_ids)

        history: List[Dict[str, Any]] = []
        for period in selected:
            label = period.label()
            row = empty_history_row(label)
            eps = eps_map.get(period)
            row["eps"] = round_or_none(eps)

            ratio_column = ratio_cols.get(period)
            if ratio_column is not None:
                values = self._extract_ratio(df_ratio_year, ratio_row_map, ratio_column)
                for metric, value in values.items():
                    row[metric] = round_or_none(value)

            previous_year = period.previous_year_same_quarter()
            if previous_year in eps_map:
                row["eps_growth_yoy"] = round_or_none(calc_growth(eps, eps_map[previous_year]))

            history.append(row)

        return history

    def fa_fetch_ticker(self, ticker: str, sector: str = DEFAULT_SECTOR) -> Optional[Dict[str, Any]]:
        ticker = str(ticker).strip().upper()
        if not ticker or Finance is None:
            return None

        try:
            finance = Finance(symbol=ticker, source=self.source)

            # 1. Thu thập Quý
            df_income_q = self._call(finance, "income_statement", ticker, period="quarter")
            time.sleep(2.5)
            df_ratio_q = self._call(finance, "ratio", ticker, period="quarter")

            history = []
            if df_income_q is not None:
                history = self._build_quarter_history(ticker, df_income_q, df_ratio_q)
                if history and (all(r["eps"] is None for r in history) or len(history) < 3):
                    history = []

            # 2. Nếu hoàn toàn không có Quý -> Fallback sang Năm (UPCoM)
            if not history:
                time.sleep(2.5)
                df_ratio_y = self._call(finance, "ratio", ticker, period="year")
                time.sleep(2.5)
                df_income_y = self._call(finance, "income_statement", ticker, period="year")
                history = self._build_year_history(ticker, df_ratio_y, df_income_y)
                if history and len(history) < 3:
                    history = []

            if not history:
                return None

            return {
                "ticker": ticker,
                "sector": sector,
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
                "history": history,
            }
        except Exception:
            return None


def fa_read_symbols(path: str) -> List[Tuple[str, str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Không tìm thấy file {path}")
    results = []
    seen = set()
    header_keys = {"symbol", "ticker", "stock", "mã cp", "ma cp", "macp"}

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = [r for r in reader if r and any(c.strip() for c in r)]

    if not rows:
        return results

    sector_index = 1
    start = 0
    first = [c.strip().lower() for c in rows[0]]
    if first and first[0] in header_keys:
        start = 1
        for index, name in enumerate(first):
            if name in ("sector", "exchange", "san", "sàn", "nhóm ngành", "industry", "group"):
                sector_index = index
                break

    for row in rows[start:]:
        ticker = row[0].strip().upper()
        if not ticker or ticker.lower() in header_keys or ticker in seen:
            continue
        seen.add(ticker)
        sector = row[sector_index].strip() if len(row) > sector_index and row[sector_index].strip() else DEFAULT_SECTOR
        results.append((ticker, sector))
    return results


def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _atomic_write_json(path: str, payload: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=4)
    os.replace(temporary, path)


def fa_run_and_save(
    symbols_path: str = "data/symbols.csv",
    output_path: str = "data/financial_data.json",
    diagnostics_path: str = "data/fa_diagnostics.json",
    limit: Optional[int] = LIMIT,
    request_delay: float = 3.0,
    checkpoint_every: int = 5,
    refetch: bool = False,
    only: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:

    fetcher = FinancialDataFetcher(request_delay=request_delay)
    symbols = fa_read_symbols(symbols_path)

    if only:
        wanted = {s.strip().upper() for s in only}
        symbols = [(t, s) for t, s in symbols if t in wanted]

    if limit is not None:
        symbols = symbols[:limit]

    results = _load_json(output_path)
    diagnostics_store = _load_json(diagnostics_path)

    total = len(symbols)
    logger.info(f"\n🚀 Bắt đầu thu thập dữ liệu FA (Tổng: {total} mã)...")

    for index, (ticker, sector) in enumerate(symbols, start=1):
        # Nếu đã có dữ liệu và là dữ liệu Quý chuẩn thì bỏ qua
        if not refetch and ticker in results and results[ticker].get("history"):
            first_p = results[ticker]["history"][0]["period"]
            # Nếu trước đó bị lưu nhầm Năm ở mã sàn HOSE/HNX hoặc mã rác thì cào lại
            if not ("-Năm" in first_p and ticker in ["PPC", "SCR", "DBT", "CNG", "LSS", "SSB", "UEM"]):
                logger.info(f"[{index}/{total}] {ticker}: Đã có dữ liệu -> Bỏ qua")
                continue

        record = fetcher.fa_fetch_ticker(ticker, sector=sector)

        if record is None:
            logger.info(f"[{index}/{total}] {ticker}: Bỏ qua (Không có dữ liệu đủ chuẩn)")
        else:
            results[ticker] = record
            periods = [row["period"] for row in record["history"]]
            logger.info(f"[{index}/{total}] {ticker}: Lưu {len(periods)} kỳ ({', '.join(periods)})")

        if index % checkpoint_every == 0:
            _atomic_write_json(output_path, results)
            _atomic_write_json(diagnostics_path, fetcher.diag.merge_into(diagnostics_store))

        if index < total:
            time.sleep(request_delay)

    _atomic_write_json(output_path, results)
    _atomic_write_json(diagnostics_path, fetcher.diag.merge_into(diagnostics_store))

    logger.info(f"\n✅ Hoàn tất! Dữ liệu đã lưu tại: {output_path}")
    return results


if __name__ == "__main__":
    setup_logging()
    fa_run_and_save()