"""輕量策略驗證：跨一籃代表股，比較單一 vs 組合訊號的 edge。

比較變體（訊號日皆為「法人連買>=K」）：
  A 基準         ：法人連買
  B +抗跌         ：且進場前動能為正
  C +營收不衰退    ：且最新月營收 YoY>=0
  D 組合(B+C)     ：抗跌 且 營收不衰退
輸出各變體：交易數、勝率、平均報酬、期望值（皆已含手續費+證交稅）。

用法：
    python -m scripts.run_validation --hold 10 --k 4
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.backtest import signal_trades
from src.db import connect

# 代表性一籃子（跨產業、含上市/上櫃龍頭）
BASKET_TWSE = ["2330", "2317", "2454", "2308", "2382", "3711", "2412", "2882",
               "2891", "1301", "2002", "1216", "2207", "2603", "2379", "3034",
               "2357", "2395", "1101", "2884", "2105", "2206", "6669", "2618",
               "3008", "2409", "2344", "3231"]
BASKET_TPEX = ["6488", "3105", "4966", "8299", "3529", "5274", "6415", "1785"]


def _market_investor():
    """從 DB 取每檔市場，決定用投信(上市)或外資(上櫃)。"""
    try:
        with connect() as conn:
            info = pd.read_sql("SELECT stock_id, type FROM stock_info", conn)
        return dict(zip(info["stock_id"], info["type"]))
    except Exception:
        return {}


def _stats(df, label):
    if df.empty:
        return {"變體": label, "交易數": 0}
    s = df["ret"]
    return {"變體": label, "交易數": len(s),
            "勝率%": round((s > 0).mean() * 100, 1),
            "平均報酬%": round(s.mean() * 100, 2),
            "中位%": round(s.median() * 100, 2),
            "期望值%": round(s.mean() * 100, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--start", default="2023-01-01")
    args = ap.parse_args()

    mkt = _market_investor()
    all_trades = []
    basket = [(s, "trust") for s in BASKET_TWSE] + [(s, "foreign") for s in BASKET_TPEX]
    for sid, default_inv in basket:
        inv = "foreign" if mkt.get(sid) == "tpex" else default_inv
        try:
            t = signal_trades(sid, investor=inv, consec_k=args.k, hold_days=args.hold, start=args.start)
            if not t.empty:
                all_trades.append(t)
            print(f"  {sid}({'外資' if inv=='foreign' else '投信'}) 訊號 {len(t)}")
        except Exception as e:
            print(f"  {sid} 失敗 {e}")
        time.sleep(0.4)

    if not all_trades:
        print("無交易資料")
        return
    df = pd.concat(all_trades, ignore_index=True)

    rows = [
        _stats(df, "A 基準(法人連買)"),
        _stats(df[df.mom_ok], "B +抗跌(動能>0)"),
        _stats(df[df.rev_known & df.rev_ok], "C +營收不衰退"),
        _stats(df[df.mom_ok & df.rev_known & df.rev_ok], "D 組合(抗跌+營收)"),
    ]
    out = pd.DataFrame(rows)
    print(f"\n=== 策略驗證（連買>={args.k}日、持有{args.hold}日、含成本；共 {len(df)} 筆基準訊號）===")
    print(out.to_string(index=False))
    print("\n看點：B/C/D 的勝率與期望值若明顯高於 A，代表『加條件過濾』確實提升 edge。")
    print("注意：允許重疊持有、樣本有限，屬方向性驗證，非精算績效。")


if __name__ == "__main__":
    main()
