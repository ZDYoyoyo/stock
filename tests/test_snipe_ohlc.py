"""昨日隔日沖鎖碼候選 → 今日開高低收走勢（snipe_ohlc）測試。跑法：python -m pytest tests/ -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _tmp(monkeypatch, tmp_path):
    from src import db, picks_tracker as pt
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
    monkeypatch.setattr(pt, "PICKS_CSV", tmp_path / "picks.csv")
    yield
    db.clear_cache()


def _seed():
    from src import db, picks_tracker as pt
    # 昨日(D1)鎖碼精選：9999；今日(D2)開高走低、8888 開低走高
    price = [
        {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94, "close": 100, "volume": 10},
        {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101, "close": 102, "volume": 10},
        {"date": "D1", "stock_id": "8888", "open": 48, "high": 50, "low": 47, "close": 50, "volume": 10},
        {"date": "D2", "stock_id": "8888", "open": 49, "high": 55, "low": 48, "close": 54, "volume": 10},
    ]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame(
        [{"stock_id": "9999", "name": "妖股", "今主力淨額": 1234},
         {"stock_id": "8888", "name": "強股", "今主力淨額": -50}])}, n=15)


def test_snipe_ohlc_computes_gap_and_oc():
    from src import picks_tracker as pt
    _seed()
    so = pt.snipe_ohlc("D3")             # today>D2；prior 鎖碼日=D1，今交易日=D2
    assert so["date"] == "D1" and so["trade_date"] == "D2"
    r = {x["stock_id"]: x for x in so["rows"]}
    # 9999：昨收100→今開108(跳空+8%)、今收102(盤中 (102-108)/108=-5.56% 開高走低)
    assert r["9999"]["跳空%"] == 8.0
    assert r["9999"]["盤中%"] == -5.56
    assert r["9999"]["今高"] == 110 and r["9999"]["今低"] == 101
    assert r["9999"]["漲跌%"] == 2.0
    assert r["9999"]["鎖碼淨額"] == 1234        # 存檔時記下的當日主力淨額(買多少)
    assert r["8888"]["鎖碼淨額"] == -50
    # 8888：昨收50→今開49(跳空-2%)、今收54(盤中 (54-49)/49=+10.2% 開低走高)
    assert r["8888"]["跳空%"] == -2.0
    assert r["8888"]["盤中%"] == 10.2

    stats = pt.snipe_ohlc_stats(so)
    assert stats["n"] == 2 and stats["oc_down"] == 1     # 9999 盤中走低、8888 走高


def test_snipe_ohlc_empty_when_no_prior():
    from src import picks_tracker as pt
    assert pt.snipe_ohlc("D3") == {}                     # 無任何鎖碼紀錄


def test_snipe_ohlc_pick_label_on_non_trading_day():
    """pick 標籤落在非交易日(如週末)→ 應退到 ≤標籤的最後交易日查昨收，不撈空。"""
    from src import db, picks_tracker as pt
    # 交易日只有 D1、D3(D2 為非交易日)；pick 卻被標在 D2
    price = [
        {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94, "close": 100, "volume": 10},
        {"date": "D3", "stock_id": "9999", "open": 108, "high": 110, "low": 101, "close": 102, "volume": 10},
    ]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D2", {"隔日沖鎖碼": pd.DataFrame([{"stock_id": "9999", "name": "妖股"}])}, n=15)
    so = pt.snipe_ohlc("D4")
    assert so["date"] == "D1" and so["trade_date"] == "D3"   # 基準退到 D1、今日 D3
    assert so["rows"][0]["跳空%"] == 8.0                      # 昨收100(D1)→今開108(D3)


def test_snipe_ohlc_explain_columns():
    """解釋漲跌原因的欄：預估vs實際賣壓、今主力淨額、高檔回落%、振幅%、量能倍數、當沖比%。"""
    from src import db, picks_tracker as pt
    import pandas as pd
    # D1 收100；D2 開108 高110 低101 收102（衝高後回落）
    price = [{"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94,
              "close": 100, "volume": 100},
             {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101,
              "close": 102, "volume": 500}]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
        db.upsert(conn, "day_trade", [{"date": "D2", "stock_id": "9999", "dt_vol": 250}])
    # 存 pick 時一併記下當時的預估賣壓
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame(
        [{"stock_id": "9999", "name": "妖股", "今主力淨額": 1234, "預估賣壓佔量%": 6.5}])}, n=15)

    so = pt.snipe_ohlc("D3")
    r = so["rows"][0]
    assert r["預估賣壓%"] == 6.5                  # 昨天存下的預測值
    assert r["高檔回落%"] == -7.27                # (102-110)/110 衝高後被倒
    assert r["振幅%"] == 9.0                      # (110-101)/100
    assert r["當沖比%"] == 50.0                   # 250/500
    # 量能倍數：前一日量100、今日500 → 5倍（樣本<5日則留空，這裡僅1日→NA）
    assert "量能倍數" in r
    # 分點不可用（測試環境無 Sponsor）→ 實際賣壓/今主力淨額留白，不炸
    assert "實際賣壓%" in r and "今主力淨額" in r


def test_snipe_ohlc_carries_blacklists_from_pick_day():
    """昨天列出的兩份黑名單要跟著 pick 存下、追蹤時原樣顯示（不事後重算，快取會被 prune）。"""
    from src import db, picks_tracker as pt
    import pandas as pd
    price = [{"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94,
              "close": 100, "volume": 100},
             {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101,
              "close": 102, "volume": 500},
             {"date": "D1", "stock_id": "8888", "open": 48, "high": 50, "low": 47,
              "close": 50, "volume": 100},
             {"date": "D2", "stock_id": "8888", "open": 49, "high": 55, "low": 48,
              "close": 54, "volume": 300}]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame([
        {"stock_id": "9999", "name": "妖股", "今主力淨額": 1234,
         "全市場黑名單": "凱基台北72%、美林68% +3", "本檔黑名單": "元大、群益 +1"},
        {"stock_id": "8888", "name": "強股", "今主力淨額": 500},   # 無黑名單→留白
    ])}, n=15)

    r = {x["stock_id"]: x for x in pt.snipe_ohlc("D3")["rows"]}
    assert r["9999"]["全市場黑名單"] == "凱基台北72%、美林68% +3"
    assert r["9999"]["本檔黑名單"] == "元大、群益 +1"
    assert r["8888"]["全市場黑名單"] == "" and r["8888"]["本檔黑名單"] == ""


def test_snipe_ohlc_html_shows_blacklist_columns():
    from src import report_html as rh
    so = {"date": "D1", "trade_date": "D2", "rows": [
        {"rank": 1, "stock_id": "9999", "name": "妖股", "鎖碼淨額": 1234,
         "全市場黑名單": "凱基台北72%", "本檔黑名單": "元大", "預估賣壓%": 6.5,
         "實際賣壓%": 8.1, "今主力淨額": -300, "昨收": 100.0, "今開": 108.0,
         "今高": 110.0, "今低": 101.0, "今收": 102.0, "漲跌%": 2.0, "跳空%": 8.0,
         "盤中%": -5.56, "高檔回落%": -7.27, "振幅%": 9.0, "量能倍數": 5.0, "當沖比%": 50.0}]}
    h = rh._snipe_ohlc_html(so, None)
    assert "全市場黑名單" in h and "本檔黑名單" in h
    assert "凱基台北72%" in h and "元大" in h


def test_snipe_ohlc_html_survives_rows_without_blacklists():
    """舊 picks.csv 產生的 rows 沒有黑名單鍵 → 該欄自動略過，不炸。"""
    from src import report_html as rh
    so = {"date": "D1", "trade_date": "D2", "rows": [
        {"rank": 1, "stock_id": "9999", "name": "妖股", "鎖碼淨額": 1234,
         "昨收": 100.0, "今開": 108.0, "今高": 110.0, "今低": 101.0, "今收": 102.0,
         "漲跌%": 2.0, "跳空%": 8.0, "盤中%": -5.56}]}
    h = rh._snipe_ohlc_html(so, None)
    assert "妖股" in h


def test_parse_bl():
    """『分點+張數』字串 → dict；壞格式/空值不炸。"""
    from src import picks_tracker as pt
    assert pt._parse_bl("凱基台北+1234、美林+890") == {"凱基台北": 1234, "美林": 890}
    assert pt._parse_bl("元大+-50") == {"元大": -50}
    assert pt._parse_bl("") == {} and pt._parse_bl(None) == {}
    assert pt._parse_bl(pd.NA) == {}
    assert pt._parse_bl("沒有張數的名字") == {}


def test_snipe_ohlc_blacklist_dump(monkeypatch):
    """昨天黑名單各買幾張 → 今天各賣幾張（倒貨%＋逐點對照）。"""
    from src import db, picks_tracker as pt, broker_client as bc, broker_signal as bs
    import pandas as pd
    price = [{"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94,
              "close": 100, "volume": 500},
             {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101,
              "close": 102, "volume": 500}]
    with db.connect() as conn:
        db.upsert(conn, "price", price)
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame([
        {"stock_id": "9999", "name": "妖股", "今主力淨額": 2000,
         "黑名單明細": "凱基台北+1200、美林+800、元大+400"}])}, n=15)

    # 今日：凱基台北全倒、美林倒一半、元大反手續買
    monkeypatch.setattr(bc, "available", lambda: True)
    monkeypatch.setattr(bs, "_branch_net",
                        lambda sid, d: {"凱基台北": -1200, "美林": -400, "元大": 300}
                        if d == "D2" else {})

    r = pt.snipe_ohlc("D3")["rows"][0]
    assert r["黑名單買張"] == 2400
    assert r["黑名單賣張"] == 1600            # 1200 + 400（元大續買不算賣）
    assert r["倒貨%"] == 66.7
    assert "凱基台北 買1200→賣1200(100%)" in r["黑名單逐點"]
    assert "美林 買800→賣400(50%)" in r["黑名單逐點"]
    assert "元大 買400→今再買+300" in r["黑名單逐點"]


def test_snipe_ohlc_blacklist_blank_without_broker(monkeypatch):
    """無分點(無 Sponsor)→ 黑名單買賣欄留白，不謊報「沒賣」。"""
    from src import db, picks_tracker as pt, broker_client as bc
    import pandas as pd
    with db.connect() as conn:
        db.upsert(conn, "price", [
            {"date": "D1", "stock_id": "9999", "open": 95, "high": 100, "low": 94,
             "close": 100, "volume": 500},
            {"date": "D2", "stock_id": "9999", "open": 108, "high": 110, "low": 101,
             "close": 102, "volume": 500}])
    pt.save("D1", {"隔日沖鎖碼": pd.DataFrame([
        {"stock_id": "9999", "name": "妖股", "黑名單明細": "凱基台北+1200"}])}, n=15)
    monkeypatch.setattr(bc, "available", lambda: False)
    r = pt.snipe_ohlc("D3")["rows"][0]
    assert pd.isna(r["黑名單買張"]) and pd.isna(r["黑名單賣張"]) and r["黑名單逐點"] == ""


def test_picks_csv_backward_compatible(tmp_path, monkeypatch):
    """舊 picks.csv 沒有『預估賣壓%』『黑名單』欄也要能讀（向後相容）。"""
    from src import picks_tracker as pt
    import pandas as pd
    old = tmp_path / "old_picks.csv"
    pd.DataFrame([{"date": "D1", "track": "隔日沖鎖碼", "rank": 1,
                   "stock_id": "9999", "name": "妖股"}]).to_csv(old, index=False)
    monkeypatch.setattr(pt, "PICKS_CSV", old)
    df = pt._load()
    assert "預估賣壓%" in df.columns and pd.isna(df.iloc[0]["預估賣壓%"])
    for c in ("全市場黑名單", "本檔黑名單", "黑名單明細"):
        assert c in df.columns and pd.isna(df.iloc[0][c])
