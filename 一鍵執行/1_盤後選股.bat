@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ============================================
echo   盤後選股 run_all --skip-longterm --notify
echo   (波段+成長+當沖+環境+持股+推播；略長期軌省 FinMind 額度)
echo ============================================
echo   (長期價值軌改『每週』跑一次 → 4_更新長期價值.bat)
echo   (跑完會自動把今日累積資料上傳 GitHub)
powershell -NoProfile -Command "$s=Get-Date; Write-Host ('開始時間：'+$s.ToString('yyyy-MM-dd HH:mm:ss')); python -m scripts.run_all --skip-longterm --notify; $e=Get-Date; $d=$e-$s; if($d.Hours){$el='{0}時{1}分{2}秒' -f $d.Hours,$d.Minutes,$d.Seconds}elseif($d.Minutes){$el='{0}分{1}秒' -f $d.Minutes,$d.Seconds}else{$el='{0}秒' -f $d.Seconds}; Write-Host ('結束時間：'+$e.ToString('HH:mm:ss')+'   經過時間：'+$el)"
echo.
echo 完成。報告在 reports\screener\ (用瀏覽器開 .html)
pause
