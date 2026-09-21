# strategies/ta_signal.py
"""
TA SIGNAL — Tra cứu realtime cho BẤT KỲ mã cổ phiếu nào.

Ví dụ:
    /signal FPT
    /signal VCB
    /signal HPG

Không phụ thuộc data/watch_list.json.

Luồng:
    User nhập mã
        ↓
    Historical OHLCV + Live data
        ↓
    Tạo current/live bar
        ↓
    EMA20 / RSI14 / Volume SMA20 / ATR14
        ↓
    BUY / WATCH / SELL / NO SIGNAL
        ↓
    Stop Loss + Take Profit + Risk/Reward
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

DB_PATH = Path("data/market_data.db")
TABLE_NAME = "historical_ohlcv"

EMA_PERIOD = 20
RSI_PERIOD = 14
VOLUME_SMA_PERIOD = 20
ATR_PERIOD = 14

# BUY
VOLUME_SPIKE_RATIO = 1.0
RSI_BUY_MIN = 45
RSI_BUY_MAX = 70

# SELL
RSI_SELL_THRESHOLD = 75

# Risk management
ATR_STOP_MULTIPLIER = 2.0
RISK_REWARD_TARGET = 2.0

MIN_BARS_REQUIRED = 60


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    """Mở kết nối SQLite."""
    return sqlite3.connect(DB_PATH)


def load_historical_data(
    ticker: str,
    limit: int = 150,
) -> pd.DataFrame:
    """
    Lấy dữ liệu OHLCV lịch sử của một mã.

    Database hiện tại:
        symbol
        date
        open
        high
        low
        close
        volume
    """

    ticker = ticker.upper().strip()

    query = f"""
        SELECT
            symbol,
            date,
            open,
            high,
            low,
            close,
            volume
        FROM {TABLE_NAME}
        WHERE symbol = ?
        ORDER BY date DESC
        LIMIT ?
    """

    with get_connection() as conn:
        df = pd.read_sql_query(
            query,
            conn,
            params=(ticker, limit),
        )

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    df = df.sort_values("date").reset_index(drop=True)

    return df


# ============================================================
# REALTIME DATA
# ============================================================

def load_live_ticks(ticker: str) -> pd.DataFrame:
    """
    Lấy dữ liệu realtime hiện có trong project.

    ------------------------------------------------------------
    QUAN TRỌNG
    ------------------------------------------------------------
    Hàm này cần được nối với nguồn realtime của project.

    Nếu project của bạn hiện tại đã có hàm lấy live data trong
    ta_strategy.py thì copy phần lấy live-bar từ đó vào đây.

    Nếu chưa có live table, hàm trả về DataFrame rỗng.
    Khi đó hệ thống vẫn chạy bằng dữ liệu OHLCV gần nhất.
    """

    # ---------------------------------------------------------
    # PLACEHOLDER
    # ---------------------------------------------------------
    # Ví dụ nếu project có bảng live_ticks:
    #
    # query = """
    #     SELECT time, price, volume
    #     FROM live_ticks
    #     WHERE symbol = ?
    #     ORDER BY time
    # """
    #
    # with get_connection() as conn:
    #     return pd.read_sql_query(
    #         query,
    #         conn,
    #         params=(ticker,),
    #     )

    return pd.DataFrame()


