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


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把『當沖比率%』併進 df（依 stock_id）。signals 可預先算好重用。"""
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute()
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
