"""三大法人 + 融券 流向 enrich — 把外資/投信/自營近N日淨買賣超、融券變化，
批次併進任何含 stock_id 的候選表（給日報各軌顯示「誰在買賣」）。

一次查全表、groupby 批次算，不逐檔打 API/DB，很快。
單位：張（正=買超/增加）。近N日以 institutional 最新日往回數 N 個交易日。
"""
from __future__ import annotations

import pandas as pd

from .db import connect


def institution_flows(days: int = 10) -> pd.DataFrame:
    """回傳每檔 近days日 外資/投信/自營 淨買賣超(張) + 融券增減(張)。"""
    with connect() as conn:
        inst = pd.read_sql(
            "SELECT date, stock_id, foreign_net, trust_net, dealer_net FROM institutional", conn)
        mg = pd.read_sql("SELECT date, stock_id, short_balance FROM margin", conn)

    out = pd.DataFrame(columns=["stock_id", "外資", "投信", "自營", "融券增減"])
    if not inst.empty:
        win = sorted(inst["date"].unique())[-days:]
        iw = inst[inst["date"].isin(win)]
        agg = iw.groupby("stock_id").agg(
            外資=("foreign_net", "sum"),
            投信=("trust_net", "sum"),
            自營=("dealer_net", "sum")).reset_index()
        out = agg
    # 融券增減 = 窗末餘額 − 窗初餘額（近days日淨變化）
    if not mg.empty:
        mwin = sorted(mg["date"].unique())[-days:]
        mw = mg[mg["date"].isin(mwin)].dropna(subset=["short_balance"])
        if not mw.empty:
            first = mw.sort_values("date").groupby("stock_id")["short_balance"].first()
            last = mw.sort_values("date").groupby("stock_id")["short_balance"].last()
            sc = (last - first).rename("融券增減").reset_index()
            out = out.merge(sc, on="stock_id", how="left") if not out.empty else sc
    return out


def enrich(df: pd.DataFrame, days: int = 10, flows: pd.DataFrame | None = None) -> pd.DataFrame:
    """把 外資/投信/自營/融券增減 併進 df（依 stock_id）。flows 可預先算好重用。"""
    if df is None or df.empty:
        return df
    f = flows if flows is not None else institution_flows(days)
    if f is None or f.empty:
        return df
    df = df.merge(f, on="stock_id", how="left")
    for c in ("外資", "投信", "自營", "融券增減"):
        if c in df.columns:
            df[c] = df[c].astype("Int64")  # 保留 NA、整數顯示
    return df
