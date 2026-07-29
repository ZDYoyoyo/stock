"""T16 嚴格樣本外腳本的純函式測試（不跑回測、不碰檔案）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from scripts import run_t16_oos as oos


def test_curve_metrics_growth_positive():
    # 單調成長曲線 → CAGR 正、回撤 0
    eq = pd.Series([1_000_000 * (1.001 ** i) for i in range(300)])
    m = oos._curve_metrics(eq)
    assert m["CAGR_%"] > 0
    assert m["MaxDD_%"] == 0.0        # 沒回檔


def test_curve_metrics_drawdown_negative():
    # 先漲後腰斬 → 回撤明顯為負
    eq = pd.Series([100, 120, 150, 90, 80], dtype=float)
    m = oos._curve_metrics(eq)
    assert m["MaxDD_%"] < -30           # 150→80 約 -46%


def test_curve_metrics_too_short():
    assert oos._curve_metrics(pd.Series([1.0]))["Sharpe"] == 0.0


def test_cfg_label_readable():
    cfg = {"maxpos": 10, "stop": ("pct", 0.08), "regime": 45.0, "lookback": 10}
    lbl = oos._cfg_label(cfg)
    assert "持10" in lbl and "停損8%" in lbl and "regime45" in lbl and "LB10" in lbl
    cfg2 = {"maxpos": 5, "stop": None, "regime": None, "lookback": 20}
    assert "無停損" in oos._cfg_label(cfg2) and "無regime" in oos._cfg_label(cfg2)


def test_grid_size():
    # 3×2×2×2 = 24 組
    assert len(list(oos._grid())) == 24
