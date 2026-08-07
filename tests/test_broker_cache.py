"""分點淨額本機快取測試（DB 用 tmp、bc._raw monkeypatch）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import broker_signal as bs
from src import broker_client as bc


def _use_tmp_db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(bs, "_cache_ready", False)   # 每測重新 init 到新 tmp DB


def _raw_rows():
    # FinMind 格式：buy/sell 單位為股；甲淨買 +5(張)、乙淨賣 −3(張)
    return [{"securities_trader": "甲", "buy": 8000, "sell": 3000},
            {"securities_trader": "乙", "buy": 1000, "sell": 4000}]


def test_cache_miss_fetches_then_hit_serves_without_refetch(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_raw(sid, date):
        calls["n"] += 1
        return _raw_rows()
    monkeypatch.setattr(bc, "_raw", fake_raw)

    net1 = bs._branch_net("1303", "2026-08-05")
    assert net1 == {"甲": 5.0, "乙": -3.0}
    assert calls["n"] == 1                       # 未命中→抓一次

    # 第二次即使底層改成回 None，仍由快取回相同結果（證明沒再抓）
    monkeypatch.setattr(bc, "_raw", lambda sid, date: None)
    net2 = bs._branch_net("1303", "2026-08-05")
    assert net2 == {"甲": 5.0, "乙": -3.0}


def test_empty_not_cached(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bc, "_raw", lambda sid, date: None)
    assert bs._branch_net("9999", "2026-08-05") == {}
    # 未快取空 → 下次底層有資料時能抓到（不會被鎖成空）
    monkeypatch.setattr(bc, "_raw", lambda sid, date: _raw_rows())
    assert bs._branch_net("9999", "2026-08-05") == {"甲": 5.0, "乙": -3.0}


def test_prune_keeps_recent_dates(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bc, "_raw", lambda sid, date: _raw_rows())
    for d in ["2026-08-01", "2026-08-04", "2026-08-05"]:
        bs._branch_net("1303", d)
    bs.prune_cache(keep_days=2)
    from src import db
    with db.connect() as c:
        left = sorted(r[0] for r in c.execute("SELECT DISTINCT date FROM broker_net").fetchall())
    assert left == ["2026-08-04", "2026-08-05"]   # 只留最近 2 個日期
