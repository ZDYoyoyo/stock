"""千張大戶回補：分級聚合純函式測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_holders import aggregate_market


def _row(sid, lv, pct, date="2026-07-31"):
    return {"date": date, "stock_id": sid, "HoldingSharesLevel": lv, "percent": pct}


def test_aggregate_maps_levels_to_pct1000_and_pct400():
    rows = [
        _row("2330", "1-999", 1.05),
        _row("2330", "1,000-5,000", 3.21),
        _row("2330", "400,001-600,000", 1.05),
        _row("2330", "600,001-800,000", 0.94),
        _row("2330", "800,001-1,000,000", 0.73),
        _row("2330", "more than 1,000,001", 85.09),
        _row("2330", "total", 100.0),
        _row("2330", "差異數調整（說明4）", 0.0),
    ]
    out = {r["stock_id"]: r for r in aggregate_market(rows)}
    r = out["2330"]
    assert r["pct_1000"] == 85.09                       # 只算千張級
    assert r["pct_400"] == round(1.05 + 0.94 + 0.73 + 85.09, 2)   # 87.81，≥400張加總含千張
    assert r["date"] == "2026-07-31"


def test_aggregate_filters_non_common_stocks():
    # ETF(0050)、權證(非4碼) 應被濾掉，只留 4 碼普通股
    rows = [
        _row("0050", "more than 1,000,001", 50.0),
        _row("2330", "more than 1,000,001", 85.0),
        _row("03001", "more than 1,000,001", 10.0),
    ]
    out = {r["stock_id"] for r in aggregate_market(rows)}
    assert out == {"2330"}


def test_aggregate_empty():
    assert aggregate_market([]) == []
