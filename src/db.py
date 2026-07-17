"""SQLite 儲存層：建表與讀寫 helper。"""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, DATA_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price (
    date TEXT, stock_id TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,           -- 成交張數
    PRIMARY KEY (date, stock_id)
);
CREATE TABLE IF NOT EXISTS institutional (
    date TEXT, stock_id TEXT,
    foreign_net INTEGER,      -- 外資買賣超（張）
    trust_net INTEGER,        -- 投信買賣超（張）
    dealer_net INTEGER,       -- 自營商買賣超（張）
    PRIMARY KEY (date, stock_id)
);
CREATE TABLE IF NOT EXISTS margin (
    date TEXT, stock_id TEXT,
    margin_balance INTEGER,   -- 融資今日餘額（張）
    PRIMARY KEY (date, stock_id)
);
CREATE TABLE IF NOT EXISTS revenue (
    stock_id TEXT, year INTEGER, month INTEGER,
    revenue REAL,
    PRIMARY KEY (stock_id, year, month)
);
CREATE TABLE IF NOT EXISTS stock_info (
    stock_id TEXT PRIMARY KEY, stock_name TEXT, industry TEXT, type TEXT
);
"""


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(_SCHEMA)


def upsert(conn, table: str, rows: list[dict]):
    """以主鍵覆寫寫入。rows 為 dict list，欄位需與資料表一致。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)
