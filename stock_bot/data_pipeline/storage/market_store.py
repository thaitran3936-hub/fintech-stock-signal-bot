import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class MarketStore:

    def __init__(self, db_path="data/market_data.db"):

        self.db_path = db_path

        Path(db_path).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False
        )

        self.cursor = self.conn.cursor()

        # Khóa để tránh nhiều thread
        # cùng lúc sửa dữ liệu
        self.lock = threading.Lock()

        # ==========================================
        # LƯU DỮ LIỆU MỚI NHẤT TRONG RAM
        # ==========================================

        self.latest = {}

        # Hàng đợi dữ liệu chờ ghi xuống SQLite
        self.queue = []

        # ==========================================
        # TỰ ĐỘNG FLUSH
        # ==========================================

        # Cứ 3 giây ghi queue xuống SQLite
        self.flush_interval = 3

        # Trạng thái của thread tự động flush
        self.flush_running = True

        # ==========================================
        # TẠO DATABASE
        # ==========================================

        self._create_database()

        # ==========================================
        # TẠO THREAD TỰ ĐỘNG FLUSH
        # ==========================================

        self.flush_thread = threading.Thread(
            target=self._auto_flush,
            daemon=True
        )

        self.flush_thread.start()

    # =========================================================
    # 1. TẠO DATABASE
    # =========================================================

    def _create_database(self):

        with self.lock:

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    price REAL,
                    volume REAL,
                    bid REAL,
                    ask REAL,
                    data_type TEXT,
                    timestamp TEXT
                )
            """)

            # Index giúp tìm dữ liệu theo mã nhanh hơn
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_market_symbol
                ON market_data(symbol)
            """)

            # Index giúp tìm dữ liệu theo thời gian nhanh hơn
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_market_timestamp
                ON market_data(timestamp)
            """)

            self.conn.commit()

    # =========================================================
    # 2. THÊM DỮ LIỆU VÀO RAM
    # =========================================================

    def save(
        self,
        symbol,
        price=None,
        volume=None,
        bid=None,
        ask=None,
        data_type=None
    ):

        # Không lưu nếu không có mã cổ phiếu
        if not symbol:
            return

        timestamp = datetime.now().isoformat()

        record = {
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "bid": bid,
            "ask": ask,
            "data_type": data_type,
            "timestamp": timestamp
        }

        with self.lock:

            # Cập nhật dữ liệu mới nhất trong RAM
            self.latest[symbol] = record

            # Đưa dữ liệu vào hàng đợi
            self.queue.append(record)

    # =========================================================
    # 3. GHI HÀNG ĐỢI XUỐNG SQLITE
    # =========================================================

    def flush(self):

        with self.lock:

            # Nếu không có dữ liệu thì không làm gì
            if not self.queue:
                return 0

            # Lấy toàn bộ dữ liệu đang chờ
            records = self.queue

            # Tạo queue mới để tiếp tục nhận dữ liệu
            self.queue = []

            # Ghi nhiều bản ghi cùng lúc
            self.cursor.executemany(
                """
                INSERT INTO market_data
                (
                    symbol,
                    price,
                    volume,
                    bid,
                    ask,
                    data_type,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["symbol"],
                        r["price"],
                        r["volume"],
                        r["bid"],
                        r["ask"],
                        r["data_type"],
                        r["timestamp"]
                    )
                    for r in records
                ]
            )

            # Xác nhận giao dịch
            self.conn.commit()

            return len(records)

    # =========================================================
    # 4. TỰ ĐỘNG FLUSH MỖI 3 GIÂY
    # =========================================================

    def _auto_flush(self):

        while self.flush_running:

            # Chờ 3 giây
            threading.Event().wait(
                self.flush_interval
            )

            try:

                # Ghi dữ liệu trong queue xuống SQLite
                count = self.flush()

                if count > 0:

                    print(
                        f"[STORE] Đã ghi "
                        f"{count} bản ghi vào SQLite"
                    )

            except Exception as e:

                # Không để lỗi SQLite
                # làm chết thread
                print(
                    f"[STORE ERROR] "
                    f"Lỗi ghi dữ liệu: {e}"
                )

    # =========================================================
    # 5. LẤY GIÁ MỚI NHẤT CỦA 1 MÃ
    # =========================================================

    def get_latest(self, symbol):

        with self.lock:

            return self.latest.get(symbol)

    # =========================================================
    # 6. LẤY GIÁ MỚI NHẤT CỦA TẤT CẢ MÃ
    # =========================================================

    def get_all_latest(self):

        with self.lock:

            return self.latest.copy()

    # =========================================================
    # 7. ĐÓNG DATABASE
    # =========================================================

    def close(self):

        # Dừng thread tự động flush
        self.flush_running = False

        # Ghi nốt dữ liệu còn trong queue
        self.flush()

        # Đóng database
        with self.lock:

            self.conn.close()

        print("[STORE] MarketStore đã đóng.")