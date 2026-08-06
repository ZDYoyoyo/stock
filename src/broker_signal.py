"""分點主力淨額 + 隔日沖偵測 enrich（需 FinMind Sponsor 分點；不可用則欄留白）。

補「當沖比看不到隔日沖」的盲區：
  - 當沖＝當日買賣沖銷（day_trade_signal 已抓）。
  - 隔日沖＝同一分點『昨買今賣』：昨日淨買、今日淨賣，兩天對沖掉的張數＝隔日沖回吐量。
    回吐量佔今日量比高＝隔日沖客今天在倒貨（隔天常殺低）→ 警示訊號。

主力淨額＝今日『前15大買超分點淨額 + 前15大賣超分點淨額』（籌碼是否集中在少數主力進出）。

分點資料量大（全市場單日數百萬列）→ 不落 DB，逐檔 on-demand（每檔 T/T-1 各 1 call，僅對顯示的候選股）。
"""
from __future__ import annotations

import pandas as pd

from . import broker_client as bc

_TOP = 15   # 主力＝前幾大分點（對齊 broker_client._TOP_BRANCHES）
_COLS = ["stock_id", "主力淨額", "隔日沖賣壓%"]


def _branch_net(sid: str, date: str) -> dict:
    """{分點: 今日淨買張}（buy−sell，股÷1000→張）。不可用/無資料回 {}。"""
    rows = bc._raw(sid, date)
    if not rows:
        return {}
    net: dict[str, float] = {}
    for d in rows:
        name = d.get("securities_trader") or d.get("securities_trader_id") or "?"
        net[name] = net.get(name, 0) + (d.get("buy", 0) - d.get("sell", 0)) / 1000
    return net


def _one(sid: str, day: str, prev: str | None, vol) -> dict:
    """單檔：主力淨額(今)＋隔日沖賣壓%(昨買今賣對沖量÷今日量)。無分點回 {}。"""
    net_t = _branch_net(sid, day)
    if not net_t:
        return {}
    vals = sorted(net_t.values(), reverse=True)
    top_buy = sum(v for v in vals[:_TOP] if v > 0)
    top_sell = sum(v for v in vals[-_TOP:] if v < 0)
    out = {"主力淨額": int(round(top_buy + top_sell))}

    # 隔日沖賣壓：昨日『前15大買超分點』今日轉淨賣的量(capped 到昨買量) ÷ 今日量。
    # 只取昨日大買家(非全分點)→ 濾掉大型股正常兩面換手的雜訊，抓真正『昨進今出』的隔日沖大戶。
    if prev and vol and vol > 0:
        net_y = _branch_net(sid, prev)
        if net_y:
            top_buyers = sorted(((k, v) for k, v in net_y.items() if v > 0),
                                key=lambda x: x[1], reverse=True)[:_TOP]
            overlap = sum(min(v, -net_t.get(k, 0))
                          for k, v in top_buyers if net_t.get(k, 0) < 0)
            out["隔日沖賣壓%"] = round(overlap / vol * 100, 1)
    return out


def compute(stock_ids, day: str, prev: str | None, vol_map: dict | None = None) -> pd.DataFrame:
    """對候選股算 [主力淨額, 隔日沖賣壓%]。不可用（無 Sponsor 分點）回空表。

    只查傳入的 stock_ids（應為報告『顯示』的候選聯集，控制 call 數）。每檔 T/T-1 各 1 call。
    """
    if not bc.available():
        return pd.DataFrame(columns=_COLS)
    ids = sorted({str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()})
    if not ids:
        return pd.DataFrame(columns=_COLS)
    vol_map = vol_map or {}
    rows = []
    for sid in ids:
        s = _one(sid, day, prev, vol_map.get(sid))
        if s:
            rows.append({"stock_id": sid, **s})
    if not rows:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame(rows)
    for c in _COLS:                       # 保證 schema 一致（某些檔可能缺隔日沖賣壓%）
        if c not in df.columns:
            df[c] = pd.NA
    df["主力淨額"] = df["主力淨額"].astype("Int64")
    return df[_COLS]


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None) -> pd.DataFrame:
    """把主力淨額/隔日沖賣壓% 併進 df（依 stock_id）。signals 由 compute 預先算好重用。"""
    if df is None or df.empty or signals is None or signals.empty:
        return df
    dup = [c for c in signals.columns if c != "stock_id" and c in df.columns]
    s = signals.drop(columns=dup) if dup else signals
    return df.merge(s, on="stock_id", how="left")
