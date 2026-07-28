"""T16 — 相對強弱／抗跌強勢股。

概念：大盤下跌期間仍逆勢上漲或明顯抗跌的股票，通常是資金認同的強勢族群領頭羊。
作法：計算每檔近 N 日報酬 減 大盤(全樣本中位數)報酬 = 相對強弱(RS)，取 RS 最高者。
資料來源：本地 SQLite。
"""
import pandas as pd

from ..config import T16
from ..db import read_table


def run() -> pd.DataFrame:
    price = read_table("price", use_cache=True)[["date", "stock_id", "close", "volume"]]
    info = read_table("stock_info", use_cache=True)[["stock_id", "stock_name", "type"]]
    if price.empty:
        raise SystemExit("資料庫是空的，請先執行：python -m scripts.update_data")

    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    market_map = info.set_index("stock_id")["type"].to_dict()

    dates = sorted(price["date"].unique())[-T16.LOOKBACK_DAYS:]
    price = price[price["date"].isin(dates)]

    # 個股報酬
    rets = {}
    vols = {}
    for sid, g in price.groupby("stock_id"):
        g = g.sort_values("date")
        if len(g) < T16.LOOKBACK_DAYS - 1:
            continue
        f, l = g["close"].iloc[0], g["close"].iloc[-1]
        if f > 0:
            rets[sid] = (l - f) / f
            vols[sid] = g["volume"].mean()
    if not rets:
        return pd.DataFrame()

    market_ret = pd.Series(rets).median()  # 以全市場中位數當「大盤」基準

    rows = []
    for sid, r in rets.items():
        is_tpex = market_map.get(sid) == "tpex"
        min_vol = T16.MIN_AVG_VOLUME_TPEX if is_tpex else T16.MIN_AVG_VOLUME
        if vols.get(sid, 0) < min_vol:
            continue
        rs = r - market_ret
        if r < T16.MIN_RETURN:          # 至少要正報酬(抗跌但續跌者不取)
            continue
        if r > T16.MAX_RETURN:          # 漲太多=過度延伸/噴出，不宜追，排除
            continue
        rows.append({
            "stock_id": sid,
            "name": name_map.get(sid, ""),
            "market": "上櫃" if is_tpex else "上市",
            "return_%": round(r * 100, 2),
            "vs_market_%": round(rs * 100, 2),
            "avg_vol_lots": int(vols[sid]),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("vs_market_%", ascending=False).reset_index(drop=True)
    # asof=區間末日（最新交易日）；window=區間長度，供報告標示（漲幅為區間累積非單日）
    df.attrs["asof"] = max(dates)
    df.attrs["window"] = len(dates)
    return df
