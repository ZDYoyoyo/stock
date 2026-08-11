"""個股深掘 基本面：月營收/獲利能力/估值/配息 純函式測試（mock FinMind）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import stock_deepdive as dd


def _mock_fetch(mapping):
    """回一個假 fetch：依 dataset 名回傳對應假資料。"""
    def f(dataset, start_date=None, end_date=None, data_id=None, retries=3):
        return mapping.get(dataset, [])
    return f


def test_monthly_revenue_yoy_mom_cum(monkeypatch):
    # 2024 全年 + 2025 前2月；rev 單位＝元
    rows = []
    for m in range(1, 13):
        rows.append({"revenue_year": 2024, "revenue_month": m, "revenue": 100e8})
    rows.append({"revenue_year": 2025, "revenue_month": 1, "revenue": 130e8})
    rows.append({"revenue_year": 2025, "revenue_month": 2, "revenue": 120e8})
    monkeypatch.setattr("src.finmind_client.fetch",
                        _mock_fetch({"TaiwanStockMonthRevenue": rows}))
    df = dd.monthly_revenue("9999", months=3)
    last = df.iloc[-1]                     # 2025/02
    assert last["月份"] == "2025/02"
    assert last["營收億"] == 120.0
    assert last["營收YoY%"] == 20.0        # 120 vs 100
    assert last["營收MoM%"] == -7.7        # 120 vs 130
    # 累計YoY：(130+120) vs (100+100)=25%
    assert last["累計YoY%"] == 25.0


def test_profitability_margins_and_eps_yoy(monkeypatch):
    # 5 季，每季 Revenue=100、GrossProfit=50→毛利率50%；EPS 逐季，看 vs 去年同季年增
    dates = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]
    eps = [1.0, 1.2, 1.4, 1.6, 2.0]
    fs = []
    for dt, e in zip(dates, eps):
        fs += [{"date": dt, "type": "Revenue", "value": 100.0},
               {"date": dt, "type": "GrossProfit", "value": 50.0},
               {"date": dt, "type": "OperatingIncome", "value": 30.0},
               {"date": dt, "type": "IncomeAfterTaxes", "value": 20.0},
               {"date": dt, "type": "EPS", "value": e}]
    monkeypatch.setattr("src.finmind_client.fetch",
                        _mock_fetch({"TaiwanStockFinancialStatements": fs}))
    df = dd.profitability("9999", quarters=8)
    last = df.iloc[-1]                     # 2025-03
    assert last["毛利率%"] == 50.0 and last["營益率%"] == 30.0 and last["淨利率%"] == 20.0
    assert last["EPS單季"] == 2.0
    assert last["EPS年增%"] == 100.0       # 2.0 vs 去年同季(2024-03)=1.0


def test_dividends_parses_roc_year_and_streak(monkeypatch):
    """民國+季字串（季配）→ 按年加總、連續配息年數不把『季』當『年』。"""
    rows = [
        {"year": "112年", "CashEarningsDistribution": 3.0, "StockEarningsDistribution": 0},
        {"year": "113年第1季", "CashEarningsDistribution": 1.0, "StockEarningsDistribution": 0},
        {"year": "113年第2季", "CashEarningsDistribution": 1.5, "StockEarningsDistribution": 0},
        {"year": "113年第3季", "CashEarningsDistribution": 1.5, "StockEarningsDistribution": 0.5},
    ]
    monkeypatch.setattr("src.finmind_client.fetch",
                        _mock_fetch({"TaiwanStockDividend": rows}))
    df, streak = dd.dividends("9999", years=6)
    assert streak == 2                     # 112(西元2023)、113(2024) 兩個『年』都有現金→連續2年（非4季）
    row113 = df[df["年度"] == 2024].iloc[0]
    assert row113["現金股利"] == 4.0       # 1.0+1.5+1.5 季配加總
    assert row113["股票股利"] == 0.5


def test_valuation_position(monkeypatch):
    data = [{"date": f"2026-01-{d:02d}", "PER": p, "PBR": 2.0, "dividend_yield": 3.0}
            for d, p in enumerate([10, 20, 30, 15, 25] * 5, start=1)]
    monkeypatch.setattr("src.finmind_client.fetch",
                        _mock_fetch({"TaiwanStockPER": data}))
    v = dd.valuation_snapshot("9999")
    assert v["PER"] == data[-1]["PER"]     # 末筆現值
    assert 0 <= v["PER近1年位置%"] <= 100


def test_fundamentals_empty_on_no_data(monkeypatch):
    monkeypatch.setattr("src.finmind_client.fetch", _mock_fetch({}))
    assert dd.monthly_revenue("9999").empty
    assert dd.profitability("9999").empty
    assert dd.valuation_snapshot("9999") == {}
    df, streak = dd.dividends("9999")
    assert df.empty and streak == 0