def merge_live_bar(
    historical: pd.DataFrame,
    live_ticks: pd.DataFrame,
) -> pd.DataFrame:
    """
    Ghép dữ liệu realtime vào historical OHLCV.

    Nếu không có live_ticks:
        trả về historical.

    Nếu có:
        tạo/cập nhật bar hiện tại.
    """

    if historical.empty:
        return historical

    if live_ticks.empty:
        return historical.copy()

    live = live_ticks.copy()

    # Chuẩn hóa tên cột
    live.columns = [
        str(c).lower()
        for c in live.columns
    ]

    # Kiểm tra các cột cần thiết
    if "price" not in live.columns:
        return historical.copy()

    # Volume có thể không tồn tại
    if "volume" not in live.columns:
        live["volume"] = 0

    current_price = float(
        pd.to_numeric(
            live["price"],
            errors="coerce",
        ).dropna().iloc[-1]
    )

    valid_prices = pd.to_numeric(
        live["price"],
        errors="coerce",
    ).dropna()

    current_open = float(valid_prices.iloc[0])
    current_high = float(valid_prices.max())
    current_low = float(valid_prices.min())

    current_volume = float(
        pd.to_numeric(
            live["volume"],
            errors="coerce",
        ).fillna(0).sum()
    )

    result = historical.copy()

    # Ngày hiện tại
    today = pd.Timestamp.now().normalize()

    # Nếu đã có bar hôm nay → cập nhật
    today_mask = result["date"].dt.normalize() == today

    if today_mask.any():

        idx = result.index[today_mask][-1]

        result.loc[idx, "close"] = current_price
        result.loc[idx, "high"] = max(
            float(result.loc[idx, "high"]),
            current_high,
        )
        result.loc[idx, "low"] = min(
            float(result.loc[idx, "low"]),
            current_low,
        )
        result.loc[idx, "volume"] = max(
            float(result.loc[idx, "volume"]),
            current_volume,
        )

    else:

        new_bar = pd.DataFrame(
            [{
                "symbol": result["symbol"].iloc[-1],
                "date": pd.Timestamp.now(),
                "open": current_open,
                "high": current_high,
                "low": current_low,
                "close": current_price,
                "volume": current_volume,
            }]
        )

        result = pd.concat(
            [result, new_bar],
            ignore_index=True,
        )

    return result.sort_values(
        "date"
    ).reset_index(drop=True)


# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(
    close: pd.Series,
    period: int = EMA_PERIOD,
) -> pd.Series:

    return close.ewm(
        span=period,
        adjust=False,
    ).mean()


def calculate_rsi(
    close: pd.Series,
    period: int = RSI_PERIOD,
) -> pd.Series:

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


