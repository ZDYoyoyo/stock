"""借券賣出餘額 enrich — 法人真實空單（SBL, Securities Borrowing & Lending）。

借券賣出餘額＝機構／外資向券商借股票放空、尚未回補的在外空單張數。
與『融券』的差別（很重要）：
  - 融券＝散戶信用交易放空 → 散戶指標（已有欄：融券餘額／券資比%／融券佔量%）。
  - 借券賣出＝法人／外資真實看空部位 → 法人指標，此欄補的就是這塊。
餘額高＝法人空方壓力大；後續回補（餘額下降）對股價是潛在買盤。
⚠️ 為絕對張數：大型股天生較高，判讀請對照股本／成交量，別只看大小（同券資比%的提醒）。

資料：FinMind TaiwanDailyShortSaleBalances 的 SBLShortSalesCurrentDayBalance（單位股，÷1000→張）。
免費層僅能逐檔(data_id)查詢 → 只對『當日候選股集合』抓，不落 DB（僅需當日值，比照 day_trade_signal）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .db import read_table
from .finmind_client import fetch

_DATASET = "TaiwanDailyShortSaleBalances"
_COLS = ["stock_id", "借券賣出餘額"]


def _latest_date() -> str:
    """本地 price 表最新交易日 ISO（YYYY-MM-DD）；無資料回今日。"""
    px = read_table("price", use_cache=True)
    if px is None or px.empty:
        return date.today().isoformat()
    return sorted(px["date"].unique())[-1]


def compute(stock_ids, start_date: str | None = None) -> pd.DataFrame:
    """回傳每檔 [借券賣出餘額]（張，依 stock_id）。只查傳入的候選股；抓不到回空表。

    免費層逐檔查詢 → stock_ids 應為當日報告的候選股聯集（幾十檔），省額度。
    """
    ids = sorted({str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()})
    if not ids:
        return pd.DataFrame(columns=_COLS)

    end_iso = _latest_date()
    start = start_date or (date.fromisoformat(end_iso) - timedelta(days=14)).isoformat()

    rows = []
    for sid in ids:
        try:
            data = fetch(_DATASET, start_date=start, end_date=end_iso, data_id=sid)
        except Exception:
            continue
        if not data:
            continue
        latest = max(data, key=lambda r: r.get("date", ""))
        cur = latest.get("SBLShortSalesCurrentDayBalance")
        if cur is None:
            continue
        rows.append((sid, int(round(cur / 1000))))   # 股 → 張
    return pd.DataFrame(rows, columns=_COLS)


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把『借券賣出餘額』併進 df（依 stock_id）。signals 可預先算好重用。

    未傳 signals 時，只查 df 自身的 stock_id（避免逐檔掃全市場）。
    """
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute(df["stock_id"].tolist())
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
