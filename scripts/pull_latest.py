"""一鍵更新程式碼 — 取代手動 `git pull`，永不因本地重生資料檔而中止。

問題：本地跑盤後（run_all / sync_data）會重寫 data/history/ 下的 CSV
（picks.csv、各資料表鏡像）。這些是「DB 的鏡像、可自動重生」的檔案，
一旦有未提交改動，`git pull` 會為了保護它們而整個中止（Aborting），
連程式碼都更新不到。

解法：pull 前若 data/history/ 有未提交改動，先呼叫 commit_data 幫你
dump+commit+push 上去（**不再丟棄**，因為現在要一天天累積歷史），
工作區乾淨後 pull 一定不會因它中止。commit_data 也會保護 data/portfolio.csv。
若 portfolio.csv 擋住，請先 `python -m scripts.sync_portfolio` 再來。

用法（專案根目錄）：
    python -m scripts.pull_latest        # = 安全版 git pull（更新程式碼）
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGEN_DIR = "data/history"          # 資料鏡像；pull 前若有未提交改動先 commit+push 上去
PROTECT = "data/portfolio.csv"      # 你的持股，永不丟


def _git(*args, quiet=False):
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    if not quiet:
        if r.stdout.strip():
            print(r.stdout.strip())
        if r.returncode != 0 and r.stderr.strip():
            print(r.stderr.strip())
    return r


def _branch() -> str:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                       cwd=str(ROOT), capture_output=True, text=True)
    return r.stdout.strip() or "HEAD"


def _portfolio_dirty() -> bool:
    r = subprocess.run(["git", "status", "--porcelain", "--", PROTECT],
                       cwd=str(ROOT), capture_output=True, text=True)
    return bool(r.stdout.strip())


def main() -> int:
    br = _branch()

    # 1) 先把 data/history/ 的本地累積資料 commit 起來（不再丟棄！）。
    #    早期版本是「丟棄採遠端版」，但現在要一天天累積歷史，丟棄會刪掉還沒上傳的資料。
    #    改為：有未提交的資料改動 → 呼叫 commit_data 幫你 dump+commit+push，
    #    工作區乾淨後 pull 一定不會因它中止。（commit_data 也會保護 portfolio.csv）
    dirty = subprocess.run(["git", "status", "--porcelain", "--", REGEN_DIR],
                           cwd=str(ROOT), capture_output=True, text=True)
    if dirty.stdout.strip():
        print("📦 偵測到本機累積資料未上傳，先幫你 commit+push …")
        from scripts import commit_data
        commit_data.sync()

    # 2) 若持股檔有未提交改動，先提醒（避免 pull 因它中止或誤蓋）
    if _portfolio_dirty():
        print("⚠️ data/portfolio.csv 有未提交改動 → 為保護持股先不丟。")
        print("   請先跑 `python -m scripts.sync_portfolio` 同步持股，再回來 pull。")
        return 2

    # 3) pull（網路失敗指數退避重試）
    print(f"⬇️ 拉取遠端最新程式碼（{br}）…")
    for attempt, wait in enumerate([2, 4, 8, 16, 0], 1):
        r = _git("pull", "origin", br)
        if r.returncode == 0:
            print("✅ 程式碼已更新到最新")
            return 0
        if wait and ("could not resolve" in r.stderr.lower()
                     or "timed out" in r.stderr.lower()
                     or "connection" in r.stderr.lower()):
            print(f"   網路問題，{wait}s 後重試（第 {attempt} 次）…")
            time.sleep(wait)
            continue
        break
    print("⚠️ pull 未完成，請看上方訊息")
    return 1


if __name__ == "__main__":
    sys.exit(main())
