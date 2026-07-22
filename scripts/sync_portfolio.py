"""持股雲端同步（Git）— 跨設備共用同一份 data/portfolio.csv。

data/portfolio.csv 已納入 git 追蹤（私有 repo 僅你可見）。不同設備靠 git 同步持股：
  換設備 / 開盤中守衛前 → 先 pull 拉最新
  在某台改了持股後       → sync 推回雲端

用法（專案根目錄）：
    python -m scripts.sync_portfolio          # 一鍵：提交本機持股改動 → 拉遠端 → 推送
    python -m scripts.sync_portfolio pull      # 只拉遠端最新持股（開盤中守衛前先跑）
⚠️ 只動 data/portfolio.csv，不碰你其他未提交的改動。
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PF = "data/portfolio.csv"


def _git(*args, check=False):
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0 and r.stderr.strip():
        print(r.stderr.strip())
    return r


def _branch() -> str:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                       cwd=str(ROOT), capture_output=True, text=True)
    return r.stdout.strip() or "HEAD"


def pull():
    print("⬇️ 拉取遠端最新持股…")
    _git("pull", "--rebase", "--autostash", "origin", _branch())
    print("✅ 已更新到最新持股")


def sync():
    br = _branch()
    if not (ROOT / PF).exists():
        print("尚無持股檔（data/portfolio.csv）。先用「記錄持股」新增再同步。")
        print("仍為你拉取遠端最新…")
        pull()
        return
    # 1) 只提交 portfolio.csv 的改動
    _git("add", PF)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", PF],
                            cwd=str(ROOT)).returncode
    if staged != 0:  # 有改動
        _git("commit", "-m", f"更新持股 {date.today().isoformat()}")
        print("✅ 已提交本機持股改動")
    else:
        print("（本機持股無改動）")
    # 2) 拉遠端（避免與他台/雲端衝突）
    print("⬇️ 同步遠端…")
    _git("pull", "--rebase", "--autostash", "origin", br)
    # 3) 推送
    print("⬆️ 推送…")
    r = _git("push", "origin", br)
    print("☁️ 持股同步完成" if r.returncode == 0 else "⚠️ 推送失敗，請看上方訊息")


if __name__ == "__main__":
    (pull if len(sys.argv) > 1 and sys.argv[1] == "pull" else sync)()
