"""借券賣出餘額 enrich 單元測試（不碰網路：直接餵 signals）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import sbl_signal


def test_enrich_maps_by_stock_id():
    df = pd.DataFrame({"stock_id": ["2330", "2317"], "close": [1000.0, 200.0]})
    sig = pd.DataFrame({"stock_id": ["2330", "2317"], "借券賣出餘額": [13282, 61699]})
    out = sbl_signal.enrich(df, sig)
    assert list(out["借券賣出餘額"]) == [13282, 61699]
    assert "close" in out.columns          # 原欄保留


def test_enrich_missing_stock_left_blank():
    df = pd.DataFrame({"stock_id": ["2330", "9999"]})
    sig = pd.DataFrame({"stock_id": ["2330"], "借券賣出餘額": [13282]})
    out = sbl_signal.enrich(df, sig)
    assert out.loc[out["stock_id"] == "2330", "借券賣出餘額"].iloc[0] == 13282
    assert pd.isna(out.loc[out["stock_id"] == "9999", "借券賣出餘額"].iloc[0])  # 抓不到→留白


def test_enrich_empty_signals_passthrough():
    df = pd.DataFrame({"stock_id": ["2330"], "close": [1000.0]})
    out = sbl_signal.enrich(df, pd.DataFrame(columns=["stock_id", "借券賣出餘額"]))
    assert "借券賣出餘額" not in out.columns   # 空訊號不硬加空欄
    assert list(out["stock_id"]) == ["2330"]


def test_enrich_empty_df_passthrough():
    assert sbl_signal.enrich(pd.DataFrame(), None) is not None


def test_compute_empty_ids_no_network():
    # 空候選 → 直接回空表，完全不呼叫 FinMind（省額度、離線可測）
    out = sbl_signal.compute([])
    assert list(out.columns) == ["stock_id", "借券賣出餘額"]
    assert out.empty


def test_fetch_market_day_parses_and_shares_to_lots(monkeypatch):
    # 全市場單日：股÷1000→張、缺值/空代號略過
    fake = [
        {"stock_id": "1303", "SBLShortSalesCurrentDayBalance": 20651000},
        {"stock_id": "2330", "SBLShortSalesCurrentDayBalance": 13282000},
        {"stock_id": "9999", "SBLShortSalesCurrentDayBalance": None},   # 缺值→略
        {"stock_id": "", "SBLShortSalesCurrentDayBalance": 5000},        # 空代號→略
    ]
    monkeypatch.setattr(sbl_signal, "fetch", lambda *a, **k: fake)
    rows = sbl_signal.fetch_market_day("2026-08-05")
    assert {r["stock_id"]: r["sbl_balance"] for r in rows} == {"1303": 20651, "2330": 13282}
    assert all(r["date"] == "2026-08-05" for r in rows)


def test_fetch_market_day_api_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(sbl_signal, "fetch", boom)
    assert sbl_signal.fetch_market_day("2026-08-05") == []


def _tmp_sbl_db(monkeypatch, tmp_path, rows):
    """建暫時 DB、寫入 sbl 列、把 db.DB_PATH 指過去，供 compute_from_db 讀。"""
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
        db.upsert(conn, "sbl", rows)


def test_compute_from_db_latest_and_delta(monkeypatch, tmp_path):
    rows = [
        {"date": "2026-08-03", "stock_id": "1303", "sbl_balance": 19096},
        {"date": "2026-08-04", "stock_id": "1303", "sbl_balance": 19927},
        {"date": "2026-08-05", "stock_id": "1303", "sbl_balance": 20651},
    ]
    _tmp_sbl_db(monkeypatch, tmp_path, rows)
    out = sbl_signal.compute_from_db(["1303"])
    assert out.loc[0, "借券賣出餘額"] == 20651
    assert out.loc[0, "借券增減"] == 724            # 20651 − 19927


def test_compute_from_db_single_day_delta_na(monkeypatch, tmp_path):
    _tmp_sbl_db(monkeypatch, tmp_path,
                [{"date": "2026-08-05", "stock_id": "2330", "sbl_balance": 13282}])
    out = sbl_signal.compute_from_db(["2330"])
    assert out.loc[0, "借券賣出餘額"] == 13282
    assert pd.isna(out.loc[0, "借券增減"])           # 只有一天→增減留白


def test_compute_from_db_no_history_empty(monkeypatch, tmp_path):
    _tmp_sbl_db(monkeypatch, tmp_path, [])
    out = sbl_signal.compute_from_db(["1303"])
    assert list(out.columns) == ["stock_id", "借券賣出餘額", "借券增減"]
    assert out.empty
