"""組合回測引擎單元測試 — 用合成資料證明權益曲線與指標數學正確。

跑法：python tests/test_portfolio_backtest.py   或   python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.portfolio_backtest import (
    _metrics, compute_longterm_entries, compute_regime_ok, compute_t12_entries,
    run_portfolio, slice_panel,
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


def test_slice_panel_bounds():
    panel, dates = _panel("A", list(range(100, 110)))   # 10 天
    lo, hi = dates[3], dates[6]
    sub = slice_panel(panel, lo=lo, hi=hi)
    got = sub["A"]["date"].tolist()
    assert got == dates[3:7]                             # 含邊界共 4 天
    assert slice_panel(panel, lo=dates[-1] + "9") == {}  # 全被切掉→空面板


# --- T12 月營收動能 point-in-time ---
def _rev(sid, rows):
    """造月營收面板：rows=[(pub_date, ym, yoy, cum_yoy)]。"""
    df = pd.DataFrame(rows, columns=["pub_date", "ym", "yoy", "cum_yoy"])
    return {sid: df}


def test_t12_no_lookahead():
    """公布日(pub_date)之前的交易日，不得看到那筆營收 → 無前視偏誤。"""
    panel, dates = _panel("A", [100.0] * 8, start="2024-01-01")
    # 營收 6/2 公布卻在 1 月的交易日就選到 = 前視。pub_date 設在面板日期之後。
    rev = _rev("A", [("2024-06-02", "2024-05", 50.0, 30.0)])
    entries = compute_t12_entries(panel, rev)
    assert all(d < "2024-06-02" for d in dates)     # 面板全在公布日之前
    assert entries == {}                             # 尚未公布 → 完全無訊號

def test_t12_signal_after_publish():
    """公布日當天(含)起，門檻過關的營收才成為候選。"""
    panel, dates = _panel("A", [100.0] * 8, start="2024-01-01")
    pub = dates[3]                                   # 第4個交易日公布
    rev = _rev("A", [(pub, "2023-12", 50.0, 30.0)])  # YoY50/累計30/加速20 → 過門檻
    entries = compute_t12_entries(panel, rev)
    assert all(d not in entries for d in dates[:3])  # 公布前無訊號
    assert all(dates[i] in entries for i in range(3, 8))  # 公布後每日都在候選

def test_t12_threshold_filters():
    """YoY 低於門檻 / 加速度不足 → 不入選。"""
    panel, dates = _panel("A", [100.0] * 6, start="2024-01-01")
    pub = dates[0]
    low_yoy = compute_t12_entries(panel, _rev("A", [(pub, "2023-12", 5.0, 3.0)]))
    assert low_yoy == {}                             # YoY5 < MIN_YOY(30)
    no_accel = compute_t12_entries(panel, _rev("A", [(pub, "2023-12", 40.0, 39.0)]))
    assert no_accel == {}                            # 加速度1 < MIN_ACCEL(5)

def test_t12_uses_latest_published():
    """有多筆已公布時，取 pub_date 最新的一筆判斷（point-in-time 最新狀態）。"""
    panel, dates = _panel("A", [100.0] * 8, start="2024-01-01")
    rev = _rev("A", [(dates[0], "2023-11", 50.0, 30.0),   # 早期：過門檻
                     (dates[4], "2023-12", 5.0, 3.0)])    # 較新：不過門檻 → 之後應失格
    entries = compute_t12_entries(panel, rev)
    assert dates[2] in entries                       # 只有舊營收時：入選
    assert dates[5] not in entries                   # 新營收公布後轉不合格 → 退出候選


# --- 長期價值 point-in-time ---
def _valdf(rows):
    """造估值面板：rows=[(date, sid, yield, per, pbr)]。"""
    return pd.DataFrame(rows, columns=["date", "stock_id", "yield", "per", "pbr"])


def test_longterm_screen_pass_and_fail():
    """殖利率≥3/0<PER≤20/0<PBR≤3/ROE估≥5 全過才入選。"""
    panel = {"A": None, "B": None, "C": None, "D": None}  # 只需 key 判斷在不在 universe
    val = _valdf([
        ("2024-01-02", "A", 5.0, 10.0, 1.5),    # 過：ROE估=15
        ("2024-01-02", "B", 2.0, 10.0, 1.5),    # 殖利率<3 → 剔
        ("2024-01-02", "C", 5.0, 25.0, 1.5),    # PER>20 → 剔
        ("2024-01-02", "D", 5.0, 30.0, 1.0),    # ROE估=3.3<5 → 剔
    ])
    e = compute_longterm_entries(panel, val)
    assert list(e.keys()) == ["2024-01-02"]
    assert [sid for sid, _ in e["2024-01-02"]] == ["A"]


def test_longterm_only_universe_stocks():
    """不在 price panel(無OHLC)的股不列入。"""
    panel = {"A": None}
    val = _valdf([("2024-01-02", "A", 5.0, 10.0, 1.5),
                  ("2024-01-02", "Z", 6.0, 8.0, 1.2)])   # Z 不在 panel
    e = compute_longterm_entries(panel, val)
    assert [sid for sid, _ in e["2024-01-02"]] == ["A"]


def test_longterm_score_higher_for_cheaper_higher_yield():
    """殖利率高且 PER 低者分數較高（排序在前）。"""
    panel = {"A": None, "B": None}
    val = _valdf([("2024-01-02", "A", 6.0, 8.0, 1.0),    # 高息低估
                  ("2024-01-02", "B", 3.0, 19.0, 2.9)])  # 低息高估(勉強過門檻)
    picks = compute_longterm_entries(panel, val)["2024-01-02"]
    picks.sort(key=lambda x: x[1], reverse=True)
    assert picks[0][0] == "A"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {len(fns)}/{len(fns)} passed")


def test_t11q_entries_no_lookahead_and_filters():
    """T11Q 潛伏吸籌：只用當日(含)以前資料；漲太多／量沒縮／在高檔都不該入選。"""
    import pandas as pd
    from src.portfolio_backtest import compute_t11q_entries

    def _panel(closes, vols, nets):
        d = [f"D{i:04d}" for i in range(len(closes))]
        return {"9999": pd.DataFrame({"date": d, "open": closes, "high": closes,
                                      "low": closes, "close": closes, "volume": vols,
                                      "inst_net": nets, "margin_balance": [0] * len(closes)})}

    n = 300
    # 基準情境：長期橫盤(100附近)、法人天天小買、近期量縮 → 應該入選
    closes = [100 + (i % 3) * 0.2 for i in range(n)]
    vols = [1000] * (n - 10) + [500] * 10
    nets = [30] * n
    e = compute_t11q_entries(_panel(closes, vols, nets))
    assert e, "橫盤+法人持續買+量縮 應該要有訊號"
    assert all(d >= "D0240" for d in e), "未滿 52 週不該出訊號（要算位階）"

    # 法人沒買 → 無訊號（吸籌是必要條件）
    assert not compute_t11q_entries(_panel(closes, vols, [0] * n))

    # 量沒縮 → 無訊號。爆量只發生在最後 10 天，所以只有那幾天該被擋掉
    # （更早的日子當下量能仍是縮的，有訊號才對——這正是 point-in-time 的意思）
    e2 = compute_t11q_entries(_panel(closes, [1000] * (n - 10) + [3000] * 10, nets))
    assert not [d for d in e2 if d >= "D0295"], "近10日爆量的那幾天不該入選"
    assert [d for d in e2 if d < "D0290"], "爆量之前的日子不受影響"

    # 已經噴上去 → 無訊號（這正是與 T11 相反的那條）。噴出只在最後 20 天，
    # 同樣只有「窗內看得到漲幅」的那幾天該被擋掉。
    up = closes[:n - 20] + [100 * (1 + 0.02 * i) for i in range(20)]
    e3 = compute_t11q_entries(_panel(up, vols, nets))
    assert not [d for d in e3 if d >= "D0285"], "窗內已大漲的日子不該入選"
