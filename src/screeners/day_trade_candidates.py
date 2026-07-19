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
