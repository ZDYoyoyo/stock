"""回補千張大戶歷史 → DB big_holders 表（大戶持股趨勢，供多週增減欄與個股深掘曲線）。

TDCC 免費端點只給「最新一週快照」，趨勢得靠 update_holders 每週往前累積（起步很淺）。
FinMind Sponsor 的 TaiwanStockHoldingSharesPer 可一次抓全市場某週（1 call/週），
且日期標籤與 pct 標準與 TDCC 完全一致 → 回補列可無縫併入現有 big_holders。

用法（專案根目錄）：
    python -m scripts.backfill_holders                # 回補近 52 週（缺的才抓）
    python -m scripts.backfill_holders --weeks 104    # 回補近 N 週
    python -m scripts.backfill_holders --start 2024-01-01
    python -m scripts.backfill_holders --force        # 不跳過已存在的週次（重抓覆蓋）

日更仍走免費 TDCC（scripts.update_holders 不受影響，掉回免費照跑）。
回補後記得 `python -m scripts.sync_data dump` 寫回 CSV 並 commit（DB 是暫時的）。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.finmind_client import fetch

_DATASET = "TaiwanStockHoldingSharesPer"
_STOCK_RE = re.compile(r"^[1-9]\d{3}$")            # 4 位數普通股（對齊 TDCC 口徑）
_LV_1000 = "more than 1,000,001"                    # 千張大戶（≥1000張＝≥1,000,001股）
_LV_400 = {                                         # ≥400張（≥400,001股）＝千張以下大戶累加
    "400,001-600,000", "600,001-800,000", "800,001-1,000,000", _LV_1000,
}


def aggregate_market(rows: list[dict]) -> list[dict]:
    """全市場某週分級明細 → [{date, stock_id, pct_1000, pct_400}]（只留普通股）。

    pct_1000 = 「more than 1,000,001」分級的 percent；
    pct_400  = 所有 ≥400,001 股分級的 percent 加總（含千張級）。
    """
    agg: dict[str, dict] = {}
    for r in rows:
        sid = str(r.get("stock_id", ""))
        if not _STOCK_RE.match(sid):
            continue
        lv = r.get("HoldingSharesLevel")
        pct = r.get("percent") or 0.0
        d = agg.setdefault(sid, {"date": r.get("date"), "stock_id": sid,
                                 "pct_1000": 0.0, "pct_400": 0.0})
        if lv == _LV_1000:
            d["pct_1000"] += pct
        if lv in _LV_400:
            d["pct_400"] += pct
    return [{"date": v["date"], "stock_id": k,
             "pct_1000": round(v["pct_1000"], 2), "pct_400": round(v["pct_400"], 2)}
            for k, v in agg.items()]


def _weekly_dates(start: str, end: str, probe: str = "2330") -> list[str]:
    """以最具流動性股探出區間內的週次結算日（FinMind 大戶週頻日期標籤）。"""
    data = fetch(_DATASET, start_date=start, end_date=end, data_id=probe)
    return sorted({r["date"] for r in data})


def _existing_weeks() -> set[str]:
    with db.connect() as conn:
        try:
            import pandas as pd
            return set(pd.read_sql("SELECT DISTINCT date FROM big_holders", conn)["date"].tolist())
        except Exception:
            return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=52, help="回補近 N 週（預設 52）")
    ap.add_argument("--start", type=str, default=None, help="起始日 YYYY-MM-DD（優先於 --weeks）")
    ap.add_argument("--force", action="store_true", help="不跳過已存在的週次（重抓覆蓋）")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次請求間隔秒數")
    args = ap.parse_args()

    db.init_db()
    end = date.today().isoformat()
    start = args.start or (date.today() - timedelta(weeks=args.weeks + 1)).isoformat()

    print(f"探週次日期 {start} ~ {end} …")
    weeks = _weekly_dates(start, end)
    if not weeks:
        raise SystemExit("探不到週次日期（檢查 FINMIND_TOKEN 與會員等級）。")

    have = set() if args.force else _existing_weeks()
    todo = [w for w in weeks if w not in have]
    print(f"區間週次 {len(weeks)}，已有 {len(have & set(weeks))}，待補 {len(todo)}")

    n_rows = n_weeks = 0
    for i, w in enumerate(todo, 1):
        raw = fetch(_DATASET, start_date=w, end_date=w)   # 全市場單週（1 call）
        rows = aggregate_market(raw)
        if not rows:
            print(f"  [{i}/{len(todo)}] {w} —（無資料）")
            continue
        with db.connect() as conn:                        # 逐週 commit：中斷可續跑
            n_rows += db.upsert(conn, "big_holders", rows)
        n_weeks += 1
        print(f"  [{i}/{len(todo)}] {w}  {len(rows)} 檔")
        time.sleep(args.sleep)

    print(f"\n✅ 完成：千張大戶 {n_weeks} 週 / {n_rows} 筆 → DB big_holders")
    print("   記得 dump 寫回 CSV：python -m scripts.sync_data dump --keep-days 260")


if __name__ == "__main__":
    main()
