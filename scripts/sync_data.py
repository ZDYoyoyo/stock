"""資料同步：CSV 歷史 ⇄ SQLite DB（雲端每日累積用）。

用法（專案根目錄）：
    python -m scripts.sync_data load    # 用 data/history/*.csv 重建 DB（session 開始時）
    python -m scripts.sync_data dump    # 把 DB 寫回 CSV（更新資料後、commit 前）
    python -m scripts.sync_data dump --keep-days 900   # 只留近 N 交易日，控制檔案大小

雲端每日流程：load → update_data → 分析 → dump → git commit data/history。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import datastore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["load", "dump"])
    ap.add_argument("--keep-days", type=int, default=None,
                    help="dump 時只保留近 N 個交易日（控制 CSV 大小）")
    args = ap.parse_args()

    if args.action == "load":
        if not datastore.has_history():
            print("（data/history 無 CSV，跳過 load；首次請先 dump 現有 DB）")
            return
        res = datastore.load()
        print("✅ 由 CSV 重建 DB：" + "，".join(f"{k} {v}" for k, v in res.items()))
    else:
        res = datastore.dump(keep_days=args.keep_days)
        print("✅ DB 寫回 CSV（data/history）：" + "，".join(f"{k} {v}" for k, v in res.items()))


if __name__ == "__main__":
    main()
