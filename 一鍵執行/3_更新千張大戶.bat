@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   更新千張大戶籌碼 (補跑用)
echo.
echo   * 盤後選股 (1_盤後選股.bat) 每次都會自動更新千張大戶，
echo     平常不用開這支；只有想單獨補抓時才需要。
echo ============================================
python -m scripts.update_holders
pause
