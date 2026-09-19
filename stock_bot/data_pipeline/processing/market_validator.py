import logging
import math


logger = logging.getLogger(__name__)


class MarketValidator:

    # =========================================================
    # KIỂM TRA MỘT BẢN GHI DỮ LIỆU
    # =========================================================

    @staticmethod
    def validate(record):

        # -----------------------------------------
        # 1. Kiểm tra record có phải dictionary
        # -----------------------------------------

        if not isinstance(record, dict):
            logger.warning(
                "Dữ liệu không phải dictionary."
            )
            return False

        # -----------------------------------------
        # 2. Kiểm tra mã cổ phiếu
        # -----------------------------------------

        symbol = record.get("symbol")

        if not symbol:
            logger.warning(
                "Dữ liệu thiếu mã cổ phiếu."
            )
            return False

        if not isinstance(symbol, str):
            logger.warning(
                f"Mã cổ phiếu không hợp lệ: {symbol}"
            )
            return False

        symbol = symbol.strip().upper()

        if not symbol:
            logger.warning(
                "Mã cổ phiếu rỗng."
            )
            return False

        # -----------------------------------------
        # 3. Kiểm tra giá
        # -----------------------------------------

        price = record.get("price")

        if price is not None:

            if not MarketValidator._is_valid_number(price):

                logger.warning(
                    f"{symbol}: giá không hợp lệ: {price}"
                )

                return False

            if price <= 0:

                logger.warning(
                    f"{symbol}: giá phải lớn hơn 0: {price}"
                )

                return False

        # -----------------------------------------
        # 4. Kiểm tra khối lượng
        # -----------------------------------------

        volume = record.get("volume")

        if volume is not None:

            if not MarketValidator._is_valid_number(volume):

                logger.warning(
                    f"{symbol}: khối lượng không hợp lệ: {volume}"
                )

                return False

            if volume < 0:

                logger.warning(
                    f"{symbol}: khối lượng không được âm: {volume}"
                )

                return False

        # -----------------------------------------
        # 5. Kiểm tra BID
        # -----------------------------------------

        bid = record.get("bid")

        if bid is not None:

            if not MarketValidator._is_valid_number(bid):

                logger.warning(
                    f"{symbol}: bid không hợp lệ: {bid}"
                )

                return False

            if bid < 0:

                logger.warning(
                    f"{symbol}: bid không được âm: {bid}"
                )

                return False

        # -----------------------------------------
        # 6. Kiểm tra ASK
        # -----------------------------------------

        ask = record.get("ask")

        if ask is not None:

            if not MarketValidator._is_valid_number(ask):

                logger.warning(
                    f"{symbol}: ask không hợp lệ: {ask}"
                )

                return False

            if ask < 0:

                logger.warning(
                    f"{symbol}: ask không được âm: {ask}"
                )

                return False

        # -----------------------------------------
        # 7. Kiểm tra quan hệ BID / ASK
        # -----------------------------------------

        if (
            bid is not None
            and ask is not None
            and bid > ask
        ):

            logger.warning(
                f"{symbol}: bid ({bid}) "
                f"lớn hơn ask ({ask})."
            )

            return False

        # -----------------------------------------
        # Dữ liệu hợp lệ
        # -----------------------------------------

        return True

    # =========================================================
    # KIỂM TRA SỐ
    # =========================================================

    @staticmethod
    def _is_valid_number(value):

        try:

            number = float(value)

            return math.isfinite(number)

        except (TypeError, ValueError):

            return False