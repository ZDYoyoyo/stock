"""P5 — 長歷史回補：逐檔 FinMind 抓多年 OHLCV+法人+資券，存成壓縮面板。

FinMind 免費層只能逐檔抓（全市場批次會 400），故取流動性前 N 檔逐檔回補。
輸出 data/history/backtest_panel.csv.gz（gzip；pandas 直接讀、零新依賴、寫一次不動，
不影響每日滾動 CSV 的 commit/clone 速度）。

⚠️ 免費層拿不到已下市股 → 仍有部分倖存者偏誤（要完全消除需 TWSE 舊版逐日端點）。

用法：
    python -m scripts.backfill_history --universe 150 --start 2022-01-01   # 全新回補（整份重寫）
    python -m scripts.backfill_history --update                            # 增量：補新日子＋新股，不重抓舊的
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.run_portfolio_backtest import _load_panel_stock, _pick_universe
from src.db import read_table

PANEL_PATH = ROOT / "data" / "history" / "backtest_panel.csv.gz"
_COLS = ["stock_id", "date", "open", "high", "low", "close", "volume", "inst_net", "margin_balance"]


def _market_map() -> dict:
    info = read_table("stock_info")
    return dict(zip(info["stock_id"].astype(str), info["type"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=150)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--out", default=str(PANEL_PATH))
    ap.add_argument("--update", action="store_true",
                    help="增量：讀現有面板，只補『新股』與『每檔缺的新日子』，不重抓已有的")
    args = ap.parse_args()

    out_path = Path(args.out)
    old = None
    last_by_sid = {}
    if args.update and out_path.exists():
        old = pd.read_csv(out_path, dtype={"stock_id": str})
        last_by_sid = old.groupby("stock_id")["date"].max().to_dict()
        print(f"增量模式：現有 {old['stock_id'].nunique()} 檔、{len(old):,} 列，"
              f"最新日 {old['date'].max()}")

    # universe = 流動性前 N ∪ 現有面板已有的股（增量時不丟舊股）
    mkt = _market_map()
    targets = dict(_pick_universe(args.universe))            # sid -> market
    if old is not None:
        for sid in last_by_sid:
            targets.setdefault(sid, mkt.get(sid, "twse"))

    mode = "增量更新" if args.update else "全新回補"
    print(f"{mode} {len(targets)} 檔 → {args.out}")
    frames, ok, fail, skip = [], 0, 0, 0
    items = list(targets.items())
    for i, (sid, market) in enumerate(items, 1):
        # 已有的股：只從『最後一天』起抓（含當天，靠後面 dedupe 去重）；新股：從 --start
        start_for = last_by_sid.get(sid, args.start)
        try:
            df = _load_panel_stock(sid, market, start_for, with_margin=True)
            if df is not None and not df.empty:
                df = df.copy()
                df["stock_id"] = sid
                frames.append(df[[c for c in _COLS if c in df.columns]])
                ok += 1
            else:
                skip += 1
            if i % 10 == 0 or i == len(items):
                print(f"  [{i}/{len(items)}] 抓到 {ok} 檔")
        except Exception as e:
            fail += 1
            print(f"  ✗ {sid} {str(e)[:60]}")

    if not frames and old is None:
        print("無資料，結束")
        return
    parts = ([old] if old is not None else []) + frames
    panel = (pd.concat(parts, ignore_index=True)
             .drop_duplicates(["stock_id", "date"], keep="last")   # 新資料覆蓋舊
             .sort_values(["stock_id", "date"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_path, index=False, encoding="utf-8", compression="gzip")

    size_mb = out_path.stat().st_size / 1e6
    print(f"\n✅ {panel['stock_id'].nunique()} 檔、{len(panel):,} 列、最新日 {panel['date'].max()}"
          f" → {args.out}（{size_mb:.1f} MB, gzip）")
    if fail or skip:
        print(f"   （{fail} 檔失敗、{skip} 檔無新資料）")


if __name__ == "__main__":
    main()

