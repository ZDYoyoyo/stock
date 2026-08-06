"""SQLite 儲存層：建表與讀寫 helper。"""
import sqlite3
from contextlib import contextmanager

import pandas as pd

from .config import DB_PATH, DATA_DIR

# 進程內整表快取：run_all 一次會有多個 screener 各自讀 price/institutional/margin 全表，
# 造成同一張表被重複讀好幾次。use_cache=True 時同一進程只讀一次（回傳副本，呼叫端可安全改）。
_TABLE_CACHE: dict = {}


def read_table(table: str, use_cache: bool = False) -> "pd.DataFrame":
    """讀整張表為 DataFrame（統一 data-access 入口，取代散落各處的 SELECT *）。

    use_cache=True：同一進程快取該表，之後回傳副本。資料更新後請先 clear_cache()。
    """
    if use_cache and table in _TABLE_CACHE:
        return _TABLE_CACHE[table].copy()
    with connect() as conn:
        df = pd.read_sql(f"SELECT * FROM {table}", conn)
    if use_cache:
        _TABLE_CACHE[table] = df
        return df.copy()
    return df


def clear_cache() -> None:
    """清空整表快取（資料更新後、或需要重讀最新資料時呼叫）。"""
    _TABLE_CACHE.clear()

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
    total_net INTEGER,        -- 三大法人買賣超合計（張，官方合計欄；對得起券商App今日數）
    PRIMARY KEY (date, stock_id)
);
CREATE TABLE IF NOT EXISTS margin (
    date TEXT, stock_id TEXT,
    margin_balance INTEGER,   -- 融資今日餘額（張）
    short_balance INTEGER,    -- 融券今日餘額（張）
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
CREATE TABLE IF NOT EXISTS big_holders (
    date TEXT, stock_id TEXT,
    pct_1000 REAL,            -- 千張大戶(≥1000張)持股比%
    pct_400 REAL,             -- 大戶(≥400張)持股比%
    PRIMARY KEY (date, stock_id)
);
CREATE TABLE IF NOT EXISTS sbl (
    date TEXT, stock_id TEXT,
    sbl_balance INTEGER,      -- 借券賣出餘額（張，法人真實空單；股÷1000）
    PRIMARY KEY (date, stock_id)
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


# 舊 DB 補欄遷移：{表: [(欄, 型別), …]}。CREATE TABLE IF NOT EXISTS 不會替既有表加欄，
# 故對舊 .db 以 ALTER TABLE 補上（欄已存在則略過）。
_MIGRATIONS = {
    "margin": [("short_balance", "INTEGER")],
    "institutional": [("total_net", "INTEGER")],
}


def init_db():
    with connect() as conn:
        conn.executescript(_SCHEMA)
        for table, adds in _MIGRATIONS.items():
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, coltype in adds:
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def upsert(conn, table: str, rows: list[dict]):
    """以主鍵覆寫寫入。rows 為 dict list，欄位需與資料表一致。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)
