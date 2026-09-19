import sqlite3
from pathlib import Path


class HistoricalStore:
    def __init__(self, db_path="data/market_data.db"):
        self.db_path = db_path

        Path(db_path).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.conn = sqlite3.connect(
            self.db_path
        )

        self.cursor = self.conn.cursor()

        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS historical_ohlcv (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                UNIQUE(symbol, date)
            )
        """)

        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_historical_symbol_date
            ON historical_ohlcv(symbol, date)
        """)

        self.conn.commit()

    def save(self, df):
        if df is None or df.empty:
            return 0

        records = []

        for _, row in df.iterrows():

            date = str(row["date"])

            # Lấy phần ngày YYYY-MM-DD
            date = date[:10]

            records.append((
                str(row["symbol"]).upper()
                if "symbol" in row
                else None,

                date,
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"]
            ))

        records = [
            r for r in records
            if r[0] is not None
        ]

        self.cursor.executemany("""
            INSERT OR IGNORE INTO historical_ohlcv
            (
                symbol,
                date,
                open,
                high,
                low,
                close,
                volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, records)

        self.conn.commit()

        return self.cursor.rowcount

    def get_history(self, symbol):
        self.cursor.execute("""
            SELECT
                symbol,
                date,
                open,
                high,
                low,
                close,
                volume
            FROM historical_ohlcv
            WHERE symbol = ?
            ORDER BY date
        """, (symbol.upper(),))

        return self.cursor.fetchall()

    def get_last_date(self, symbol):
        self.cursor.execute("""
            SELECT MAX(date)
            FROM historical_ohlcv
            WHERE symbol = ?
        """, (symbol.upper(),))

        result = self.cursor.fetchone()

        if result is None:
            return None

        return result[0]

    def close(self):
        self.conn.close()