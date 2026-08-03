"""法人/資券多時窗同格堆疊（今/10/20/60）— 顯示機制測試。

跑法：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import report_html as rh


def _row():
    return {"stock_id": "2330", "name": "台積電",
            "外資今日": 100, "外資": 500, "外資20日": 1200, "外資60日": -300,
            "投信今日": 10, "投信": -20, "投信20日": 50, "投信60日": 80,
            "自營今日": 5, "自營": 8, "自營20日": None, "自營60日": 12,
            "融資今日": -30, "融資增減": -100, "融資20日": -250, "融資60日": 400,
            "融券今日": 3, "融券增減": -343, "融券20日": 20, "融券60日": None}


_COLS = ["stock_id", "name", "外資今日", "外資", "投信今日", "投信",
         "自營今日", "自營", "融資今日", "融資增減", "融券今日", "融券增減"]


def test_fmt_group_text_positional_and_missing():
    assert rh.fmt_group_text([100, 500, 1200, -300]) == "+100／+500／+1,200／-300"
    assert rh.fmt_group_text([5, 8, pd.NA, 12]) == "+5／+8／—／+12"


def test_group_source_cols_covers_all_windows():
    src = rh.group_source_cols()
    for base in ("外資", "投信", "自營", "融資", "融券"):
        assert f"{base}今日" in src or f"{base}增減" in src or base in src
    assert "外資20日" in src and "外資60日" in src


def test_html_table_merges_five_cells():
    df = pd.DataFrame([_row()])
    html = rh._table(df, _COLS, signed_cols=[])
    # 今日等來源欄併入 anchor → 表頭剩 代號/名稱 + 5 個法人資券欄 = 7
    assert html.count("<th>") == 7
    assert rh._GROUP_HEAD in html
    for v in ("+100", "+500", "+1,200", "-300"):   # 外資今/10/20/60
        assert v in html
    assert "—" in html                              # 自營20日缺值


def test_html_hides_today_source_columns():
    df = pd.DataFrame([_row()])
    html = rh._table(df, _COLS, signed_cols=[])
    header = html.split("<tbody>")[0]
    # 「外資今日」不應獨立成表頭欄（已併進外資格）
    assert "外資今日" not in header.replace("<small>", "").replace("</small>", "")
