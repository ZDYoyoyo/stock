"""T12 — 月營收動能股（全市場官方月營收，免 FinMind）。

用法（專案根目錄）：
    python -m scripts.run_t12

找營收 YoY 強、近月動能加速（加速度=單月YoY−累計YoY>0）、累計也成長的成長股。
⚠️ 研究用途，非投資建議。門檻在 src/config.py 的 T12。
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR, T12
from src.screeners import revenue_momentum as t12


def main():
    print("[T12] 抓全市場月營收（TWSE+TPEX OpenAPI）…")
    df = t12.run()
    ym = df.attrs.get("ym", "?")
    if df.empty:
        print(f"（{ym} 無符合條件標的，可放寬 config.T12 門檻）")
        return

    cols = ["stock_id", "name", "market", "產業", "close",
            "YoY%", "累計YoY%", "MoM%", "加速度", "站上20MA", "score"]
    view = df[cols].head(T12.TOP_N)
    print(f"\n=== T12 月營收動能股（營收月 {ym}，前 {len(view)}）===")
    print(view.to_string(index=False))
    print("\n看點：加速度>0=近月成長率高於年初至今均(動能加速)；站上20MA=市場也認同。")
    print("⚠️ 營收僅財務面一角，仍需搭配籌碼/技術；已排除營建(認列失真)與 YoY 爆表(基期效應)雜訊。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_T12月營收動能.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# T12 月營收動能股 — 營收月 {ym}（{today}）\n\n")
        f.write("> ⚠️ 研究用途，非投資建議。加速度=單月YoY−累計YoY（>0=動能加速）。\n\n")
        f.write(view.to_markdown(index=False) + "\n")
    print(f"\n✅ 報告 → {path}")


if __name__ == "__main__":
    main()
