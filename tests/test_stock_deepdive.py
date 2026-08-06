"""個股深掘資料層單元測試（DB 用 tmp、分點 monkeypatch）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import stock_deepdive as dd


def _tmp_db(monkeypatch, tmp_path, price, inst=None, margin=None, sbl=None):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
        db.upsert(conn, "price", price)
        if inst:
            db.upsert(conn, "institutional", inst)
        if margin:
            db.upsert(conn, "margin", margin)
        if sbl:
            db.upsert(conn, "sbl", sbl)


def test_chip_timeline_computes_changes(monkeypatch, tmp_path):
    price = [{"date": "2026-08-04", "stock_id": "1303", "close": 170.5, "volume": 900},
             {"date": "2026-08-05", "stock_id": "1303", "close": 177.0, "volume": 800}]
    sbl = [{"date": "2026-08-04", "stock_id": "1303", "sbl_balance": 19927},
           {"date": "2026-08-05", "stock_id": "1303", "sbl_balance": 20651}]
    margin = [{"date": "2026-08-04", "stock_id": "1303", "margin_balance": 53695, "short_balance": 1495},
              {"date": "2026-08-05", "stock_id": "1303", "margin_balance": 55817, "short_balance": 1579}]
    _tmp_db(monkeypatch, tmp_path, price, margin=margin, sbl=sbl)
    tl = dd.chip_timeline("1303", 30)
    last = tl.iloc[-1]
    assert last["漲跌%"] == round((177.0 - 170.5) / 170.5 * 100, 2)   # +3.81
    assert last["借券增減"] == 724            # 20651 − 19927
    assert last["融資增減"] == 2122           # 55817 − 53695
    assert last["融券增減"] == 84


def test_chip_timeline_empty_when_no_price(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path, [])
    assert dd.chip_timeline("9999", 30).empty


def test_broker_timeline_main_net_and_pressure(monkeypatch):
    tl = pd.DataFrame({"date": ["D1", "D2"], "量": [1000, 1000]})
    # D1 甲昨買 +600；D2 甲今賣 −400 → 隔日沖賣壓 400/1000=40%
    nets = {"D1": {"甲": 600, "乙": 200, "丙": -100},
            "D2": {"甲": -400, "乙": 50, "丁": 300}}
    monkeypatch.setattr(dd, "_branch_nets", lambda sid, dates: nets)
    bt = dd.broker_timeline("1303", tl)
    d2 = bt[bt["date"] == "D2"].iloc[0]
    assert d2["主力淨額"] == -50            # 前15買(300+50) + 前15賣(−400)
    assert d2["隔日沖賣壓%"] == 40.0
    assert pd.isna(bt[bt["date"] == "D1"].iloc[0]["隔日沖賣壓%"])   # 首日無前日


def test_daytrader_regulars_counts_repeat_offenders(monkeypatch):
    tl = pd.DataFrame({"date": ["D1", "D2", "D3", "D4"], "量": [1000] * 4})
    # 甲：D1買→D2賣(次1)、D3買→D4賣(次2)；乙只一次(D3買→D4賣)
    nets = {"D1": {"甲": 500}, "D2": {"甲": -300},
            "D3": {"甲": 400, "乙": 300}, "D4": {"甲": -200, "乙": -100}}
    monkeypatch.setattr(dd, "_branch_nets", lambda sid, dates: nets)
    reg = dd.daytrader_regulars("1303", tl)
    top = reg.iloc[0]
    assert top["分點"] == "甲" and top["隔日沖次數"] == 2


def test_broker_timeline_empty_when_unavailable(monkeypatch):
    tl = pd.DataFrame({"date": ["D1"], "量": [1000]})
    monkeypatch.setattr(dd, "_branch_nets", lambda sid, dates: {})
    assert dd.broker_timeline("1303", tl).empty
