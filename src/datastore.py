"""資料持久層：DB ⇄ CSV（讓雲端每天累積歷史，存進 git）。

雲端 session 是暫時的、DB(data/stock.db)被 gitignore；為了讓價量/法人/融資/千張
歷史能一天天累積（尤其千張大戶週趨勢需要多週資料），把資料以 CSV 存在
data/history/（進 git）。流程：
  session 開始 → load()  用 CSV 重建 DB
  update_data 抓當天增量
  結束 → dump() 把 DB 寫回 CSV → git commit

CSV 為文字、git diff 乾淨、只記新增列，repo 長得慢。
"""
from __future__ import annotations

import pandas as pd

from . import db
from .config import DATA_DIR

HISTORY_DIR = DATA_DIR / "history"

# 表 → 排序鍵（也是去重主鍵）
_TABLES = {
    "price": ["date", "stock_id"],
    "institutional": ["date", "stock_id"],
    "margin": ["date", "stock_id"],
    "big_holders": ["date", "stock_id"],
    "sbl": ["date", "stock_id"],
    "stock_info": ["stock_id"],
}


def dump(keep_days: int | None = None) -> dict:
    """把 DB 各表寫成 data/history/<table>.csv（排序、去重）。回傳各表列數。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    with db.connect() as conn:
        for table, keys in _TABLES.items():
            try:
                df = pd.read_sql(f"SELECT * FROM {table}", conn)
            except Exception:
                continue
            if df.empty:
                out[table] = 0
                continue
            df = df.drop_duplicates(subset=keys, keep="last").sort_values(keys)
            # 時間序列表可選擇只留近 keep_days 個交易日，控制檔案大小
            if keep_days and "date" in df.columns:
                recent = sorted(df["date"].unique())[-keep_days:]
                df = df[df["date"].isin(recent)]
            df.to_csv(HISTORY_DIR / f"{table}.csv", index=False, encoding="utf-8")
            out[table] = len(df)
    return out


def load() -> dict:
    """用 data/history/*.csv 重建 DB（若 CSV 不存在則略過）。回傳各表載入列數。"""
    db.init_db()
    out = {}
    with db.connect() as conn:
        for table, keys in _TABLES.items():
            path = HISTORY_DIR / f"{table}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, dtype={"stock_id": str})
            if df.empty:
                out[table] = 0
                continue
            db.upsert(conn, table, df.to_dict("records"))
            out[table] = len(df)
    return out


def has_history() -> bool:
    return HISTORY_DIR.exists() and any(HISTORY_DIR.glob("*.csv"))
