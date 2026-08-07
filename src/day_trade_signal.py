"""當沖比率 enrich — 妖股對殺偵測。

當沖比率%＝當日沖銷成交量 ÷ 當日總成交量 ×100。高（>50%）＝當沖客在『對殺』，
妖股特徵（暴漲跌、法人不玩、資券帳面沖銷）。例：5328 華容當沖比率 ~79% ＝ 純對殺盤。

資料：TWSE + TPEX 官方當沖統計（免費，各1呼叫/日）＋ 本地 DB 總成交量。
只需『當日』值（非歷史），故每次現抓最新交易日，不落 DB。
"""
from __future__ import annotations

import pandas as pd

from . import tpex_client as tp
from . import twse_client as tw
from .db import read_table


def compute(date_ymd: str | None = None) -> pd.DataFrame:
    """回傳每檔 [當沖比率%]（依 stock_id）。抓不到當沖資料回空表。"""
    px = read_table("price", use_cache=True)[["date", "stock_id", "volume"]]
    if px.empty:
        return pd.DataFrame(columns=["stock_id", "當沖比率%"])
    last_iso = sorted(px["date"].unique())[-1]
    ymd = date_ymd or last_iso.replace("-", "")
    vol = px[px["date"] == last_iso].set_index("stock_id")["volume"]

    dt: dict = {}
    for client in (tw, tp):
        try:
            dt.update(client.day_trade(ymd))
        except Exception:
            pass
    if not dt:
        return pd.DataFrame(columns=["stock_id", "當沖比率%"])

    rows = []
    for sid, v in vol.items():
        d = dt.get(sid)
        if d is not None and v and v > 0:
            rows.append((sid, round(d / v * 100, 1)))
    return pd.DataFrame(rows, columns=["stock_id", "當沖比率%"])


def fetch_market_day(date_iso: str) -> list[dict]:
    """全市場某交易日當沖量 → [{date, stock_id, dt_vol(張)}]（供 backfill_daytrade 落 DB）。"""
    ymd = date_iso.replace("-", "")
    dt: dict = {}
    for client in (tw, tp):
        try:
            dt.update(client.day_trade(ymd))
        except Exception:
            pass
    return [{"date": date_iso, "stock_id": sid, "dt_vol": int(v)}
            for sid, v in dt.items() if v is not None]


def trend(stock_ids, n: int = 5) -> pd.DataFrame:
    """當沖比熱度趨勢（讀 DB day_trade 歷史）：回 [stock_id, 當沖比均{n}日, 當沖比趨勢]。

    當沖比趨勢＝今日 vs 前 n-1 日均：🔥升溫(今>均×1.2)／❄降溫(今<均×0.8)／➖持平。
    （非多空方向，只表當沖熱度升降——升溫＝資金/妖股湧入、波動加大。）資料不足回空。
    """
    cols = ["stock_id", f"當沖比均{n}日", "當沖比趨勢"]
    try:
        px = read_table("price", use_cache=True)[["date", "stock_id", "volume"]]
        dt = read_table("day_trade", use_cache=True)[["date", "stock_id", "dt_vol"]]
    except Exception:
        return pd.DataFrame(columns=cols)
    if px.empty or dt.empty:
        return pd.DataFrame(columns=cols)
    ids = {str(s).strip() for s in stock_ids}
    m = dt.merge(px, on=["date", "stock_id"])
    m = m[m["stock_id"].isin(ids) & (m["volume"] > 0)]
    if m.empty:
        return pd.DataFrame(columns=cols)
    m["ratio"] = m["dt_vol"] / m["volume"] * 100
    rows = []
    for sid, g in m.groupby("stock_id"):
        g = g.sort_values("date").tail(n)
        if len(g) < 2:                      # 至少 2 天才有趨勢意義
            continue
        today = g["ratio"].iloc[-1]
        base = g["ratio"].iloc[:-1].mean()  # 前 n-1 日均（不含今日）
        if base and base > 0:
            tag = "🔥升溫" if today > base * 1.2 else ("❄降溫" if today < base * 0.8 else "➖持平")
        else:
            tag = "➖持平"
        rows.append({"stock_id": sid, f"當沖比均{n}日": round(g["ratio"].mean(), 1),
                     "當沖比趨勢": tag})
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把『當沖比率%』併進 df（依 stock_id）。signals 可預先算好重用。"""
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute()
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
