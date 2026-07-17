"""盤中即時監控（TWSE MIS，免開戶）— 盯一籃股票並警示。

⚠️ 盤中(09:00~13:30)執行才有即時資料，約 20 秒延遲，適合監控/警示，非搶帽子。
   本工具只監控與提示，不下單；進出仍由你判斷、嚴設停損。

用法（本機、盤中執行）：
    python -m scripts.monitor_intraday --stocks 2330,2454,6669
    python -m scripts.monitor_intraday --from-daytrade 10   # 用當沖候選前10檔
    python -m scripts.monitor_intraday --stocks 2330 --once # 只抓一次（測試）

警示：接近漲/跌停、急拉急殺(±門檻)、突破當日高/跌破當日低。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.realtime import quote
from src.screeners import day_trade_candidates as dt


def _alerts(q, jump_pct):
    a = []
    p, lu, ld = q["price"], q["limit_up"], q["limit_down"]
    if p and lu and p >= lu * 0.985:
        a.append("🔴接近漲停")
    if p and ld and p <= ld * 1.015:
        a.append("🟢接近跌停")
    if q["chg_%"] is not None:
        if q["chg_%"] >= jump_pct:
            a.append(f"🚀急拉+{q['chg_%']}%")
        elif q["chg_%"] <= -jump_pct:
            a.append(f"⚡急殺{q['chg_%']}%")
    if p and q["high"] and p >= q["high"]:
        a.append("突破當日高")
    if p and q["low"] and p <= q["low"]:
        a.append("跌破當日低")
    return " ".join(a)


def _print_table(quotes, jump_pct):
    print(f"\n{'代號':<6}{'名稱':<8}{'現價':>9}{'漲跌%':>8}{'量':>9}  警示")
    print("-" * 70)
    for q in quotes:
        if not q["price"]:
            continue
        chg = f"{q['chg_%']:+.2f}" if q["chg_%"] is not None else "—"
        vol = f"{int(q['volume']):,}" if q["volume"] else "—"
        print(f"{q['stock_id']:<6}{(q['name'] or '')[:6]:<8}{q['price']:>9.2f}"
              f"{chg:>8}{vol:>9}  {_alerts(q, jump_pct)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", help="代號逗號分隔，如 2330,2454")
    ap.add_argument("--from-daytrade", type=int, metavar="N",
                    help="改用當沖候選前 N 檔")
    ap.add_argument("--interval", type=int, default=30, help="輪詢秒數")
    ap.add_argument("--jump", type=float, default=3.0, help="急拉急殺警示門檻%")
    ap.add_argument("--once", action="store_true", help="只抓一次")
    args = ap.parse_args()

    if args.from_daytrade:
        ids = dt.run().head(args.from_daytrade)["stock_id"].tolist()
    elif args.stocks:
        ids = [s.strip() for s in args.stocks.split(",")]
    else:
        print("請用 --stocks 或 --from-daytrade 指定監控清單")
        return
    print(f"監控 {len(ids)} 檔：{', '.join(ids)}（每 {args.interval}s 更新，Ctrl+C 結束）")

    try:
        while True:
            quotes = quote(ids)
            if not quotes:
                print("（抓不到報價；盤中才有即時資料）")
            else:
                _print_table(quotes, args.jump)
                print(f"更新時間 {quotes[0].get('time','')}")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止監控。")


if __name__ == "__main__":
    main()
