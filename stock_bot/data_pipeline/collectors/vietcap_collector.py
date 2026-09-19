import sys
from pathlib import Path
import json
import logging
import time
from typing import Callable, Optional

import requests
import socketio


# =========================
# LOGGER
# =========================
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# =========================
# PROJECT ROOT
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))


# =========================
# MARKET STORE
# =========================
from stock_bot.data_pipeline.storage.market_store import MarketStore
from stock_bot.data_pipeline.processing.vietcap_parser import VietcapParser
from stock_bot.data_pipeline.processing.market_validator import MarketValidator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


class VietcapCollector:
    BASE_URL = "https://trading.vietcap.com.vn"
    SOCKET_PATH = "ws/price/socket.io"

    # Các chỉ số thị trường
    INDEX_SYMBOLS = [
        "VNINDEX",
        "VN30",
        "VN100",
        "HNXIndex",
        "HNX30",
        "HNXUpcomIndex",
    ]

    def __init__(
        self,
        on_data: Optional[Callable] = None
    ):
        self.on_data = on_data

        self.symbols = []
        self.symbols_by_exchange = {
            "HOSE": [],
            "HNX": [],
            "UPCOM": []
        }

        self.sio = socketio.Client(
            logger=False,
            engineio_logger=False,
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=2,
            reconnection_delay_max=10,
        )

        self.connected = False
        self.running = False

        self._register_events()

    # =========================================================
    # 1. LẤY DANH SÁCH MÃ TỪ VIETCAP
    # =========================================================

    def load_symbols(self):
        """
        Lấy toàn bộ mã cổ phiếu từ Vietcap theo 3 sàn:
        HOSE / HNX / UPCOM
        """
        exchanges = ["HOSE", "HNX", "UPCOM"]
        all_symbols = []

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://trading.vietcap.com.vn/",
            "Origin": "https://trading.vietcap.com.vn"
        }

        for exchange in exchanges:
            try:
                url = (
                    f"{self.BASE_URL}"
                    "/api/price/v1/w/priceboard/tickers/price/group"
                )

                response = requests.post(
                    url,
                    json={"group": exchange},
                    headers=headers,
                    timeout=10
                )

                logger.info(
                    f"Vietcap {exchange}: HTTP {response.status_code}"
                )

                if response.status_code != 200:
                    logger.error(
                        f"Không lấy được danh sách {exchange}"
                    )
                    continue

                data = response.json()
                symbols = self._extract_symbols(data)

                if not symbols:
                    # In mẫu dữ liệu nhận được để kiểm tra cấu trúc nếu bị 0 mã
                    sample_str = str(data)[:200]
                    logger.warning(
                        f"Vietcap trả về 0 mã cho {exchange}. Sample JSON: {sample_str}"
                    )
                    continue

                self.symbols_by_exchange[exchange] = symbols
                all_symbols.extend(symbols)

                logger.info(
                    f"{exchange}: {len(symbols)} mã"
                )

            except Exception as e:
                logger.error(
                    f"Lỗi lấy danh sách {exchange}: {e}"
                )

        # Loại mã trùng
        self.symbols = list(dict.fromkeys(all_symbols))

        logger.info(
            f"TỔNG CỘNG: {len(self.symbols)} mã"
        )

        return self.symbols

    # =========================================================
    # 2. TÌM SYMBOL TRONG RESPONSE VIETCAP (ĐÃ CẬP NHẬT)
    # =========================================================

    def _extract_symbols(self, data):
        """
        Bắt linh hoạt mã cổ phiếu từ mọi cấu trúc JSON của Vietcap:
        - Dict có trường: 's', 'sym', 'symbol', 'ticker', 'code', 'stocksymbol'
        - Mảng chuỗi trực tiếp: ["ACB", "SSI", ...]
        """
        symbols = []

        def recursive_find(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_lower = str(key).lower()
                    # Mở rộng các trường khóa ngắn thường được dùng trong bảng giá Vietcap
                    if key_lower in [
                        "s", "sym", "symbol", "ticker", "code", 
                        "stocksymbol", "secsymbol", "tickersymbol"
                    ]:
                        if isinstance(value, str):
                            symbol = value.strip().upper()
                            if symbol and symbol.isalnum() and 2 <= len(symbol) <= 10:
                                symbols.append(symbol)
                    recursive_find(value)

            elif isinstance(obj, list):
                for item in obj:
                    recursive_find(item)

            elif isinstance(obj, str):
                # Xử lý khi response trả về mảng chuỗi mã trực tiếp
                symbol = obj.strip().upper()
                if symbol and symbol.isalnum() and 3 <= len(symbol) <= 6:
                    symbols.append(symbol)

        recursive_find(data)
        return list(dict.fromkeys(symbols))

    # =========================================================
    # 3. SOCKET.IO EVENTS
    # =========================================================

    def _register_events(self):
        @self.sio.event
        def connect():
            self.connected = True
            logger.info("✓ Đã kết nối Vietcap WebSocket")
            self._subscribe()

        @self.sio.event
        def disconnect():
            self.connected = False
            logger.warning("⚠ Vietcap WebSocket bị ngắt kết nối")

        @self.sio.on("w-match-price")
        def on_match_price(data):
            self._handle_data("match_price", data)

        @self.sio.on("w-bid-ask")
        def on_bid_ask(data):
            self._handle_data("bid_ask", data)

        @self.sio.on("index")
        def on_index(data):
            self._handle_data("index", data)

    # =========================================================
    # 4. SUBSCRIBE REALTIME
    # =========================================================

    def _subscribe(self):
        if not self.connected:
            return

        if not self.symbols:
            logger.warning("Chưa có danh sách mã để subscribe")
            return

        index_payload = json.dumps(
            {"symbols": self.INDEX_SYMBOLS},
            separators=(",", ":")
        )
        self.sio.emit("index", index_payload)

        batch_size = 100
        batches = [
            self.symbols[i:i + batch_size]
            for i in range(0, len(self.symbols), batch_size)
        ]

        logger.info(
            f"Subscribe {len(self.symbols)} mã → {len(batches)} nhóm"
        )

        for batch_number, batch in enumerate(batches, start=1):
            payload = json.dumps(
                {"symbols": batch},
                separators=(",", ":")
            )

            self.sio.emit("w-match-price", payload)
            self.sio.emit("w-bid-ask", payload)

            logger.info(
                f"Đã subscribe nhóm {batch_number}/{len(batches)} ({len(batch)} mã)"
            )
            time.sleep(0.1)

    # =========================================================
    # 5. XỬ LÝ DỮ LIỆU REALTIME
    # =========================================================

    def _handle_data(self, data_type, data):
        logger.info(f"Đã nhận dữ liệu Vietcap: {data_type}")

        # In gói dữ liệu đầu tiên để kiểm tra
        if not hasattr(self, "_received_first_data"):
            self._received_first_data = True

            print("\n========== DỮ LIỆU VIETCAP MẪU ==========")
            print(data)
            print("==========================================\n")

        try:
            # Gửi dữ liệu ra ngoài cho hàm receive_data()
            if self.on_data:
                self.on_data(data_type, data)

        except Exception as e:
            logger.error(
                f"Lỗi xử lý dữ liệu {data_type}: {e}"
            )

    
    # =========================================================
    # 6. KẾT NỐI
    # =========================================================

    def connect(self):
        """Kết nối tới Vietcap WebSocket."""
        self.sio.connect(
            self.BASE_URL,
            socketio_path=self.SOCKET_PATH,
            transports=["websocket"],
            wait_timeout=10
        )


    # =========================================================
    # 7. CHẠY COLLECTOR
    # =========================================================

    def run(self):
        self.running = True

        self.load_symbols()

        if not self.symbols:
            logger.error("Không có mã cổ phiếu nào.")
            return

        while self.running:

            try:
                logger.info("Đang kết nối Vietcap WebSocket...")

                self.connect()

                # Giữ collector chạy
                while self.running:
                    time.sleep(1)

            except KeyboardInterrupt:
                logger.info("Đang dừng collector...")
                self.running = False

            except Exception as e:
                logger.error(f"Lỗi kết nối Vietcap: {e}")

            finally:
                if self.running:
                    logger.warning(
                        "⚠ Mất kết nối Vietcap. "
                        "Sẽ kết nối lại sau 3 giây..."
                    )

                    try:
                        if self.sio.connected:
                            self.sio.disconnect()
                    except Exception:
                        pass

                    time.sleep(3)

        self.stop()


    # =========================================================
    # 8. DỪNG
    # =========================================================

    def stop(self):
        self.running = False

        try:
            if self.sio.connected:
                self.sio.disconnect()
        except Exception:
            pass

        self.connected = False

        logger.info("Vietcap Collector đã dừng.")


