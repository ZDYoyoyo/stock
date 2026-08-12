"""ATR 風控價位（停損價/停損%/目標價）向量化計算測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest


@pytest.fixture
def _db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield db
    db.clear_cache()


def _seed(db, sid="9999", n=20, hl=2.0, close=100.0):
    """每日 high-low=hl、收盤固定 → ATR≈hl，方便驗算。"""
    rows = [{"date": f"2026-07-{d:02d}", "stock_id": sid, "open": close,
             "high": close + hl / 2, "low": close - hl / 2, "close": close, "volume": 100}
            for d in range(1, n + 1)]
    with db.connect() as conn:
        db.upsert(conn, "price", rows)


def test_levels_stop_and_target(_db):
    from src import risk
    _seed(_db, hl=2.0, close=100.0)          # ATR≈2
    lv = risk.levels(atr_mult=2.0, target_r=2.0)
    r = lv[lv["stock_id"] == "9999"].iloc[0]
    assert r["ATR"] == pytest.approx(2.0, abs=0.01)
    assert r["停損價"] == pytest.approx(96.0, abs=0.01)     # 100 − 2×2
    assert r["停損%"] == pytest.approx(-4.0, abs=0.05)      # 距離 4 元 = 4%
    assert r["目標價"] == pytest.approx(108.0, abs=0.01)    # 100 + 2×4（風報比 1:2）


def test_stop_pct_scales_with_volatility(_db):
    """波動大的股停損%自然較寬——這正是用 ATR 而非固定%的理由。"""
    from src import risk
    _seed(_db, sid="1111", hl=1.0, close=100.0)     # 低波動
    _seed(_db, sid="2222", hl=6.0, close=100.0)     # 高波動
    lv = risk.levels().set_index("stock_id")
    assert abs(lv.loc["2222", "停損%"]) > abs(lv.loc["1111", "停損%"]) * 3


def test_levels_skips_insufficient_history(_db):
    from src import risk
    _seed(_db, sid="3333", n=5)               # 不足 14 日 → 不出 ATR
    lv = risk.levels(period=14)
    assert "3333" not in set(lv["stock_id"])


def test_enrich_merges_and_keeps_rows(_db):
    from src import risk
    _seed(_db, sid="9999")
    df = pd.DataFrame([{"stock_id": "9999", "name": "測試"},
                       {"stock_id": "0000", "name": "無資料"}])
    out = risk.enrich(df)
    assert len(out) == 2                                  # 不因缺 ATR 掉列
    assert pd.notna(out.loc[0, "停損價"]) and pd.isna(out.loc[1, "停損價"])
