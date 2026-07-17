"""一鍵跑全部：環境紅綠燈 + 三軌選股，輸出單一整合報告。

流程：
  (可選) 增量更新資料
  環境：多空紅綠燈 + 全球市場（費半等）
  波段軌：T11 法人吸貨 + T16 抗跌強勢 + 雙訊號交集
  長期軌：價值+成長+配息（較慢，FinMind 深掘；--skip-longterm 可略過）
  當沖軌：當沖候選（高波動+高流動）

用法（專案根目錄）：
    python -m scripts.run_all                 # 全部
    python -m scripts.run_all --no-update     # 不重抓資料
    python -m scripts.run_all --skip-longterm # 略過較慢的長期軌
"""
import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR
from src import regime as regime_mod, global_market as gm
from src.screeners import institutional_accumulation as t11
from src.screeners import relative_strength as t16
from src.screeners import day_trade_candidates as daytrade
from src.screeners import long_term_value as lt


def _update(days: int):
    print(f"[更新] 抓最近 {days} 天資料 …")
    subprocess.run([sys.executable, "-m", "scripts.update_data", "--days", str(days)],
                   cwd=str(ROOT), check=True)


def _section(f, title, df, cols, n=15):
    f.write(f"\n## {title}\n\n")
    if df is None or df.empty:
        f.write("（今日無符合條件標的）\n")
    else:
        keep = [c for c in cols if c in df.columns]
        f.write(df[keep].head(n).to_markdown(index=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--skip-longterm", action="store_true")
    ap.add_argument("--days", type=int, default=12)
    args = ap.parse_args()

    if not args.no_update:
        _update(args.days)

    print("[環境] 多空紅綠燈 + 全球市場 …")
    reg = regime_mod.assess()
    glob = gm.fetch()
    print("   " + regime_mod.summary_line(reg))
    print("   " + gm.sox_signal(glob))

    print("[波段] T11 + T16 …")
    df11, df16 = t11.run(), t16.run()
    print("[當沖] 候選掃描 …")
    dfdt = daytrade.run()
    dflt = None
    if not args.skip_longterm:
        print("[長期] 價值+成長+配息（較慢）…")
        dflt = lt.run(verbose=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_run_all.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 台股每日整合報告 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。先看環境紅綠燈決定要不要出手。\n\n")
        f.write("## 🚦 環境紅綠燈\n\n")
        f.write(f"- **{regime_mod.summary_line(reg)}**\n")
        f.write(f"- {gm.sox_signal(glob)}\n")
        f.write(f"- 全球：{' ｜ '.join(gm.summary_lines(glob))}\n")

        _section(f, "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", df11,
                 ["stock_id", "name", "market", "investor", "close",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "score"])
        _section(f, "🟡 波段｜T16 抗跌強勢", df16,
                 ["stock_id", "name", "market", "return_%", "vs_market_%"])
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n### ⭐ 波段雙訊號交集（法人買且抗跌）\n\n")
            nm = df11.set_index("stock_id")["name"].to_dict()
            f.write("".join(f"- {s} {nm.get(s,'')}\n" for s in both) if both else "（無）\n")

        _section(f, "🟢 長期｜價值+成長+配息", dflt,
                 ["stock_id", "name", "產業", "close", "殖利率%", "PER",
                  "ROE估%", "營收YoY%", "連配息年", "score"])
        _section(f, "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", dfdt,
                 ["stock_id", "name", "market", "close", "今日振幅%", "均振幅%", "量能倍數"])

    print(f"\n✅ 整合報告 → {path}")
    print(f"   波段T11 {len(df11)} / T16 {len(df16)} ｜ 當沖 {len(dfdt)}"
          + (f" ｜ 長期 {len(dflt)}" if dflt is not None else " ｜ 長期(略過)"))


if __name__ == "__main__":
    main()
