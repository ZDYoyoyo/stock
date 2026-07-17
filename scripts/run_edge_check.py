"""驗證訊號是否有『真 edge』：法人連買 vs 任意日進場基準（多持有天數）。

回答的核心問題：訊號的報酬是選股功力(alpha) 還是只是大盤漂移(beta)？
作法：同一籃股票，比較「訊號日進場」與「任意日進場」在 5/10/20/40 天持有的期望報酬。
若訊號期望值沒有明顯高於基準 → 沒有真 edge。

用法：
    python -m scripts.run_edge_check
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.backtest import _load_stock, _revenue_yoy_lookup, _round_trip_return
from src.db import connect
from scripts.run_validation import BASKET_TWSE, BASKET_TPEX

HOLDS = [5, 10, 20, 40]
K, MOM = 4, 10


def main():
    with connect() as c:
        mkt = dict(pd.read_sql("SELECT stock_id,type FROM stock_info", c).values)
    agg = {h: {"A": [], "D": [], "base": []} for h in HOLDS}

    for sid in BASKET_TWSE + BASKET_TPEX:
        inv = "foreign" if mkt.get(sid) == "tpex" else "trust"
        df = _load_stock(sid, "2023-01-01")
        if df.empty or len(df) < 80:
            time.sleep(0.3)
            continue
        yoy = _revenue_yoy_lookup(sid, "2023-01-01")
        col = "trust_net" if inv == "trust" else "foreign_net"
        net, op, cl, dt = (df[col].tolist(), df["open"].tolist(),
                           df["close"].tolist(), df["date"].tolist())
        consec = [0] * len(net)
        for i in range(len(net)):
            consec[i] = consec[i - 1] + 1 if (net[i] > 0 and i > 0 and net[i - 1] > 0) \
                else (1 if net[i] > 0 else 0)
        for h in HOLDS:
            le = -1
            for i in range(MOM, len(net) - h - 1):     # 基準：任意日輪動
                if i <= le or op[i + 1] <= 0:
                    continue
                agg[h]["base"].append(_round_trip_return(op[i + 1], cl[i + 1 + h]))
                le = i + 1 + h
            le = -1
            for i in range(MOM, len(net) - h - 1):     # 訊號 A / D
                if consec[i] < K or i <= le or op[i + 1] <= 0 or cl[i - MOM] <= 0:
                    continue
                r = _round_trip_return(op[i + 1], cl[i + 1 + h])
                agg[h]["A"].append(r)
                yv = yoy(dt[i])
                if cl[i] > cl[i - MOM] and yv is not None and yv >= 0:
                    agg[h]["D"].append(r)
                le = i + 1 + h
        time.sleep(0.3)

    print(f"\n{'持有':>4} | {'基準(任意日)':>16} | {'A法人連買':>16} | {'D組合':>14} | A超額")
    print("-" * 78)
    for h in HOLDS:
        b, a, d = (pd.Series(agg[h]["base"]), pd.Series(agg[h]["A"]), pd.Series(agg[h]["D"]))
        bm, am, dm = b.mean() * 100, a.mean() * 100, d.mean() * 100
        print(f"{h:>4} | 期望{bm:+.2f}% 勝{(b>0).mean()*100:.0f}% n{len(b):>4} | "
              f"期望{am:+.2f}% 勝{(a>0).mean()*100:.0f}% n{len(a):>4} | "
              f"期望{dm:+.2f}% n{len(d):>4} | {am-bm:+.2f}%")
    print("\n若『A超額』時正時負且接近 0 → 訊號賺的是 beta 非 alpha，勿當機械買訊。")


if __name__ == "__main__":
    main()
