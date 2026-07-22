"""個股籌碼診斷 CLI — 三大法人(外資/投信/自營)＋融資融券逐日 + 今vs昨漲跌歸因。

用法（專案根目錄）：
    python -m scripts.diagnose 2330            # 診斷台積電
    python -m scripts.diagnose 2330 --days 15  # 看更長天數
前置：DB 需有近期資料（跑過 update_data）。
⚠️ 研究用途，非投資建議；歸因為規則式推敲，非唯一原因。
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR
from src.db import connect
from src.screeners import chip_diagnosis as cd


def _name(sid: str) -> str:
    with connect() as conn:
        r = conn.execute("SELECT stock_name FROM stock_info WHERE stock_id=?", (sid,)).fetchone()
    return r[0] if r else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stock_id")
    ap.add_argument("--days", type=int, default=10, help="逐日表天數")
    args = ap.parse_args()

    sid = args.stock_id.strip()
    df = cd._fetch(sid, args.days)
    if df.empty:
        print(f"查無 {sid} 的資料（確認代號、或先跑 update_data）。")
        return
    nm = _name(sid)
    tbl = cd.daily_table(df).tail(args.days)
    lines = cd.attribute(df)
    verdict = cd.one_line(df)

    print(f"\n=== {sid} {nm}　籌碼診斷（近 {args.days} 日）===")
    print("單位：法人=淨買賣超張(正=買超)；融資/融券=餘額張；增減=較前一日\n")
    print(tbl.to_string(index=False))
    print(f"\n── 今日漲跌歸因（今 vs 昨）──")
    for ln in lines:
        print(f"  ・{ln}")
    print(f"\n🔎 一句話：{verdict}")
    print("\n⚠️ 研究用途，非投資建議；歸因為規則式推敲，消息面/國際盤等未納入。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{date.today().isoformat()}_籌碼診斷_{sid}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {sid} {nm} 籌碼診斷 — {date.today().isoformat()}\n\n")
        f.write("> 單位：法人=淨買賣超張(正=買超)；融資/融券=餘額張；增減=較前一日。研究用途非投資建議。\n\n")
        f.write(tbl.to_markdown(index=False) + "\n\n")
        f.write("## 今日漲跌歸因（今 vs 昨）\n\n")
        for ln in lines:
            f.write(f"- {ln}\n")
        f.write(f"\n**🔎 一句話：{verdict}**\n")
    print(f"\n✅ 報告 → {path}")


if __name__ == "__main__":
    main()
