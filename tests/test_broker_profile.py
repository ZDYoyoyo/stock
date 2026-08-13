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
    """『沖哥』每檔都昨買今賣；『抱哥』每檔都昨買今續抱。

    需同時建 price 交易日曆——相鄰交易日檢查靠它（快取稀疏會有缺日，不能當隔日）。
    """
    with db.connect() as c:
        db.upsert(c, "price", [{"date": d, "stock_id": "0001", "close": 10, "volume": 1}
                               for d in ("D1", "D2")])
    for i in range(n_stocks):
        sid = f"{1000 + i}"
        _put(db, sid, "D1", {"沖哥": 100, "抱哥": 100})
        _put(db, sid, "D2", {"沖哥": -80, "抱哥": 50})


def test_profile_separates_flipper_from_holder(_db):
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)
    p = bp.build(min_ops=5, cache=bp.load_cache())
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
    with _db.connect() as c:
        _db.upsert(c, "price", [{"date": d, "stock_id": "0001", "close": 10, "volume": 1}
                                for d in ("D1", "D2")])
    _put(_db, "9999", "D1", {"路人": 100})
    _put(_db, "9999", "D2", {"路人": -100})
    assert bp.build(min_ops=10, cache=bp.load_cache()).empty   # 只有 1 次樣本 → 濾掉
    assert not bp.build(min_ops=1, cache=bp.load_cache()).empty


def test_expected_pressure_uses_history(_db):
    """前瞻預估：今日買量 × 該分點歷史回吐率。"""
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)                       # 沖哥回吐量%=80
    pmap = bp.as_map(bp.build(min_ops=5, cache=bp.load_cache()))
    est = bp.expected_pressure({"沖哥": 1000, "抱哥": 500}, vol=10000, pmap=pmap)
    assert est["預估賣壓張"] == 800                       # 1000 × 80%（抱哥不計）
    assert est["預估賣壓佔量%"] == 8.0                    # 800 / 10000
    assert est["隔日沖分點數"] == 1


def test_expected_pressure_zero_when_no_flippers(_db):
    from src import broker_profile as bp
    _seed_flipper(_db, n_stocks=6)
    pmap = bp.as_map(bp.build(min_ops=5, cache=bp.load_cache()))
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
    pmap = bp.as_map(bp.build(min_ops=5, cache=bp.load_cache()))
    out = bp.annotate(["沖哥", "抱哥", "沒資料的"], pmap)
    assert "🔥隔日沖大戶" in out[0] and "100%" in out[0] and "6樣本" in out[0]
    assert "🏦偏長線" in out[1]
    assert out[2] == "—"


# ---- 持久化累計（進 DB→CSV→git，跨機器/跨月累積）----

def _seed_price(db, days):
    """交易日曆（判斷相鄰用）。"""
    with db.connect() as c:
        db.upsert(c, "price", [{"date": d, "stock_id": "1000", "close": 10, "volume": 1}
                               for d in days])


def test_update_from_cache_is_idempotent(_db):
    from src import broker_profile as bp
    _seed_price(_db, ["D1", "D2", "D3"])
    _put(_db, "1000", "D1", {"沖哥": 100})
    _put(_db, "1000", "D2", {"沖哥": -80})
    first = bp.update_from_cache()
    assert first["新增轉換"] == 1 and first["總樣本"] == 1
    again = bp.update_from_cache()
    assert again["新增轉換"] == 0 and again["總樣本"] == 1     # 重跑不重複累加


def test_persisted_survives_cache_prune(_db):
    """快取被修剪(模擬 60 天後)，檔案仍在——這就是進 DB 的意義。"""
    from src import broker_profile as bp
    _seed_price(_db, ["D1", "D2"])
    _put(_db, "1000", "D1", {"沖哥": 100})
    _put(_db, "1000", "D2", {"沖哥": -80})
    bp.update_from_cache()
    with _db.connect() as c:
        c.execute("DELETE FROM broker_net")            # 模擬 prune_cache 清空
    bp._cached = None
    p = bp.build(min_ops=1)
    assert not p.empty and p.iloc[0]["分點"] == "沖哥"   # 靠持久化計數器仍讀得到
    assert p.iloc[0]["隔日沖率%"] == 100.0


def test_non_adjacent_days_not_counted(_db):
    """快取缺日(該檔中間幾天沒被抓)→ 不能把 D1→D5 當成『隔日』。"""
    from src import broker_profile as bp
    _seed_price(_db, ["D1", "D2", "D3", "D4", "D5"])
    _put(_db, "1000", "D1", {"沖哥": 100})
    _put(_db, "1000", "D5", {"沖哥": -100})            # 隔了 4 個交易日
    st = bp.update_from_cache()
    assert st["新增轉換"] == 0                          # 不相鄰 → 不計
    assert bp.build(min_ops=1, cache=bp.load_cache()).empty
