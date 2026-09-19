import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
import pandas as pd
from vnstock import Finance


class FinancialDataFetcher:
    """
    Module thu thập, chuẩn hóa và lưu trữ BCTC (FA) phục vụ bot tín hiệu.
    Tích hợp: Tự động tránh Rate Limit (Sleep + Auto Retry) và Lưu lũy tiến (Checkpoint).
    """

    def __init__(self, source: str = "KBS", min_valid_year: int = 2024, request_delay: float = 3.5):
        self.source = source
        self.min_valid_year = min_valid_year
        self.request_delay = request_delay  # Giữ tần suất dưới 20 requests/phút

    def _extract_from_df(self, df: pd.DataFrame, is_quarter: bool = True) -> Tuple[Dict[str, float], str]:
        if df is None or df.empty or "item_id" not in df.columns:
            return {}, ""

        df = df.set_index("item_id")
        pattern = r"^\d{4}-Q[1-4]" if is_quarter else r"^\d{4}"
        time_cols = [str(c).strip() for c in df.columns if re.match(pattern, str(c).strip())]

        if not time_cols:
            time_cols = [c for c in df.columns if c not in ["item", "item_en", "item_id"]]
            if not time_cols:
                return {}, ""

        time_cols.sort()
        latest_col = time_cols[-1]

        def extract_val(row_id: str, col_name: str = latest_col) -> float:
            if row_id in df.index and col_name in df.columns:
                raw_val = df.loc[row_id, col_name]
                if isinstance(raw_val, pd.Series):
                    raw_val = raw_val.iloc[0]
                try:
                    return float(str(raw_val).replace(",", "").strip())
                except (ValueError, TypeError):
                    return 0.0
            return 0.0

        def to_percentage(val: float) -> float:
            if abs(val) < 2 and val != 0.0:
                return val * 100
            return val

        def calc_growth(curr: float, prev: float) -> float:
            if prev != 0.0:
                return ((curr - prev) / abs(prev)) * 100
            return 0.0

        pe = extract_val("pe_ratio")
        pb = extract_val("pb_ratio")
        eps = extract_val("trailing_eps") or extract_val("earning_per_share") or extract_val("eps")

        roe = to_percentage(extract_val("roe"))
        debt_equity = extract_val("debt_to_equity") or extract_val("debtPerEquity")
        net_margin = to_percentage(extract_val("net_margin"))

        pat_key = [k for k in df.index if str(k).startswith("profit_after_tax_for_shareholders")]
        profit_yoy_raw = extract_val(pat_key[0]) if pat_key else (
            extract_val("profit_growth_yoy") or extract_val("eps_growth_yoy") or extract_val("eps_growth")
        )
        eps_growth_yoy = to_percentage(profit_yoy_raw)

        rev_yoy_raw = extract_val("net_revenue") or extract_val("revenue_growth_yoy") or extract_val("revenue_growth")
        revenue_growth_yoy = to_percentage(rev_yoy_raw)

        eps_growth_qoq = 0.0
        if len(time_cols) >= 2 and is_quarter:
            prev_col = time_cols[-2]
            curr_eps = extract_val("earning_per_share", latest_col) or extract_val("trailing_eps", latest_col)
            prev_eps = extract_val("earning_per_share", prev_col) or extract_val("trailing_eps", prev_col)
            eps_growth_qoq = calc_growth(curr_eps, prev_eps)
            revenue_growth_qoq = revenue_growth_yoy
        else:
            eps_growth_qoq = eps_growth_yoy
            revenue_growth_qoq = revenue_growth_yoy

        metrics = {
            "eps": round(eps, 2),
            "pe": round(pe, 2),
            "pb": round(pb, 2),
            "roe": round(roe, 2),
            "debt_equity": round(debt_equity, 2),
            "net_margin": round(net_margin, 2),
            "eps_growth_qoq": round(eps_growth_qoq, 2),
            "eps_growth_yoy": round(eps_growth_yoy, 2),
            "revenue_growth_qoq": round(revenue_growth_qoq, 2),
        }
        return metrics, latest_col

    def get_clean_financials(self, ticker: str, sector: str = "Chưa phân loại", max_retries: int = 3) -> Dict[str, Any]:
        """Thu thập BCTC có tích hợp tự động chờ khi chạm trần API."""
        ticker_clean = str(ticker).strip().upper()
        if not ticker_clean or ticker_clean in ["SYMBOL", "TICKER", "STOCK", "MÃ CP"]:
            return {}

        for attempt in range(max_retries):
            try:
                fa = Finance(symbol=ticker_clean, source=self.source)

                # 1. Thử cào theo Quý
                df_quarter = fa.ratio(period="quarter")
                metrics, period = self._extract_from_df(df_quarter, is_quarter=True)
                year_val = int(period.split("-")[0]) if period and "-" in period else 0

                # 2. Fallback sang Năm
                if not metrics or year_val < self.min_valid_year:
                    time.sleep(1.0)  # Giãn nhẹ giữa 2 lần request cùng 1 mã
                    df_year = fa.ratio(period="year")
                    metrics_year, period_year = self._extract_from_df(df_year, is_quarter=False)

                    try:
                        year_only = int(period_year.split("-")[0]) if period_year else 0
                    except ValueError:
                        year_only = 0

                    if metrics_year and year_only >= self.min_valid_year:
                        metrics = metrics_year
                        period = period_year

                if not metrics or not period:
                    return {}

                return {
                    "ticker": ticker_clean,
                    "sector": sector,
                    "period": period,
                    "updated_at": datetime.now().strftime("%Y-%m-%d"),
                    **metrics,
                }

            except Exception as e:
                err_str = str(e).lower()
                if "rate limit" in err_str or "giới hạn" in err_str or "exceeded" in err_str:
                    wait_time = 50
                    print(f"\n Chạm Rate Limit khi xử lý {ticker_clean}. Tự động tạm dừng {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return {}

        return {}

    def run_and_save(
        self,
        symbols_path: str = "data/symbols.csv",
        output_path: str = "data/financial_data.json",
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Thu thập dữ liệu toàn bộ mã có cơ chế lưu checkpoint:
        - Đã cào mã nào thì lưu ngay mã đó vào file JSON.
        - Chạy lại không bị cào trùng mã đã lưu.
        """
        if not os.path.exists(symbols_path):
            print(f"Không tìm thấy file {symbols_path}")
            return {}

        ticker_info = {}
        with open(symbols_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                parts = line.strip().split(",")
                if parts and parts[0]:
                    sym = parts[0].strip().upper()
                    if sym not in ["SYMBOL", "TICKER", "STOCK", "MÃ CP", ""]:
                        sector = parts[1].strip() if len(parts) > 1 else "Chưa phân loại"
                        ticker_info[sym] = sector

        tickers = list(ticker_info.keys())
        if limit:
            tickers = tickers[:limit]

        # Đọc dữ liệu cũ nếu file json đã tồn tại (tránh cào lại)
        results: Dict[str, Any] = {}
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    results = json.load(f)
                print(f" Đã tìm thấy {len(results)} mã đã thu thập từ trước trong {output_path}.")
            except Exception:
                results = {}

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        print(f"Bắt đầu quy trình thu thập dữ liệu (Tổng: {len(tickers)} mã)...")
        count = 0
        for sym in tickers:
            # Bỏ qua nếu mã này đã được cào thành công trước đó
            if sym in results:
                continue

            count += 1
            data = self.get_clean_financials(sym, sector=ticker_info.get(sym, "Chưa phân loại"))

            if data:
                results[sym] = data
                print(
                    f" [{len(results)}/{len(tickers)}] {sym} ({data['period']}) | "
                    f"EPS: {data['eps']} | P/E: {data['pe']} | ROE: {data['roe']}% | YoY: {data['eps_growth_yoy']}%"
                )
            else:
                print(f" [-] {sym}: Bỏ qua (Không có BCTC từ {self.min_valid_year})")

            # Lưu lũy tiến sau mỗi 5 mã thành công
            if count % 5 == 0:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)

            # Giãn cách để giữ tần suất an toàn với API Guest
            time.sleep(self.request_delay)

        # Lưu lần cuối toàn bộ danh sách
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)

        print(f"\n Hoàn tất toàn bộ quy trình! Đã lưu {len(results)} mã vào: {output_path}")
        return results


if __name__ == "__main__":
    fetcher = FinancialDataFetcher(request_delay=3.2)
    # limit=None để quét trọn vẹn 1.523 mã
    fetcher.run_and_save(symbols_path="data/symbols.csv", output_path="data/financial_data.json", limit=None)