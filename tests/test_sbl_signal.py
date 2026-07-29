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
