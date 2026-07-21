"""執行 T11 法人默默吸貨篩選，輸出到終端機 + CSV + Markdown。

用法（專案根目錄）：
    python -m scripts.run_t11
前置：先跑過 python -m scripts.update_data
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR
from src.screeners import institutional_accumulation as t11
from src import report_html


def main():
    df = t11.run()
    if df.empty:
        print("⚠️ 沒有符合條件的股票（可到 src/config.py 放寬 T11 門檻）。")
        return

    top = df.head(10)
    print("\n=== T11 法人默默吸貨 候選（前 10）===")
    print(report_html.rename_cn(top).to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    csv_path = OUTPUT_DIR / f"{today}_T11.csv"
    md_path = OUTPUT_DIR / f"{today}_T11.md"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# T11 法人默默吸貨候選 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。法人買不代表一定漲，請再自行判斷。\n\n")
        f.write(report_html.rename_cn(top).to_markdown(index=False))
        f.write("\n")
    print(f"\n✅ 已輸出：\n  {csv_path}\n  {md_path}")


if __name__ == "__main__":
    main()
