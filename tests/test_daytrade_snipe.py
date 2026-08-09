"""隔日沖鎖碼候選 screener 測試（tmp DB、分點 monkeypatch）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src.screeners import daytrade_snipe as snipe
from src import broker_signal as bs
from src import broker_client as bc


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield
    db.clear_cache()


def _seed_price():
    from src import db
    dates = ["D1", "D2", "D3", "D4", "D5"]
    rows = []
    for i, d in enumerate(dates):
        # 9999：D5 收 110（前日 100）→ +10% 漲停；1111：一路平盤（非漲停）
        rows.append({"date": d, "stock_id": "9999", "close": 100.0 if d != "D5" else 110.0,
                     "volume": 2000})
        rows.append({"date": d, "stock_id": "1111", "close": 50.0, "volume": 2000})
    info = [{"stock_id": "9999", "stock_name": "妖股", "type": "twse", "industry": "電子"},
            {"stock_id": "1111", "stock_name": "牛皮", "type": "twse", "industry": "傳產"}]
    with db.connect() as conn:
        db.upsert(conn, "price", rows)
        db.upsert(conn, "stock_info", info)


# 甲：D1買→D2賣、D3買→D4賣（隔日沖常客 2 次），且 D5 今日大買 → 應標🎯
_NETS = {
    ("9999", "D1"): {"甲": 500}, ("9999", "D2"): {"甲": -300},
    ("9999", "D3"): {"甲": 400}, ("9999", "D4"): {"甲": -200},
    ("9999", "D5"): {"甲": 600, "乙": -100},
}


def test_snipe_flags_locked_regular(monkeypatch):
    _seed_price()
    monkeypatch.setattr(bc, "available", lambda *a, **k: True)
    monkeypatch.setattr(bs, "_branch_net", lambda sid, d: _NETS.get((sid, d), {}))
    df = snipe.run(gain_th=9.0, top_n=15, lookback=8)
    assert list(df["stock_id"]) == ["9999"]                 # 只有漲停股入選、牛皮股濾掉
    r = df.iloc[0]
    assert r["漲跌%"] == 10.0
    assert r["今主力淨額"] == 500                            # D5：甲600 買 + 乙−100 賣
    assert r["昨主力淨額"] == -200                           # D4：甲 −200（昨vs今對照）
    assert "🎯" in str(r["隔日沖鎖碼"]) and "甲" in str(r["隔日沖鎖碼"])


def test_snipe_sell_pressure(monkeypatch):
    _seed_price()
    # 丙 昨日(D4)大買 300、今日(D5)倒貨 −200 → 隔日沖賣壓% 應反映『昨進今出』
    nets = {("9999", "D4"): {"丙": 300}, ("9999", "D5"): {"甲": 100, "丙": -200}}
    monkeypatch.setattr(bc, "available", lambda *a, **k: True)
    monkeypatch.setattr(bs, "_branch_net", lambda sid, d: nets.get((sid, d), {}))
    df = snipe.run(gain_th=9.0)
    r = df.iloc[0]
    assert r["昨主力淨額"] == 300                            # D4：丙 +300
    assert r["今主力淨額"] == -100                           # D5：甲+100、丙−200
    assert r["隔日沖賣壓%"] == 10.0                          # 丙昨買今賣對沖 200 ÷ 今量 2000 = 10%


def test_snipe_no_broker_still_lists_limitup(monkeypatch):
    _seed_price()
    monkeypatch.setattr(bc, "available", lambda *a, **k: False)   # 無 Sponsor
    df = snipe.run(gain_th=9.0)
    assert list(df["stock_id"]) == ["9999"]                 # 仍出漲停清單
    assert pd.isna(df.iloc[0]["今主力淨額"])                 # 分點欄留白
    assert pd.isna(df.iloc[0]["昨主力淨額"])
    assert pd.isna(df.iloc[0]["隔日沖賣壓%"])
    assert pd.isna(df.iloc[0]["隔日沖鎖碼"])


def test_snipe_no_lock_no_flag(monkeypatch):
    _seed_price()
    # 甲今日淨賣（主力淨額<0，非鎖碼）→ 不查常客、不標🎯
    nets = dict(_NETS)
    nets[("9999", "D5")] = {"甲": -600, "乙": 100}
    monkeypatch.setattr(bc, "available", lambda *a, **k: True)
    monkeypatch.setattr(bs, "_branch_net", lambda sid, d: nets.get((sid, d), {}))
    df = snipe.run(gain_th=9.0)
    assert df.iloc[0]["今主力淨額"] == -500
    assert pd.isna(df.iloc[0]["隔日沖鎖碼"])                 # 未鎖碼 → 無標記
