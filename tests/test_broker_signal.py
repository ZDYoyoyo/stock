"""分點主力淨額 + 隔日沖賣壓% 單元測試（離線：monkeypatch broker_client）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import broker_signal as brk


@pytest.fixture(autouse=True)
def _isolate_broker_cache(monkeypatch, tmp_path):
    """每測用 tmp DB 隔離分點快取：避免假日期(DAY/PREV)寫入真 DB 或跨測互污染。"""
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(brk, "_cache_ready", False)


def _rows(nets: dict):
    """{分點: 淨買張} → FinMind 分點 rows（張×1000→股，拆成 buy 或 sell 單邊）。"""
    out = []
    for name, lots in nets.items():
        shares = int(lots * 1000)
        if shares >= 0:
            out.append({"securities_trader": name, "buy": shares, "sell": 0})
        else:
            out.append({"securities_trader": name, "buy": 0, "sell": -shares})
    return out


def _patch(monkeypatch, day_nets, prev_nets, available=True):
    monkeypatch.setattr(brk.bc, "available", lambda *a, **k: available)

    def fake_raw(sid, date):
        return _rows(prev_nets if date == "PREV" else day_nets)
    monkeypatch.setattr(brk.bc, "_raw", fake_raw)


def test_main_force_net_top15(monkeypatch):
    # 主力淨額 = 前15大買超淨額 + 前15大賣超淨額；此例分點<15 → 全計
    day = {"甲": 500, "乙": 300, "丙": -200, "丁": -100}
    _patch(monkeypatch, day, {})
    out = brk.compute(["2330"], "DAY", None)
    assert out.loc[0, "主力淨額"] == 500  # 800 買 −300 賣


def test_daytrade_pressure_top_buyers_only(monkeypatch):
    # 昨日大買家 甲(+600) 今日轉賣 −400 → 隔日沖回吐 400；量 1000 → 40%
    prev = {"甲": 600, "乙": 200}
    day = {"甲": -400, "乙": 50, "丙": -100}   # 丙昨沒買，不算
    _patch(monkeypatch, day, prev)
    out = brk.compute(["1303"], "DAY", "PREV", {"1303": 1000})
    assert out.loc[0, "隔日沖賣壓%"] == 40.0


def test_daytrade_pressure_capped_at_yesterday_buy(monkeypatch):
    # 甲昨買 100、今賣 −500 → 對沖只算 min(100,500)=100（capped 到昨買量）
    prev = {"甲": 100}
    day = {"甲": -500}
    _patch(monkeypatch, day, prev)
    out = brk.compute(["1303"], "DAY", "PREV", {"1303": 1000})
    assert out.loc[0, "隔日沖賣壓%"] == 10.0


def test_no_prev_no_pressure_col(monkeypatch):
    _patch(monkeypatch, {"甲": 100}, {})
    out = brk.compute(["1303"], "DAY", None, {"1303": 1000})
    assert "主力淨額" in out.columns
    assert out["隔日沖賣壓%"].isna().all()   # 無前日 → 賣壓留白


def test_unavailable_returns_empty(monkeypatch):
    _patch(monkeypatch, {"甲": 100}, {}, available=False)
    out = brk.compute(["1303"], "DAY", "PREV", {"1303": 1000})
    assert list(out.columns) == ["stock_id", "主力淨額", "隔日沖賣壓%"]
    assert out.empty


def test_enrich_maps_and_keeps_cols(monkeypatch):
    df = pd.DataFrame({"stock_id": ["1303", "2330"], "name": ["南亞", "台積電"]})
    sig = pd.DataFrame({"stock_id": ["1303"], "主力淨額": [2762], "隔日沖賣壓%": [4.6]})
    out = brk.enrich(df, sig)
    assert out.loc[out.stock_id == "1303", "主力淨額"].iloc[0] == 2762
    assert pd.isna(out.loc[out.stock_id == "2330", "隔日沖賣壓%"].iloc[0])
    assert "name" in out.columns
