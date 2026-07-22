"""盤中持股守衛 — 持續監控你的持股，一觸停損/停利就推 Telegram。

⚠️ 盤中(09:00~13:30)在本機掛著跑。每檔觸價只推一次(不洗版)，Ctrl+C 結束。
   即時價用 TWSE MIS(~20秒延遲)；只提醒不下單。

即時價來源：有設 SHIOAJI_API_KEY(永豐金)＝券商級即時 → 預設間隔縮短到 15s；
           否則用免費 TWSE MIS(~20秒延遲) → 預設 60s（更快沒意義且會撞流量）。

用法：
    python -m scripts.monitor_portfolio                 # 自動判斷間隔(Shioaji 15s / MIS 60s)
    python -m scripts.monitor_portfolio --interval 30   # 手動指定
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
    ap.add_argument("--interval", type=int, default=None,
                    help="檢查間隔秒數（未指定則依報價源自動：Shioaji 15s / MIS 60s）")
    ap.add_argument("--once", action="store_true", help="只檢查一次")
    args = ap.parse_args()

    if pf.load().empty:
        print("目前沒有持股。先用 python -m scripts.portfolio add ... 新增。")
        return

    # 報價源偵測：有永豐金 Shioaji 即時 → 可縮短間隔；否則 MIS 有延遲，維持較長間隔
    try:
        from src import shioaji_client
        fast = shioaji_client.available()
    except Exception:
        fast = False
    src_name = "永豐金 Shioaji（即時）" if fast else "TWSE MIS（~20秒延遲）"
    interval = args.interval if args.interval is not None else (15 if fast else 60)
    if fast and args.interval is not None and args.interval < 5:
        interval = 5  # 護欄：Shioaji 有流量限制，別衝太快

    token, chat = _tg()
    push_on = bool(token and chat)
    if not push_on:
        print("⚠️ 未設定 Telegram（.env 填 TELEGRAM_*），本次僅終端機提醒、不推手機。")

    n = len(pf.load())
    print(f"🛡️ 盤中持股守衛啟動，監控 {n} 檔，報價源：{src_name}，每 {interval}s 檢查（Ctrl+C 結束）")
    if fast:
        print("   ⚡ 已偵測到 Shioaji → 間隔縮短。注意行情流量依下單量而定，盯太多檔+太密可能撞限。")
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
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止守衛。")


if __name__ == "__main__":
    main()
