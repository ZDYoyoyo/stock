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


# ---- 各軌回測驗證徽章（避免看到清單就當成保證會賺）----

def test_backtest_verdicts_cover_all_tracks():
    for key in ("T11", "T16", "T12", "長期", "當沖", "隔日沖鎖碼"):
        lvl, short, detail = rh.BACKTEST_VERDICTS[key]
        assert lvl in ("ok", "no", "warn") and short and detail
    # 只有 T16 通過嚴格樣本外；T11/T12/長期 無 edge
    assert rh.BACKTEST_VERDICTS["T16"][0] == "ok"
    assert all(rh.BACKTEST_VERDICTS[k][0] == "no" for k in ("T11", "T12", "長期"))


def test_verdict_badge_and_md():
    badge = rh._verdict_badge("T12")
    assert 'class="bt bt-no"' in badge and "❌" in badge and "樣本外失效" in badge
    assert rh.verdict_md("T16").startswith("🔬 **回測驗證：✅")
    assert rh._verdict_badge(None) == "" and rh.verdict_md("持股") == ""   # 無此軌→不顯示


def test_build_puts_badge_in_heading():
    df = pd.DataFrame([{"stock_id": "2330", "name": "台積電", "今日收盤": 1085.0}])
    page = rh.build("2026-08-16", {"regime": "中性"}, ["x"], "sox",
                    [{"bt": "T16", "title": "🟡 波段｜T16 抗跌強勢", "df": df,
                      "cols": ["stock_id", "name", "今日收盤"], "signed": []}])
    assert "🟡 波段｜T16 抗跌強勢<span class=\"bt bt-ok\">" in page


def test_no_csv_note_makes_no_false_promise():
    """未輸出 CSV 時不可寫「完整清單見 CSV」（該檔根本不存在）。"""
    df = pd.DataFrame([{"stock_id": f"{i}", "name": "x", "今日收盤": 10.0} for i in range(20)])
    page = rh.build("2026-08-16", {"regime": "中性"}, ["x"], "sox",
                    [{"title": "測試軌", "df": df, "n": 5,
                      "cols": ["stock_id", "name", "今日收盤"], "signed": []}])
    assert "需在控制台勾選「輸出 CSV」" in page
    assert "完整清單見同資料夾 CSV" not in page


def test_table_headers_are_sortable():
    """表頭可點排序（純視圖）：每個 th 帶 onclick，且排序 JS 有嵌進頁面。"""
    import pandas as pd
    from src import report_html as rh
    df = pd.DataFrame([{"stock_id": "9999", "name": "妖股", "停損%": -8.7},
                       {"stock_id": "8888", "name": "強股", "停損%": -1.8}])
    h = rh._table(df, ["stock_id", "name", "停損%"], ["停損%"])
    assert h.count("onclick=\"sortT(this)\"") == 3      # 三個欄都可點
    assert "function sortT" in rh._SORT_JS and "localeCompare" in rh._SORT_JS


def test_t16_entries_rank_modes():
    """T16 排序模式：篩選結果相同，只有 score 換算方式不同；rs 為預設(已驗證那個)。"""
    import pandas as pd
    from src.portfolio_backtest import compute_t16_entries
    # 兩檔同樣漲 10%，但 9999 波動大、8888 波動小
    import itertools
    rows = []
    for i, d in enumerate([f"D{n:02d}" for n in range(40)]):
        rows.append({"date": d, "stock_id": "9999", "open": 100, "high": 100,
                     "low": 100, "close": 100 * (1.0025 ** i) * (1.05 if i % 2 else 0.95)})
        rows.append({"date": d, "stock_id": "8888", "open": 100, "high": 100,
                     "low": 100, "close": 100 * (1.0025 ** i)})
    panel = {sid: g.reset_index(drop=True)
             for sid, g in pd.DataFrame(rows).groupby("stock_id")}
    rs = compute_t16_entries(panel, rank="rs")
    lv = compute_t16_entries(panel, rank="lowvol")
    assert rs and lv
    d = sorted(set(rs) & set(lv))[-1]
    assert {s for s, _ in rs[d]} == {s for s, _ in lv[d]}      # 篩選結果一致
    # lowvol：分數 = −波動率 → 波動小的 8888 分數較高（排前面）
    m = dict(lv[d])
    assert m["8888"] > m["9999"]


def test_update_holders_writes_week(monkeypatch, tmp_path):
    """盤後順手更新千張大戶：寫進 DB 並回傳資料週（重跑同一週＝覆蓋，不重複累積）。"""
    import importlib
    from src import db, tdcc_client as tdcc
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    rows = [{"date": "2026-08-28", "stock_id": "1101", "pct_1000": 52.4, "pct_400": 56.3},
            {"date": "2026-08-28", "stock_id": "2330", "pct_1000": 78.1, "pct_400": 80.0}]
    monkeypatch.setattr(tdcc, "fetch", lambda: rows)
    ra = importlib.import_module("scripts.run_all")

    assert ra._update_holders() == "2026-08-28"
    assert ra._update_holders() == "2026-08-28"          # 冪等：再跑一次仍是同一週
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM big_holders").fetchone()[0]
    assert n == 2                                        # 覆蓋而非疊加
    db.clear_cache()


def test_update_holders_survives_source_failure(monkeypatch, tmp_path):
    """TDCC 掛掉不可以擋住整份日報（大戶欄留白即可）。"""
    import importlib
    from src import db, tdcc_client as tdcc
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t2.db"))
    db.clear_cache()
    ra = importlib.import_module("scripts.run_all")

    def _boom():
        raise RuntimeError("TDCC 503")
    monkeypatch.setattr(tdcc, "fetch", _boom)
    assert ra._update_holders() == ""
    monkeypatch.setattr(tdcc, "fetch", lambda: [])
    assert ra._update_holders() == ""
    db.clear_cache()
