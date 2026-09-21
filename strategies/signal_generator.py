"""
strategies/signal_generator.py — Bộ điều phối & Định dạng Tín hiệu
Nhiệm vụ:
  1. Nhận danh sách sự kiện (BUY / WATCH / SELL) từ ta_strategy.py.
  2. Lọc trùng lặp (Deduplication / Chống Spam): Không gửi lại cùng 1 loại tín hiệu
     cho cùng 1 mã trong khoảng thời gian cấu hình (VD: trong vòng 1 ngày hoặc 4 tiếng).
  3. Format tin nhắn Telegram HTML đẹp mắt, phân biệt rõ BUY (🟢), WATCH (🟡), SELL (🔴).
  4. Lưu lịch sử các tín hiệu đã phát vào data/signal_history.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_SIGNAL_HISTORY_PATH = "data/signal_history.json"
DEDUP_COOLDOWN_HOURS = 4  # Tránh spam: Cùng 1 mã + cùng signal_type thì cách nhau ít nhất 4 tiếng mới báo lại


# ----------------------------------------------------------------------------
# 1. Quản lý Lịch sử & Anti-Spam (Deduplication)
# ----------------------------------------------------------------------------
def load_signal_history(path: str | Path = DEFAULT_SIGNAL_HISTORY_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_signal_history(history: list[dict], path: str | Path = DEFAULT_SIGNAL_HISTORY_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def is_duplicate_signal(event: dict, history: list[dict], cooldown_hours: int = DEDUP_COOLDOWN_HOURS) -> bool:
    """Kiểm tra xem tín hiệu này vừa mới gửi cách đây không lâu hay chưa."""
    ticker = event.get("ticker")
    signal_type = event.get("signal_type")
    event_time_str = event.get("time")

    if not ticker or not signal_type or not event_time_str:
        return False

    try:
        current_time = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        current_time = datetime.now()

    for item in reversed(history):
        if item.get("ticker") == ticker and item.get("signal_type") == signal_type:
            try:
                prev_time = datetime.strptime(item.get("time"), "%Y-%m-%d %H:%M:%S")
                if current_time - prev_time < timedelta(hours=cooldown_hours):
                    return True  # Bị trùng trong khoảng thời gian cooldown -> Bỏ qua
            except ValueError:
                continue
    return False


# ----------------------------------------------------------------------------
# 2. Template Định dạng Tin nhắn Telegram (HTML Format)
# ----------------------------------------------------------------------------
def format_telegram_message(event: dict) -> str:
    """Biến đổi dict event thành mẫu tin nhắn Telegram trực quan."""
    ticker = event.get("ticker", "N/A")
    signal_type = event.get("signal_type", "INFO")
    price = f"{event.get('price', 0):,.1f}"
    time_str = event.get("time", "")
    ta = event.get("ta_criteria", {})
    fa = event.get("fa_criteria", {})

    roe_str = f"{fa.get('ROE')}%" if fa.get("ROE") else "N/A"
    eps_str = f"{fa.get('EPS_Growth')}%" if fa.get("EPS_Growth") else "N/A"

    if signal_type == "BUY":
        stop_loss_str = f"{event.get('stop_loss', 0):,.1f}"
        return f"""🟢 <b>[TÍN HIỆU MUA CHÍNH THỨC] #{ticker}</b>
⏰ <i>Thời gian: {time_str}</i>

💰 <b>Giá Mua:</b> {price} VNĐ
🛑 <b>Cắt lỗ (Stoploss 2xATR):</b> {stop_loss_str} VNĐ

⚙️ <b>Chỉ báo Kỹ thuật (TA):</b>
  • EMA20: {ta.get('EMA20_Status')}
  • RSI(14): {ta.get('RSI')} (Chuẩn tích lũy)
  • Vol Ratio: {ta.get('Volume_Ratio')}x SMA20 (Bùng nổ)

📊 <b>Nền tảng Doanh nghiệp (FA):</b>
  • ROE: {roe_str} | EPS Tăng trưởng: {eps_str}

🎯 <b>Khuyến nghị:</b> Đạt đủ 3/3 điều kiện. Xuống tiền giải ngân theo tỷ trọng quản trị rủi ro."""

    elif signal_type == "WATCH":
        return f"""🟡 <b>[CẢNH BÁO SỚM - RÌNH LỆNH] #{ticker}</b>
⏰ <i>Thời gian: {time_str}</i>

💰 <b>Giá Hiện tại:</b> {price} VNĐ

⚙️ <b>Trạng thái Kỹ thuật (TA):</b>
  • EMA20: {ta.get('EMA20_Status')} (Xu hướng tốt)
  • RSI(14): {ta.get('RSI')} (Động lượng khỏe)
  • Vol Ratio: {ta.get('Volume_Ratio')}x SMA20 <i>(Cần >= 1.2x)</i>

💡 <b>Ghi chú:</b> {event.get('note', 'Mã tích lũy đẹp, chờ dòng tiền xác nhận để MUA.')}
🎯 <b>Khuyến nghị:</b> Đưa vào Watchlist rình lệnh. Mua ngay khi Vol bùng nổ trong phiên."""

    elif signal_type == "SELL":
        pnl = event.get("pnl_pct", 0)
        pnl_icon = "🚀 +" if pnl >= 0 else "🔻 "
        return f"""🔴 <b>[TÍN HIỆU BÁN / ĐÓNG VỊ THẾ] #{ticker}</b>
⏰ <i>Thời gian: {time_str}</i>

💰 <b>Giá Bán:</b> {price} VNĐ (Giá mua: {event.get('entry_price', 'N/A')})
📈 <b>Hiệu suất (P&L):</b> {pnl_icon}{pnl}%

⚠️ <b>Lý do Bán:</b> {event.get('sell_reason', 'Chốt lời/Cắt lỗ theo kỷ luật')}

🎯 <b>Khuyến nghị:</b> Thực hiện bán chốt lời hoặc cắt lỗ theo đúng nguyên tắc quản trị."""

    return f"ℹ️ <b>[{signal_type}] #{ticker}</b> - Giá: {price}"


# ----------------------------------------------------------------------------
# 3. Hàm Xử lý Chính (Main Processing)
# ----------------------------------------------------------------------------
def process_signals(raw_events: list[dict], history_path: str | Path = DEFAULT_SIGNAL_HISTORY_PATH) -> list[dict]:
    """Lọc danh sách sự kiện từ TA, trả về danh sách tín hiệu hợp lệ sẵn sàng gửi Telegram."""
    history = load_signal_history(history_path)
    valid_signals = []

    for event in raw_events:
        # Kiểm tra chống trùng lặp
        if is_duplicate_signal(event, history):
            print(f"[SignalGen] Bỏ qua tín hiệu trùng: {event.get('ticker')} ({event.get('signal_type')})")
            continue

        # Tạo nội dung tin nhắn Telegram
        event["formatted_message"] = format_telegram_message(event)
        valid_signals.append(event)
        history.append(event)

    if valid_signals:
        save_signal_history(history, history_path)
        print(f"[SignalGen] Đã xử lý & lưu {len(valid_signals)} tín hiệu mới.")

    return valid_signals