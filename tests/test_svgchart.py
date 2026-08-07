"""inline SVG 迷你圖純函式測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import svgchart as sc


def test_bars_signed_colors_and_svg():
    svg = sc.bars([100, -50, 0], ["D1", "D2", "D3"], signed=True, unit=" 張")
    assert svg.startswith("<svg") and "</svg>" in svg
    assert sc._UP in svg and sc._DOWN in svg          # 有紅(正)有綠(負)
    assert "<rect" in svg and "<title>" in svg        # 柱＋tooltip
    assert "D1: 100 張" in svg


def test_bars_skips_nan_none():
    import math
    svg = sc.bars([10, None, float("nan"), 20], ["a", "b", "c", "d"])
    # 只有 2 個有效點 → 2 根 rect（不含 baseline line）
    assert svg.count("<rect") == 2


def test_bars_empty():
    assert "無資料" in sc.bars([None, None])


def test_line_polyline_and_dots():
    svg = sc.line([1.0, 2.5, 2.0], ["D1", "D2", "D3"], unit="%")
    assert "<polyline" in svg and svg.count("<circle") == 3
    assert "D2: 2.50%" in svg


def test_line_needs_two_points():
    assert "資料不足" in sc.line([5.0], ["D1"])
