"""Walk-forward 樣本外驗證的共用計算（T16／T12 等各軌回測共用，避免各寫一份會漂移）。

只放**與策略無關**的純計算：
  - curve_metrics(): 由連續權益曲線算 CAGR/最大回撤/夏普/年化波動
  - bh_curve():      等權買入持有曲線（OOS 基準對照）
  - make_folds():    expanding-train 折切（train 從頭到切點、test 接在後面）

折的選參數/評估邏輯留在各軌腳本（那部分本來就因策略而異）。
"""
from __future__ import annotations

import math

import pandas as pd

from .portfolio_backtest import TRADING_DAYS, slice_panel


def curve_metrics(equity: pd.Series) -> dict:
    """由連續權益曲線算 CAGR/最大回撤/夏普/年化波動（串接 OOS 曲線用）。"""
    if equity is None or len(equity) < 2:
        return {"CAGR_%": 0.0, "MaxDD_%": 0.0, "Sharpe": 0.0, "Vol_%": 0.0}
    rets = equity.pct_change().dropna()
    years = len(equity) / TRADING_DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    vol = rets.std() * math.sqrt(TRADING_DAYS)
    sharpe = (rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0
    dd = (equity / equity.cummax() - 1).min()
    return {"CAGR_%": round(cagr * 100, 2), "MaxDD_%": round(dd * 100, 2),
            "Sharpe": round(sharpe, 2), "Vol_%": round(vol * 100, 2)}


def bh_curve(panel: dict, lo: str, hi: str, init_cap: float = 1_000_000) -> pd.Series:
    """等權買入持有權益曲線（區間 [lo,hi]），供 OOS 基準對照。"""
    sub = slice_panel(panel, lo=lo, hi=hi)
    norm = {}
    for sid, df in sub.items():
        df = df.sort_values("date")
        c0 = df["close"].iloc[0] if len(df) else 0
        if c0 > 0:
            norm[sid] = dict(zip(df["date"], df["close"] / c0))
    all_dates = sorted({d for m in norm.values() for d in m})
    vals = []
    for d in all_dates:
        xs = [m[d] for m in norm.values() if d in m]
        vals.append(sum(xs) / len(xs) if xs else 1.0)
    return pd.Series([v * init_cap for v in vals], index=all_dates)


def make_folds(dates: list, n_folds: int = 3) -> list:
    """expanding-train 折：切點 25/50/75% → [(名稱, train起, train迄, test起, test迄), …]。

    train 一律從資料最初開始（越後面的折 train 越長），test 緊接其後、彼此不重疊
    → 三段 test 可串成一條連續的樣本外曲線。
    """
    n = len(dates)
    cuts = [n * (i + 1) // (n_folds + 1) for i in range(n_folds + 1)]
    folds = []
    for i in range(n_folds):
        tr_hi, te_hi = cuts[i], cuts[i + 1]
        if tr_hi + 1 >= te_hi:
            continue
        folds.append((f"F{i + 1}", dates[0], dates[tr_hi],
                      dates[tr_hi + 1], dates[te_hi if te_hi < n else n - 1]))
    return folds
