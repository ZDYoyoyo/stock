"""長期持有軌 — 價值+成長+配息選股。

用法（專案根目錄）：
    python -m scripts.run_longterm
前置：DB 需有價格（跑過 update_data，用來定位最新交易日）。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR
from src.screeners import long_term_value as lt
from src import report_html


def main():
    print("長期價值篩選（TWSE 估值粗篩 → FinMind 深掘）…")
    df = lt.run()
    if df.empty:
        print("無符合條件標的（可放寬 config.LONGTERM 門檻）。")
        return

    print("\n=== 長期持有候選（前 15）===")
    print(report_html.rename_cn(df.head(15)).to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_長期價值.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 長期持有候選（價值+成長+配息）— {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。長期持有仍須看產業前景與財報品質。\n\n")
        f.write(report_html.rename_cn(df.head(20)).to_markdown(index=False))
        f.write(f"\n\n> {report_html.GLOSSARY}\n")
    print(f"\n✅ 已輸出：{path}")


if __name__ == "__main__":
    main()
