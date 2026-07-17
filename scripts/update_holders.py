"""更新千張大戶籌碼（TDCC 集保股權分散，每週更新一次即可）。

TDCC 只給最新一週，本腳本把每週快照存進 DB，累積出趨勢。
建議每週六或日跑一次（集保週五結算、週末公布）。

用法：
    python -m scripts.update_holders
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, tdcc_client as tdcc


def main():
    print("抓 TDCC 集保股權分散（千張大戶）…")
    rows = tdcc.fetch()
    if not rows:
        print("抓不到資料")
        return
    db.init_db()
    with db.connect() as conn:
        n = db.upsert(conn, "big_holders", rows)
    print(f"✅ 更新 {n} 檔，資料週={rows[0]['date']} → data/stock.db")


if __name__ == "__main__":
    main()
