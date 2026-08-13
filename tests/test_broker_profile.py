"""跨股票分點行為檔案（全市場黑名單）＋前瞻預估賣壓 測試。

跑法：python -m pytest tests/ -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    from src import db, broker_profile as bp
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    db.init_db()
    bp._cached = None                     # 清模組級記憶體快取
    yield db
    db.clear_cache()
    bp._cached = None


def _put(db, sid, date, nets):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO broker_net(date, stock_id, nets) VALUES(?,?,?)",
                  (date, sid, json.dumps(nets, ensure_ascii=False)))


def _seed_flipper(db, n_stocks=6):
    """『沖哥』每檔都昨買今賣；『抱哥』每檔都昨買今續抱。"""
    for i in range(n_stocks):
        sid = f"{1000 + i}"
        _put(db, sid, "D1", {"沖哥": 100, "抱哥": 100})
        _put(db, sid, "D2", {"沖哥": -80, "抱哥": 50})


def test_profile_separates_flipper_from_holder(_db):
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)
    p = bp.build(min_ops=5)
    m = p.set_index("分點")
    assert m.loc["沖哥", "隔日沖率%"] == 100.0          # 6/6 次昨買今賣
    assert m.loc["沖哥", "回吐量%"] == 80.0             # 買100 倒80
    assert m.loc["沖哥", "分點類型"] == "🔥隔日沖大戶"
    assert m.loc["抱哥", "隔日沖率%"] == 0.0
    assert m.loc["抱哥", "分點類型"] == "🏦偏長線"
    assert m.loc["沖哥", "股票數"] == 6                  # 跨股票樣本才是重點


def test_profile_min_ops_filters_noise(_db):
    """樣本太少不進檔案——避免『買完隔天剛好賣一次』被誤判成慣犯。"""
    from src import broker_profile as bp
    _put(_db, "9999", "D1", {"路人": 100})
    _put(_db, "9999", "D2", {"路人": -100})
    assert bp.build(min_ops=10).empty                    # 只有 1 次樣本 → 濾掉
    assert not bp.build(min_ops=1).empty


def test_expected_pressure_uses_history(_db):
    """前瞻預估：今日買量 × 該分點歷史回吐率。"""
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)                       # 沖哥回吐量%=80
    pmap = bp.as_map(bp.build(min_ops=5))
    est = bp.expected_pressure({"沖哥": 1000, "抱哥": 500}, vol=10000, pmap=pmap)
    assert est["預估賣壓張"] == 800                       # 1000 × 80%（抱哥不計）
    assert est["預估賣壓佔量%"] == 8.0                    # 800 / 10000
    assert est["隔日沖分點數"] == 1


def test_expected_pressure_zero_when_no_flippers(_db):
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)
    pmap = bp.as_map(bp.build(min_ops=5))
    est = bp.expected_pressure({"抱哥": 900}, vol=10000, pmap=pmap)
    assert est["預估賣壓張"] == 0 and est["隔日沖分點數"] == 0


def test_profile_empty_without_cache(_db):
    from src import broker_profile as bp
    assert bp.build().empty                              # 新機器無快取 → 空表，不炸
    assert bp.as_map(bp.build()) == {}
    assert bp.expected_pressure({"甲": 100}, 1000, {}) == {}


def test_annotate_labels(_db):
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)
    pmap = bp.as_map(bp.build(min_ops=5))
    out = bp.annotate(["沖哥", "抱哥", "沒資料的"], pmap)
    assert "🔥隔日沖大戶" in out[0] and "100%" in out[0] and "6樣本" in out[0]
    assert "🏦偏長線" in out[1]
    assert out[2] == "—"
