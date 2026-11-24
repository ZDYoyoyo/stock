"""一般會員(FinMind 免費層)強化：排雷部分降級、法人/資券完整性、費半時序、分點欄隱藏、鎖碼分組戰績、廣度提示。
跑法：python -m pytest tests/ -q"""
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


# ---------- ③ 完整性：法人/資券整批缺也要喊 ----------
def _seed_counts(n_price, n_inst_today, n_margin_today=20):
    from src import db
    ids = [str(9000 + i) for i in range(20)]
    with db.connect() as conn:
        db.upsert(conn, "price", [{"date": d, "stock_id": s, "close": 10, "volume": 1}
                                  for d in ("D1", "D2") for s in ids[: (20 if d == "D1" else n_price)]])
        db.upsert(conn, "institutional", [{"date": d, "stock_id": s, "foreign_net": 1}
                                          for d in ("D1", "D2")
                                          for s in ids[: (20 if d == "D1" else n_inst_today)]])
        db.upsert(conn, "margin", [{"date": d, "stock_id": s, "margin_balance": 1}
                                   for d in ("D1", "D2")
                                   for s in ids[: (20 if d == "D1" else n_margin_today)]])


def test_data_health_flags_missing_institutional():
    """price 正常、法人只剩 8/20（10-29 實際踩過 760 筆）→ 要警示。"""
    import importlib
    ra = importlib.import_module("scripts.run_all")
    _seed_counts(n_price=20, n_inst_today=8)
    msg = ra._data_health("D2")
    assert "資料可能不完整" in msg and "法人只有 8 筆" in msg and "20 筆" in msg
    assert "資券" not in msg


def test_data_health_quiet_when_all_tables_complete():
    import importlib
    ra = importlib.import_module("scripts.run_all")
    _seed_counts(n_price=20, n_inst_today=19, n_margin_today=19)
    assert ra._data_health("D2") == ""


# ---------- ① 排雷：財報抓不到只降級財報那半，且只試一次 ----------
def test_landmine_degrades_per_stock_and_stops_retrying(monkeypatch):
    from src.screeners import landmine
    calls = []

    def boom(sid):
        calls.append(sid)
        raise RuntimeError("FinMind TaiwanStockMonthRevenue 重試 3 次仍失敗")

    monkeypatch.setattr(landmine, "_fin_flags", boom)
    monkeypatch.setattr(landmine, "_chip_tech_flags",
                        lambda sid, ctx, m: ((["跌破季線(60MA)"] if sid == "1111" else []), {}, True))
    monkeypatch.setattr(landmine, "_market_median_return", lambda price: 0.0)
    df = landmine.scan(["1111", "2222", "3333"], sleep=0, verbose=False)
    assert calls == ["1111"]                          # 第一次失敗後不再逐檔重試
    assert len(df) == 3                               # 不再整批丟掉
    red = df.set_index("stock_id")["紅旗"]
    assert red["1111"] == "跌破季線(60MA)；⚠財報未查"   # 籌碼/技術紅旗保留
    assert red["2222"] == "⚠財報未查"
    assert df.attrs["fin_skipped"] == 3


# ---------- ② 費半時序：台股已交易過就不能說「隔日」 ----------
def test_sox_signal_stale_vs_forward():
    from src import global_market as gm
    stale = gm.sox_signal([{"symbol": "^SOX", "chg_%": -10.73, "date": "2026-11-20"},
                           {"symbol": "^TWII", "chg_%": 1.0, "date": "2026-11-23"}])
    assert "隔日" not in stale and "已反應" in stale and "2026-11-20" in stale
    fwd = gm.sox_signal([{"symbol": "^SOX", "chg_%": -10.73, "date": "2026-11-23"},
                         {"symbol": "^TWII", "chg_%": 1.0, "date": "2026-11-23"}])
    assert "隔日易承壓" in fwd
    assert gm.sox_signal([{"symbol": "^SOX", "chg_%": 0.2}]) == "費半持平 0.2%"   # 沒日期 → 舊行為


