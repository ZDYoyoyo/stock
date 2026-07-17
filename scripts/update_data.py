"""更新資料地基：抓 TWSE 全市場逐日資料寫入 SQLite（上市）。

用法（專案根目錄）：
    python -m scripts.update_data --days 40
假日自動略過。三大法人當日盤後較晚公布，抓不到會自動跳過該日。
"""
import argparse
import time
from datetime import date, timedelta

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, twse_client as tw, tpex_client as tp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40, help="回抓最近幾個日曆天")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次請求間隔秒數")
    ap.add_argument("--market", choices=["all", "twse", "tpex"], default="all",
                    help="抓哪個市場（預設兩者都抓）")
    args = ap.parse_args()

    end = date.today()
    all_days = [(end - timedelta(days=i)) for i in range(args.days)][::-1]

    sources = []
    if args.market in ("all", "twse"):
        sources.append(("twse", tw))
    if args.market in ("all", "tpex"):
        sources.append(("tpex", tp))

    db.init_db()
    n_price = n_inst = n_margin = 0
    last_ymd = {name: None for name, _ in sources}

    with db.connect() as conn:
        for d in all_days:
            ymd = d.strftime("%Y%m%d")
            line = [d.isoformat()]
            for name, client in sources:
                prows = client.price(ymd)
                if not prows:      # 假日/無資料
                    line.append(f"{name}:—")
                    continue
                last_ymd[name] = ymd
                n_price += db.upsert(conn, "price", prows)
                irows = client.institutional(ymd)
                n_inst += db.upsert(conn, "institutional", irows)
                mrows = client.margin(ymd)
                n_margin += db.upsert(conn, "margin", mrows)
                line.append(f"{name}:p{len(prows)}/i{len(irows)}/m{len(mrows)}")
                time.sleep(args.sleep)
            if any(":—" not in x for x in line[1:]):
                print("  " + "  ".join(line))

        # 用最後一個交易日補中文股名 + 標記市場
        for name, client in sources:
            if last_ymd[name]:
                names = client.stock_names(last_ymd[name])
                rows = [{"stock_id": sid, "stock_name": nm, "industry": "", "type": name}
                        for sid, nm in names.items()]
                db.upsert(conn, "stock_info", rows)

    print(f"\n✅ 完成：price {n_price} / inst {n_inst} / margin {n_margin} 筆 → data/stock.db")


if __name__ == "__main__":
    main()
