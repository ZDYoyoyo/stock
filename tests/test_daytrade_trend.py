"""當沖比熱度趨勢單元測試（DB 用 tmp、read_table 快取每測清空）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import day_trade_signal as dts


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()                       # 清 read_table 進程快取，避免跨測污染
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield
    db.clear_cache()


def _seed(price, day_trade):
    from src import db
    with db.connect() as conn:
        db.upsert(conn, "price", price)
        db.upsert(conn, "day_trade", day_trade)


def _px(date, sid, vol):
    return {"date": date, "stock_id": sid, "close": 100.0, "volume": vol}


def test_trend_heating_up(monkeypatch):
    # 前4日當沖比≈10%，今日衝到 40% → 升溫
    price = [_px(d, "5328", 1000) for d in ["D1", "D2", "D3", "D4", "D5"]]
    dtv = [{"date": d, "stock_id": "5328", "dt_vol": v}
           for d, v in zip(["D1", "D2", "D3", "D4", "D5"], [100, 100, 100, 100, 400])]
    _seed(price, dtv)
    out = dts.trend(["5328"], n=5).set_index("stock_id")
    assert out.loc["5328", "當沖比趨勢"] == "🔥升溫"
    assert out.loc["5328", "當沖比均5日"] == round((10 + 10 + 10 + 10 + 40) / 5, 1)  # 16.0


def test_trend_cooling_down(monkeypatch):
    # 前4日≈50%，今日掉到 10% → 降溫
    price = [_px(d, "5328", 1000) for d in ["D1", "D2", "D3", "D4", "D5"]]
    dtv = [{"date": d, "stock_id": "5328", "dt_vol": v}
           for d, v in zip(["D1", "D2", "D3", "D4", "D5"], [500, 500, 500, 500, 100])]
    _seed(price, dtv)
    out = dts.trend(["5328"], n=5).set_index("stock_id")
    assert out.loc["5328", "當沖比趨勢"] == "❄降溫"


def test_trend_needs_two_days(monkeypatch):
    _seed([_px("D1", "5328", 1000)], [{"date": "D1", "stock_id": "5328", "dt_vol": 500}])
    assert dts.trend(["5328"], n=5).empty   # 僅 1 天 → 無趨勢


def test_fetch_market_day_shape(monkeypatch):
    # monkeypatch TWSE/TPEX 回傳 → 組出落 DB 的 rows
    monkeypatch.setattr(dts.tw, "day_trade", lambda ymd: {"2330": 6033})
    monkeypatch.setattr(dts.tp, "day_trade", lambda ymd: {"6488": 1200})
    rows = dts.fetch_market_day("2026-08-05")
    got = {r["stock_id"]: r["dt_vol"] for r in rows}
    assert got == {"2330": 6033, "6488": 1200}
    assert all(r["date"] == "2026-08-05" for r in rows)
