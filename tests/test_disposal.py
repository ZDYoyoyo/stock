"""處置股／注意股警示：民國日期解析＋期間判定＋標籤（不打網路，純函式）。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import disposal as dp


def test_roc_to_iso():
    assert dp._roc_to_iso("115/08/10") == "2026-08-10"      # TWSE 格式
    assert dp._roc_to_iso("1150813") == "2026-08-13"        # TPEX 格式
    assert dp._roc_to_iso("115-08-10") == "2026-08-10"
    assert dp._roc_to_iso("") is None
    assert dp._roc_to_iso("亂碼") is None
    assert dp._roc_to_iso("115/13/99") is None              # 非法月日


def test_period_both_formats():
    assert dp._period("115/07/31～115/08/13") == ("2026-07-31", "2026-08-13")   # 全形～
    assert dp._period("1150813~1150821") == ("2026-08-13", "2026-08-21")        # 半形~
    assert dp._period("") == (None, None)


def test_label_by_today_position():
    rec = {"起": "2026-08-10", "迄": "2026-08-14"}
    assert "🚫處置中" in dp._label(rec, "2026-08-12")        # 期間內
    assert "至08-14" in dp._label(rec, "2026-08-12")
    assert dp._label(rec, "2026-08-15") is None              # 已結束→不標
    up = dp._label({"起": "2026-08-20", "迄": "2026-08-26"}, "2026-08-12")
    assert up is not None and "將處置" in up                  # 尚未開始


def test_compute_prefers_disposal_over_notice(monkeypatch):
    monkeypatch.setattr(dp, "fetch_disposals", lambda: {
        "1111": {"起": "2026-08-10", "迄": "2026-08-14"},     # 處置中
        "2222": {"起": "2026-08-01", "迄": "2026-08-05"}})    # 已結束
    monkeypatch.setattr(dp, "fetch_notices", lambda: {"1111": "股價異常", "3333": "週轉率過高"})
    m = dp.compute("2026-08-12")
    assert "🚫處置中" in m["1111"]        # 處置優先於注意
    assert "2222" not in m                # 已結束不標
    assert "注意股" in m["3333"]


def test_enrich_adds_column_blank_when_clean():
    df = pd.DataFrame([{"stock_id": "1111", "name": "處置股"},
                       {"stock_id": "9999", "name": "正常股"}])
    out = dp.enrich(df, {"1111": "🚫處置中(至08-14·分盤)"})
    assert out.loc[0, "處置警示"].startswith("🚫")
    assert out.loc[1, "處置警示"] == ""     # 無警示留空，不用「—」製造雜訊


def test_enrich_noop_on_empty_map():
    df = pd.DataFrame([{"stock_id": "1111"}])
    assert "處置警示" not in dp.enrich(df, {}).columns   # 抓不到→不加欄，不影響版面


def test_fetch_handles_network_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(dp.requests, "get", boom)
    assert dp.fetch_disposals() == {} and dp.fetch_notices() == {}   # graceful
