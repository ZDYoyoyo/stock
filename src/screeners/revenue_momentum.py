"""T12 — 月營收動能股（全市場官方月營收，免 FinMind）。

概念：找「營收年增(YoY)強、且近月動能加速、累計也成長」的成長股——
回測證明『營收不衰退』是組合 edge 的關鍵因子，這是它的正面選股版。

加速度 = 最新月 YoY − 累計 YoY：
  > 0 代表最近這個月的成長率，比年初至今的平均還高 → 動能正在加速（非一次性）。

資料：TWSE/TPEX 官方月營收 OpenAPI（src/revenue_client）+ 本地 DB 股價（動能/流動性確認）。
門檻見 config.T12。⚠️ 研究用途，非投資建議。
"""
from __future__ import annotations

import pandas as pd

from ..config import T12
from ..db import connect
from ..revenue_client import fetch_all


def _price_ctx():
    """回傳 {sid: (close, avg_vol, above_ma20)}，供動能/流動性確認。"""
    with connect() as conn:
        px = pd.read_sql("SELECT date, stock_id, close, volume FROM price", conn)
    out = {}
    for sid, g in px.groupby("stock_id"):
        g = g.sort_values("date")
        close = g["close"].tolist()
        if not close:
            continue
        avg_vol = g["volume"].tail(20).mean()
        ma20 = sum(close[-20:]) / len(close[-20:]) if len(close) >= 20 else None
        above = (ma20 is not None and close[-1] >= ma20)
        out[sid] = (close[-1], avg_vol, above)
    return out


def _excluded(industry: str) -> bool:
    return any(k in (industry or "") for k in T12.EXCLUDE_INDUSTRIES)


def run() -> pd.DataFrame:
    rows = fetch_all()
    if not rows:
        raise SystemExit("抓不到月營收（TWSE/TPEX OpenAPI 無回應，稍後再試）")
    pctx = _price_ctx()
    ym = next((r["ym"] for r in rows if r["ym"]), None)

    results = []
    for r in rows:
        yoy, cum, mom = r["yoy"], r["cum_yoy"], r["mom"]
        if yoy is None or cum is None:
            continue
        # YoY/累計 需在合理區間：太低=沒動能，太高=基期效應(去年同月近0)雜訊
        if not (T12.MIN_YOY <= yoy <= T12.MAX_YOY):
            continue
        if not (T12.MIN_CUM_YOY <= cum <= T12.MAX_CUM_YOY):
            continue
        accel = yoy - cum
        if T12.REQUIRE_ACCEL and accel < T12.MIN_ACCEL:
            continue
        if _excluded(r["industry"]):     # 營建認列不規律，非動能股該用的濾網 → 排除
            continue

        pc = pctx.get(r["stock_id"])
        if pc is None:
            continue
        close, avg_vol, above = pc
        if close < T12.MIN_CLOSE or avg_vol < T12.MIN_AVG_VOLUME:
            continue

        results.append({
            "stock_id": r["stock_id"],
            "name": r["name"],
            "market": "上櫃" if r["market"] == "tpex" else "上市",
            "產業": r["industry"],
            "close": round(close, 2),
            "營收月": r["ym"],
            "YoY%": round(yoy, 1),
            "累計YoY%": round(cum, 1),
            "MoM%": round(mom, 1) if mom is not None else None,
            "加速度": round(accel, 1),
            "站上20MA": "✔" if above else "",
            "_above": above,
        })

    df = pd.DataFrame(results)
    if df.empty:
        df.attrs["ym"] = ym
        return df

    # 評分：YoY + 累計YoY×0.5 + 加速度 + 站上20MA加分（各項設上限，避免極端值洗版排序）
    df["score"] = (df["YoY%"].clip(upper=T12.SCORE_CAP_YOY)
                   + df["累計YoY%"].clip(upper=T12.SCORE_CAP_CUM) * 0.5
                   + df["加速度"].clip(upper=T12.SCORE_CAP_ACCEL)
                   + df["_above"].astype(int) * (10 if T12.ABOVE_MA20_BONUS else 0)).round(1)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df = df.drop(columns=["_above"])
    df.attrs["ym"] = ym
    return df
