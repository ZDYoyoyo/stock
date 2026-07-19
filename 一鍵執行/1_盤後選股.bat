@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   盤後選股 run_all --notify
echo   (含三軌+環境+持股，會抓資料，約需幾分鐘)
echo ============================================
python -m scripts.run_all --notify
echo.
echo 完成。報告在 reports\screener\ (用瀏覽器開 .html)
pause
