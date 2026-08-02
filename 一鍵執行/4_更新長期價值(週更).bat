@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   更新長期價值軌 (每週跑一次即可)
echo   (逐檔深掘 FinMind：配息/EPS/營收，較慢、較耗額度)
echo   (長期價值慢變，且回測此期間無 edge，不必每天跑)
echo ============================================
powershell -NoProfile -Command "$s=Get-Date; Write-Host ('開始時間：'+$s.ToString('yyyy-MM-dd HH:mm:ss')); python -m scripts.run_longterm; $e=Get-Date; $d=$e-$s; if($d.Hours){$el='{0}時{1}分{2}秒' -f $d.Hours,$d.Minutes,$d.Seconds}elseif($d.Minutes){$el='{0}分{1}秒' -f $d.Minutes,$d.Seconds}else{$el='{0}秒' -f $d.Seconds}; Write-Host ('結束時間：'+$e.ToString('HH:mm:ss')+'   經過時間：'+$el)"
echo.
echo 完成。長期價值報告在 reports\ (用瀏覽器開 .html)
pause
