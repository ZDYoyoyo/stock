@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo 啟動控制台中... (若有錯誤訊息會顯示在這裡)
python gui.py
