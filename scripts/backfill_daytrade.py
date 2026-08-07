"""回補當沖成交量歷史 → DB day_trade 表（供當沖比熱度趨勢欄）。

當沖比%＝當日沖銷量÷總量，單日值只是快照；要有歷史才能看『熱度升溫/降溫』（妖股啟動 vs 退燒）。
資料源 TWSE+TPEX 官方當沖統計（免費，各 1 call/交易日），端點吃日期參數→可回補歷史。

用法（專案根目錄）：
    python -m scripts.backfill_daytrade                # 回補 price 表所有交易日（缺的才抓）
    python -m scripts.backfill_daytrade --days 60      # 只補最近 N 交易日
    python -m scripts.backfill_daytrade --force        # 不跳過已存在的日子（重抓覆蓋）

交易日曆取自本地 price 表；跑前請先 `python -m scripts.sync_data load`。
回補後記得 `python -m scripts.sync_data dump` 寫回 CSV 並 commit（DB 是暫時的）。
"""
import argparse
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import db, day_trade_signal as dts


def _trading_days() -> list[str]:
    with db.connect() as conn:
        return sorted(pd.read_sql("SELECT DISTINCT date FROM price", conn)["date"].tolist())


def _existing_days() -> set[str]:
    with db.connect() as conn:
        try:
            return set(pd.read_sql("SELECT DISTINCT date FROM day_trade", conn)["date"].tolist())
        except Exception:
            return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="只補最近 N 交易日（預設補全部缺的）")
    ap.add_argument("--force", action="store_true", help="不跳過已存在的日子")
    ap.add_argument("--sleep", type=float, default=0.3, help="每次請求間隔秒數")
    args = ap.parse_args()

    db.init_db()
    days = _trading_days()
    if not days:
        raise SystemExit("price 表是空的，請先 python -m scripts.sync_data load")
    if args.days:
        days = days[-args.days:]

    have = set() if args.force else _existing_days()
    todo = [d for d in days if d not in have]
    print(f"交易日 {len(days)}，已有 {len(have)}，待補 {len(todo)}")

    n_rows = n_days = 0
    for i, d in enumerate(todo, 1):
        rows = dts.fetch_market_day(d)
        if not rows:                        # 假日/當日尚未公布
            print(f"  [{i}/{len(todo)}] {d} —（無資料）")
            continue
        with db.connect() as conn:          # 逐日 commit：中斷可續跑
            n_rows += db.upsert(conn, "day_trade", rows)
        n_days += 1
        print(f"  [{i}/{len(todo)}] {d}  {len(rows)} 檔")
        time.sleep(args.sleep)

    print(f"\n✅ 完成：當沖 {n_days} 交易日 / {n_rows} 筆 → DB day_trade")
    print("   記得 dump 寫回 CSV：python -m scripts.sync_data dump --keep-days 260")


if __name__ == "__main__":
    main()