def calculate_atr(
    df: pd.DataFrame,
    period: int = ATR_PERIOD,
) -> pd.Series:

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"] -
        df["low"]
    )

    tr2 = (
        df["high"] -
        previous_close
    ).abs()

    tr3 = (
        df["low"] -
        previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return atr


def calculate_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    result = df.copy()

    result["ema20"] = calculate_ema(
        result["close"]
    )

    result["rsi14"] = calculate_rsi(
        result["close"]
    )

    result["volume_sma20"] = (
        result["volume"]
        .rolling(VOLUME_SMA_PERIOD)
        .mean()
    )

    result["volume_ratio"] = (
        result["volume"]
        / result["volume_sma20"]
    )

    result["atr14"] = calculate_atr(
        result
    )

    # EMA slope
    result["ema20_rising"] = (
        result["ema20"]
        > result["ema20"].shift(1)
    )

    return result


# ============================================================
# SIGNAL
# ============================================================

def evaluate_signal(
    df: pd.DataFrame,
) -> dict:

    if len(df) < MIN_BARS_REQUIRED:

        return {
            "signal": "NO SIGNAL",
            "reason": (
                f"Không đủ dữ liệu "
                f"({len(df)}/{MIN_BARS_REQUIRED} bars)"
            ),
        }

    row = df.iloc[-1]

    previous_row = (
        df.iloc[-2]
        if len(df) >= 2
        else None
    )

    price = float(row["close"])
    ema20 = float(row["ema20"])
    rsi14 = float(row["rsi14"])
    volume = float(row["volume"])
    volume_sma20 = float(row["volume_sma20"])
    volume_ratio = float(row["volume_ratio"])
    atr14 = float(row["atr14"])

    ema20_rising = bool(
        row["ema20_rising"]
    )

    # ---------------------------------------------------------
    # BUY CONDITIONS
    # ---------------------------------------------------------

    trend_ok = (
        price > ema20
        and ema20_rising
    )

    volume_ok = (
        volume_ratio >=
        VOLUME_SPIKE_RATIO
    )

    momentum_ok = (
        RSI_BUY_MIN
        <= rsi14
        <= RSI_BUY_MAX
    )

    buy_score = sum(
        [
            trend_ok,
            volume_ok,
            momentum_ok,
        ]
    )

    # ---------------------------------------------------------
    # SELL CONDITIONS
    # ---------------------------------------------------------

    price_below_ema = (
        price < ema20
    )

    rsi_declining = False

    if previous_row is not None:

        previous_rsi = float(
            previous_row["rsi14"]
        )

        rsi_declining = (
            rsi14 < previous_rsi
        )

    rsi_sell = (
        rsi14 > RSI_SELL_THRESHOLD
        and rsi_declining
    )

    # ---------------------------------------------------------
    # SIGNAL
    # ---------------------------------------------------------

    if rsi_sell or price_below_ema:

        signal = "SELL"

    elif buy_score == 3:

        signal = "BUY"

    elif buy_score >= 2:

        signal = "WATCH"

    else:

        signal = "NO SIGNAL"

    # ---------------------------------------------------------
    # STOP LOSS
    # ---------------------------------------------------------

    stop_loss = (
        price
        - ATR_STOP_MULTIPLIER * atr14
    )

    # Không cho Stop Loss âm
    stop_loss = max(
        0,
        stop_loss,
    )

    # ---------------------------------------------------------
    # TAKE PROFIT
    # ---------------------------------------------------------

    risk = price - stop_loss

    take_profit = (
        price
        + risk * RISK_REWARD_TARGET
    )

    # ---------------------------------------------------------
    # RISK / REWARD
    # ---------------------------------------------------------

    reward = (
        take_profit - price
    )

    if risk > 0:

        rr = reward / risk

    else:

        rr = np.nan

    return {
        "signal": signal,

        "price": price,
        "ema20": ema20,
        "rsi14": rsi14,

        "volume": volume,
        "volume_sma20": volume_sma20,
        "volume_ratio": volume_ratio,

        "atr14": atr14,

        "trend_ok": trend_ok,
        "volume_ok": volume_ok,
        "momentum_ok": momentum_ok,

        "buy_score": buy_score,

        "price_below_ema": price_below_ema,
        "rsi_sell": rsi_sell,

        "stop_loss": stop_loss,
        "take_profit": take_profit,

        "risk": risk,
        "reward": reward,
        "risk_reward": rr,

        "ema20_rising": ema20_rising,
    }


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze_symbol(
    ticker: str,
) -> Optional[dict]:

    ticker = ticker.upper().strip()

    if not ticker:
        return None

    # 1. Historical
    historical = load_historical_data(
        ticker
    )

    if historical.empty:

        return {
            "ticker": ticker,
            "signal": "NOT FOUND",
            "reason": (
                f"Không tìm thấy dữ liệu "
                f"cho mã {ticker}"
            ),
        }

    # 2. Live data
    live_ticks = load_live_ticks(
        ticker
    )

    # 3. Historical + current live bar
    df = merge_live_bar(
        historical,
        live_ticks,
    )

    # 4. Indicators
    df = calculate_indicators(
        df
    )

    # 5. Signal
    result = evaluate_signal(
        df
    )

    result["ticker"] = ticker

    # Thời điểm dữ liệu
    result["data_time"] = (
        df["date"].iloc[-1]
    )

    # Cho biết có live data hay không
    result["is_realtime"] = (
        not live_ticks.empty
    )

    return result


# ============================================================
# FORMAT TELEGRAM
# ============================================================

def format_signal_message(
    result: dict,
) -> str:

    ticker = result["ticker"]

    signal = result.get(
        "signal",
        "NO SIGNAL",
    )

    if signal == "BUY":
        signal_icon = "🟢"

    elif signal == "WATCH":
        signal_icon = "🟡"

    elif signal == "SELL":
        signal_icon = "🔴"

    elif signal == "NOT FOUND":
        signal_icon = "❌"

    else:
        signal_icon = "⚪"

    # Không tìm thấy mã
    if signal == "NOT FOUND":

        return (
            f"❌ Không tìm thấy dữ liệu "
            f"cho mã {ticker}."
        )

    # Không đủ dữ liệu
    if signal == "NO SIGNAL" and (
        "price" not in result
    ):

        return (
            f"📊 {ticker} — TA SIGNAL\n\n"
            f"⚪ {result.get('reason', '')}"
        )

    price = result["price"]
    ema20 = result["ema20"]
    rsi14 = result["rsi14"]

    volume = result["volume"]
    volume_sma20 = result["volume_sma20"]
    volume_ratio = result["volume_ratio"]

    atr14 = result["atr14"]

    stop_loss = result["stop_loss"]
    take_profit = result["take_profit"]

    risk = result["risk"]
    reward = result["reward"]
    rr = result["risk_reward"]

    trend_text = (
        "✓"
        if result["trend_ok"]
        else "✗"
    )

    volume_text = (
        "✓"
        if result["volume_ok"]
        else "✗"
    )

    momentum_text = (
        "✓"
        if result["momentum_ok"]
        else "✗"
    )

    realtime_text = (
        "🟢 REALTIME"
        if result["is_realtime"]
        else "🟡 DATA CUỐI CÙNG"
    )

    data_time = result[
        "data_time"
    ]

    return (
        f"📊 {ticker} — TA SIGNAL\n"
        f"{realtime_text}\n\n"

        f"💰 Giá hiện tại: "
        f"{price:,.2f}\n\n"

        f"📈 CHỈ BÁO TA\n"
        f"• EMA20: {ema20:,.2f}\n"
        f"• RSI14: {rsi14:.2f}\n"
        f"• Volume: {volume:,.0f}\n"
        f"• Volume SMA20: "
        f"{volume_sma20:,.0f}\n"
        f"• Volume Ratio: "
        f"{volume_ratio:.2f}x\n"
        f"• ATR14: {atr14:,.2f}\n\n"

        f"🔎 ĐIỀU KIỆN BUY\n"
        f"• Giá > EMA20 & EMA tăng: "
        f"{trend_text}\n"
        f"• Volume đạt chuẩn: "
        f"{volume_text}\n"
        f"• RSI trong vùng BUY: "
        f"{momentum_text}\n"
        f"• Score: "
        f"{result['buy_score']}/3\n\n"

        f"🔔 TÍN HIỆU: "
        f"{signal_icon} {signal}\n\n"

        f"🛡️ RISK MANAGEMENT\n"
        f"• Stop Loss: "
        f"{stop_loss:,.2f}\n"
        f"• Take Profit: "
        f"{take_profit:,.2f}\n"
        f"• Risk: "
        f"{risk:,.2f}\n"
        f"• Reward: "
        f"{reward:,.2f}\n"
        f"• Risk/Reward: "
        f"1:{rr:.2f}\n\n"

        f"⏱️ Dữ liệu: "
        f"{data_time}\n\n"

        f"⚠️ Tín hiệu được tạo "
        f"theo bộ quy tắc TA của bot."
    )


# ============================================================
# TEST COMMAND LINE
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("TA SIGNAL — REALTIME STOCK ANALYSIS")
    print("=" * 60)

    ticker = input(
        "Nhập mã cổ phiếu: "
    ).strip().upper()

    result = analyze_symbol(
        ticker
    )

    if result is None:

        print("Mã không hợp lệ.")

    else:

        print()
        print(
            format_signal_message(
                result
            )
        )