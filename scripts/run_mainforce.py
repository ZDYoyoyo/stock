"""主力籌碼綜合 — 找主力進駐、散戶退場的股票（免費；有分點會員自動加值）。

用法（專案根目錄）：
    python -m scripts.run_mainforce                 # 全市場主力進駐排行（前25）
    python -m scripts.run_mainforce --stocks 2330,2454   # 看指定股籌碼全貌
    python -m scripts.run_mainforce --top 40

分數 = 法人佔量% + 千張大戶週增 + 散戶退場(融資減)。
偵測到 FinMind Sponsor token → 自動多出「分點主力買賣超」欄。門檻在 config.MAINFORCE。
⚠️ 研究用途，非投資建議。
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR, MAINFORCE as M
from src.screeners import main_force as mf
from src import broker_client as bc
from src import report_html
from src.db import connect


def _latest_price_date() -> str:
    with connect() as conn:
        r = conn.execute("SELECT MAX(date) FROM price").fetchone()
    return r[0] if r and r[0] else date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", help="逗號分隔代號，只看這幾檔的籌碼")
    ap.add_argument("--top", type=int, default=M.TOP_N)
    args = ap.parse_args()

    has_branch = bc.available()
    print(f"分點資料：{'✅ 可用（FinMind Sponsor）' if has_branch else '⚪ 免費模式（法人+千張+融資綜合）'}")

    df = mf.run()
    if df.empty:
        print("（無資料，先跑 update_data / update_holders）")
        return

    if args.stocks:
        want = [s.strip() for s in args.stocks.split(",") if s.strip()]
        df = df[df["stock_id"].isin(want)].reset_index(drop=True)
        title = "指定股籌碼"
    else:
        df = df.head(args.top).reset_index(drop=True)
        title = f"主力進駐排行前{len(df)}"

    px_date = _latest_price_date()
    df = mf.enrich_branches(df, px_date, n=len(df) if args.stocks else M.BRANCH_ENRICH_N)

    cols = [c for c in ["stock_id", "name", "market", "close", "法人淨買張", "法人佔量%",
                        "千張大戶%", "千張週增pp", "融資增減%",
                        "主力買超張", "主力賣超張", "主力淨額張", "主力分"]
            if c in df.columns]
    print(f"\n=== 主力籌碼綜合（{title}）===")
    print(report_html.rename_cn(df[cols]).to_string(index=False))
    if not has_branch:
        print("\n💡 想看真實『券商分點主力進出』→ 辦 FinMind Sponsor、把 token 填進 .env 即自動啟用。")
    print("解讀：主力分高=法人買+大戶增持+散戶退場（籌碼集中）；仍需搭配環境紅綠燈與技術面。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_主力籌碼.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 主力籌碼綜合 — {title}（{today}）\n\n")
        f.write("> ⚠️ 研究用途，非投資建議。分數=法人佔量%+千張大戶週增+散戶退場(融資減)。\n")
        f.write(f"> 分點資料：{'FinMind Sponsor 已啟用' if has_branch else '免費模式（辦會員可加值）'}\n\n")
        f.write(report_html.rename_cn(df[cols]).to_markdown(index=False) + "\n")
    print(f"\n✅ 報告 → {path}")


if __name__ == "__main__":
    main()
