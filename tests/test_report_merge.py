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
            "外資今日": 100, "外資昨日": 200, "外資": 500, "外資20日": 1200,
            "投信今日": 10, "投信昨日": -15, "投信": -20, "投信20日": 50,
            "自營今日": 5, "自營昨日": 7, "自營": 8, "自營20日": None,
            "融資今日": -30, "融資昨日": -40, "融資增減": -100, "融資20日": -250,
            "融券今日": 3, "融券昨日": 6, "融券增減": -343, "融券20日": 20}


_COLS = ["stock_id", "name", "外資今日", "外資", "投信今日", "投信",
         "自營今日", "自營", "融資今日", "融資增減", "融券今日", "融券增減"]


def test_fmt_group_text_positional_and_missing():
    assert rh.fmt_group_text([100, 200, 500, 1200]) == "+100／+200／+500／+1,200"
    assert rh.fmt_group_text([5, 7, 8, pd.NA]) == "+5／+7／+8／—"


def test_group_source_cols_covers_all_windows():
    src = rh.group_source_cols()
    for base in ("外資", "投信", "自營", "融資", "融券"):
        assert f"{base}今日" in src or f"{base}增減" in src or base in src
    assert "外資昨日" in src and "外資20日" in src     # 今/昨/10/20
    assert "外資60日" not in src                       # 60日已移除


def test_html_table_merges_five_cells():
    df = pd.DataFrame([_row()])
    html = rh._table(df, _COLS, signed_cols=[])
    # 今/昨等來源欄併入 anchor → 表頭剩 代號/名稱 + 5 個法人資券欄 = 7（th 現帶 data-col 屬性）
    assert html.count("<th ") == 7
    assert rh._GROUP_HEAD in html
    for v in ("+100", "+200", "+500", "+1,200"):   # 外資今/昨/10/20
        assert v in html
    assert "—" in html                              # 自營20日缺值


def test_html_hides_today_source_columns():
    df = pd.DataFrame([_row()])
    html = rh._table(df, _COLS, signed_cols=[])
    header = html.split("<tbody>")[0]
    # 「外資今日」不應獨立成表頭欄（已併進外資格）
    assert "外資今日" not in header.replace("<small>", "").replace("</small>", "")


def test_price_tag_prefers_today_close_over_base_close():
    """收盤價篩選標記：多數軌同時有 close(基準) 與 今日收盤 → data-price 標在今日收盤。"""
    df = pd.DataFrame([{"stock_id": "2330", "name": "台積電", "close": 1080.0, "今日收盤": 1085.0}])
    html = rh._table(df, ["stock_id", "name", "close", "今日收盤"], signed_cols=[])
    assert 'data-price="1085.0"' in html                 # 標在今日收盤
    assert 'data-col="close" data-price' not in html      # 基準收盤不標


def test_price_tag_falls_back_to_close_then_absent():
    """第6軌只有 close → 標 close；追蹤/無價欄的表 → 無 data-price（不受篩選）。"""
    snipe = pd.DataFrame([{"stock_id": "3374", "name": "精材", "close": 95.0}])
    assert 'data-price="95.0"' in rh._table(snipe, ["stock_id", "name", "close"], [])
    noprice = pd.DataFrame([{"stock_id": "3374", "name": "精材", "鎖碼淨額": 500}])
    assert "data-price" not in rh._table(noprice, ["stock_id", "name", "鎖碼淨額"], [])


def test_id_cell_links_to_kchart():
    """代號欄→Goodinfo K 線圖外連（新分頁）；stock_id 與 代號 兩種欄名都涵蓋。"""
    a = rh._table(pd.DataFrame([{"stock_id": "2330", "name": "台積電", "close": 1080.0}]),
                  ["stock_id", "name", "close"], [])
    assert 'href="https://goodinfo.tw/tw/ShowK_Chart.asp?STOCK_ID=2330"' in a
    assert 'target="_blank"' in a and 'class="klink"' in a
    b = rh._table(pd.DataFrame([{"代號": "1101", "名稱": "台泥", "現價": 38.0}]),
                  ["代號", "名稱", "現價"], [])
    assert "STOCK_ID=1101" in b


def test_klink_passthrough_on_blank():
    assert rh._klink("") == ""                     # 空代號不做連結
    assert "STOCK_ID=2330" in rh._klink("2330")


def test_price_filter_control_and_js_present():
    df = pd.DataFrame([{"stock_id": "2330", "name": "台積電", "今日收盤": 1085.0}])
    page = rh.build("2026-08-11", {"regime": "中性"}, ["x"], "sox",
                    [{"title": "測試軌", "df": df, "cols": ["stock_id", "name", "今日收盤"], "signed": []}])
    assert 'id="pxmax"' in page and "function pxApply" in page
