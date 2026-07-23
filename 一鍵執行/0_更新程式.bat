@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   從 GitHub 更新程式 + 相依套件
echo ============================================
python -m scripts.pull_latest
pip install -r requirements.txt
echo.
echo 更新完成
pause
