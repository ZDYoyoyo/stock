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

from src import db, twse_client as tw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40, help="回抓最近幾個日曆天")
    ap.add_argument("--sleep", type=float, default=0.4, help="每次請求間隔秒數")
    args = ap.parse_args()

    end = date.today()
    all_days = [(end - timedelta(days=i)) for i in range(args.days)][::-1]

    db.init_db()
    n_price = n_inst = n_margin = n_trading = 0
    last_trading_ymd = None

    with db.connect() as conn:
        for d in all_days:
            ymd = d.strftime("%Y%m%d")
            prows = tw.price(ymd)
            if not prows:          # 假日/無資料
                continue
            n_trading += 1
            last_trading_ymd = ymd
            n_price += db.upsert(conn, "price", prows)

            irows = tw.institutional(ymd)
            n_inst += db.upsert(conn, "institutional", irows)

            mrows = tw.margin(ymd)
            n_margin += db.upsert(conn, "margin", mrows)

            print(f"  {d.isoformat()}  price={len(prows)} inst={len(irows)} margin={len(mrows)}")
            time.sleep(args.sleep)

        # 用最後一個交易日補中文股名
        if last_trading_ymd:
            names = tw.stock_names(last_trading_ymd)
            rows = [{"stock_id": sid, "stock_name": name,
                     "industry": "", "type": "twse"}
                    for sid, name in names.items()]
            db.upsert(conn, "stock_info", rows)

    print(f"\n✅ 完成：{n_trading} 個交易日 | price {n_price} / inst {n_inst} / margin {n_margin} 筆")
    print("→ data/stock.db")


if __name__ == "__main__":
    main()
