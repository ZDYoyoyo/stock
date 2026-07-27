"""把本機累積的盤後資料 commit + push 到 GitHub，讓歷史一天天累積、跨裝置可見。

背景：資料實體累積在本機 data/stock.db（被 gitignore），要進 git 得先 dump 成
data/history/*.csv 再 commit。這支腳本就是「一鍵把本機資料存上 GitHub」：

  1) sync_data dump      DB 各表 → data/history/*.csv（去重、可選只留近 N 日）
  2) git add             data/history + data/portfolio.csv（持股）
  3) 無變動 → 直接結束（不產生空 commit）
  4) git commit          訊息含日期
  5) git pull            先併遠端，避免 push 被拒（網路失敗指數退避重試）
  6) git push            （網路失敗指數退避重試）

用法（專案根目錄）：
    python -m scripts.commit_data
    python -m scripts.commit_data --keep-days 900   # 只保留近 N 交易日，控制檔案大小
    python -m scripts.commit_data -m "自訂訊息"

⚠️ 只碰 data/history 與 data/portfolio.csv，不會動到程式碼或其他檔案。
"""
import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.pull_latest import _git, _branch  # 重用 git helper 與分支偵測
from src import datastore

DATA_PATHS = ["data/history", "data/portfolio.csv"]
_NET = ("could not resolve", "timed out", "connection", "unable to access", "failed to connect")


def _net_error(stderr: str) -> bool:
    s = stderr.lower()
    return any(k in s for k in _NET)


def _retry_git(*args) -> bool:
    """對會走網路的 git 指令做指數退避重試（2/4/8/16s）。成功回 True。"""
    for wait in (2, 4, 8, 16, 0):
        r = _git(*args)
        if r.returncode == 0:
            return True
        if wait and _net_error(r.stderr):
            print(f"   網路問題，{wait}s 後重試 …")
            time.sleep(wait)
            continue
        break
    return False


def sync(keep_days=None, message=None) -> int:
    """dump → add → commit（若有變動）→ pull → push。回傳 exit code。"""
    br = _branch()

    # 1) DB → CSV
    res = datastore.dump(keep_days=keep_days)
    print("📤 DB 寫回 CSV：" + "，".join(f"{k} {v}" for k, v in res.items()))

    # 2) 加入資料檔（只加資料，不碰程式碼）
    _git("add", "--", *DATA_PATHS, quiet=True)

    # 3) 沒有實際變動就不 commit（避免空 commit）
    staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *DATA_PATHS],
                            cwd=str(ROOT))
    if staged.returncode == 0:
        print("✅ 資料無變動，無需 commit。")
        return 0

    # 4) commit
    msg = message or f"chore(data): 累積盤後資料 {date.today().isoformat()}"
    if _git("commit", "-m", msg).returncode != 0:
        print("⚠️ commit 失敗，請看上方訊息。")
        return 1
    print(f"📝 已 commit：{msg}")

    # 5) 先併遠端（避免非快轉 push 被拒）
    if not _retry_git("pull", "origin", br):
        print("⚠️ pull 未完成（可能有衝突或網路問題），暫不 push；請看上方訊息。")
        return 1

    # 6) push
    if _retry_git("push", "-u", "origin", br):
        print("🚀 資料已上傳到 GitHub。")
        return 0
    print("⚠️ push 未完成，資料已在本機 commit，下次會再嘗試上傳。")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="把本機累積的盤後資料 commit+push 到 GitHub")
    ap.add_argument("--keep-days", type=int, default=None,
                    help="dump 時只保留近 N 個交易日（控制 CSV 大小；預設全留）")
    ap.add_argument("-m", "--message", default=None, help="自訂 commit 訊息")
    args = ap.parse_args()
    return sync(keep_days=args.keep_days, message=args.message)


if __name__ == "__main__":
    sys.exit(main())
