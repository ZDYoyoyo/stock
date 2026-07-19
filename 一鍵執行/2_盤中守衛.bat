@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   盤中持股守衛 (觸停損自動推 Telegram)
echo   盤中掛著，關閉請按 Ctrl+C
echo ============================================
python -m scripts.monitor_portfolio
pause
