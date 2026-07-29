"""回補估值面板（殖利率/PER/PBR）→ data/history/valuation_panel.csv.gz（供長期價值軌回測）。

長期價值是慢因子 → 用『月頻』回補（每月首交易日的 TWSE 估值 BWIBBU_d，免費逐日端點），
55 個月僅 ~55 次呼叫、省額度。對齊 backtest_panel 的月份。

⚠️ BWIBBU_d 僅上市(TWSE)；上櫃估值另有端點，本版先做上市（長期價值/高息股多在上市）。
產出每檔每月一列：date, stock_id, yield, per, pbr（ROE估=pbr/per×100 回測時自算）。

用法：
    python -m scripts.backfill_valuation                 # 對齊面板月份全量回補
    python -m scripts.backfill_valuation --update        # 增量：只補面板沒有的新月份
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src import twse_client as tw

PANEL_PATH = ROOT / "data" / "history" / "backtest_panel.csv.gz"
OUT_PATH = ROOT / "data" / "history" / "valuation_panel.csv.gz"


def _month_first_days() -> list[str]:
    """面板每月首交易日（ISO），供月頻回補對齊。"""
    d = pd.read_csv(PANEL_PATH, dtype={"stock_id": str})
    months: dict[str, str] = {}
    for dt in sorted(d["date"].unique()):
        months.setdefault(dt[:7], dt)   # 每月第一個出現的交易日
    return list(months.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.4, help="每次請求間隔秒數")
    ap.add_argument("--update", action="store_true", help="增量：只補現有面板沒有的月份")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    out_path = Path(args.out)
    old = None
    have_dates = set()
    if args.update and out_path.exists():
        old = pd.read_csv(out_path, dtype={"stock_id": str})
        have_dates = set(old["date"].unique())
        print(f"現有估值面板 {old['date'].nunique()} 月、{len(old):,} 列")

    days = [d for d in _month_first_days() if d not in have_dates]
    print(f"要回補 {len(days)} 個月首交易日估值 → {args.out}")

    frames, ok, empty = [], 0, 0
    for i, iso in enumerate(days, 1):
        ymd = iso.replace("-", "")
        try:
            val = tw.valuation(ymd)
        except Exception:
            val = []
        if val:
            frames.append(pd.DataFrame([{
                "date": iso, "stock_id": v["stock_id"],
                "yield": v["yield"], "per": v["per"], "pbr": v["pbr"],
            } for v in val]))
            ok += 1
        else:
            empty += 1
        if i % 10 == 0 or i == len(days):
            print(f"  [{i}/{len(days)}] 成功 {ok} 月")
        time.sleep(args.sleep)

    if not frames and old is None:
        print("無資料，結束")
        return
    parts = ([old] if old is not None else []) + frames
    panel = (pd.concat(parts, ignore_index=True)
             .drop_duplicates(["date", "stock_id"], keep="last")
             .sort_values(["date", "stock_id"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_path, index=False, encoding="utf-8", compression="gzip")

    size_kb = out_path.stat().st_size / 1e3
    print(f"\n✅ {panel['date'].nunique()} 月、{panel['stock_id'].nunique()} 檔、{len(panel):,} 列"
          f"（{panel['date'].min()}~{panel['date'].max()}）→ {args.out}（{size_kb:.0f} KB, gzip）")
    if empty:
        print(f"   （{empty} 月無估值資料）")


if __name__ == "__main__":
    main()
