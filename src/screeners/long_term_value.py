"""長期持有軌 — 價值+成長+配息 選股。

與波段(籌碼)完全不同的邏輯，適合持有數月~數年：
  第一段（粗篩，TWSE 全市場估值，免費一次抓）：
    殖利率 >= MIN_YIELD、0 < 本益比 <= MAX_PER、股價淨值比 <= MAX_PBR
  第二段（深掘，FinMind 逐檔，僅對粗篩後的短名單）：
    近月營收 YoY、連續配息年數
  綜合評分：殖利率 + 低估值(PER/PBR 越低越好) + 營收成長 + 配息穩定。
"""
import time

import pandas as pd

from ..config import LONGTERM as L
from ..db import connect
from .. import twse_client as tw
from ..enrich import dividend_years, _revenue_yoy


def _latest_trading_day() -> str:
    with connect() as conn:
        r = pd.read_sql("SELECT MAX(date) d FROM price", conn)
    d = r["d"].iloc[0]
    return d.replace("-", "") if d else None


def run() -> pd.DataFrame:
    ymd = _latest_trading_day()
    if not ymd:
        raise SystemExit("DB 無價格資料，請先 update_data")

    val = tw.valuation(ymd)
    if not val:
        raise SystemExit("抓不到 TWSE 估值資料（BWIBBU）")

    # 第一段：估值粗篩
    coarse = [v for v in val
              if v["yield"] >= L.MIN_YIELD
              and 0 < v["per"] <= L.MAX_PER
              and 0 < v["pbr"] <= L.MAX_PBR]
    # 先用『殖利率高 + PER 低』粗排，取前 N 檔進第二段（控制 FinMind 請求數）
    coarse.sort(key=lambda v: (v["yield"] / max(v["per"], 1)), reverse=True)
    coarse = coarse[:L.DEEP_DIVE_N]

    rows = []
    for v in coarse:
        sid = v["stock_id"]
        _, last_yoy, _avg3 = _revenue_yoy(sid)
        dy = dividend_years(sid)
        rows.append({
            "stock_id": sid, "name": v["name"],
            "close": v["close"], "殖利率%": v["yield"],
            "PER": v["per"], "PBR": v["pbr"],
            "營收YoY%": last_yoy, "連配息年": dy,
        })
        time.sleep(0.3)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 評分：殖利率(×3) + 低PER(倒數) + 成長(YoY) + 配息穩定(年數×1.5)
    df["score"] = (
        df["殖利率%"] * 3
        + (100 / df["PER"].clip(lower=1))
        + df["營收YoY%"].fillna(0).clip(-30, 50) * 0.5
        + df["連配息年"] * 1.5
    ).round(2)
    return df.sort_values("score", ascending=False).reset_index(drop=True)
