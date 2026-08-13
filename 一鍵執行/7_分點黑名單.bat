@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   分點行為檔案（隔日沖黑名單）
echo   哪些券商分點是隔日沖慣犯、哪些是偏長線買盤
echo   ※ 樣本靠每日盤後選股累積，跑越多天越準
echo ============================================
powershell -NoProfile -Command "$s=Get-Date; Write-Host ('開始時間：'+$s.ToString('yyyy-MM-dd HH:mm:ss')); python -m scripts.run_broker_profile; $e=Get-Date; $d=$e-$s; if($d.Minutes){$el='{0}分{1}秒' -f $d.Minutes,$d.Seconds}else{$el='{0}秒' -f $d.Seconds}; Write-Host ('結束時間：'+$e.ToString('HH:mm:ss')+'   經過時間：'+$el)"
echo.
echo 完成。報告在 reports\broker_profile.html (用瀏覽器開)
pause
