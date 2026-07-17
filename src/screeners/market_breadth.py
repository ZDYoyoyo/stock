"""市場廣度（breadth）— 判斷大盤氣氛/能否進場。

用本地全市場價格算：
  - 當日上漲/下跌家數（相對前一交易日收盤）
  - 站上 20 日均線的比例（多方 vs 空方結構）
資料窗口若不足 20 日，均線比例回報 None。
"""
import pandas as pd

from ..db import connect


def run() -> dict:
    with connect() as conn:
        price = pd.read_sql("SELECT date, stock_id, close FROM price", conn)
    if price.empty:
        return {}

    dates = sorted(price["date"].unique())
    last, prev = dates[-1], dates[-2] if len(dates) >= 2 else None

    # 漲跌家數
    up = down = flat = 0
    if prev:
        c_last = price[price["date"] == last].set_index("stock_id")["close"]
        c_prev = price[price["date"] == prev].set_index("stock_id")["close"]
        common = c_last.index.intersection(c_prev.index)
        diff = c_last[common] - c_prev[common]
        up, down, flat = int((diff > 0).sum()), int((diff < 0).sum()), int((diff == 0).sum())

    # 站上 20 日均線比例
    pct_above = None
    if len(dates) >= 20:
        above = total = 0
        for sid, g in price.groupby("stock_id"):
            g = g.sort_values("date").tail(20)
            if len(g) >= 20:
                total += 1
                if g["close"].iloc[-1] > g["close"].mean():
                    above += 1
        if total:
            pct_above = round(above / total * 100, 1)

    posture = _posture(up, down, pct_above)
    return {"date": last, "up": up, "down": down, "flat": flat,
            "pct_above_ma20": pct_above, "posture": posture}


def _posture(up, down, pct_above) -> str:
    ratio = up / down if down else 999
    if pct_above is not None:
        if pct_above >= 60 and ratio > 1:
            return "偏多（多數站上均線）"
        if pct_above <= 30:
            return "偏空（多數跌破均線，逆勢做多勝率低）"
    if ratio >= 2:
        return "當日普漲"
    if ratio <= 0.5:
        return "當日普跌（觀望）"
    return "中性/分歧"


def summary_line(b: dict) -> str:
    if not b:
        return "市場廣度：無資料"
    pa = f"{b['pct_above_ma20']}%" if b["pct_above_ma20"] is not None else "N/A"
    return (f"市場廣度 {b['date']}：漲{b['up']}/跌{b['down']} 家，"
            f"站上20MA {pa} → {b['posture']}")
