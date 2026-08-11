"""個股深掘 同業比較＋基本面納入定調＋財報紅旗 callout 測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import stock_deepdive as dd


@pytest.fixture
def _db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield db
    db.clear_cache()


def test_industry_peers_ranks_by_amount(_db, monkeypatch):
    # 三檔同業 + 一檔別業；本檔 1000。同業依成交額(close×volume)排序取前 2。
    with _db.connect() as conn:
        _db.upsert(conn, "stock_info", [
            {"stock_id": "1000", "stock_name": "本檔", "type": "twse"},
            {"stock_id": "1001", "stock_name": "同業大", "type": "twse"},
            {"stock_id": "1002", "stock_name": "同業中", "type": "twse"},
            {"stock_id": "1003", "stock_name": "同業小", "type": "twse"},
            {"stock_id": "2000", "stock_name": "別業", "type": "twse"}])
        _db.upsert(conn, "price", [
            {"date": "2026-08-11", "stock_id": s, "close": 100, "volume": v}
            for s, v in [("1000", 50), ("1001", 900), ("1002", 500), ("1003", 100), ("2000", 999)]])
    imap = {"1000": "水泥", "1001": "水泥", "1002": "水泥", "1003": "水泥", "2000": "鋼鐵"}
    peers, ind = dd.industry_peers("1000", n=2, imap=imap)
    assert ind == "水泥"
    assert [p[0] for p in peers] == ["1001", "1002"]     # 依成交額前2、排除別業與自己


def test_peer_table_marks_target_and_appends(monkeypatch):
    monkeypatch.setattr(dd, "valuation_snapshot",
                        lambda s: {"PER": 10.0, "PBR": 1.0, "殖利率%": 5.0})
    monkeypatch.setattr(dd, "monthly_revenue",
                        lambda s, months=1: pd.DataFrame([{"月份": "2026/07", "營收YoY%": 8.0}]))
    self_val = {"PER": 30.0, "PBR": 5.0, "殖利率%": 1.0}
    self_rev = pd.DataFrame([{"月份": "2026/07", "營收YoY%": 44.0}])
    df = dd.peer_table("2330", "台積電", self_val, self_rev, [("1101", "台泥")])
    assert df.iloc[0]["代號"] == "★2330" and df.iloc[0]["月營收YoY%"] == 44.0
    assert df.iloc[1]["代號"] == "1101" and df.iloc[1]["PER"] == 10.0


def test_fund_vote_and_flag_callouts():
    from scripts import run_stock as rs
    # 強基本面：營收+、EPS+、含金量高 → +3
    health = pd.DataFrame([{"含金量%": 120}])
    assert rs._fund_vote({"營收YoY%": 30, "EPS年增%": 20}, health, []) == 3
    # 弱：營收崩、EPS衰、含金量低、有紅旗 → -4
    weak = pd.DataFrame([{"含金量%": 20}])
    assert rs._fund_vote({"營收YoY%": -20, "EPS年增%": -30}, weak, ["營收崩"]) == -4
    # 無資料 → 0
    assert rs._fund_vote({}, pd.DataFrame(), []) == 0
    # 紅旗 callout
    assert "無明顯財務紅旗" in rs._flags_md([])
    assert "財務紅旗" in rs._flags_md(["獲利崩(EPS年增-60%)"])
    assert "warn" in rs._flags_html(["獲利崩"]) and "無明顯" in rs._flags_html([])
