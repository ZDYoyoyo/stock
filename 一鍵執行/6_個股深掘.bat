@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   個股籌碼深掘（單檔病歷表）
echo   拉齊：價量/法人/資券/借券 + 分點主力/隔日沖常客 + 千張大戶
echo ============================================
set /p SID=請輸入股票代號(例 1303)後按 Enter: 
powershell -NoProfile -Command "$s=Get-Date; Write-Host ('開始時間：'+$s.ToString('yyyy-MM-dd HH:mm:ss')); python -m scripts.run_stock %SID%; $e=Get-Date; $d=$e-$s; if($d.Minutes){$el='{0}分{1}秒' -f $d.Minutes,$d.Seconds}else{$el='{0}秒' -f $d.Seconds}; Write-Host ('結束時間：'+$e.ToString('HH:mm:ss')+'   經過時間：'+$el)"
echo.
echo 完成。報告在 reports\stock\ (用瀏覽器開 .html)
pause
