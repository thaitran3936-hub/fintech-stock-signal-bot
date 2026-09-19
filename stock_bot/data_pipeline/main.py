from stock_bot.data_pipeline.collectors.vietcap_collector import VietcapCollector
from stock_bot.data_pipeline.processing.vietcap_parser import VietcapParser
from stock_bot.data_pipeline.processing.market_validator import MarketValidator
from stock_bot.data_pipeline.storage.market_store import MarketStore
from stock_bot.data_pipeline.data_service import DataService


def handle_realtime_data(
    data_type,
    data,
    market_store
):

    print(
        f"\n[REALTIME] Nhận dữ liệu: {data_type}"
    )

    # ==========================================
    # CHỈ XỬ LÝ GIÁ KHỚP
    # ==========================================

    if data_type != "match_price":

        print(
            f"[REALTIME] Bỏ qua loại dữ liệu: "
            f"{data_type}"
        )

        return

    # ==========================================
    # 1. PARSE
    # ==========================================

    parsed_data = VietcapParser.parse_match_price(
        data
    )

    if not parsed_data:

        print(
            "[REALTIME] Không có dữ liệu "
            "match_price hợp lệ."
        )

        return

    valid_count = 0
    invalid_count = 0

    # ==========================================
    # 2. VALIDATE
    # ==========================================

    for item in parsed_data:

        if not MarketValidator.validate(item):

            invalid_count += 1
            continue

        # ======================================
        # 3. LƯU MARKET STORE
        # ======================================

        market_store.save(
            symbol=item["symbol"],
            price=item["price"],
            volume=item["volume"],
            data_type="match_price"
        )

        valid_count += 1

    # ==========================================
    # 4. LOG KẾT QUẢ
    # ==========================================

    if valid_count > 0:

        print(
            f"[REALTIME] Đã lưu "
            f"{valid_count} bản ghi."
        )

    if invalid_count > 0:

        print(
            f"[REALTIME] Bỏ qua "
            f"{invalid_count} bản ghi không hợp lệ."
        )


def main():

    print(
        "=========================================="
    )

    print(
        "      DATA PIPELINE - STOCK BOT"
    )

    print(
        "=========================================="
    )

    # ==========================================
    # 1. TẠO MARKET STORE
    # ==========================================

    market_store = MarketStore()

    # ==========================================
    # 2. TẠO DATA SERVICE
    # ==========================================

    data_service = DataService(
        market_store=market_store
    )

    # ==========================================
    # 3. TẠO VIETCAP COLLECTOR
    # ==========================================

    collector = VietcapCollector(
        on_data=lambda data_type, data:
        handle_realtime_data(
            data_type,
            data,
            market_store
        )
    )

    # ==========================================
    # 4. CHẠY COLLECTOR
    # ==========================================

    try:

        collector.run()

    except KeyboardInterrupt:

        print(
            "\n[MAIN] Người dùng dừng hệ thống."
        )

    except Exception as e:

        print(
            f"\n[MAIN ERROR] {e}"
        )

    finally:

        # ======================================
        # ĐÓNG COLLECTOR
        # ======================================

        collector.stop()

        # ======================================
        # ĐÓNG DATASERVICE
        # ======================================

        data_service.close()

        # ======================================
        # ĐÓNG MARKET STORE
        # ======================================

        market_store.close()

        print(
            "[MAIN] Data Pipeline đã đóng."
        )


if __name__ == "__main__":

    main()