# data/fetcher_price.py
import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = "data/market_data.db"

def get_price_history(ticker: str, limit: int = 80) -> pd.DataFrame:
    """
    Kéo lịch sử giá OHLCV của 1 mã từ file SQLite data/market_data.db
    Trả về DataFrame sắp xếp theo ngày TĂNG DẦN
    """
    ticker = ticker.upper().strip()
    db_file = Path(DB_PATH)
    
    if not db_file.exists():
        print(f"⚠️ Chưa tìm thấy file CSDL tại {DB_PATH}")
        return pd.DataFrame()

    try:
        conn = sqlite3.connect(DB_PATH)
        # Giả sử bảng tên là historical_data hoặc daily_prices (tuỳ TV data đặt tên)
        # Truy vấn lấy 'limit' phiên gần nhất của ticker
        query = """
            SELECT date, open, high, low, close, volume 
            FROM historical_prices 
            WHERE ticker = ? 
            ORDER BY date DESC 
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=(ticker, limit))
        conn.close()

        if df.empty:
            return pd.DataFrame()

        # Chuẩn hóa cột & ép kiểu số
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Đảo chiều lại để ngày cũ ở trên, ngày mới ở dưới (chuẩn để tính EMA, RSI, ATR)
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        return df

    except Exception as e:
        # Nếu chưa biết chính xác tên bảng, báo lỗi để kiểm tra lại tên bảng
        print(f"❌ Lỗi truy vấn CSDL cho mã {ticker}: {e}")
        return pd.DataFrame()