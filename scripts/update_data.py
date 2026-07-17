"""更新資料地基（T01~T03 + 融資）：抓 FinMind 全市場資料寫入 SQLite。

用法：
    python -m scripts.update_data --days 30
在專案根目錄執行。第一次會建立 data/stock.db。
"""
import argparse
from datetime import date, timedelta

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.finmind_client import fetch


def _to_lots(shares) -> int:
    """股數轉張數（1 張 = 1000 股）。"""
    try:
        return int(round(float(shares) / 1000))
    except (TypeError, ValueError):
        return 0


def update_stock_info(conn):
    data = fetch("TaiwanStockInfo", start_date="2020-01-01")
    rows, seen = [], set()
    for d in data:
        sid = d["stock_id"]
        if sid in seen:
            continue
        seen.add(sid)
        rows.append({
            "stock_id": sid,
            "stock_name": d.get("stock_name", ""),
            "industry": d.get("industry_category", ""),
            "type": d.get("type", ""),
        })
    print(f"stock_info: {db.upsert(conn, 'stock_info', rows)} 檔")


def update_price(conn, start, end):
    data = fetch("TaiwanStockPrice", start_date=start, end_date=end)
    rows = [{
        "date": d["date"], "stock_id": d["stock_id"],
        "open": d.get("open"), "high": d.get("max"),
        "low": d.get("min"), "close": d.get("close"),
        "volume": _to_lots(d.get("Trading_Volume")),
    } for d in data]
    print(f"price: {db.upsert(conn, 'price', rows)} 筆")


def update_institutional(conn, start, end):
    data = fetch("TaiwanStockInstitutionalInvestorsBuySell", start_date=start, end_date=end)
    # 依 (date, stock_id) 彙整三大法人淨買超（股 → 張）
    agg: dict[tuple, dict] = {}
    name_map = {
        "Foreign_Investor": "foreign_net", "Foreign_Dealer_Self": "foreign_net",
        "Investment_Trust": "trust_net",
        "Dealer_self": "dealer_net", "Dealer_Self": "dealer_net",
        "Dealer_Hedging": "dealer_net",
    }
    for d in data:
        key = (d["date"], d["stock_id"])
        row = agg.setdefault(key, {"date": d["date"], "stock_id": d["stock_id"],
                                   "foreign_net": 0, "trust_net": 0, "dealer_net": 0})
        col = name_map.get(d.get("name"))
        if col:
            net = _to_lots(d.get("buy", 0)) - _to_lots(d.get("sell", 0))
            row[col] += net
    print(f"institutional: {db.upsert(conn, 'institutional', list(agg.values()))} 筆")


def update_margin(conn, start, end):
    data = fetch("TaiwanStockMarginPurchaseShortSale", start_date=start, end_date=end)
    rows = [{
        "date": d["date"], "stock_id": d["stock_id"],
        "margin_balance": _to_lots(d.get("MarginPurchaseTodayBalance", 0) * 1000)
        if isinstance(d.get("MarginPurchaseTodayBalance"), (int, float)) else 0,
    } for d in data]
    # MarginPurchaseTodayBalance 單位已是「張」，直接取用
    for r, d in zip(rows, data):
        r["margin_balance"] = int(d.get("MarginPurchaseTodayBalance") or 0)
    print(f"margin: {db.upsert(conn, 'margin', rows)} 筆")


def update_revenue(conn, start):
    data = fetch("TaiwanStockMonthRevenue", start_date=start)
    rows = [{
        "stock_id": d["stock_id"],
        "year": d.get("revenue_year"), "month": d.get("revenue_month"),
        "revenue": d.get("revenue"),
    } for d in data]
    print(f"revenue: {db.upsert(conn, 'revenue', rows)} 筆")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="回抓最近幾天（日資料）")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    s, e = start.isoformat(), end.isoformat()
    rev_start = (end - timedelta(days=400)).isoformat()  # 月營收多抓一年

    db.init_db()
    with db.connect() as conn:
        print(f"更新區間 {s} ~ {e}")
        update_stock_info(conn)
        update_price(conn, s, e)
        update_institutional(conn, s, e)
        update_margin(conn, s, e)
        update_revenue(conn, rev_start)
    print("✅ 資料更新完成 → data/stock.db")


if __name__ == "__main__":
    main()
