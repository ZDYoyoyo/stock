"""T22 — 每日盤後自動掃描。

流程：增量更新最近資料 → 跑 T11(法人吸貨) + T16(抗跌強勢) → 輸出當日報告。
建議每個交易日傍晚（三大法人 T86 公布後，約 18:00~20:00）執行。

用法（專案根目錄）：
    python -m scripts.daily_scan                # 增量更新 + 掃描
    python -m scripts.daily_scan --no-update    # 只用現有 DB 掃描（不重抓）

本機排程：
    Linux/macOS cron（週一~五 19:00）：
        0 19 * * 1-5 cd /path/to/stock && /usr/bin/python3 -m scripts.daily_scan >> logs/scan.log 2>&1
    Windows 工作排程器：
        建立每日 19:00 觸發，動作 = python -m scripts.daily_scan（起始位置設為專案資料夾）
"""
import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR
from src.screeners import institutional_accumulation as t11
from src.screeners import relative_strength as t16
from src.screeners import market_breadth as breadth


def _update_data(days: int):
    print(f"[1/3] 增量更新最近 {days} 天資料 …")
    subprocess.run([sys.executable, "-m", "scripts.update_data", "--days", str(days)],
                   cwd=str(ROOT), check=True)


def _section(f, title, df, cols):
    f.write(f"\n## {title}\n\n")
    if df.empty:
        f.write("（今日無符合條件標的）\n")
    else:
        f.write(df[cols].head(15).to_markdown(index=False))
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true", help="不重抓資料，只用現有 DB")
    ap.add_argument("--days", type=int, default=12, help="增量更新回抓天數")
    args = ap.parse_args()

    if not args.no_update:
        _update_data(args.days)

    print("[2/3] 執行篩選 T11(法人吸貨) + T16(抗跌強勢) …")
    b = breadth.run()
    print("   " + breadth.summary_line(b))
    df11 = t11.run()
    df16 = t16.run()

    print("[3/3] 輸出報告 …")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_daily_scan.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 每日盤後掃描 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。法人買≠一定漲；上櫃波動高，部位放小、嚴設停損。\n\n")
        f.write(f"**{breadth.summary_line(b)}**\n")
        _section(f, "T11 法人默默吸貨（上市看投信／上櫃看外資）", df11,
                 ["stock_id", "name", "market", "investor", "close",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "margin_chg_%", "score"])
        _section(f, "T16 抗跌強勢股（相對大盤強弱）", df16,
                 ["stock_id", "name", "market", "return_%", "vs_market_%", "avg_vol_lots"])
        # 交集：兩張清單都出現＝法人買且抗跌，訊號更強
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n## ⭐ 雙訊號交集（法人吸貨 且 抗跌強勢）\n\n")
            if both:
                names = df11.set_index("stock_id")["name"].to_dict()
                for sid in both:
                    f.write(f"- {sid} {names.get(sid, '')}\n")
            else:
                f.write("（今日無交集）\n")

    print(f"✅ 完成 → {path}")
    print(f"   T11 {len(df11)} 檔 / T16 {len(df16)} 檔")


if __name__ == "__main__":
    main()
