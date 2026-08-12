"""風控工具（B）— ATR 停損 + 部位試算。

- atr(): 由 DB 的 high/low/close 算 ATR（波動）
- plan_trade(): 給帳戶大小/單筆風險%/進場價 → 算停損價、建議張數、風險金額、停利與風險報酬比

原則：先決定「這筆最多能賠多少（風險%）」，再由停損距離反推張數 →
      虧損固定、部位由波動決定，避免凹單與過度下注。
"""
from __future__ import annotations

import pandas as pd

from .db import connect


def atr(sid: str, period: int = 14) -> float | None:
    """平均真實區間 ATR（近 period 日）。需 DB 有 high/low/close。"""
    with connect() as conn:
        df = pd.read_sql(
            "SELECT date, high, low, close FROM price WHERE stock_id=? ORDER BY date",
            conn, params=(sid,))
    if len(df) < period + 1:
        return None
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return round(tr.tail(period).mean(), 2)


def plan_trade(sid: str, account: float, risk_pct: float = 1.0,
               entry: float | None = None, atr_mult: float = 2.0,
               target_r: float = 2.0, lot_shares: int = 1000,
               max_position_pct: float = 25.0) -> dict:
    """產生一筆交易的風控計畫。

    account   : 總資金
    risk_pct  : 單筆願承受的最大虧損占總資金 %（建議 0.5~2）
    entry     : 進場價（None 則用 DB 最新收盤）
    atr_mult  : 停損 = 進場 - atr_mult×ATR
    target_r  : 停利設在幾倍風險（R）
    """
    a = atr(sid)
    if a is None:
        return {"stock_id": sid, "error": "ATR 資料不足（DB 需更多日 K）"}

    with connect() as conn:
        px = pd.read_sql(
            "SELECT close FROM price WHERE stock_id=? ORDER BY date DESC LIMIT 1",
            conn, params=(sid,))
    if entry is None:
        if px.empty:
            return {"stock_id": sid, "error": "無收盤價"}
        entry = float(px["close"].iloc[0])

    stop = round(entry - atr_mult * a, 2)
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"stock_id": sid, "error": "停損距離異常"}

    risk_amount = account * risk_pct / 100
    lots = int(risk_amount / risk_per_share) // lot_shares

    # 單檔部位上限：避免停損很近時押注過度集中
    capped = False
    max_lots_by_pos = int(account * max_position_pct / 100 / (entry * lot_shares))
    if lots > max_lots_by_pos:
        lots, capped = max_lots_by_pos, True
    if lots < 1:
        return {"stock_id": sid, "note": "以此風險%/部位上限買不到 1 張，需調高風險或選低價股",
                "entry": entry, "stop": stop, "atr": a}

    actual_shares = lots * lot_shares
    position_value = round(actual_shares * entry)
    actual_risk = round(actual_shares * risk_per_share)
    target = round(entry + target_r * risk_per_share, 2)

    return {
        "stock_id": sid,
        "entry": entry,
        "atr": a,
        "stop": stop,                       # 停損價
        "stop_%": round(-risk_per_share / entry * 100, 2),
        "target": target,                   # 停利價（target_r 倍風險）
        "target_%": round(target_r * risk_per_share / entry * 100, 2),
        "risk_reward": f"1:{target_r:g}",
        "suggest_lots": lots,               # 建議張數
        "position_value": position_value,   # 部位金額
        "position_%": round(position_value / account * 100, 1),
        "risk_amount": actual_risk,         # 觸停損實際虧損
        "risk_%": round(actual_risk / account * 100, 2),
        "capped_by_position_limit": capped,  # True=受單檔部位上限而縮量
    }


# ---- 全市場 ATR 價位（供每日報告併欄；不需帳戶大小，故與 plan_trade 分開）----

_LEVEL_COLS = ["stock_id", "ATR", "停損價", "停損%", "目標價"]


def levels(atr_mult: float = 2.0, target_r: float = 2.0, period: int = 14):
    """每檔 ATR 停損/目標價（進場價＝最新收盤）。一次讀全表向量化，不逐檔查 DB。

    停損價＝收盤−atr_mult×ATR；目標價＝收盤＋target_r×(收盤−停損價)
    → 風報比固定 1:target_r（故不另列欄，寫在報告說明）。
    停損%＝停損距離佔股價%，**因股而異**：波動大的股本來就該把停損放寬，
    用固定 5% 停損會在高波動股被雜訊掃出場。
    """
    import pandas as pd
    from .db import read_table

    px = read_table("price", use_cache=True)[["date", "stock_id", "high", "low", "close"]]
    if px.empty:
        return pd.DataFrame(columns=_LEVEL_COLS)
    px = px.sort_values(["stock_id", "date"])
    g = px.groupby("stock_id", sort=False)
    prev_c = g["close"].shift(1)
    tr = pd.concat([px["high"] - px["low"],
                    (px["high"] - prev_c).abs(),
                    (px["low"] - prev_c).abs()], axis=1).max(axis=1)
    px = px.assign(_tr=tr)
    atr_s = (px.groupby("stock_id", sort=False)["_tr"]
             .apply(lambda s: s.tail(period).mean() if s.notna().sum() >= period else None))
    last = px.groupby("stock_id", sort=False)["close"].last()

    out = pd.DataFrame({"stock_id": atr_s.index, "ATR": atr_s.values,
                        "_close": last.reindex(atr_s.index).values})
    out = out[out["ATR"].notna() & (out["ATR"] > 0) & out["_close"].notna()]
    if out.empty:
        return pd.DataFrame(columns=_LEVEL_COLS)
    stop = out["_close"] - atr_mult * out["ATR"]
    stop = stop.where(stop > 0)                      # 停損價須為正（低價股極端波動）
    risk = out["_close"] - stop
    out["ATR"] = out["ATR"].round(2)
    out["停損價"] = stop.round(2)
    out["停損%"] = (-risk / out["_close"] * 100).round(1)
    out["目標價"] = (out["_close"] + target_r * risk).round(2)
    return out[_LEVEL_COLS].reset_index(drop=True)


def enrich(df, lv=None):
    """把 ATR/停損價/停損%/目標價 併進 df（依 stock_id）。lv 可預算好重用。"""
    if df is None or getattr(df, "empty", True) or "stock_id" not in df.columns:
        return df
    s = lv if lv is not None else levels()
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
