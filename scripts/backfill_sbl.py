"""回補借券賣出餘額歷史 → DB sbl 表（法人真實空單，供借券增減/趨勢欄與未來回測）。

借券賣出餘額＝法人/外資向券商借股放空、尚未回補的在外空單（張）。單日數字無意義，
要有歷史才能算『借券增減』與趨勢。Sponsor 可一次抓全市場（1 call/交易日）。

用法（專案根目錄）：
    python -m scripts.backfill_sbl                 # 回補 price 表所有交易日（缺的才抓）
    python -m scripts.backfill_sbl --days 10       # 只補最近 N 交易日（每日盤後增量）
    python -m scripts.backfill_sbl --force         # 不跳過已存在的日子（重抓覆蓋）

交易日曆取自本地 price 表；跑前請先 `python -m scripts.sync_data load`。
回補後記得 `python -m scripts.sync_data dump` 寫回 CSV 並 commit（DB 是暫時的）。
"""
import argparse
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import db, sbl_signal


def _trading_days() -> list[str]:
    with db.connect() as conn:
        return sorted(pd.read_sql("SELECT DISTINCT date FROM price", conn)["date"].tolist())


def _existing_days() -> set[str]:
    with db.connect() as conn:
        try:
            return set(pd.read_sql("SELECT DISTINCT date FROM sbl", conn)["date"].tolist())
        except Exception:
            return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="只補最近 N 交易日（預設補全部缺的）")
    ap.add_argument("--force", action="store_true", help="不跳過已存在的日子")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次請求間隔秒數")
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
        rows = sbl_signal.fetch_market_day(d)
        if not rows:                # 假日/當日尚未公布
            print(f"  [{i}/{len(todo)}] {d} —（無資料）")
            continue
        # 逐日開關連線 commit：縮短寫鎖、進度即時落地（中斷可續跑），避免單一長交易鎖整個 DB
        with db.connect() as conn:
            n_rows += db.upsert(conn, "sbl", rows)
        n_days += 1
        print(f"  [{i}/{len(todo)}] {d}  {len(rows)} 檔")
        time.sleep(args.sleep)

    print(f"\n✅ 完成：借券 {n_days} 交易日 / {n_rows} 筆 → DB sbl")
    print("   記得 dump 寫回 CSV：python -m scripts.sync_data dump --keep-days 260")


if __name__ == "__main__":
    main()
