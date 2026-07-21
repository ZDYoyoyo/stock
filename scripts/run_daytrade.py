"""當沖候選 EOD 掃描 — 輸出盤中值得盯的高波動高流動股。

用法（專案根目錄）：
    python -m scripts.run_daytrade
前置：DB 需有近期資料（跑過 update_data）。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR
from src.screeners import day_trade_candidates as dt
from src import report_html
from src import regime as regime_mod

# 顯示欄序：多空傾向/與大盤 放前面（開盤前先定調方向），再看波動/量能
_COLS = ["stock_id", "name", "market", "close", "多空傾向", "與大盤",
         "當日振幅%", "均振幅%", "量能倍數", "日均量", "score"]


def main():
    df = dt.run()
    if df.empty:
        print("無符合條件標的（可放寬 config.DAYTRADE 門檻）。")
        return

    reg = regime_mod.assess()
    df = dt.add_bias(df, reg)
    cols = [c for c in _COLS if c in df.columns]

    print(f"\n=== 當沖候選（高波動+高流動，前 20）｜大盤：{regime_mod.summary_line(reg)} ===")
    print(report_html.rename_cn(df[cols].head(20)).to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_當沖候選.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 當沖候選（EOD 掃描）— {today}\n\n")
        f.write(f"> 🚦 {regime_mod.summary_line(reg)}\n")
        f.write("> ⚠️ 盤後掃描非即時訊號，僅縮小盤中要盯的清單。當沖勝率低、成本高，非投資建議。\n")
        f.write("> 用法：先看『多空傾向』定調今天這檔找多方或空方 setup，盤中再靠量價進出、嚴設停損。\n\n")
        f.write(report_html.rename_cn(df[cols].head(25)).to_markdown(index=False))
        f.write(f"\n\n> 📖 {report_html.DAYTRADE_NOTE}\n")
        f.write(f"> 📖 {report_html.BIAS_NOTE}\n")
    print(f"\n✅ 已輸出：{path}")


if __name__ == "__main__":
    main()
