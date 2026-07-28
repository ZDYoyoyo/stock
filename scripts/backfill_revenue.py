"""回補月營收歷史面板 → data/history/revenue_panel.csv.gz（供 T12 回測）。

為什麼要另建：官方月營收 OpenAPI 只給『最新月』，回測要 point-in-time 需要每月序列。
FinMind TaiwanStockMonthRevenue 有多年歷史（免費層，逐檔），故逐檔回補。

產出每檔每月一列：stock_id, pub_date, ym, yoy, cum_yoy
  - yoy      單月營收年增% = 當月 / 去年同月 − 1
  - cum_yoy  累計營收年增% = 年初至當月累計 / 去年同期累計 − 1
  - pub_date 該月營收『實際公布日』：FinMind create_time；早期無 create_time 者
             以『次月10日』(台股法定公布期限)保守估 → 回測對齊，無前視偏誤。

universe 對齊 backtest_panel.csv.gz（同一批股票，回測才能兩面板對得上）。
額度：每檔 1 次 FinMind call；預設對齊面板 ~200 檔。--max-new 續傳保護。

用法：
    python -m scripts.backfill_revenue                    # 對齊股價面板全量
    python -m scripts.backfill_revenue --limit 10         # 小樣本試管線
    python -m scripts.backfill_revenue --update           # 增量：只補面板沒有的新股
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.finmind_client import fetch

PANEL_PATH = ROOT / "data" / "history" / "backtest_panel.csv.gz"
OUT_PATH = ROOT / "data" / "history" / "revenue_panel.csv.gz"


def _pub_date(ym: str, create_time: str) -> str:
    """公布日：有 create_time 用它；否則次月10日（台股每月10日前須公布上月營收）。"""
    ct = str(create_time or "").strip()
    if len(ct) >= 10 and ct[:4].isdigit():
        return ct[:10]
    y, m = int(ym[:4]), int(ym[5:7])
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{ny:04d}-{nm:02d}-10"


def _build_rows(sid: str, data: list) -> list:
    """把 FinMind 月營收原始列 → [{stock_id, pub_date, ym, yoy, cum_yoy}] 升序。"""
    rev = {}          # (year, month) -> revenue
    ctime = {}        # (year, month) -> create_time
    for d in data:
        y, m = d.get("revenue_year"), d.get("revenue_month")
        r = d.get("revenue")
        if y is None or m is None or r is None:
            continue
        rev[(y, m)] = r
        ctime[(y, m)] = d.get("create_time", "")

    out = []
    for (y, m) in sorted(rev.keys()):
        prev = rev.get((y - 1, m))
        yoy = (rev[(y, m)] - prev) / prev * 100 if prev and prev > 0 else None
        # 累計：年初至當月
        cum = sum(rev[(y, mm)] for mm in range(1, m + 1) if (y, mm) in rev)
        cum_prev_ok = all((y - 1, mm) in rev for mm in range(1, m + 1))
        cum_prev = sum(rev[(y - 1, mm)] for mm in range(1, m + 1)) if cum_prev_ok else None
        cum_yoy = (cum - cum_prev) / cum_prev * 100 if cum_prev and cum_prev > 0 else None
        if yoy is None and cum_yoy is None:
            continue
        ym = f"{y:04d}-{m:02d}"
        out.append({
            "stock_id": sid,
            "pub_date": _pub_date(ym, ctime.get((y, m))),
            "ym": ym,
            "yoy": round(yoy, 2) if yoy is not None else None,
            "cum_yoy": round(cum_yoy, 2) if cum_yoy is not None else None,
        })
    return out


def _universe() -> list:
    df = pd.read_csv(PANEL_PATH, dtype={"stock_id": str})
    return sorted(df["stock_id"].unique())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01",
                    help="抓多早（需早於回測起始1年以上，才能算首年YoY/累計基期）")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 檔（小樣本試管線；0=全部）")
    ap.add_argument("--update", action="store_true",
                    help="增量：讀現有面板，只補『新股』，現有股原封保留、不碰 API")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    out_path = Path(args.out)
    old = None
    have = set()
    if args.update and out_path.exists():
        old = pd.read_csv(out_path, dtype={"stock_id": str})
        have = set(old["stock_id"].unique())
        print(f"現有月營收面板 {len(have)} 檔、{len(old):,} 列")

    sids = _universe()
    if args.limit:
        sids = sids[:args.limit]
    targets = [s for s in sids if s not in have]
    print(f"要抓 {len(targets)} 檔月營收（start={args.start}）→ {args.out}")

    frames, ok, fail, empty = [], 0, 0, 0
    for i, sid in enumerate(targets, 1):
        try:
            data = fetch("TaiwanStockMonthRevenue", start_date=args.start, data_id=sid)
            rows = _build_rows(sid, data) if data else []
            if rows:
                frames.append(pd.DataFrame(rows))
                ok += 1
            else:
                empty += 1
        except Exception as e:
            fail += 1
            print(f"  ✗ {sid} {str(e)[:60]}")
        if i % 20 == 0 or i == len(targets):
            print(f"  [{i}/{len(targets)}] 成功 {ok} 檔")

    if not frames and old is None:
        print("無資料，結束")
        return
    parts = ([old] if old is not None else []) + frames
    panel = (pd.concat(parts, ignore_index=True)
             .drop_duplicates(["stock_id", "ym"], keep="last")
             .sort_values(["stock_id", "pub_date"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_path, index=False, encoding="utf-8", compression="gzip")

    size_kb = out_path.stat().st_size / 1e3
    print(f"\n✅ {panel['stock_id'].nunique()} 檔、{len(panel):,} 列、公布日 "
          f"{panel['pub_date'].min()}→{panel['pub_date'].max()} → {args.out}"
          f"（{size_kb:.0f} KB, gzip）")
    if fail or empty:
        print(f"   （{fail} 檔失敗、{empty} 檔無月營收）")


if __name__ == "__main__":
    main()
