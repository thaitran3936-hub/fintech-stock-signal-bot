
from datetime import datetime, timedelta
from pathlib import Path
import time

import pandas as pd

from stock_bot.data_pipeline.collectors.historical_collector import (
    HistoricalCollector
)
from stock_bot.data_pipeline.storage.historical_store import (
    HistoricalStore
)


class HistoricalUpdater:

    def __init__(self):

        self.collector = HistoricalCollector()
        self.store = HistoricalStore()

        # ==================================================
        # GIỚI HẠN API
        # ==================================================

        # 4 giây / request
        # ≈ 15 requests / phút
        # thấp hơn giới hạn 20 requests / phút
        self.sleep_seconds = 4

        # Khi API báo vượt giới hạn
        self.rate_limit_wait = 60

        # Số lần thử lại khi gặp rate limit
        self.max_retry = 3

        # Danh sách mã bị lỗi
        self.failed_symbols = []

    # ======================================================
    # KIỂM TRA CÓ PHẢI CUỐI TUẦN KHÔNG
    # ======================================================

    def is_weekend(self):

        today = datetime.now()

        # Monday = 0
        # Tuesday = 1
        # Wednesday = 2
        # Thursday = 3
        # Friday = 4
        # Saturday = 5
        # Sunday = 6

        return today.weekday() >= 5

    # ======================================================
    # KIỂM TRA CÓ PHẢI LỖI RATE LIMIT KHÔNG
    # ======================================================

    def is_rate_limit_error(self, error):

        message = str(error).lower()

        keywords = [
            "rate limit",
            "request limit",
            "maximum api request",
            "20 requests",
            "too many requests",
            "429",
            "wait to retry",
            "giới hạn tối đa số lượt yêu cầu",
            "vượt giới hạn"
        ]

        return any(
            keyword in message
            for keyword in keywords
        )

    # ======================================================
    # CẬP NHẬT 1 MÃ
    # ======================================================

    def update_symbol(self, symbol):

        symbol = symbol.upper()

        print(
            f"\n===== CẬP NHẬT {symbol} ====="
        )

        try:

            # ==================================================
            # KIỂM TRA CUỐI TUẦN
            # ==================================================

            if self.is_weekend():

                print(
                    f"[UPDATE] {symbol}: "
                    f"hôm nay là cuối tuần, "
                    f"thị trường không giao dịch."
                )

                return True

            # ==================================================
            # KIỂM TRA DATABASE
            # ==================================================

            last_date = self.store.get_last_date(
                symbol
            )

            # ==================================================
            # MÃ CHƯA CÓ DỮ LIỆU
            # ==================================================

            if last_date is None:

                print(
                    f"[UPDATE] {symbol}: "
                    f"chưa có dữ liệu lịch sử."
                )

                start_date = "2025-01-01"

                end_date = datetime.now().strftime(
                    "%Y-%m-%d"
                )

                print(
                    f"[UPDATE] Tải lịch sử ban đầu:"
                    f"\n  Từ: {start_date}"
                    f"\n  Đến: {end_date}"
                )

            # ==================================================
            # MÃ ĐÃ CÓ DỮ LIỆU
            # ==================================================

            else:

                print(
                    f"[UPDATE] {symbol}: "
                    f"ngày cuối trong DB = {last_date}"
                )

                start_date = (
                    datetime.strptime(
                        last_date,
                        "%Y-%m-%d"
                    )
                    + timedelta(days=1)
                ).strftime("%Y-%m-%d")

                end_date = datetime.now().strftime(
                    "%Y-%m-%d"
                )

                # ==========================================
                # ĐÃ CẬP NHẬT TỚI HÔM NAY
                # ==========================================

                if start_date > end_date:

                    print(
                        f"[UPDATE] {symbol}: "
                        f"đã cập nhật tới hôm nay."
                    )

                    return True

                print(
                    f"[UPDATE] Lấy dữ liệu mới:"
                    f"\n  Từ: {start_date}"
                    f"\n  Đến: {end_date}"
                )

            # ==================================================
            # THỬ GỌI API
            # ==================================================

            for attempt in range(
                1,
                self.max_retry + 1
            ):

                try:

                    df = self.collector.get_history(
                        symbol=symbol,
                        start=start_date,
                        end=end_date
                    )

                    # ==========================================
                    # KHÔNG CÓ DỮ LIỆU
                    # ==========================================

                    if df is None or df.empty:

                        print(
                            f"[UPDATE] {symbol}: "
                            f"không có dữ liệu mới."
                        )

                        return True

                    # ==========================================
                    # LƯU SQLITE
                    # ==========================================

                    saved = self.store.save(
                        df
                    )

                    print(
                        f"[UPDATE] {symbol}: "
                        f"đã lưu/thêm {saved} dòng."
                    )

                    return True

                except Exception as e:

                    # ==========================================
                    # RATE LIMIT
                    # ==========================================

                    if self.is_rate_limit_error(e):

                        print(
                            f"[RATE LIMIT] {symbol}: "
                            f"đã vượt giới hạn API."
                        )

                        print(
                            f"[RATE LIMIT] "
                            f"Chờ {self.rate_limit_wait} giây..."
                        )

                        time.sleep(
                            self.rate_limit_wait
                        )

                        print(
                            f"[RATE LIMIT] "
                            f"Thử lại {symbol} "
                            f"({attempt}/{self.max_retry})..."
                        )

                        continue

                    # ==========================================
                    # LỖI KHÁC
                    # ==========================================

                    print(
                        f"[UPDATE ERROR] "
                        f"{symbol}: {e}"
                    )

                    self.failed_symbols.append(
                        symbol
                    )

                    return False

            # ==================================================
            # ĐÃ THỬ LẠI NHƯNG VẪN RATE LIMIT
            # ==================================================

            print(
                f"[UPDATE ERROR] {symbol}: "
                f"vượt giới hạn API sau "
                f"{self.max_retry} lần thử."
            )

            self.failed_symbols.append(
                symbol
            )

            return False

        except Exception as e:

            print(
                f"[UPDATE ERROR] "
                f"{symbol}: {e}"
            )

            self.failed_symbols.append(
                symbol
            )

            return False

    # ======================================================
    # CẬP NHẬT TOÀN BỘ MÃ
    # ======================================================

    def update_all(self):

        # ==================================================
        # ĐƯỜNG DẪN DANH SÁCH MÃ
        # ==================================================

        symbols_path = Path(
            "data/symbols.csv"
        )

        if not symbols_path.exists():

            print(
                "[UPDATE ERROR] "
                "Không tìm thấy data/symbols.csv"
            )

            return

        # ==================================================
        # ĐỌC DANH SÁCH MÃ
        # ==================================================

        try:

            df_symbols = pd.read_csv(
                symbols_path
            )

        except Exception as e:

            print(
                f"[UPDATE ERROR] "
                f"Không đọc được symbols.csv: {e}"
            )

            return

        # ==================================================
        # KIỂM TRA CỘT SYMBOL
        # ==================================================

        if "symbol" not in df_symbols.columns:

            print(
                "[UPDATE ERROR] "
                "File symbols.csv không có cột symbol."
            )

            return

        # ==================================================
        # CHUẨN HÓA DANH SÁCH
        # ==================================================

        symbols = (
            df_symbols["symbol"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .drop_duplicates()
            .tolist()
        )

        total = len(symbols)

        print(
            "\n=========================================="
        )

        print(
            "[UPDATE] BẮT ĐẦU CẬP NHẬT LỊCH SỬ"
        )

        print(
            f"[UPDATE] Tổng số mã: {total}"
        )

        print(
            f"[UPDATE] Nghỉ giữa mỗi mã: "
            f"{self.sleep_seconds} giây"
        )

        # ==================================================
        # KIỂM TRA CUỐI TUẦN TRƯỚC KHI CHẠY TOÀN BỘ
        # ==================================================

        if self.is_weekend():

            print(
                "[UPDATE] Hôm nay là cuối tuần."
            )

            print(
                "[UPDATE] Thị trường không giao dịch."
            )

            print(
                "[UPDATE] Không gọi API."
            )

            print(
                "=========================================="
            )

            return

        print(
            "=========================================="
        )

        success = 0
        failed = 0

        # ==================================================
        # CẬP NHẬT TỪNG MÃ
        # ==================================================

        for index, symbol in enumerate(
            symbols,
            start=1
        ):

            print(
                f"\n[{index}/{total}]"
            )

            result = self.update_symbol(
                symbol
            )

            if result:

                success += 1

            else:

                failed += 1

            # ==================================================
            # NGHỈ GIỮA CÁC REQUEST
            # ==================================================

            if index < total:

                print(
                    f"[UPDATE] Nghỉ "
                    f"{self.sleep_seconds} giây..."
                )

                time.sleep(
                    self.sleep_seconds
                )

        # ==================================================
        # LƯU DANH SÁCH MÃ LỖI
        # ==================================================

        if self.failed_symbols:

            failed_path = Path(
                "data/history_failed_symbols.csv"
            )

            failed_df = pd.DataFrame(
                {
                    "symbol": self.failed_symbols
                }
            )

            failed_df.drop_duplicates(
                inplace=True
            )

            failed_df.to_csv(
                failed_path,
                index=False
            )

            print(
                f"\n[UPDATE] Đã lưu danh sách "
                f"{len(failed_df)} mã lỗi:"
            )

            print(
                f"         {failed_path}"
            )

        # ==================================================
        # TỔNG KẾT
        # ==================================================

        print(
            "\n=========================================="
        )

        print(
            "[UPDATE] HOÀN THÀNH"
        )

        print(
            f"[UPDATE] Tổng số mã: {total}"
        )

        print(
            f"[UPDATE] Thành công: {success}"
        )

        print(
            f"[UPDATE] Lỗi: {failed}"
        )

        print(
            "=========================================="
        )

    # ======================================================
    # ĐÓNG
    # ======================================================

    def close(self):

        self.store.close()

        print(
            "[UPDATE] HistoricalUpdater đã đóng."
        )


# ==========================================================
# MAIN
# ==========================================================

if __name__ == "__main__":

    updater = HistoricalUpdater()

    try:

        updater.update_all()

    except KeyboardInterrupt:

        print(
            "\n[UPDATE] Người dùng dừng chương trình."
        )

    finally:

        updater.close()

