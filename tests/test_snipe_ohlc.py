"""昨日隔日沖鎖碼候選 → 今日開高低收走勢（snipe_ohlc）測試。跑法：python -m pytest tests/ -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _tmp(monkeypatch, tmp_path):
    from src import db, picks_tracker as pt
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    monkeypatch.setattr(pt, "PICKS_CSV", tmp_path / "picks.csv")
    yield
    db.clear_cache()


def _seed():
    from src import db, picks_tracker as pt
    # 昨日(D1)鎖碼精選：9999；今日(D2)開高走低、8888 開低走高
    price = [
        {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94, "close": 100, "volume": 10},
        {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101, "close": 102, "volume": 10},
        {"date": "D1", "stock_id": "8888", "open": 48, "high": 50, "low": 47, "close": 50, "volume": 10},
        {"date": "D2", "stock_id": "8888", "open": 49, "high": 55, "low": 48, "close": 54, "volume": 10},
    ]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame(
        [{"stock_id": "9999", "name": "妖股"}, {"stock_id": "8888", "name": "強股"}])}, n=15)


def test_snipe_ohlc_computes_gap_and_oc():
    from src import picks_tracker as pt
    _seed()
    so = pt.snipe_ohlc("D3")             # today>D2；prior 鎖碼日=D1，今交易日=D2
    assert so["date"] == "D1" and so["trade_date"] == "D2"
    r = {x["stock_id"]: x for x in so["rows"]}
    # 9999：昨收100→今開108(跳空+8%)、今收102(盤中 (102-108)/108=-5.56% 開高走低)
    assert r["9999"]["跳空%"] == 8.0
    assert r["9999"]["盤中%"] == -5.56
    assert r["9999"]["今高"] == 110 and r["9999"]["今低"] == 101
    assert r["9999"]["漲跌%"] == 2.0
    # 8888：昨收50→今開49(跳空-2%)、今收54(盤中 (54-49)/49=+10.2% 開低走高)
    assert r["8888"]["跳空%"] == -2.0
    assert r["8888"]["盤中%"] == 10.2

    stats = pt.snipe_ohlc_stats(so)
    assert stats["n"] == 2 and stats["oc_down"] == 1     # 9999 盤中走低、8888 走高


def test_snipe_ohlc_empty_when_no_prior():
    from src import picks_tracker as pt
    assert pt.snipe_ohlc("D3") == {}                     # 無任何鎖碼紀錄


def test_snipe_ohlc_pick_label_on_non_trading_day():
    """pick 標籤落在非交易日(如週末)→ 應退到 ≤標籤的最後交易日查昨收，不撈空。"""
    from src import db, picks_tracker as pt
    # 交易日只有 D1、D3(D2 為非交易日)；pick 卻被標在 D2
    price = [
        {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94, "close": 100, "volume": 10},
        {"date": "D3", "stock_id": "9999", "open": 108, "high": 110, "low": 101, "close": 102, "volume": 10},
    ]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D2", {"隔日沖鎖碼": pd.DataFrame([{"stock_id": "9999", "name": "妖股"}])}, n=15)
    so = pt.snipe_ohlc("D4")
    assert so["date"] == "D1" and so["trade_date"] == "D3"   # 基準退到 D1、今日 D3
    assert so["rows"][0]["跳空%"] == 8.0                      # 昨收100(D1)→今開108(D3)
