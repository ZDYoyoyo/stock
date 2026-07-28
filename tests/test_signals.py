"""src/signals 單元測試 — 護住『回測＝實際』共用的訊號原語。

跑法：python -m pytest tests/ -q      或    python tests/test_signals.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.signals import (
    FEE, TAX, T11Params, consec_buy_series, consecutive_net_days,
    round_trip_return, t11_pass,
)


# --- consecutive_net_days（時間順序 舊→新）---
def test_consec_buy_streak():
    assert consecutive_net_days([-5, 0, 1, 2, 3]) == 3      # 最近3天連買

def test_consec_sell_streak():
    assert consecutive_net_days([9, -1, -2, -3]) == -3      # 最近3天連賣

def test_consec_flat_latest_breaks():
    assert consecutive_net_days([1, 2, 0]) == 0             # 最新平盤→0

def test_consec_sign_change_stops_at_latest_run():
    assert consecutive_net_days([1, 1, -1, 1]) == 1         # 只算最近一段

def test_consec_empty_and_na():
    assert consecutive_net_days([]) == 0
    assert consecutive_net_days([1, 2, pd.NA]) == 0         # NA 在最新→中斷


def test_consec_matches_old_chip_streak():
    """比對舊 chip_signal._streak（吃 新→舊）的等價性。"""
    def old_streak(nets):  # 舊實作（新→舊）
        for v in nets:
            if v and v > 0:
                sign = 1; break
            if v and v < 0:
                sign = -1; break
        else:
            return 0
        n = 0
        for v in nets:
            if (v > 0 and sign > 0) or (v < 0 and sign < 0):
                n += 1
            else:
                break
        return n * sign
    for newest_first in ([3, 2, 1, -1], [-2, -1, 5], [0, 1, 1], [1], [], [-1, -1, -1]):
        chrono = list(newest_first)[::-1]
        assert consecutive_net_days(chrono) == old_streak(newest_first), newest_first


# --- consec_buy_series ---
def test_consec_buy_series_basic():
    assert consec_buy_series([1, 2, -1, 3, 4]) == [1, 2, 0, 1, 2]

def test_consec_buy_series_matches_old_inline():
    """比對舊回測內嵌迴圈的等價性。"""
    net = [1, -1, 2, 3, 0, 5, 6, 7]
    old = [0] * len(net)
    for i in range(len(net)):
        old[i] = old[i - 1] + 1 if (net[i] > 0 and i > 0 and net[i - 1] > 0) \
            else (1 if net[i] > 0 else 0)
    assert consec_buy_series(net) == old


# --- round_trip_return ---
def test_round_trip_flat_is_negative_by_costs():
    """進出同價時，報酬應為負（被手續費+證交稅吃掉）。"""
    r = round_trip_return(100, 100)
    assert r < 0 and abs(r + (FEE + FEE + TAX)) < 1e-3   # ≈ -(買費+賣費+稅)

def test_round_trip_gain():
    r = round_trip_return(100, 110)
    assert 0.085 < r < 0.10        # 毛 +10%，扣成本後略低

def test_round_trip_invalid_entry():
    assert round_trip_return(0, 100) == 0.0
    assert round_trip_return(-5, 100) == 0.0


# --- t11_pass（point-in-time）---
def _win(gain=0.05, consec=5, buy_ratio=0.08, margin_inc=0.0, above=0.0):
    """造一個近似 10 日窗口，預設應通過 T11。"""
    n = 10
    first = 100.0
    last = first * (1 + gain)
    closes = [first + (last - first) * i / (n - 1) for i in range(n)]
    total_vol = 100000
    nets = [0] * (n - consec) + [total_vol * buy_ratio / consec] * consec  # 最近 consec 天連買
    vols = [total_vol / n] * n
    m0 = 1000.0
    margin = [m0, m0 * (1 + margin_inc)]
    ma20 = last / (1 + above)     # above=0 → 收盤=MA20
    return closes, vols, nets, margin, ma20

def test_t11_pass_typical():
    assert t11_pass(*_win()) is True

def test_t11_fail_not_enough_consec():
    assert t11_pass(*_win(consec=2)) is False

def test_t11_fail_price_gain_too_high():
    assert t11_pass(*_win(gain=0.20)) is False        # 已噴出

def test_t11_fail_price_crashed():
    assert t11_pass(*_win(gain=-0.15)) is False        # 崩跌接刀

def test_t11_fail_low_buy_ratio():
    assert t11_pass(*_win(buy_ratio=0.01)) is False    # 吃貨力道不足

def test_t11_fail_margin_surge():
    assert t11_pass(*_win(margin_inc=0.30)) is False   # 融資暴增（散戶追）

def test_t11_fail_extended_above_ma20():
    assert t11_pass(*_win(above=0.30)) is False         # 高於 MA20 太多


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {passed}/{len(fns)} passed")
