"""技術面訊號 enrich — 均線結構、乖離、成交金額，補『趨勢位置』。

籌碼看「誰在買」，技術面看「現在在趨勢哪個位置」，兩者互補（波段進出場尤其需要）。

產出（供各軌 enrich）：
  - 均線排列：5/10/20MA 多空排列。🔴多頭(5>10>20)／🟢空頭(5<10<20)／⚪糾結（其餘）。
  - 20MA乖離%：(收盤−20MA)/20MA×100。正=站上均線偏強、過大=過熱追高風險；負=跌破。
  - 成交額億：今日量(張)×收盤×1000 ÷1億。資金權重——張數不分價位會誤判
      （2000元股100張 ≠ 20元股100張），成交額才是真投入資金。

資料：price 表（收盤/量）。一次查全表 groupby，不逐檔打 DB，快。
台股慣例配色：紅＝強/正、綠＝弱/負。
"""
from __future__ import annotations

import pandas as pd

from .db import read_table

_COLS = ["stock_id", "均線排列", "20MA乖離%", "成交額億"]


def _ma(closes: list, n: int):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def compute() -> pd.DataFrame:
    """回傳每檔 [均線排列, 20MA乖離%, 成交額億]（依 stock_id）。資料不足回空表。"""
    px = read_table("price", use_cache=True)[["date", "stock_id", "close", "volume"]]
    if px.empty:
        return pd.DataFrame(columns=_COLS)

    rows = []
    for sid, g in px.groupby("stock_id"):
        g = g.sort_values("date")
        closes = g["close"].tolist()
        if not closes:
            continue
        c = closes[-1]
        ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)

        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                align = "🔴多頭"
            elif ma5 < ma10 < ma20:
                align = "🟢空頭"
            else:
                align = "⚪糾結"
        else:
            align = "—"

        bias = round((c - ma20) / ma20 * 100, 1) if ma20 and ma20 > 0 else None
        vol = g["volume"].iloc[-1]
        amt = round(c * vol / 1e5, 2) if pd.notna(vol) else None   # 張×元×1000÷1e8 = 億元
        rows.append((sid, align, bias, amt))

    return pd.DataFrame(rows, columns=_COLS)


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把技術面欄併進 df（依 stock_id）。signals 可預先算好重用。"""
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute()
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
