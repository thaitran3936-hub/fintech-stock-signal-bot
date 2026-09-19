import pandas as pd

from stock_bot.data_pipeline.storage.historical_store import HistoricalStore
from stock_bot.data_pipeline.storage.market_store import MarketStore


class DataService:

    def __init__(self, market_store=None):

        self.historical_store = HistoricalStore()

        # Nhận MarketStore từ bên ngoài
        self.market_store = market_store

    # ==========================================
    # LẤY DỮ LIỆU LỊCH SỬ
    # ==========================================

    def get_history(self, symbol):

        symbol = symbol.upper()

        data = self.historical_store.get_history(
            symbol
        )

        columns = [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        df = pd.DataFrame(
            data,
            columns=columns
        )

        return df

    # ==========================================
    # LẤY DỮ LIỆU REALTIME MỚI NHẤT
    # ==========================================

    def get_latest(self, symbol):

        symbol = symbol.upper()

        if self.market_store is None:
            return None

        data = self.market_store.get_latest(
            symbol
        )

        return data

    # ==========================================
    # ĐÓNG KẾT NỐI
    # ==========================================

    def close(self):

        self.historical_store.close()


# ==========================================
# TEST DATASERVICE
# ==========================================

if __name__ == "__main__":

    # Tạo MarketStore
    market_store = MarketStore()

    # Truyền MarketStore vào DataService
    service = DataService(
        market_store=market_store
    )

    try:

        # ======================================
        # TEST DỮ LIỆU LỊCH SỬ
        # ======================================

        df = service.get_history("FPT")

        print(
            "Số dòng dữ liệu FPT:",
            len(df)
        )

        print("\n5 dòng đầu:")

        print(
            df.head()
        )

        # ======================================
        # TEST REALTIME GIẢ
        # ======================================

        market_store.save(
            symbol="FPT",
            price=126.50,
            volume=1500000,
            bid=126.40,
            ask=126.50,
            data_type="match_price"
        )

        latest = service.get_latest("FPT")

        print("\nDữ liệu realtime FPT:")

        print(
            latest
        )

    finally:

        service.close()

        market_store.close()