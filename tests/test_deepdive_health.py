"""個股深掘 財務體質：資產負債＋現金流健檢（mock FinMind）。

重點守『現金流去累計還原單季』——不還原會把含金量/營運CF算爆。
跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import stock_deepdive as dd


def _mock_fetch(mapping):
    def f(dataset, start_date=None, end_date=None, data_id=None, retries=3):
        return mapping.get(dataset, [])
    return f


def test_financial_health_decumulates_cashflow(monkeypatch):
    # 一年四季：資產負債(時點)＋現金流(累計YTD)＋損益(單季淨利)
    bs = []
    for dt in ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"):
        bs += [{"date": dt, "type": "TotalAssets", "value": 1000e8},
               {"date": dt, "type": "Liabilities", "value": 300e8},
               {"date": dt, "type": "CurrentAssets", "value": 400e8},
               {"date": dt, "type": "CurrentLiabilities", "value": 200e8},
               {"date": dt, "type": "EquityAttributableToOwnersOfParent", "value": 700e8},
               {"date": dt, "type": "CapitalStock", "value": 100e8}]   # 股數=100e8/10=10e8
    # 營運CF 累計 YTD：Q1=100, H1=250, 9M=400, FY=600（億）→ 單季 100/150/150/200
    ytd = {"2025-03-31": 100e8, "2025-06-30": 250e8, "2025-09-30": 400e8, "2025-12-31": 600e8}
    capex = {"2025-03-31": -30e8, "2025-06-30": -80e8, "2025-09-30": -130e8, "2025-12-31": -200e8}
    cf = []
    for dt in ytd:
        cf += [{"date": dt, "type": "CashFlowsFromOperatingActivities", "value": ytd[dt]},
               {"date": dt, "type": "PropertyAndPlantAndEquipment", "value": capex[dt]}]
    fs = [{"date": dt, "type": "IncomeAfterTaxes", "value": 100e8}
          for dt in ytd]                       # 單季淨利各 100 億
    monkeypatch.setattr("src.finmind_client.fetch", _mock_fetch({
        "TaiwanStockBalanceSheet": bs, "TaiwanStockCashFlowsStatement": cf,
        "TaiwanStockFinancialStatements": fs}))

    df = dd.financial_health("9999", quarters=6)
    assert list(df["季別"]) == ["2025-03", "2025-06", "2025-09", "2025-12"]
    # 去累計：Q2 單季營運CF = 250-100 = 150 億（非 250）
    assert df.iloc[1]["營運CF億"] == 150.0
    assert df.iloc[3]["營運CF億"] == 200.0     # FY 600 - 9M 400
    assert df.iloc[0]["營運CF億"] == 100.0     # Q1 = YTD
    # 含金量 Q2 = 單季CF150 / 單季淨利100 = 150%
    assert df.iloc[1]["含金量%"] == 150
    # 自由現金流 Q2 = 單季CF150 + 單季capex(-80-(-30)=-50) = 100 億
    assert df.iloc[1]["自由現金流億"] == 100.0
    # 比率型
    assert df.iloc[0]["負債比%"] == 30.0       # 300/1000
    assert df.iloc[0]["流動比%"] == 200        # 400/200
    assert df.iloc[0]["每股淨值"] == 70.0      # 700e8 / (100e8/10)=10e8股 → 70


def test_financial_health_empty(monkeypatch):
    monkeypatch.setattr("src.finmind_client.fetch", _mock_fetch({}))
    assert dd.financial_health("9999").empty
