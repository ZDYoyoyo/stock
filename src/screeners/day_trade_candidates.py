"""當沖候選（EOD 掃描）— 縮小盤中要盯的清單。

⚠️ 這是「盤後掃描」，不是即時訊號。它找出**具備當沖特徵**的股票，
   讓你盤中人工盯這幾檔即可；真正進出仍需盤中判斷（或日後接即時 API）。
⚠️ 當沖統計上勝率最低、成本高，本清單非投資建議。

當沖標的特徵（皆由本地 DB 開高低收量計算）：
  - 高流動性：日均量夠大（進得去出得來）
  - 高波動：平均日振幅大（有價差可賺）
  - 爆量：當日量能放大（有資金/題材，盤中容易有行情）
"""
import numpy as np
import pandas as pd

from ..config import DAYTRADE as D
from ..db import connect


def run() -> pd.DataFrame:
    with connect() as conn:
        price = pd.read_sql("SELECT * FROM price", conn)
        info = pd.read_sql("SELECT stock_id, stock_name, type FROM stock_info", conn)
    if price.empty:
        raise SystemExit("DB 無資料，請先 update_data")

    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    market_map = info.set_index("stock_id")["type"].to_dict()

    dates = sorted(price["date"].unique())[-D.LOOKBACK_DAYS:]
    price = price[price["date"].isin(dates)]

    rows = []
    for sid, g in price.groupby("stock_id"):
        g = g.sort_values("date")
        if len(g) < 10:
            continue
        is_tpex = market_map.get(sid) == "tpex"
        min_vol = D.MIN_AVG_VOLUME_TPEX if is_tpex else D.MIN_AVG_VOLUME

        avg_vol = g["volume"].mean()
        if avg_vol < min_vol:
            continue

        prev_close = g["close"].shift(1)
        amp = (g["high"] - g["low"]) / prev_close * 100      # 每日振幅%
        avg_amp = amp.tail(D.LOOKBACK_DAYS).mean()
        if pd.isna(avg_amp) or avg_amp < D.MIN_AVG_AMPLITUDE:
            continue

        today_amp = amp.iloc[-1]
        vol_surge = g["volume"].iloc[-1] / avg_vol if avg_vol > 0 else 0

        rows.append({
            "stock_id": sid,
            "name": name_map.get(sid, ""),
            "market": "上櫃" if is_tpex else "上市",
            "close": round(g["close"].iloc[-1], 2),
            "當日振幅%": round(today_amp, 2),
            "均振幅%": round(avg_amp, 2),
            "量能倍數": round(vol_surge, 2),
            "日均量": int(avg_vol),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        # 評分：波動(均振幅) × 流動性(量對數) + 爆量加分
        df["score"] = (df["均振幅%"] * np.log10(df["日均量"].clip(lower=10))
                       + (df["量能倍數"] - 1).clip(lower=0) * 5).round(2)
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
    # asof=最新交易日；均振幅/量能為近 LOOKBACK 日統計，供報告標示
    df.attrs["asof"] = max(dates) if dates else None
    df.attrs["window"] = len(dates)
    return df


def _regime_dir(reg) -> int:
    """大盤方向：+1 偏多、-1 偏空、0 中性/震盪。"""
    if not reg:
        return 0
    label = str(reg.get("regime", ""))
    if "偏空" in label:
        return -1
    if "偏多" in label and "中性" not in label:
        return 1
    return 0


def add_bias(df: pd.DataFrame, reg=None) -> pd.DataFrame:
    """為當沖候選加『多空傾向』＋『與大盤』兩欄，供開盤前定調要找多方或空方 setup。

    多空票＝5 個籌碼/技術面向的方向投票加總（每項 +1 偏多／-1 偏空／0 中性）：
      ① 法人淨買佔量  ② 千張大戶週增  ③ 融資變化(散戶退＝多／暴增接刀＝空)
      ④ 相對大盤強弱  ⑤ 站上/跌破20MA
    ⚠️ 法人/大戶為『昨日收盤後』資料 → 這是**隔夜方向偏誤(bias)，非盤中即時訊號**；
       進出點仍須看盤中量價。
    """
    if df is None or df.empty:
        return df
    from . import main_force as mf

    # 籌碼（法人/大戶/融資）：複用 main_force 的 主力分組成欄
    try:
        mfmap = mf.run().set_index("stock_id")
    except Exception:
        mfmap = pd.DataFrame()

    # 技術（相對大盤 + 20MA）：從 price 一次算好
    with connect() as conn:
        price = pd.read_sql("SELECT date, stock_id, close FROM price", conn)
    win = D.LOOKBACK_DAYS
    ret, ma20 = {}, {}
    for sid, g in price.groupby("stock_id"):
        c = g.sort_values("date")["close"].tolist()
        base = c[-win] if len(c) >= win else c[0]
        ret[sid] = (c[-1] - base) / base * 100 if base else 0.0
        if len(c) >= 20:
            ma20[sid] = sum(c[-20:]) / 20
    mkt_med = pd.Series(ret).median() if ret else 0.0

    rdir = _regime_dir(reg)
    tags, votes, aligns = [], [], []
    for _, r in df.iterrows():
        sid = r["stock_id"]
        v = 0
        if sid in getattr(mfmap, "index", []):
            m = mfmap.loc[sid]
            v += 1 if m["法人佔量%"] > 3 else (-1 if m["法人佔量%"] < -3 else 0)
            hc = m["千張週增pp"]
            if pd.notna(hc):
                v += 1 if hc > 0.3 else (-1 if hc < -0.3 else 0)
            mc = m["融資增減%"]
            v += 1 if mc < -5 else (-1 if mc > 15 else 0)   # 散戶退場=多、暴增接刀=空
        rel = ret.get(sid, 0) - mkt_med
        v += 1 if rel > 5 else (-1 if rel < -5 else 0)
        if sid in ma20:
            v += 1 if r["close"] > ma20[sid] else -1
        votes.append(v)
        tag = "🔴偏多" if v >= 2 else ("🟢偏空" if v <= -2 else "⚪中性")
        tags.append(tag)
        # 與大盤順逆：候選方向與大盤方向同號=順勢、反號=逆勢
        cdir = 1 if v >= 2 else (-1 if v <= -2 else 0)
        if rdir == 0 or cdir == 0:
            aligns.append("—")
        elif cdir == rdir:
            aligns.append("順勢")
        else:
            aligns.append("逆勢⚠️")

    df = df.copy()
    df["多空票"] = votes
    df["多空傾向"] = tags
    df["與大盤"] = aligns
    return df
