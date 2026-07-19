@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   更新千張大戶籌碼 (每週跑一次即可)
echo ============================================
python -m scripts.update_holders
pause
