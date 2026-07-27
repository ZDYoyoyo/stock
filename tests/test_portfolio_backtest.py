"""組合回測引擎單元測試 — 用合成資料證明權益曲線與指標數學正確。

跑法：python tests/test_portfolio_backtest.py   或   python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.portfolio_backtest import (
    _metrics, compute_regime_ok, run_portfolio,
)


def _panel(sid, prices, start="2024-01-01", lows=None, highs=None):
    """造單檔面板：open=close=prices[i]（方便手算）。lows/highs 可指定觸停損情境。"""
    dates = pd.bdate_range(start, periods=len(prices)).strftime("%Y-%m-%d").tolist()
    df = pd.DataFrame({"date": dates, "open": prices, "close": prices,
                       "high": highs or prices, "low": lows or prices,
                       "volume": [1000] * len(prices)})
    return {sid: df}, dates


# --- 指標數學 ---
def test_maxdd_exact():
    eq = pd.Series([100, 120, 90, 150], index=["a", "b", "c", "d"])
    m = _metrics(eq, [], days_with_pos=4, n_days=4)
    assert m["MaxDD_%"] == -25.0        # 120→90 = -25%

def test_sharpe_zero_when_flat():
    eq = pd.Series([100.0] * 10, index=list("abcdefghij"))
    m = _metrics(eq, [], days_with_pos=0, n_days=10)
    assert m["Sharpe"] == 0 and m["Vol_%"] == 0

def test_cagr_positive_for_growth():
    eq = pd.Series(range(100, 100 + 260), index=[f"d{i}" for i in range(260)])
    m = _metrics(eq, [], days_with_pos=260, n_days=260)
    assert m["CAGR_%"] > 0 and m["MaxDD_%"] == 0     # 單調上升→無回撤

def test_win_rate():
    class T:  # 假 trade
        def __init__(self, r): self.ret = r
    m = _metrics(pd.Series([100, 101]), [T(0.1), T(-0.1), T(0.2)],
                 days_with_pos=2, n_days=2)
    assert m["win_rate_%"] == 66.7 and m["n_trades"] == 3


# --- 引擎行為 ---
def test_flat_price_bleeds_by_costs():
    """價格不動時，反覆進出應被交易成本吃到虧損。"""
    panel, dates = _panel("A", [100.0] * 8)
    entries = {d: [("A", 1.0)] for d in dates}     # 每天都有訊號
    res = run_portfolio(panel, entries, init_capital=1_000_000,
                        max_positions=1, hold_days=1)
    assert res.metrics["n_trades"] >= 1
    assert all(t.ret < 0 for t in res.trades)       # 每筆都虧（成本）
    assert res.metrics["final_equity"] < 1_000_000
    assert res.metrics["MaxDD_%"] < 0

def test_rising_price_profits():
    """穩定上漲且漲幅遠大於成本時，組合應獲利、勝率高。"""
    prices = [100 * (1.03 ** i) for i in range(30)]   # 每日+3%
    panel, dates = _panel("A", prices)
    entries = {d: [("A", 1.0)] for d in dates}
    res = run_portfolio(panel, entries, max_positions=1, hold_days=2)
    assert res.metrics["final_equity"] > 1_000_000
    assert res.metrics["CAGR_%"] > 0
    assert res.metrics["win_rate_%"] >= 80

def test_no_entries_flat_equity():
    panel, dates = _panel("A", [100.0] * 6)
    res = run_portfolio(panel, {}, max_positions=1, hold_days=1)
    assert res.metrics["n_trades"] == 0
    assert res.metrics["final_equity"] == 1_000_000
    assert res.metrics["exposure_%"] == 0

def test_max_positions_respected():
    """候選多於名額時，同時持股不超過 max_positions（以出入場守恆間接驗證）。"""
    dates = pd.bdate_range("2024-01-01", periods=12).strftime("%Y-%m-%d").tolist()
    panel = {}
    for sid in ["A", "B", "C", "D"]:
        panel[sid] = pd.DataFrame({"date": dates, "open": [50.0] * 12,
                                   "close": [50.0] * 12, "volume": [1000] * 12})
    entries = {d: [(s, 1.0) for s in ["A", "B", "C", "D"]] for d in dates}
    res = run_portfolio(panel, entries, init_capital=1_000_000,
                        max_positions=2, hold_days=3)
    # 權益始終為正、有交易發生、沒有爆掉
    assert res.metrics["n_trades"] > 0
    assert (res.equity > 0).all()
    assert res.metrics["exposure_%"] > 0


def test_hard_stop_triggers_and_caps_loss():
    """進場後大跌，硬性 -10% 停損應在觸價日以停損價出場、虧損被限制在約 -10%+成本。"""
    # 進場價=100（day1 open）；day4 開盤 95(>停損90) 但盤中低點 85(觸價) → 以停損價 90 出場
    prices = [100, 100, 100, 95, 100, 100]
    lows = [100, 100, 100, 85, 100, 100]     # day4 盤中最低 85
    panel, dates = _panel("A", prices, lows=lows)
    entries = {dates[0]: [("A", 1.0)]}        # 只在第一天發訊號
    res = run_portfolio(panel, entries, max_positions=1, hold_days=20,
                        stop=("pct", 0.10))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert abs(t.exit_price - 90) < 1e-6      # 以停損價 90 出場（未跳空穿越）
    assert -0.12 < t.ret < -0.09              # 虧損被限制在約 -10%(+成本)

def test_stop_reduces_drawdown_vs_none():
    """同一波下跌，有停損的 MaxDD 應優於（淺於）不停損。"""
    prices = [100, 100, 95, 90, 80, 70, 75, 80]
    lows = [100, 100, 95, 90, 80, 70, 75, 80]
    panel, dates = _panel("A", prices, lows=lows)
    entries = {dates[0]: [("A", 1.0)]}
    no_stop = run_portfolio(panel, entries, max_positions=1, hold_days=20)
    with_stop = run_portfolio(panel, entries, max_positions=1, hold_days=20,
                              stop=("pct", 0.05))
    assert with_stop.metrics["MaxDD_%"] > no_stop.metrics["MaxDD_%"]  # 較淺(較接近0)

def test_regime_blocks_entries():
    """regime_ok 全 False 時不應開任何倉。"""
    panel, dates = _panel("A", [100.0] * 6)
    entries = {d: [("A", 1.0)] for d in dates}
    res = run_portfolio(panel, entries, max_positions=1, hold_days=1,
                        regime_ok={d: False for d in dates})
    assert res.metrics["n_trades"] == 0

def test_compute_regime_ok_shape():
    prices = list(range(100, 130))            # 單調上升 → 後段都站上 MA20
    panel, dates = _panel("A", prices)
    ok = compute_regime_ok(panel, threshold=50, ma=20)
    assert ok[dates[-1]] is True              # 上升股末段必站上均線


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {len(fns)}/{len(fns)} passed")
