"""投資框架補洞：除權息預告／外資持股+市值+周轉率／真實ROE／三率三升／半年線。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import exdividend as ex
from src import stock_deepdive as dd


# ---- 除權息預告 ----

def test_exdividend_roc_and_label():
    assert ex._roc_to_iso("1150814") == "2026-08-14"
    lb = ex._label({"除權息日": "2026-08-14", "現金股利": 2.5, "股票股利": None}, "2026-08-12")
    assert "除息" in lb and "08-14" in lb and "2.5" in lb
    assert "今日" in ex._label({"除權息日": "2026-08-12", "現金股利": 1.0}, "2026-08-12")
    assert ex._label({"除權息日": "2026-08-01", "現金股利": 1.0}, "2026-08-12") is None   # 已過


def test_exdividend_compute_window(monkeypatch):
    monkeypatch.setattr(ex, "fetch_events", lambda: {
        "1111": {"除權息日": "2026-08-20", "現金股利": 3.0, "股票股利": None},
        "2222": {"除權息日": "2026-12-31", "現金股利": 1.0, "股票股利": None}})   # 太遠
    m = ex.compute("2026-08-12", within_days=45)
    assert "1111" in m and "2222" not in m


def test_exdividend_enrich_blank_when_none():
    df = pd.DataFrame([{"stock_id": "1111"}, {"stock_id": "9999"}])
    out = ex.enrich(df, {"1111": "📅除息08-20(配3)"})
    assert out.loc[0, "除權息"].startswith("📅") and out.loc[1, "除權息"] == ""


# ---- 外資持股 / 市值 / 周轉率 ----

@pytest.fixture
def _db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield db
    db.clear_cache()


def test_shareholding_marketcap_and_turnover(_db, monkeypatch):
    from src import shareholding as sh
    with _db.connect() as conn:
        _db.upsert(conn, "price", [{"date": "2026-08-12", "stock_id": "9999",
                                    "close": 100.0, "volume": 5000}])       # DB volume 已是「張」
    monkeypatch.setattr("src.finmind_client.fetch", lambda *a, **k: [
        {"stock_id": "9999", "ForeignInvestmentSharesRatio": 42.5,
         "NumberOfSharesIssued": 100_000_000}])                              # 1億股＝10萬張
    out = sh.compute()
    r = out[out["stock_id"] == "9999"].iloc[0]
    assert r["外資持股%"] == 42.5
    assert r["市值億"] == 100.0            # 100元 × 1億股 = 100億
    assert r["周轉率%"] == 5.0             # 5000張 ÷ 10萬張 = 5%


def test_shareholding_empty_on_api_failure(_db, monkeypatch):
    from src import shareholding as sh
    with _db.connect() as conn:
        _db.upsert(conn, "price", [{"date": "2026-08-12", "stock_id": "9999",
                                    "close": 10.0, "volume": 1}])
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr("src.finmind_client.fetch", boom)
    assert sh.compute().empty                  # graceful，不炸日報


# ---- 三率三升 ----

def test_three_rates_labels():
    up = pd.DataFrame([{"毛利率%": 50, "營益率%": 30, "淨利率%": 20},
                       {"毛利率%": 52, "營益率%": 32, "淨利率%": 22}])
    assert dd.three_rates(up) == "🔴三率三升"
    down = pd.DataFrame([{"毛利率%": 52, "營益率%": 32, "淨利率%": 22},
                         {"毛利率%": 50, "營益率%": 30, "淨利率%": 20}])
    assert dd.three_rates(down) == "🟢三率三降"
    mixed = pd.DataFrame([{"毛利率%": 50, "營益率%": 30, "淨利率%": 20},
                          {"毛利率%": 52, "營益率%": 28, "淨利率%": 22}])
    assert dd.three_rates(mixed) == "⚪2升1降"
    assert dd.three_rates(pd.DataFrame()) == "—"


def test_fund_vote_counts_three_rates():
    from scripts import run_stock as rs
    base = rs._fund_vote({}, pd.DataFrame(), [], "")
    assert rs._fund_vote({}, pd.DataFrame(), [], "🔴三率三升") == base + 1
    assert rs._fund_vote({}, pd.DataFrame(), [], "🟢三率三降") == base - 1


# ---- 真實 ROE（近四季淨利 ÷ 期末權益）----

def test_real_roe_ttm(monkeypatch):
    dates = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    bs = [{"date": d, "type": t, "value": v} for d in dates
          for t, v in [("TotalAssets", 1000e8), ("Liabilities", 400e8),
                       ("EquityAttributableToOwnersOfParent", 600e8), ("CapitalStock", 100e8)]]
    fs = [{"date": d, "type": "IncomeAfterTaxes", "value": 30e8} for d in dates]  # 每季30億
    def fake(dataset, **k):
        return {"TaiwanStockBalanceSheet": bs, "TaiwanStockCashFlowsStatement": [],
                "TaiwanStockFinancialStatements": fs}.get(dataset, [])
    monkeypatch.setattr("src.finmind_client.fetch", fake)
    df = dd.financial_health("9999")
    last = df.iloc[-1]
    assert last["ROE%"] == pytest.approx(20.0, abs=0.1)   # TTM 120億 ÷ 權益600億 = 20%
    assert pd.isna(df.iloc[0]["ROE%"])                     # 首季不足四季 → 不算


# ---- 半年線 120MA ----

def test_half_year_ma(_db):
    from src import tech_signal as ts
    # 130 天：前段低、近期高 → 收盤應站上 120MA
    rows = [{"date": f"2026-{(d // 28) + 1:02d}-{(d % 28) + 1:02d}", "stock_id": "9999",
             "close": 100 + d, "volume": 10} for d in range(130)]
    with _db.connect() as conn:
        _db.upsert(conn, "price", rows)
    out = ts.compute()
    r = out[out["stock_id"] == "9999"].iloc[0]
    assert "站上半年線" in r["半年線"]
    assert "半年線" in ts._COLS
