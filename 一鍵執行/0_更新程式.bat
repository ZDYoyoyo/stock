@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   先把本機累積資料存上 GitHub (dump+commit+push)
echo ============================================
python -m scripts.commit_data
echo ============================================
echo   再從 GitHub 更新程式 + 相依套件
echo ============================================
python -m scripts.pull_latest
pip install -r requirements.txt
echo.
echo 更新完成
pause
