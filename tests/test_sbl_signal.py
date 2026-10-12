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
    assert list(out.columns) == ["stock_id", "借券賣出餘額", "借券增減", "借券資料日"]
    assert out.empty


def test_compute_from_db_labels_each_stocks_own_date(monkeypatch, tmp_path):
    """停更時各檔新舊可能不一（有補到的是今天、沒補到的還停在上週）→ 逐檔標資料日。"""
    _tmp_sbl_db(monkeypatch, tmp_path, [
        {"date": "2026-10-06", "stock_id": "1303", "sbl_balance": 20651},
        {"date": "2026-10-12", "stock_id": "2330", "sbl_balance": 13282},
    ])
    out = sbl_signal.compute_from_db(["1303", "2330"]).set_index("stock_id")
    assert out.loc["1303", "借券資料日"] == "2026-10-06"
    assert out.loc["2330", "借券資料日"] == "2026-10-12"


# --- compute_latest：Sponsor 到期後的日常取用口（別讓舊值假裝成今天的）---

def _tmp_db(monkeypatch, tmp_path, sbl_rows, price_days):
    """建暫時 DB：sbl 歷史 + price 交易日（算資料新舊要用真實交易日，不是日曆日）。"""
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()                      # 整表快取是進程級的，不清會吃到別的測試的 price
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
        db.upsert(conn, "sbl", sbl_rows)
        db.upsert(conn, "price", [{"date": d, "stock_id": "1303", "open": 1.0, "high": 1.0,
                                   "low": 1.0, "close": 1.0, "volume": 1} for d in price_days])


_DAYS = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]


def test_compute_latest_fresh_db_no_date_col_and_no_api(monkeypatch, tmp_path):
    """DB 已是當日 → 不打 API、也不多一欄（常態，版面不變）。"""
    _tmp_db(monkeypatch, tmp_path,
            [{"date": "2026-10-12", "stock_id": "1303", "sbl_balance": 20651}], _DAYS)
    monkeypatch.setattr(sbl_signal, "fetch", _boom)      # 不該被呼叫
    out = sbl_signal.compute_latest(["1303"])
    assert out.loc[0, "借券賣出餘額"] == 20651
    assert "借券資料日" not in out.columns


def _boom(*a, **k):
    raise AssertionError("不該打 API")


def test_compute_latest_backfills_per_stock_when_db_stale(monkeypatch, tmp_path):
    """DB 停在 10-06（Sponsor 到期→全市場抓不到）→ 逐檔補抓(免費層可用)並寫回 DB。"""
    _tmp_db(monkeypatch, tmp_path,
            [{"date": "2026-10-06", "stock_id": "1303", "sbl_balance": 20000}], _DAYS)
    seen = {}

    def fake_fetch(dataset, start_date=None, end_date=None, data_id=None):
        seen["data_id"] = data_id                     # 必須是逐檔查，不是全市場
        return [{"date": "2026-10-09", "stock_id": data_id,
                 "SBLShortSalesCurrentDayBalance": 20100000},
                {"date": "2026-10-12", "stock_id": data_id,
                 "SBLShortSalesCurrentDayBalance": 20651000}]
    monkeypatch.setattr(sbl_signal, "fetch", fake_fetch)
    out = sbl_signal.compute_latest(["1303"])
    assert seen["data_id"] == "1303"
    assert out.loc[0, "借券賣出餘額"] == 20651
    assert out.loc[0, "借券增減"] == 551              # 補抓後『借券增減』才算得出來
    assert "借券資料日" not in out.columns             # 已補成當日 → 不需要標
    assert sbl_signal.latest_db_date() == "2026-10-12"  # 有寫回 DB（深掘曲線也接得上）


def test_compute_latest_labels_data_date_when_still_stale(monkeypatch, tmp_path):
    """補抓也拿不到當日（免費層也查不到）→ 值照出但標上資料日，不讓它假裝是今天的。"""
    _tmp_db(monkeypatch, tmp_path,
            [{"date": "2026-10-09", "stock_id": "1303", "sbl_balance": 20651}], _DAYS)
    monkeypatch.setattr(sbl_signal, "fetch", lambda *a, **k: [])
    out = sbl_signal.compute_latest(["1303"])
    assert out.loc[0, "借券賣出餘額"] == 20651
    assert out.loc[0, "借券資料日"] == "2026-10-09"


def test_compute_latest_drops_too_stale(monkeypatch, tmp_path):
    """落後超過 _MAX_STALE_DAYS 個交易日 → 整檔留白（寧可沒有，也不給過期值）。"""
    days = ["2026-10-0%d" % d for d in (1, 2, 3, 4, 5, 6, 7, 8, 9)] + ["2026-10-12"]
    _tmp_db(monkeypatch, tmp_path,
            [{"date": "2026-10-01", "stock_id": "1303", "sbl_balance": 20651}], days)
    monkeypatch.setattr(sbl_signal, "fetch", lambda *a, **k: [])
    assert sbl_signal.compute_latest(["1303"]).empty


def test_compute_latest_only_spends_api_on_live_ids(monkeypatch, tmp_path):
    """全部候選可能有上百檔（T16 四百多），只對真的會顯示的那幾檔花額度。"""
    _tmp_db(monkeypatch, tmp_path,
            [{"date": "2026-10-06", "stock_id": "1303", "sbl_balance": 20000}], _DAYS)
    asked = []
    monkeypatch.setattr(sbl_signal, "fetch",
                        lambda *a, **k: asked.append(k.get("data_id")) or [])
    sbl_signal.compute_latest(["1303", "2330", "2317"], live_ids=["1303"])
    assert asked == ["1303"]


def test_compute_latest_blank_for_fresh_rows_and_no_col_when_shown_all_fresh(monkeypatch, tmp_path):
    """資料日只在『不是當日』時才有資訊量。

    表尾那幾百檔（不在 live_ids、報告不會顯示）沒補到，不該害顯示列多出一整欄「2026-10-12」。
    """
    _tmp_db(monkeypatch, tmp_path, [
        {"date": "2026-10-12", "stock_id": "1303", "sbl_balance": 20651},   # 會顯示、已是當日
        {"date": "2026-10-08", "stock_id": "2330", "sbl_balance": 13282},   # 不顯示、仍是舊的
    ], _DAYS)
    monkeypatch.setattr(sbl_signal, "fetch", lambda *a, **k: [])
    out = sbl_signal.compute_latest(["1303", "2330"], live_ids=["1303"])
    assert "借券資料日" not in out.columns
    assert len(out) == 2                      # 舊的那檔沒被刪（只是沒標日，反正不會顯示）

    # 反過來：會顯示的那檔是舊的 → 欄位要留著，且當日的那列留白（不重複寫今天的日期）
    out2 = sbl_signal.compute_latest(["1303", "2330"], live_ids=["2330"]).set_index("stock_id")
    assert out2.loc["2330", "借券資料日"] == "2026-10-08"
    assert pd.isna(out2.loc["1303", "借券資料日"])


def test_fetch_stock_days_parses_and_shares_to_lots(monkeypatch):
    monkeypatch.setattr(sbl_signal, "fetch", lambda *a, **k: [
        {"date": "2026-10-12", "SBLShortSalesCurrentDayBalance": 20651000},
        {"date": "2026-10-09", "SBLShortSalesCurrentDayBalance": None},   # 缺值→略
    ])
    rows = sbl_signal.fetch_stock_days("1303", "2026-10-01", "2026-10-12")
    assert rows == [{"date": "2026-10-12", "stock_id": "1303", "sbl_balance": 20651}]
