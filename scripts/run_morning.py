"""早上開盤前快報 — 環境紅綠燈 + 全球市場（含昨夜美股/費半）。

盤前(約 08:00~09:00)跑：美股已收盤，費半是「昨夜完整收盤」= 真正影響今天台股開盤那根。
只看環境、不重跑選股，很快。選股請用傍晚的 run_all。

用法：
    python -m scripts.run_morning            # 印出快報
    python -m scripts.run_morning --notify    # 順便推 Telegram
⚠️ 研究用途，非投資建議。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import regime as regime_mod, global_market as gm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true", help="推播到 Telegram/Email")
    args = ap.parse_args()

    print("盤前快報：環境紅綠燈 + 全球市場（含昨夜美股）…\n")
    reg = regime_mod.assess()
    glob = gm.fetch()

    reg_line = regime_mod.summary_line(reg)
    sox = gm.sox_signal(glob)
    glob_lines = gm.summary_lines(glob)

    print(f"🚦 {reg_line}")
    if sox:
        print(sox)
    print("🌍 " + " ｜ ".join(glob_lines))

    # 有持股就順帶提醒（開盤前掃一眼）
    try:
        from src import portfolio as pf
        view, summ = pf.status()
        if not view.empty:
            print(f"\n📋 我的持股：總損益 {summ['總損益']:+,}（{summ['總報酬%']:+.2f}%）")
            alerts = view[view["狀態"].astype(str).str.contains("觸停損|停利", na=False)]
            for _, r in alerts.iterrows():
                print(f"   🔔 {r['代號']} {r['名稱']}：{r['狀態']}（現價 {r['現價']}）")
    except Exception:
        pass

    print("\n重點：費半是昨夜美股完整收盤，影響今天台股開盤方向；環境紅燈時開盤別急著追。")

    if args.notify:
        from src.notify import notify
        msg = (f"<b>🌅 台股盤前快報</b>\n🚦 {reg_line}\n{sox}\n🌍 " + " ｜ ".join(glob_lines))
        ok, ch, detail = notify(msg, subject="台股盤前快報")
        print(f"\n📲 推播（{ch}）：{'成功' if ok else '失敗 - ' + detail}")


if __name__ == "__main__":
    main()
