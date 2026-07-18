"""盤中持股守衛 — 持續監控你的持股，一觸停損/停利就推 Telegram。

⚠️ 盤中(09:00~13:30)在本機掛著跑。每檔觸價只推一次(不洗版)，Ctrl+C 結束。
   即時價用 TWSE MIS(~20秒延遲)；只提醒不下單。

用法：
    python -m scripts.monitor_portfolio                 # 每60秒檢查
    python -m scripts.monitor_portfolio --interval 30
    python -m scripts.monitor_portfolio --once          # 只檢查一次(測試)
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import portfolio as pf
from src.notify import send_telegram, _tg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="檢查間隔秒數")
    ap.add_argument("--once", action="store_true", help="只檢查一次")
    args = ap.parse_args()

    if pf.load().empty:
        print("目前沒有持股。先用 python -m scripts.portfolio add ... 新增。")
        return

    token, chat = _tg()
    push_on = bool(token and chat)
    if not push_on:
        print("⚠️ 未設定 Telegram（.env 填 TELEGRAM_*），本次僅終端機提醒、不推手機。")

    n = len(pf.load())
    print(f"🛡️ 盤中持股守衛啟動，監控 {n} 檔，每 {args.interval}s 檢查（Ctrl+C 結束）")
    alerted = set()

    try:
        while True:
            view, summary = pf.status()
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] 總損益 {summary['總損益']:+,}（{summary['總報酬%']:+.2f}%）")
            for _, r in view.iterrows():
                sid, name, price = r["代號"], r["名稱"], r["現價"]
                pl, state = r["損益%"], str(r["狀態"])
                mark = ""
                triggered = ("觸停損" in state) or ("停利" in state)
                if triggered and sid not in alerted:
                    msg = (f"🔔 <b>{sid} {name}：{state}</b>\n"
                           f"現價 {price}｜損益 {pl}%")
                    if push_on:
                        ok, _ = send_telegram(msg)
                        mark = "  → 已推播 📲" if ok else "  → 推播失敗"
                    else:
                        mark = "  → (未設 Telegram)"
                    alerted.add(sid)
                print(f"  {sid} {name}: 現價 {price}｜{pl:+}%｜{state}{mark}")

            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止守衛。")


if __name__ == "__main__":
    main()
