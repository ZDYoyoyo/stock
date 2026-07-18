"""持股追蹤器 CLI — 記錄實單、看即時損益與停損警示。

用法（專案根目錄）：
  查看持股與損益（預設）：
    python -m scripts.portfolio
  新增/更新一筆持股：
    python -m scripts.portfolio add 2330 --lots 2 --price 1000 --stop 950 --target 1100
  移除：
    python -m scripts.portfolio remove 2330

持股存在 data/portfolio.csv（可用 Excel 直接編輯；不會被 git 上傳）。
損益為毛額，未計手續費/證交稅（來回約 0.6%）。即時價盤中用 MIS、盤後用收盤。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import portfolio as pf


def _show():
    view, summary = pf.status()
    if view.empty:
        print("目前沒有持股。用 add 新增，例如：")
        print("  python -m scripts.portfolio add 2330 --lots 2 --price 1000 --stop 950")
        return
    print("\n=== 持股損益 ===")
    print(view.to_string(index=False))
    print(f"\n總成本 {summary['總成本']:,}｜總損益 {summary['總損益']:,}"
          f"（{summary['總報酬%']:+.2f}%）｜持股 {summary['持股數']} 檔")
    alerts = view[view["狀態"].str.contains("觸停損|停利", na=False)]
    if not alerts.empty:
        print("\n🔔 警示：")
        for r in alerts.itertuples():
            print(f"  {r.代號} {r.名稱}：{r.狀態}（現價 {r.現價}）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="新增/更新持股")
    a.add_argument("stock_id")
    a.add_argument("--lots", type=float, required=True, help="張數")
    a.add_argument("--price", type=float, required=True, help="買入成本均價")
    a.add_argument("--stop", type=float, default=None, help="停損價")
    a.add_argument("--target", type=float, default=None, help="停利價")
    a.add_argument("--date", default="", help="買入日 YYYY-MM-DD")
    a.add_argument("--note", default="", help="備註")

    r = sub.add_parser("remove", help="移除持股")
    r.add_argument("stock_id")

    args = ap.parse_args()

    if args.cmd == "add":
        pf.add(args.stock_id, args.lots, args.price, args.stop, args.target, args.date, args.note)
        print(f"已記錄 {args.stock_id}：{args.lots} 張 @ {args.price}"
              + (f"，停損 {args.stop}" if args.stop else ""))
        _show()
    elif args.cmd == "remove":
        pf.remove(args.stock_id)
        print(f"已移除 {args.stock_id}")
        _show()
    else:
        _show()


if __name__ == "__main__":
    main()
