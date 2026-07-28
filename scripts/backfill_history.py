"""P5 — 長歷史回補：逐檔 FinMind 抓多年 OHLCV+法人+資券，存成壓縮面板。

FinMind 免費層只能逐檔抓（全市場批次會 400），故取流動性前 N 檔逐檔回補。
輸出 data/history/backtest_panel.csv.gz（gzip；pandas 直接讀、零新依賴、寫一次不動，
不影響每日滾動 CSV 的 commit/clone 速度）。

⚠️ 免費層拿不到已下市股 → 仍有部分倖存者偏誤（要完全消除需 TWSE 舊版逐日端點）。

用法：python -m scripts.backfill_history --universe 150 --start 2022-01-01
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.run_portfolio_backtest import _load_panel_stock, _pick_universe

PANEL_PATH = ROOT / "data" / "history" / "backtest_panel.csv.gz"
_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume", "inst_net", "margin_balance"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=150)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--out", default=str(PANEL_PATH))
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"回補 {len(universe)} 檔（起始 {args.start}）→ {args.out}")
    frames, ok, fail = [], 0, 0
    for i, (sid, market) in enumerate(universe, 1):
        try:
            df = _load_panel_stock(sid, market, args.start, with_margin=True)
            if df is not None and len(df) >= 60:
                df = df.copy()
                df["stock_id"] = sid
                frames.append(df[[c for c in _COLS if c in df.columns]])
                ok += 1
                if i % 10 == 0 or i == len(universe):
                    print(f"  [{i}/{len(universe)}] 累積 {ok} 檔 ok")
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  ✗ {sid} {str(e)[:60]}")

    if not frames:
        print("無資料，結束")
        return
    panel = pd.concat(frames, ignore_index=True).sort_values(["stock_id", "date"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.out, index=False, encoding="utf-8", compression="gzip")

    size_mb = Path(args.out).stat().st_size / 1e6
    print(f"\n✅ {ok} 檔、{len(panel):,} 列 → {args.out}（{size_mb:.1f} MB, gzip）")
    if fail:
        print(f"   （{fail} 檔略過）")


if __name__ == "__main__":
    main()
