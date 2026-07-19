"""T11 — 法人默默吸貨篩選器。

概念：找出「法人低調連續買、但股價還沒噴、籌碼乾淨」的股票。
資料來源：本地 SQLite（由 scripts/update_data.py 建立）。

篩選條件（見 config.T11，可調）：
  1. 投信(或外資)近 N 日連續買超 >= MIN_CONSECUTIVE_BUY 天
  2. 期間股價漲幅 < MAX_PRICE_GAIN（還沒表態）
  3. 法人累計買超張數 / 期間總成交張數 > MIN_BUY_RATIO（吃貨力道）
  4. 融資餘額增幅 < MAX_MARGIN_INCREASE（散戶未追、籌碼乾淨）
  5. 收盤在 20 日均線附近 ±MA_NEAR_PCT（打底而非高檔）
  6. 期間日均量 > MIN_AVG_VOLUME（流動性）
"""
import pandas as pd

from ..config import T11
from ..db import connect


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with connect() as conn:
        price = pd.read_sql("SELECT * FROM price", conn)
        inst = pd.read_sql("SELECT * FROM institutional", conn)
        margin = pd.read_sql("SELECT * FROM margin", conn)
        info = pd.read_sql("SELECT * FROM stock_info", conn)
    return price, inst, margin, info


def _consecutive_buy_days(net_series: pd.Series) -> int:
    """從最近一天往回數，連續 > 0 的天數。"""
    count = 0
    for v in reversed(net_series.tolist()):
        if v > 0:
            count += 1
        else:
            break
    return count


def run() -> pd.DataFrame:
    price, inst, margin, info = _load()
    if price.empty or inst.empty:
        raise SystemExit("資料庫是空的，請先執行：python -m scripts.update_data")

    investor_col = "trust_net" if T11.USE_INVESTOR == "Investment_Trust" else "foreign_net"
    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    market_map = info.set_index("stock_id")["type"].to_dict()

    # 對齊「共同的最後法人日」：各市場法人公布時間不同（上市常慢上櫃一天），
    # 取兩市場都有的最新法人日當基準日，避免部分市場多算一天造成連買計算失真。
    inst = inst.copy()
    inst["market"] = inst["stock_id"].map(market_map)
    max_by_mkt = inst.groupby("market")["date"].max()
    asof = min(max_by_mkt) if len(max_by_mkt) else inst["date"].max()

    # 以基準日為界，取最近 N 個交易日（價量與法人都對齊到同一窗口）
    win_dates = sorted(d for d in inst["date"].unique() if d <= asof)[-T11.LOOKBACK_DAYS:]
    price = price[price["date"].isin(win_dates)]
    inst = inst[inst["date"].isin(win_dates)]

    ma20 = _ma20_by_stock(win_dates)

    min_days = T11.LOOKBACK_DAYS - T11.ALLOWED_MISSING_DAYS  # 容許偶發抓取缺日
    results = []
    for sid, pg in price.groupby("stock_id"):
        pg = pg.sort_values("date")
        if len(pg) < min_days:
            continue

        is_tpex = market_map.get(sid) == "tpex"
        min_vol = T11.MIN_AVG_VOLUME_TPEX if is_tpex else T11.MIN_AVG_VOLUME
        avg_vol = pg["volume"].mean()
        if avg_vol < min_vol:
            continue

        first_close, last_close = pg["close"].iloc[0], pg["close"].iloc[-1]
        if first_close <= 0:
            continue
        price_gain = (last_close - first_close) / first_close
        if price_gain >= T11.MAX_PRICE_GAIN or price_gain < T11.MIN_PRICE_GAIN:
            continue  # 太漲(噴出) 或 崩跌(非吸貨) 都排除

        # 市場自適應法人：上市看投信(認養訊號)、上櫃看外資(投信少碰上櫃)
        col = "foreign_net" if (is_tpex and T11.TPEX_USE_FOREIGN) else investor_col
        investor_label = "外資" if col == "foreign_net" else "投信"
        ig = inst[inst["stock_id"] == sid].sort_values("date")
        if ig.empty:
            continue
        consec = _consecutive_buy_days(ig[col])
        if consec < T11.MIN_CONSECUTIVE_BUY:
            continue

        cum_net = ig[col].sum()
        total_vol = pg["volume"].sum()
        buy_ratio = cum_net / total_vol if total_vol > 0 else 0
        if buy_ratio < T11.MIN_BUY_RATIO:
            continue

        # 融資增幅
        mg = margin[margin["stock_id"] == sid].sort_values("date")
        margin_chg = _pct_change(mg["margin_balance"]) if not mg.empty else 0.0
        if margin_chg > T11.MAX_MARGIN_INCREASE:
            continue

        # 均線：只排除「已明顯噴出（高於 MA20 太多）」，打底/回檔（低於 MA）保留
        ma = ma20.get(sid)
        if ma and ma > 0 and (last_close - ma) / ma > T11.MAX_ABOVE_MA20:
            continue

        results.append({
            "stock_id": sid,
            "name": name_map.get(sid, ""),
            "market": "上櫃" if is_tpex else "上市",
            "investor": investor_label,
            "close": round(last_close, 2),
            "price_gain_%": round(price_gain * 100, 2),
            "consec_buy_days": consec,
            "cum_net_lots": int(cum_net),
            "buy_ratio_%": round(buy_ratio * 100, 2),
            "margin_chg_%": round(margin_chg * 100, 2),
            "avg_vol_lots": int(avg_vol),
        })

    df = pd.DataFrame(results)
    if not df.empty:
        # 綜合評分：吃貨力道(權重2) + 連買天數(權重3) + 相對強弱(抗跌/上漲加分)
        df["score"] = (df["buy_ratio_%"] * 2 + df["consec_buy_days"] * 3
                       + df["price_gain_%"] * 1).round(2)
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
    # asof=法人資料對齊基準日；window=區間漲幅/連買計算的交易日數
    # （供報告標示，避免把「區間漲幅」誤讀成單日、把 asof 收盤誤讀成最新日）
    df.attrs["asof"] = asof
    df.attrs["window"] = len(win_dates)
    return df


def _ma20_by_stock(win_dates) -> dict:
    """算每檔到基準日為止的 20 日均線（不含基準日之後的資料）。"""
    asof = max(win_dates)
    with connect() as conn:
        px = pd.read_sql("SELECT date, stock_id, close FROM price WHERE date <= ?",
                         conn, params=(asof,))
    out = {}
    for sid, g in px.groupby("stock_id"):
        g = g.sort_values("date").tail(20)
        if len(g) >= 20:
            out[sid] = g["close"].mean()
    return out


def _pct_change(series: pd.Series) -> float:
    vals = series.tolist()
    if len(vals) < 2 or vals[0] == 0:
        return 0.0
    return (vals[-1] - vals[0]) / abs(vals[0])