# ---------- 一般會員：分點欄整欄空就隱藏，有值就保留 ----------
def test_drop_dead_broker_cols():
    from src.report_html import drop_dead_broker_cols
    df = pd.DataFrame({"stock_id": ["1", "2"], "主力淨額": [None, "—"], "全市場黑名單": ["", ""],
                       "倒貨%": [float("nan"), 50.0], "處置警示": ["", ""]})
    out = drop_dead_broker_cols(df)
    assert "主力淨額" not in out.columns and "全市場黑名單" not in out.columns
    assert "倒貨%" in out.columns          # 有一格有值 → 保留
    assert "處置警示" in out.columns        # 非分點欄即使全空也不碰
    assert drop_dead_broker_cols(None) is None


# ---------- ⑤ 鎖碼：依開高幅度分組 + 累積戰績 ----------
def test_gap_groups_buckets():
    from src import picks_tracker as pt
    g = {x["label"]: x for x in pt._gap_groups([(9.9, 0.0), (3.0, -2.0), (1.0, -4.0), (0.0, 1.0), (-5.0, 6.0)])}
    assert g["開高>7%"]["n"] == 1 and g["開高>7%"]["oc_down"] == 0
    assert g["開高0~7%"]["n"] == 2 and g["開高0~7%"]["oc"] == -3.0 and g["開高0~7%"]["oc_down"] == 2
    assert g["開平/開低"]["n"] == 2 and g["開平/開低"]["oc"] == 3.5    # 跳空 0 歸開平/開低


def test_snipe_track_record_point_in_time():
    from src import db, picks_tracker as pt
    with db.connect() as conn:
        db.upsert(conn, "price", [
            {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94, "close": 100, "volume": 1},
            {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101, "close": 102, "volume": 1},
            {"date": "D2", "stock_id": "8888", "open": 50, "high": 55, "low": 49, "close": 55, "volume": 1},
            {"date": "D3", "stock_id": "8888", "open": 54, "high": 56, "low": 53, "close": 56, "volume": 1},
            {"date": "D3", "stock_id": "7777", "open": 0, "high": 0, "low": 0, "close": 30, "volume": 0},
        ])
    pd.DataFrame([{"date": "D1", "track": "隔日沖鎖碼", "rank": 1, "stock_id": "9999", "name": "A"},
                  {"date": "D2", "track": "隔日沖鎖碼", "rank": 1, "stock_id": "8888", "name": "B"},
                  {"date": "D3", "track": "隔日沖鎖碼", "rank": 1, "stock_id": "7777", "name": "C"},  # 無隔日 → 不算
                  {"date": "D1", "track": "波段T11", "rank": 1, "stock_id": "8888", "name": "B"}]
                 ).to_csv(pt.PICKS_CSV, index=False)
    r = pt.snipe_track_record()
    assert r["n"] == 2 and r["days"] == 2 and r["since"] == "D1"
    g = {x["label"]: x for x in r["groups"]}
    assert g["開高>7%"]["n"] == 1 and g["開高>7%"]["oc_down"] == 1     # 9999：跳空+8%、盤中-5.6%
    assert g["開平/開低"]["n"] == 1                                     # 8888：跳空-1.8%


def test_snipe_stats_line_has_groups_and_record():
    from src.report_html import snipe_stats_line
    st = {"gap": 2.0, "oc": -1.0, "oc_down": 1, "n": 2,
          "groups": [{"label": "開高0~7%", "n": 2, "oc": -1.0, "oc_down": 1}],
          "record": {"since": "D1", "days": 3, "n": 10,
                     "groups": [{"label": "開平/開低", "n": 4, "oc": -0.5, "oc_down": 2}]}}
    line = snipe_stats_line(st)
    assert "今日分組：開高0~7% 2 檔" in line and "累積戰績（D1 起 3 個交易日、10 檔次）" in line
    assert "走低 50%" in line
    assert snipe_stats_line({}) == ""


# ---------- ④ 燈號：當天廣度一面倒跟燈號唱反調時加註、不改燈號 ----------
def test_regime_breadth_flip_note():
    from src import regime as r
    base = {"date": "D", "pct_above_ma20": 17.3, "suggested_exposure": "0~2 成", "swing_advice": "x"}
    up = r.summary_line({**base, "regime": "🔴 偏空", "adv_decl": "1904/61",
                         "breadth": {"up": 1904, "down": 61}})
    assert "🔴 偏空" in up and "⚡今日普漲" in up
    calm = r.summary_line({**base, "regime": "🔴 偏空", "adv_decl": "1217/632",
                           "breadth": {"up": 1217, "down": 632}})
    assert "⚡" not in calm
