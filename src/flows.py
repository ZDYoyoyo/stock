"""三大法人 + 融券 流向 enrich — 把外資/投信/自營近N日淨買賣超、融券變化，
批次併進任何含 stock_id 的候選表（給日報各軌顯示「誰在買賣」）。

一次查全表、groupby 批次算，不逐檔打 API/DB，很快。
單位：張（正=買超/增加）。近N日以 institutional 最新日往回數 N 個交易日。
"""
from __future__ import annotations

import pandas as pd

from .db import read_table


_FLOW_COLS = ("外資", "投信", "自營", "融資增減", "融券增減")


def _window_delta(df: pd.DataFrame, col: str, name: str, days: int) -> pd.DataFrame:
    """近days日『窗末餘額 − 窗初餘額』的淨變化（張），回傳 [stock_id, name]。"""
    w = df.dropna(subset=[col])
    if w.empty:
        return pd.DataFrame(columns=["stock_id", name])
    win = sorted(w["date"].unique())[-days:]
    w = w[w["date"].isin(win)].sort_values("date")
    first = w.groupby("stock_id")[col].first()
    last = w.groupby("stock_id")[col].last()
    return (last - first).rename(name).reset_index()


def institution_flows(days: int = 10) -> pd.DataFrame:
    """回傳每檔 近days日 外資/投信/自營 淨買賣超(張) + 融資增減 + 融券增減(張)。"""
    inst = read_table("institutional", use_cache=True)[
        ["date", "stock_id", "foreign_net", "trust_net", "dealer_net"]]
    mg = read_table("margin", use_cache=True)[
        ["date", "stock_id", "margin_balance", "short_balance"]]

    out = pd.DataFrame(columns=["stock_id", *_FLOW_COLS])
    if not inst.empty:
        win = sorted(inst["date"].unique())[-days:]
        iw = inst[inst["date"].isin(win)]
        out = iw.groupby("stock_id").agg(
            外資=("foreign_net", "sum"),
            投信=("trust_net", "sum"),
            自營=("dealer_net", "sum")).reset_index()
    # 融資/融券增減 = 近days日 窗末餘額 − 窗初餘額
    if not mg.empty:
        for col, name in (("margin_balance", "融資增減"), ("short_balance", "融券增減")):
            delta = _window_delta(mg, col, name, days)
            if not delta.empty:
                out = out.merge(delta, on="stock_id", how="left") if not out.empty else delta
    return out


def enrich(df: pd.DataFrame, days: int = 10, flows: pd.DataFrame | None = None) -> pd.DataFrame:
    """把 外資/投信/自營/融券增減 併進 df（依 stock_id）。flows 可預先算好重用。"""
    if df is None or df.empty:
        return df
    f = flows if flows is not None else institution_flows(days)
    if f is None or f.empty:
        return df
    df = df.merge(f, on="stock_id", how="left")
    for c in _FLOW_COLS:
        if c in df.columns:
            df[c] = df[c].astype("Int64")  # 保留 NA、整數顯示
    return df
