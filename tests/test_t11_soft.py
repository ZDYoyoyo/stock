"""T11 放寬版（連買天數 4→3）— 確認『同一套邏輯、只換一個門檻』真的成立。

run_all 是跑一次 3 天版（＝4 天版的超集），再依 consec_buy_days 切成兩軌顯示；
所以這裡盯兩件事：(1) 3 天版包含 4 天版的全部，(2) 多出來的都是剛好連買 3 天的。
若哪天有人在 run() 裡又加了跟門檻連動的條件，這個測試就會紅。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import T11
from src.screeners import institutional_accumulation as t11

_DAYS = [f"2026-10-{d:02d}" for d in (1, 2, 3, 6, 7, 8, 9, 10, 13, 14)]


def _seed(monkeypatch, tmp_path):
    """兩檔都是『橫盤+法人買+籌碼乾淨』，差別只在連買天數：A 連4天、B 連3天。"""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.clear_cache()
    nets = {"AAAA": [0] * 6 + [200, 200, 200, 200],      # 連買 4 天
            "BBBB": [0] * 6 + [-50, 200, 200, 200]}      # 連買 3 天（前一天賣超打斷）
    with db.connect() as conn:
        conn.executescript(db._SCHEMA)
        for sid, ns in nets.items():
            db.upsert(conn, "price", [{"date": d, "stock_id": sid, "open": 100.0, "high": 101.0,
                                       "low": 99.0, "close": 100.0, "volume": 1000}
                                      for d in _DAYS])
            db.upsert(conn, "institutional", [{"date": d, "stock_id": sid, "foreign_net": 0,
                                               "trust_net": n, "dealer_net": 0, "total_net": n}
                                              for d, n in zip(_DAYS, ns)])
            db.upsert(conn, "margin", [{"date": d, "stock_id": sid, "margin_balance": 1000,
                                        "short_balance": 10} for d in _DAYS])
            db.upsert(conn, "stock_info", [{"stock_id": sid, "stock_name": sid,
                                            "industry": "", "type": "twse"}])


def test_default_keeps_four_day_threshold(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    out = t11.run()                                   # 不傳參數＝維持原本的 4 天
    assert list(out["stock_id"]) == ["AAAA"]
    assert out.loc[0, "consec_buy_days"] == 4


def test_soft_threshold_is_a_superset(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    strict = set(t11.run()["stock_id"])
    soft = t11.run(T11.SOFT_CONSECUTIVE_BUY)
    assert strict <= set(soft["stock_id"])            # 超集：原本選得到的一檔都不能掉
    extra = soft[soft["consec_buy_days"] < T11.MIN_CONSECUTIVE_BUY]
    assert list(extra["stock_id"]) == ["BBBB"]        # 多出來的＝剛好連買 3 天
    assert (extra["consec_buy_days"] == 3).all()


def test_soft_rows_carry_same_columns(monkeypatch, tmp_path):
    """兩軌共用同一份欄位（報告直接套同一組 cols）→ 欄位必須一致。"""
    _seed(monkeypatch, tmp_path)
    assert list(t11.run().columns) == list(t11.run(3).columns)
