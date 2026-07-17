"""T30 — 對 T11 短名單做基本面深掘，輸出「籌碼 + 基本面」合併表。

用法（專案根目錄）：
    python -m scripts.run_t30
前置：DB 需已有資料（跑過 update_data / daily_scan）。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR
from src.enrich import enrich
from src.screeners import institutional_accumulation as t11


def main():
    df = t11.run()
    if df.empty:
        print("T11 無短名單，無需深掘。")
        return

    print(f"對 T11 短名單 {len(df)} 檔做基本面深掘（FinMind）…")
    fund = enrich(df["stock_id"].tolist())
    merged = df.merge(fund, on="stock_id", how="left")

    cols = ["stock_id", "name", "market", "investor", "close", "score",
            "營收月", "營收YoY%", "近3月YoY均%", "PER", "PBR", "殖利率%", "基本面評註"]
    view = merged[cols]
    print("\n=== T11 + 基本面 ===")
    print(view.to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_T11_深掘.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# T11 短名單 + 基本面深掘 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。「法人買 + 基本面」二次驗證用。\n\n")
        f.write(view.to_markdown(index=False))
        f.write("\n")
    print(f"\n✅ 已輸出：{path}")


if __name__ == "__main__":
    main()
