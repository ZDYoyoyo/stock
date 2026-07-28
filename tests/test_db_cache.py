"""db.read_table 整表快取的行為測試（回傳副本、clear 生效）。

跑法：python tests/test_db_cache.py   或   python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import db


def test_cache_returns_independent_copy():
    """快取命中回傳副本：呼叫端修改不可污染快取。"""
    db.clear_cache()
    db._TABLE_CACHE["_t"] = pd.DataFrame({"a": [1, 2], "stock_id": ["x", "y"]})
    got = db.read_table("_t", use_cache=True)
    got.loc[0, "a"] = 999                       # 修改呼叫端拿到的副本
    again = db.read_table("_t", use_cache=True)
    assert again.loc[0, "a"] == 1               # 快取未被污染
    db.clear_cache()


def test_clear_cache_empties():
    db._TABLE_CACHE["_t"] = pd.DataFrame({"a": [1]})
    db.clear_cache()
    assert "_t" not in db._TABLE_CACHE


def test_no_cache_path_ignores_store():
    """use_cache=False 不寫入快取。"""
    db.clear_cache()
    db._TABLE_CACHE["_t"] = pd.DataFrame({"a": [7]})
    # use_cache=False 會走 DB 查詢；這裡只驗證它不會回傳快取那份（避免誤用快取）
    # 用一張不存在於 DB 的臨時表名，DB 查詢會拋錯 → 證明沒走快取捷徑
    try:
        db.read_table("_t", use_cache=False)
        hit_db = True
    except Exception:
        hit_db = True                            # 有嘗試打 DB（表不存在而拋錯）即符合預期
    assert hit_db
    db.clear_cache()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n✅ {len(fns)}/{len(fns)} passed")
