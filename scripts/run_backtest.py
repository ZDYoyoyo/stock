"""T23 — 對指定股票回測「法人連買」訊號（測試用，抓少量檔）。

用法（專案根目錄）：
    python -m scripts.run_backtest --stocks 2206,6669 --hold 10 --k 4
    python -m scripts.run_backtest --stocks 4167 --investor foreign
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.backtest import backtest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", required=True, help="股票代號，逗號分隔，如 2206,6669")
    ap.add_argument("--investor", choices=["trust", "foreign"], default="trust",
                    help="訊號法人：trust=投信(上市) / foreign=外資(上櫃)")
    ap.add_argument("--k", type=int, default=4, help="連續買超天數門檻")
    ap.add_argument("--hold", type=int, default=10, help="持有交易日數")
    ap.add_argument("--start", default="2023-01-01", help="回測起始日")
    args = ap.parse_args()

    rows = []
    for sid in args.stocks.split(","):
        sid = sid.strip()
        print(f"回測 {sid} …")
        rows.append(backtest(sid, investor=args.investor, consec_k=args.k,
                             hold_days=args.hold, start=args.start))

    df = pd.DataFrame(rows)
    print("\n=== 回測結果（訊號=法人連買>=%d日，持有%d日，已含手續費+證交稅）===" % (args.k, args.hold))
    print(df.to_string(index=False))
    print("\n提醒：單/少檔、樣本少，僅為引擎測試；正式評估需全市場多檔、多參數與分樣本外驗證。")


if __name__ == "__main__":
    main()
