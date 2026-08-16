"""Walk-forward 共用計算（折切／權益曲線指標／買入持有基準）測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from src import walkforward as wf


def test_make_folds_expanding_and_non_overlapping():
    dates = [f"d{i:04d}" for i in range(1105)]
    folds = wf.make_folds(dates)
    assert len(folds) == 3
    # train 一律從頭開始且越來越長；test 緊接 train 之後、彼此不重疊
    assert all(f[1] == dates[0] for f in folds)
    assert folds[0][2] < folds[1][2] < folds[2][2]
    for i in range(len(folds) - 1):
        assert folds[i][4] < folds[i + 1][3]          # 前折 test 迄 < 後折 test 起
    assert folds[-1][4] == dates[-1]                  # 最後一折吃到底
    # 切點 25/50/75%（與原 T16 OOS 相同）
    assert folds[0][2] == dates[276] and folds[0][3] == dates[277]


def test_make_folds_short_series_no_crash():
    """資料太短無法切出有效 train/test → 回空清單（不炸），呼叫端自然跑不出折。"""
    assert wf.make_folds([f"d{i}" for i in range(4)]) == []
    assert wf.make_folds(["d0", "d1"]) == []
    assert len(wf.make_folds([f"d{i}" for i in range(40)])) == 3   # 夠長就正常切


def test_curve_metrics_basic():
    # 剛好一年（TRADING_DAYS 日）翻倍 → CAGR≈100%，且回撤為 0（單調上升）
    n = wf.TRADING_DAYS
    eq = pd.Series([1_000_000 * (2 ** (i / (n - 1))) for i in range(n)])
    m = wf.curve_metrics(eq)
    assert m["CAGR_%"] == pytest.approx(100, abs=2)
    assert m["MaxDD_%"] == pytest.approx(0, abs=0.01)
    assert m["Sharpe"] > 0


def test_curve_metrics_drawdown_and_edge_cases():
    eq = pd.Series([100.0, 120.0, 60.0, 90.0])
    assert wf.curve_metrics(eq)["MaxDD_%"] == pytest.approx(-50.0, abs=0.01)   # 120→60
    assert wf.curve_metrics(None)["CAGR_%"] == 0.0
    assert wf.curve_metrics(pd.Series([1.0]))["Sharpe"] == 0.0


def test_bh_curve_equal_weight():
    """兩檔等權：一檔翻倍、一檔不動 → 期末權益 = 1.5 倍。"""
    panel = {
        "1111": pd.DataFrame({"date": ["d1", "d2"], "close": [10.0, 20.0]}),
        "2222": pd.DataFrame({"date": ["d1", "d2"], "close": [50.0, 50.0]}),
    }
    cur = wf.bh_curve(panel, "d1", "d2", init_cap=1_000_000)
    assert cur.iloc[0] == pytest.approx(1_000_000)
    assert cur.iloc[-1] == pytest.approx(1_500_000)
