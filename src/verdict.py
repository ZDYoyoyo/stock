"""綜合定調 — 把各軌已併欄的籌碼＋技術訊號濃縮成一句可行動的方向定調。

痛點：報告 30+ 欄要使用者自己心算。這裡用 5 面向投票，直接吐「🔴偏多／⚪觀望／🟢偏空」。
讀『已 enrich 的欄位』（不重抓 DB/API），套在任何併好欄的候選表。

配色遵台股慣例：🔴偏多(看漲)／🟢偏空(看跌)／⚪觀望（與 chip_signal/tech_signal 一致）。
⚠️ 籌碼/技術為昨日收盤後資料 → 是**方向偏誤(bias)，非盤中即時訊號**；進出點仍看盤中量價。

5 面向（各 +1 偏多／−1 偏空／0 中性）：
  ① 法人主導度%（籌碼）  ② 融資佔量%（散戶反向：退場=多／接刀=空）
  ③ 均線排列（短期技術）  ④ 季線年線（中長期技術）  ⑤ 20MA乖離%（站上/跌破月線）
"""
from __future__ import annotations

import pandas as pd


def _vote(r: pd.Series) -> int:
    v = 0
    dom = r.get("主導度%")
    if pd.notna(dom):
        v += 1 if dom > 3 else (-1 if dom < -3 else 0)
    mgv = r.get("融資佔量%")
    if pd.notna(mgv):
        v += 1 if mgv < -2 else (-1 if mgv > 5 else 0)   # 散戶退場=多、暴增接刀=空
    align = str(r.get("均線排列", ""))
    v += 1 if "多頭" in align else (-1 if "空頭" in align else 0)
    lt = str(r.get("季線年線", ""))
    v += 1 if "站上季年" in lt else (-1 if "跌破季年" in lt else 0)
    bias = r.get("20MA乖離%")
    if pd.notna(bias):
        v += 1 if bias > 0 else (-1 if bias < 0 else 0)
    return v


def label(v: int) -> str:
    return "🔴偏多" if v >= 2 else ("🟢偏空" if v <= -2 else "⚪觀望")


def add_verdict(df: pd.DataFrame) -> pd.DataFrame:
    """加『定調』欄（5 面向投票 → 🔴偏多／⚪觀望／🟢偏空）。缺欄位者該面向以中性計。"""
    if df is None or df.empty:
        return df
    df = df.copy()
    df["定調"] = df.apply(lambda r: label(_vote(r)), axis=1)
    return df
