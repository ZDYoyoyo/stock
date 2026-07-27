# CLAUDE.md — 專案脈絡與接手指南

> 這份檔案每個新 session 一開始都會自動讀到。目的：讓 Claude 不用使用者重述，
> 就能自己搞清楚「這是什麼專案、進度到哪、動工前要注意什麼」。

## 🔴 動工前第一件事：先同步（最重要）

**這個遠端容器每次 resume／compact 後，工作目錄常會被重置到某個舊 commit**，
本地會落後 origin。若沒先對齊就直接改，會把新程式疊在舊基底上而出錯（已發生過數次）。

所以**每個新 session 或 compact 後，動工前一定先做**：

```bash
git fetch origin claude/taiwan-stock-analysis-asx-mww0s6
git reset --hard origin/claude/taiwan-stock-analysis-asx-mww0s6
git log --oneline -10        # 看最近進度：commit 訊息寫得很詳細，一看就懂做到哪
```

**所有已完成的工作都安全在 origin**，不會因關 session／compact 消失。進度的真相在
git log，不在對話裡。使用者只要說「繼續台股專案，先同步再告訴我進度」，就照上面跑一遍再回報。

- 開發分支固定：`claude/taiwan-stock-analysis-asx-mww0s6`（**只 push 這條**）。
- 測試前若 `data/stock.db` 不見了（容器重置會清掉），先重建：
  `python -m scripts.sync_data load`（由已提交的 CSV 重建 DB）。

## 專案是什麼

台股盤後選股系統。抓免費官方資料（TWSE/TPEX/FinMind），跑多軌篩選，輸出**單一整合日報**
（`.md` + `.html` + 各軌完整 `.csv`）到 `reports/screener/`。使用者是**非技術背景**，
用 `一鍵執行/*.bat` 操作，看報告做台股波段／當沖／長期決策。

**五軌**：T11 法人吸貨、T16 抗跌強勢、T12 月營收動能、長期價值、當沖候選。

## 常用指令

```bash
python -m scripts.run_all                      # 一鍵全部（抓資料+選股+報告）
python -m scripts.run_all --no-update          # 用現有資料，不重抓（測試用）
python -m scripts.run_all --skip-longterm      # 略過較慢的長期軌
# 注意：沒有 --no-sync 這個參數
python -m scripts.sync_data load               # 由 CSV 重建 stock.db
```

## 程式地圖（關鍵檔）

- `scripts/run_all.py` — 主流程：跑五軌、併欄、寫 `.md`/`.html`/各軌 `.csv`、推播。
- `src/report_html.py` — HTML 輸出＋`COLUMN_LABELS`（英文欄名→中文顯示名）＋`rename_cn`。
- `src/chip_signal.py` — 籌碼訊號：連買賣天數、法人主導度%、訊號標籤。
- `src/flows.py` — 法人近10日流向、資券增減併欄。
- `src/screeners/` — 各軌篩選器 + `landmine`(地雷偵測)。
- `src/twse_client.py` / `src/tpex_client.py` — 官方資料 client（上市／上櫃）。

## 使用者偏好（做事時遵守）

- **回覆用繁體中文**；程式碼／指令用英文。
- **報告 `.md` 與 `.html` 內容要對齊**（欄位、區塊一致；使用者明確要求記住這點）。
- 台股慣例配色：**紅＝漲/正、綠＝跌/負**（勿用美股相反配色）。
- 欄位命名要**避免時間窗誤讀**：單日欄標「今日」、區間欄標「10日/區間」。
- 面向非技術使用者：註記要能實際操作（例如「完整清單見同資料夾 CSV，Excel 可開」，
  且該檔要真的存在，別開空頭支票）。

## 待辦／擱置中（使用者知道，尚未動工）

- 迷你趨勢圖（sparkline）、顏色深淺熱力圖 —— 使用者當時未選，擱置。
- MD/HTML **顯示列數**落差（T11/當沖：HTML 19–20 列 vs MD 15 列）；欄位一致、僅列數不同。

## 提醒使用者的常見事項

- 最新籌碼數值與各軌完整 CSV，需**實際跑一次 `一鍵執行/1_盤後選股.bat`** 才會產生當日檔。
