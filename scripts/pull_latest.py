"""一鍵更新程式碼 — 取代手動 `git pull`，永不因本地重生資料檔而中止。

問題：本地跑盤後（run_all / sync_data）會重寫 data/history/ 下的 CSV
（picks.csv、各資料表鏡像）。這些是「DB 的鏡像、可自動重生」的檔案，
一旦有未提交改動，`git pull` 會為了保護它們而整個中止（Aborting），
連程式碼都更新不到。

解法：pull 前先把 data/history/ 的本地改動丟棄（採遠端版；下次跑盤後
會由本地 DB 重新 dump 回來），工作區乾淨 → pull 一定過。
⚠️ 只丟 data/history/（可重生鏡像），**絕不動 data/portfolio.csv**（你的持股）。
若 portfolio.csv 擋住，請先 `python -m scripts.sync_portfolio` 再來。

用法（專案根目錄）：
    python -m scripts.pull_latest        # = 安全版 git pull（更新程式碼）
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGEN_DIR = "data/history"          # 可自動重生的資料鏡像；pull 前丟棄本地改動
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

    # 1) 丟棄 data/history/ 的本地改動（可重生鏡像，採遠端版；不碰 portfolio.csv）
    if (ROOT / REGEN_DIR).exists():
        _git("checkout", "--", REGEN_DIR, quiet=True)
        print(f"🧹 已還原 {REGEN_DIR}/ 的本地改動（可重生鏡像，採遠端版）")

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
