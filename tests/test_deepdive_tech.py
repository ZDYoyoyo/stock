"""個股深掘 技術面：均線序列（ma_series）＋多序列疊圖（svgchart.lines）測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import svgchart as sc


def test_lines_shares_yscale_and_draws_all_series():
    """多序列共用 y 範圍：每條有效序列一條 polyline；收盤線標點(circle)。"""
    series = [("收盤", "#2d7ef7", [10, 11, 12, 13]),
              ("MA5", "#e67e22", [9, 9.5, 10, 10.5]),
              ("MA20", "#8e44ad", [8, 8, 8, 8])]
    svg = sc.lines(series, ["d1", "d2", "d3", "d4"])
    assert svg.count("<polyline") == 3          # 三條序列各一條線
    assert "<circle" in svg                       # 收盤線有點(tooltip)
    assert "#2d7ef7" in svg and "#8e44ad" in svg  # 各序列顏色都畫出


def test_lines_skips_short_series_and_handles_empty():
    # 只有 1 點的序列不畫線；整體 <2 點回提示
    assert sc.lines([("a", "#000", [5])]) == "<p class='note'>（資料不足）</p>"
    svg = sc.lines([("收盤", "#111", [1, 2, 3]), ("空", "#222", [None, None, None])])
    assert svg.count("<polyline") == 1            # 全 None 的序列被跳過


@pytest.fixture
def _db(monkeypatch, tmp_path):
    from src import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    yield db
    db.clear_cache()


def test_ma_series_aligns_to_dates_using_full_history(_db):
    """MA 用完整歷史算，只回傳落在 dates 內的點（窗頭均線不因截斷而缺）。"""
    from src import stock_deepdive as dd
    # 7 天收盤 10..16，只顯示最後 3 天
    price = [{"date": f"2026-08-{d:02d}", "stock_id": "9999", "close": 9 + d, "volume": 100}
             for d in range(1, 8)]
    with _db.connect() as conn:
        _db.upsert(conn, "price", price)
    show = ["2026-08-05", "2026-08-06", "2026-08-07"]
    out = dd.ma_series("9999", show)
    assert out["dates"] == show
    assert out["收盤"] == [14.0, 15.0, 16.0]
    # MA5 於 08-05＝(10+11+12+13+14)/5=12.0（用到窗前的資料）
    assert out["MA5"][0] == 12.0
    # 只有 7 天 → MA20/MA60 應為 NaN（rolling 不足）
    import math
    assert math.isnan(out["MA20"][0])


def test_ma_series_empty_when_no_price(_db):
    from src import stock_deepdive as dd
    assert dd.ma_series("0000", ["2026-08-05"]) == {}
