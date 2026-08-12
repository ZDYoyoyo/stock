"""外資持股比 + 市值/股本 + 周轉率（FinMind TaiwanStockShareholding，1 call 全市場）。

一支資料集補三個缺口：
  - 外資持股比%：外資實際持有佔已發行股數（大戶信心；與「外資今日買賣超」不同——
    那是流量，這是**存量**。外資持股 70% 的股，短期買超 1000 張其實不算什麼）。
  - 市值(億)/已發行股數：區分大型股 vs 中小型股。同樣「漲 5%」，
    小型股可能是主力拉抬、大型股才是真趨勢；停損/部位大小也該不同。
  - 周轉率%：當日成交量 ÷ 已發行股數。**熱度指標**——同樣量 10000 張，
    對小股本是換手一輪(妖股)、對台積電是零頭。當沖選股比看絕對量更準。

股數/市值走 DB 最新收盤計算（免費）。抓不到 graceful 回空表。
⚠️ 單位鐵律：DB volume 已是「張」→ 周轉率分母股數要 /1000 換成張再比。
"""
from __future__ import annotations

import pandas as pd

_COLS = ["stock_id", "外資持股%", "市值億", "周轉率%"]
_DATASET = "TaiwanStockShareholding"


def _latest_trading_day(px: pd.DataFrame) -> str | None:
    return sorted(px["date"].unique())[-1] if not px.empty else None


def compute(day: str | None = None) -> pd.DataFrame:
    """回傳每檔 [外資持股%, 市值億, 周轉率%]。抓不到/無資料回空表。

    day＝資料日（預設用 DB 最新交易日；該日無資料時 FinMind 會回空 → 往前試 3 天）。
    """
    from datetime import datetime, timedelta
    from .db import read_table
    from .finmind_client import fetch

    px = read_table("price", use_cache=True)[["date", "stock_id", "close", "volume"]]
    if px.empty:
        return pd.DataFrame(columns=_COLS)
    last = day or _latest_trading_day(px)

    data = []
    d = last
    for _ in range(3):                       # 該日尚未公布 → 往前找
        try:
            data = fetch(_DATASET, start_date=d)
        except Exception:
            data = []
        if data:
            break
        try:
            d = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
        except ValueError:
            break
    if not data:
        return pd.DataFrame(columns=_COLS)

    sh = pd.DataFrame([{"stock_id": str(r.get("stock_id", "")),
                        "外資持股%": r.get("ForeignInvestmentSharesRatio"),
                        "_shares": r.get("NumberOfSharesIssued")} for r in data])
    sh = sh[sh["stock_id"] != ""]

    today_px = px[px["date"] == last][["stock_id", "close", "volume"]]
    m = sh.merge(today_px, on="stock_id", how="inner")
    if m.empty:
        return pd.DataFrame(columns=_COLS)

    shares = pd.to_numeric(m["_shares"], errors="coerce")
    close = pd.to_numeric(m["close"], errors="coerce")
    vol_lots = pd.to_numeric(m["volume"], errors="coerce")      # DB 已是「張」
    ok = shares > 0

    m["外資持股%"] = pd.to_numeric(m["外資持股%"], errors="coerce").round(1)
    m["市值億"] = (close * shares / 1e8).where(ok).round(0)
    # 周轉率% = 成交張數 ÷ 已發行張數（股數/1000）×100
    m["周轉率%"] = (vol_lots / (shares / 1000) * 100).where(ok).round(2)
    return m[_COLS].reset_index(drop=True)


def enrich(df, sh: pd.DataFrame | None = None):
    """把 外資持股%/市值億/周轉率% 併進 df（依 stock_id）。sh 可預算好重用。"""
    if df is None or getattr(df, "empty", True) or "stock_id" not in df.columns:
        return df
    s = sh if sh is not None else compute()
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
