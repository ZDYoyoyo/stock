"""T23 — 法人連買訊號 事件研究式回測（單檔/少量檔測試用）。

測 1~2 檔時用 FinMind 逐檔抓長歷史（一次 call 給好幾年），不需全市場。
邏輯：
  訊號日 t = 投信(上市)/外資(上櫃) 連續買超 >= K 日。
  進場：t+1 開盤買。 出場：持有 H 個交易日後收盤賣。
  報酬計入成本：買賣手續費各 0.1425%（可折）、賣出證交稅 0.3%。
輸出：訊號次數、勝率、平均/中位報酬、最佳/最差、簡易期望值。
"""
import time

import pandas as pd

from .finmind_client import fetch

FEE = 0.001425      # 手續費（單邊）
FEE_DISCOUNT = 1.0  # 折數，如 5 折填 0.5
TAX = 0.003         # 證交稅（賣出）


def _load_stock(sid: str, start: str) -> pd.DataFrame:
    """回傳含 open/close 與 投信/外資 淨買超(張) 的日資料表。"""
    px = fetch("TaiwanStockPrice", start_date=start, data_id=sid)
    time.sleep(0.3)
    inst = fetch("TaiwanStockInstitutionalInvestorsBuySell", start_date=start, data_id=sid)
    if not px:
        return pd.DataFrame()
    dfp = pd.DataFrame(px)[["date", "open", "close"]]

    # 法人 pivot：投信 / 外資 淨買超（股 → 張）
    trust, foreign = {}, {}
    for d in inst:
        net = (d.get("buy", 0) - d.get("sell", 0)) / 1000
        if d.get("name") == "Investment_Trust":
            trust[d["date"]] = trust.get(d["date"], 0) + net
        elif d.get("name") in ("Foreign_Investor", "Foreign_Dealer_Self"):
            foreign[d["date"]] = foreign.get(d["date"], 0) + net
    dfp["trust_net"] = dfp["date"].map(trust).fillna(0)
    dfp["foreign_net"] = dfp["date"].map(foreign).fillna(0)
    return dfp.sort_values("date").reset_index(drop=True)


def _round_trip_return(entry_open: float, exit_close: float) -> float:
    """含成本的報酬率。"""
    buy_cost = FEE * FEE_DISCOUNT
    sell_cost = FEE * FEE_DISCOUNT + TAX
    gross = exit_close / entry_open
    net = gross * (1 - sell_cost) / (1 + buy_cost) - 1
    return net


def backtest(sid: str, investor: str = "trust", consec_k: int = 4,
             hold_days: int = 10, start: str = "2023-01-01") -> dict:
    df = _load_stock(sid, start)
    if df.empty or len(df) < hold_days + consec_k + 1:
        return {"stock_id": sid, "error": "資料不足"}

    col = "trust_net" if investor == "trust" else "foreign_net"
    net = df[col].tolist()
    opens, closes = df["open"].tolist(), df["close"].tolist()

    # 連續買超天數（到第 i 天為止）
    consec = [0] * len(net)
    for i in range(len(net)):
        consec[i] = consec[i - 1] + 1 if (net[i] > 0 and i > 0 and net[i - 1] > 0) \
            else (1 if net[i] > 0 else 0)

    rets, last_exit = [], -1
    for i in range(len(net) - hold_days - 1):
        if consec[i] >= consec_k and i > last_exit:   # 不重疊持有
            entry = opens[i + 1]
            exit_ = closes[i + 1 + hold_days]
            if entry > 0:
                rets.append(_round_trip_return(entry, exit_))
                last_exit = i + 1 + hold_days

    if not rets:
        return {"stock_id": sid, "signals": 0, "note": "期間內無訊號"}

    s = pd.Series(rets)
    return {
        "stock_id": sid,
        "investor": "投信" if investor == "trust" else "外資",
        "period": f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
        "signals": len(rets),
        "win_rate_%": round((s > 0).mean() * 100, 1),
        "avg_ret_%": round(s.mean() * 100, 2),
        "median_ret_%": round(s.median() * 100, 2),
        "best_%": round(s.max() * 100, 2),
        "worst_%": round(s.min() * 100, 2),
        "expectancy_%": round(s.mean() * 100, 2),  # 每次訊號的期望報酬(已含成本)
    }
