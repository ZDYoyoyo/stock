"""共用訊號原語 — 回測與實際篩選共用同一份邏輯。

為什麼存在：原本「回測」測的是簡化的法人連買，「實際選股」跑的是 T11 多重濾網，
兩套訊號各自演化 → 回測結果無法代表你真正在用的策略。這個模組把兩邊會用到的
訊號計算收斂成同一份純函式（不連網、可單元測試），讓回測能測「真正的策略」。

內容：
  - 交易成本模型：round_trip_return
  - 法人連續同向天數：consecutive_net_days（統一 chip_signal._streak 與 T11 連買）
  - 每日連買計數序列：consec_buy_series（回測掃訊號用）
  - T11「法人吸貨」point-in-time 判斷：t11_pass（給某檔到 as-of 日的窗口）
單位慣例：張，正=買超/融資增加。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# --- 交易成本（單邊手續費、賣出證交稅）---
FEE = 0.001425       # 手續費（單邊）
FEE_DISCOUNT = 1.0   # 折數，如 5 折填 0.5
TAX = 0.003          # 證交稅（賣出）


def round_trip_return(entry_open: float, exit_close: float, *,
                      fee: float = FEE, fee_discount: float = FEE_DISCOUNT,
                      tax: float = TAX) -> float:
    """含成本的來回報酬率（進場開盤買、出場收盤賣）。entry<=0 視為無效回 0。"""
    if entry_open is None or entry_open <= 0 or exit_close is None:
        return 0.0
    buy_cost = fee * fee_discount
    sell_cost = fee * fee_discount + tax
    gross = exit_close / entry_open
    return gross * (1 - sell_cost) / (1 + buy_cost) - 1


def consecutive_net_days(nets) -> int:
    """當前連續同向天數：正=連買、負=連賣、0=最新日平盤/無資料。

    nets 依『時間順序（舊→新）』排列，最後一個元素為最近日。
    從最近日往回數，遇到反向或平盤即停。統一了 chip_signal._streak
    （新→舊，傳入時反轉）與 T11 連買（buy 數＝max(0, 本函式)）。
    """
    sign = 0
    n = 0
    for v in reversed(list(nets)):
        if v is None or pd.isna(v):
            break
        if v > 0:
            s = 1
        elif v < 0:
            s = -1
        else:                    # 平盤：連續中斷
            break
        if sign == 0:
            sign = s
        elif s != sign:          # 換方向：停
            break
        n += 1
    return sign * n


def consec_buy_series(nets) -> list[int]:
    """每個索引 i 的『到 i 為止連續買超天數』（回測逐日掃訊號用）。

    nets 依時間順序（舊→新）。與各回測腳本原本的內嵌迴圈等價。
    """
    seq = list(nets)
    out = [0] * len(seq)
    for i, v in enumerate(seq):
        if v is not None and not pd.isna(v) and v > 0:
            out[i] = (out[i - 1] + 1) if i > 0 else 1
        else:
            out[i] = 0
    return out


@dataclass
class T11Params:
    """T11 吸貨門檻（預設對齊 config.T11，可在回測中覆寫做敏感度分析）。"""
    lookback: int = 10
    allowed_missing: int = 2
    min_consec: int = 4
    max_gain: float = 0.12
    min_gain: float = -0.08
    min_buy_ratio: float = 0.05
    max_margin_inc: float = 0.15
    max_above_ma20: float = 0.15

    @classmethod
    def from_config(cls, T11) -> "T11Params":
        return cls(
            lookback=T11.LOOKBACK_DAYS, allowed_missing=T11.ALLOWED_MISSING_DAYS,
            min_consec=T11.MIN_CONSECUTIVE_BUY, max_gain=T11.MAX_PRICE_GAIN,
            min_gain=T11.MIN_PRICE_GAIN, min_buy_ratio=T11.MIN_BUY_RATIO,
            max_margin_inc=T11.MAX_MARGIN_INCREASE, max_above_ma20=T11.MAX_ABOVE_MA20)


def t11_pass(closes, vols, inst_nets, margin_bal, ma20, p: T11Params = None) -> bool:
    """point-in-time：給某檔到 as-of 日的近 lookback 窗口，判斷是否符合 T11 吸貨。

    參數皆為『窗口內、時間順序（舊→新）』的序列：
      closes  收盤、vols 成交量(張)、inst_nets 選定法人淨買超(張)、
      margin_bal 融資餘額；ma20 為到 as-of 的 20 日均價（可 None）。
    邏輯與 screeners/institutional_accumulation.run() 逐條對齊。
    """
    p = p or T11Params()
    closes, vols, inst_nets = list(closes), list(vols), list(inst_nets)
    if len(closes) < p.lookback - p.allowed_missing:
        return False
    first, last = closes[0], closes[-1]
    if first <= 0:
        return False
    gain = (last - first) / first
    if gain >= p.max_gain or gain < p.min_gain:
        return False
    if max(0, consecutive_net_days(inst_nets)) < p.min_consec:
        return False
    total_vol = sum(vols)
    buy_ratio = sum(inst_nets) / total_vol if total_vol > 0 else 0
    if buy_ratio < p.min_buy_ratio:
        return False
    mb = [x for x in margin_bal if x is not None and not pd.isna(x)]
    if len(mb) >= 2 and mb[0] != 0:
        if (mb[-1] - mb[0]) / abs(mb[0]) > p.max_margin_inc:
            return False
    if ma20 and ma20 > 0 and (last - ma20) / ma20 > p.max_above_ma20:
        return False
    return True
