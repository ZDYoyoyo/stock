"""T30 — 短名單基本面深掘。

對篩選器選出的短名單（幾十檔），用 FinMind 逐檔補：
  - 月營收 YoY（動能）
  - 本益比 PER / 股價淨值比 PBR / 現金殖利率
並給一句基本面評註，做「法人買 + 基本面」的二次驗證。
請求數 = 短名單檔數 × 2，遠低於 FinMind 免費額度。
"""
import time

import pandas as pd

from .finmind_client import fetch


def _revenue_yoy(sid: str):
    """回傳 (最新營收年月, 最新月YoY%, 近3月YoY均%)；無資料回 (None,None,None)。"""
    data = fetch("TaiwanStockMonthRevenue", start_date="2024-01-01", data_id=sid)
    if not data:
        return None, None, None
    rev = {(d["revenue_year"], d["revenue_month"]): d["revenue"] for d in data}
    keys = sorted(rev.keys())
    yoys = []
    for (y, m) in keys:
        prev = rev.get((y - 1, m))
        if prev and prev > 0:
            yoys.append(((y, m), (rev[(y, m)] - prev) / prev * 100))
    if not yoys:
        return None, None, None
    (ly, lm), last_yoy = yoys[-1]
    recent3 = [v for _, v in yoys[-3:]]
    return f"{ly}/{lm:02d}", round(last_yoy, 1), round(sum(recent3) / len(recent3), 1)


def _valuation(sid: str):
    """回傳 (PER, PBR, 殖利率%)。"""
    data = fetch("TaiwanStockPER", start_date="2026-06-01", data_id=sid)
    if not data:
        return None, None, None
    d = data[-1]
    return d.get("PER"), d.get("PBR"), d.get("dividend_yield")


def dividend_years(sid: str) -> int:
    """回傳最近『連續配發現金股利』的年數（含盈餘+公積現金）。"""
    data = fetch("TaiwanStockDividend", start_date="2010-01-01", data_id=sid)
    if not data:
        return 0
    by_year = {}
    for d in data:
        y = d.get("year")
        cash = (d.get("CashEarningsDistribution") or 0) + (d.get("CashStatutorySurplus") or 0)
        by_year[y] = by_year.get(y, 0) + cash
    years = sorted(by_year.keys())
    streak = 0
    for y in reversed(years):
        if by_year[y] > 0:
            streak += 1
        else:
            break
    return streak


def _note(yoy, per):
    parts = []
    if yoy is None:
        parts.append("營收無資料")
    elif yoy >= 20:
        parts.append(f"營收動能強(YoY+{yoy:.0f}%)")
    elif yoy >= 0:
        parts.append(f"營收溫和(YoY+{yoy:.0f}%)")
    else:
        parts.append(f"⚠️營收衰退({yoy:.0f}%)")
    if per is not None:
        if per <= 0:
            parts.append("⚠️虧損/PER異常")
        elif per > 40:
            parts.append(f"估值偏高(PER{per:.0f})")
        else:
            parts.append(f"PER{per:.0f}")
    return "，".join(parts)


def enrich(stock_ids, sleep: float = 0.3) -> pd.DataFrame:
    rows = []
    for sid in stock_ids:
        ym, last_yoy, avg3_yoy = _revenue_yoy(sid)
        per, pbr, yield_ = _valuation(sid)
        rows.append({
            "stock_id": sid,
            "營收月": ym,
            "營收YoY%": last_yoy,
            "近3月YoY均%": avg3_yoy,
            "PER": per,
            "PBR": pbr,
            "殖利率%": yield_,
            "基本面評註": _note(last_yoy, per),
        })
        time.sleep(sleep)
    return pd.DataFrame(rows)
