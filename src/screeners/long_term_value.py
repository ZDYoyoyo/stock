"""長期持有軌 — 價值+成長+配息+品質 選股（v2）。

與波段(籌碼)完全不同的邏輯，適合持有數月~數年。

流程：
  第一段（粗篩，TWSE 全市場估值，免費一次抓）：
    殖利率 >= MIN_YIELD、0 < PER <= MAX_PER、0 < PBR <= MAX_PBR
    估算 ROE = PBR/PER×100 >= MIN_ROE_EST（獲利能力硬門檻，免費資料的巧解：
    ROE = 淨值報酬 = (E/P)×(P/B)）
  第二段（深掘，FinMind 逐檔，僅對粗篩後短名單）：
    連續配息年數 >= MIN_DIVIDEND_YEARS（硬門檻，先查先擋、省請求）
    月營收 YoY、近四季 EPS 與年增
  產業處理：
    營收認列不規律產業（營建等）YoY 評分貢獻歸零並標註，避免完工認列造成的
    +2000% 假動能灌分。
  綜合評分：殖利率 + 低估值 + ROE + 營收/EPS 成長 + 配息穩定。
"""
import time

import pandas as pd

from ..config import LONGTERM as L
from ..db import connect
from .. import twse_client as tw
from ..enrich import dividend_years, _revenue_yoy, eps_ttm_growth, industry_map


def _latest_trading_day() -> str:
    with connect() as conn:
        r = pd.read_sql("SELECT MAX(date) d FROM price", conn)
    d = r["d"].iloc[0]
    return d.replace("-", "") if d else None


def run(verbose: bool = True) -> pd.DataFrame:
    ymd = _latest_trading_day()
    if not ymd:
        raise SystemExit("DB 無價格資料，請先 update_data")

    val = tw.valuation(ymd)
    if not val:
        raise SystemExit("抓不到 TWSE 估值資料（BWIBBU）")

    # 第一段：估值粗篩 + ROE 估算硬門檻
    coarse = []
    for v in val:
        if not (v["yield"] >= L.MIN_YIELD and 0 < v["per"] <= L.MAX_PER
                and 0 < v["pbr"] <= L.MAX_PBR):
            continue
        roe_est = v["pbr"] / v["per"] * 100
        if roe_est < L.MIN_ROE_EST:
            continue
        v["roe_est"] = round(roe_est, 1)
        coarse.append(v)
    coarse.sort(key=lambda v: (v["yield"] / max(v["per"], 1)), reverse=True)
    coarse = coarse[:L.DEEP_DIVE_N]
    if verbose:
        print(f"  粗篩通過 {len(coarse)} 檔（殖利率/估值/ROE門檻），逐檔深掘…")

    ind_map = industry_map()

    rows = []
    for v in coarse:
        sid = v["stock_id"]
        # 配息硬門檻先查先擋（省後續請求）
        dy = dividend_years(sid)
        time.sleep(0.3)
        if dy < L.MIN_DIVIDEND_YEARS:
            if verbose:
                print(f"  {sid} {v['name']} 連配息{dy}年 < {L.MIN_DIVIDEND_YEARS} → 剔除")
            continue

        _, last_yoy, _ = _revenue_yoy(sid)
        time.sleep(0.3)
        eps_ttm, eps_g = eps_ttm_growth(sid)
        time.sleep(0.3)

        industry = ind_map.get(sid, "")
        lumpy = any(k in industry for k in L.LUMPY_REVENUE_INDUSTRIES)

        rows.append({
            "stock_id": sid, "name": v["name"], "產業": industry,
            "close": v["close"], "殖利率%": v["yield"],
            "PER": v["per"], "PBR": v["pbr"], "ROE估%": v["roe_est"],
            "營收YoY%": last_yoy, "EPS近4季": eps_ttm, "EPS年增%": eps_g,
            "連配息年": dy,
            "_lumpy": lumpy,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 評分：殖利率×3 + 低PER + ROE×1.5 + 成長(營收+EPS) + 配息年×1.5
    yoy_score = df.apply(
        lambda r: 0.0 if r["_lumpy"] else
        max(min(r["營收YoY%"] if pd.notna(r["營收YoY%"]) else 0, 50), -30) * 0.4,
        axis=1)
    eps_score = df["EPS年增%"].fillna(0).clip(-30, 60) * 0.3
    df["score"] = (df["殖利率%"] * 3 + (100 / df["PER"].clip(lower=1))
                   + df["ROE估%"] * 1.5 + yoy_score + eps_score
                   + df["連配息年"].clip(upper=20) * 1.5).round(2)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    # 營建等認列不規律產業：最終清單最多保留 MAX_LUMPY 檔（依分數留最強者），避免洗版
    lumpy = df[df["_lumpy"]].head(L.MAX_LUMPY)
    non_lumpy = df[~df["_lumpy"]]
    df = pd.concat([non_lumpy, lumpy]).sort_values("score", ascending=False).reset_index(drop=True)

    # 營建等產業標註
    df["產業"] = df.apply(lambda r: f"⚠️{r['產業']}" if r["_lumpy"] else r["產業"], axis=1)
    df = df.drop(columns=["_lumpy"])
    return df
