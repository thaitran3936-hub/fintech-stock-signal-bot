import logging


logger = logging.getLogger(__name__)


class VietcapParser:

    @staticmethod
    def parse_match_price(data):
        """
        Chuẩn hóa dữ liệu giá khớp từ Vietcap.

        Kết quả chuẩn:
        {
            "symbol": "...",
            "price": ...,
            "volume": ...
        }
        """

        try:

            # -------------------------------------------------
            # Trường hợp dữ liệu là danh sách
            # -------------------------------------------------

            if isinstance(data, list):

                results = []

                for item in data:

                    parsed = VietcapParser._parse_one(item)

                    if parsed:
                        results.append(parsed)

                return results

            # -------------------------------------------------
            # Trường hợp dữ liệu là một dict
            # -------------------------------------------------

            if isinstance(data, dict):

                parsed = VietcapParser._parse_one(data)

                if parsed:
                    return [parsed]

            logger.warning(
                "Không nhận diện được cấu trúc match_price"
            )

            return []

        except Exception as e:

            logger.error(
                f"Lỗi parse match_price: {e}"
            )

            return []

    @staticmethod
    def _parse_one(item):

        if not isinstance(item, dict):
            return None

        # Các tên trường có thể gặp
        symbol = (
            item.get("symbol")
            or item.get("sym")
            or item.get("s")
            or item.get("ticker")
        )

        price = (
            item.get("price")
            or item.get("p")
            or item.get("lastPrice")
            or item.get("matchPrice")
        )

        volume = (
            item.get("volume")
            or item.get("v")
            or item.get("matchVolume")
        )

        if not symbol:
            return None

        return {
            "symbol": str(symbol).upper(),
            "price": price,
            "volume": volume
        }