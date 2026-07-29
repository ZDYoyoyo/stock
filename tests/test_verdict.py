"""綜合定調投票單元測試 — 證明 5 面向加總與門檻正確。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import verdict


def _row(**kw):
    base = {"主導度%": 0, "融資佔量%": 0, "均線排列": "⚪糾結",
            "季線年線": "—", "20MA乖離%": 0}
    base.update(kw)
    return pd.Series(base)


def test_all_bull_gives_偏多():
    r = _row(主導度=None, 均線排列="🔴多頭", 季線年線="🔴站上季年", **{"20MA乖離%": 3.0})
    r["主導度%"] = 5.0
    r["融資佔量%"] = -3.0
    assert verdict._vote(r) == 5
    assert verdict.label(verdict._vote(r)) == "🔴偏多"


def test_all_bear_gives_偏空():
    r = _row(均線排列="🟢空頭", 季線年線="🟢跌破季年")
    r["主導度%"] = -5.0
    r["融資佔量%"] = 10.0
    r["20MA乖離%"] = -3.0
    assert verdict._vote(r) == -5
    assert verdict.label(verdict._vote(r)) == "🟢偏空"


def test_mixed_gives_觀望():
    # 2 多(均線多頭+站上季年) vs 2 空(法人賣+乖離負) → 淨 0 → 觀望
    r = _row(均線排列="🔴多頭", 季線年線="🔴站上季年")
    r["主導度%"] = -5.0
    r["20MA乖離%"] = -1.0
    assert verdict._vote(r) == 0
    assert verdict.label(0) == "⚪觀望"


def test_threshold_needs_2():
    assert verdict.label(1) == "⚪觀望"    # 淨 +1 不夠
    assert verdict.label(2) == "🔴偏多"
    assert verdict.label(-1) == "⚪觀望"
    assert verdict.label(-2) == "🟢偏空"


def test_missing_columns_neutral():
    # 全缺欄位 → 各面向中性 → 觀望（不報錯）
    df = pd.DataFrame([{"stock_id": "1234"}])
    out = verdict.add_verdict(df)
    assert out["定調"].iloc[0] == "⚪觀望"


def test_empty_df_passthrough():
    assert verdict.add_verdict(pd.DataFrame()).empty
