# CLAUDE.md — 專案脈絡與接手指南

> 這份檔案每個新 session 一開始都會自動讀到。目的：讓 Claude 不用使用者重述，
> 就能自己搞清楚「這是什麼專案、進度到哪、動工前要注意什麼」。

## ⭐ 最高優先：工作原則（每個任務都適用，除非明確覆蓋）

偏好謹慎勝過速度（非瑣碎工作）；瑣碎任務用判斷力別過度。

1. **想清楚再寫**：明確講出假設；不確定就問，別猜。有歧義先列出多種解讀。有更簡單做法就 push back。搞混就停下、講明哪裡不清楚。
2. **先求簡單**：解決問題的最小程式；不做沒被要求的功能、不為單次使用建抽象。「資深工程師會不會嫌太複雜？」會的話就簡化。
3. **外科手術式修改**：只動非動不可的；只清自己的爛攤。不順手「改善」旁邊的程式/註解/格式，不重構沒壞的東西，配合既有風格。（此專案容器會重置，改動要能精準重貼，這條特別重要。）
4. **目標驅動**：先定義「成功長怎樣」，然後迭代到驗證通過，而不是照步驟走。
5. **衝突要攤開，不要折衷**：兩種模式互相矛盾時，選一個（較新/較多驗證），說明為何，另一個標記待清理。不混用。
6. **先讀再寫**：加程式前先讀 exports、直接呼叫者、共用工具。看不懂既有結構為何這樣就問。「看起來跟我無關」是最危險的一句話。
7. **每個重要步驟後設 checkpoint**：完成一步就總結「做了什麼／已驗證什麼／還剩什麼」。無法向使用者複述的狀態就不要繼續；跟丟了就停下重述。
8. **配合既有慣例，即使你不認同**：codebase 用什麼命名/風格就跟。覺得某慣例有害就攤開討論，別默默另立一套。
9. **大聲報錯**：無法確定成功就明講。「跑完了」若有跳過的紀錄就是錯的、「測試通過」若有略過就是錯的。預設暴露不確定，而非藏起來。

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

## 回測框架（P1–P6，2026-07 量化顧問專案已建）

把「每日選股系統」補上**可驗證的回測**。核心：`src/signals.py`（回測與實際**共用**的訊號原語）
＋`src/portfolio_backtest.py`（組合層級回測，產真實 CAGR／最大回撤／夏普）。

**關鍵檔**：
- `src/signals.py` — 交易成本、法人連買天數、T11 point-in-time 判斷 `t11_pass`（回測＝實際同一份）。
- `src/portfolio_backtest.py` — 引擎：`compute_t11_entries`、`run_portfolio`（含停損/regime）、
  `compute_regime_ok`、`slice_panel`、`load_panel_csv`（離線面板）。
- `scripts/run_portfolio_backtest.py` — 執行；`--compare` 四組風控對照；`--offline` 用離線面板秒跑。
- `scripts/run_oos_validation.py` — 樣本外分期＋參數敏感度。
- `scripts/backfill_history.py` — 回補長歷史→`data/history/backtest_panel.csv.gz`（gzip、寫一次不動，不拖累日常 commit）。
- `tests/` — 34 個單元測試（signals／portfolio／db 快取）；`python -m pytest tests/ -q`。
- 報告：`reports/portfolio_backtest.md`、`reports/oos_validation.md`、`reports/backtest_validation.md`。

**指令**：
```bash
python -m scripts.backfill_history --universe 150 --start 2022-01-01   # 回補離線面板（一次性）
python -m scripts.run_portfolio_backtest --offline --compare           # 離線秒跑＋四組風控對照
python -m scripts.run_oos_validation --universe 15 --start 2022-01-01  # 樣本外＋敏感度
```

**關鍵結論（事件研究／組合回測／風控對照／樣本外 四法交叉驗證）**：
T11 當**機械化進出場策略沒有 edge**——大 universe 下勉強微正（CAGR ~+3%），但遠遜於買入持有
（同期 +105%）、背 ~−37% 回撤，停損／ATR／regime／任何參數都救不了。
→ **T11 適合當選股情報，不是被驗證過的交易策略。** 未來要找「真能贏大盤的訊號」就用這套框架量化比。

**已知限制／可延伸**：
- 離線面板目前 60 檔、仍有倖存者偏誤（FinMind 免費層拿不到已下市股）。要更大 universe：
  重跑 `backfill_history --universe N`；要完全消除倖存者偏誤需 TWSE 舊版逐日端點（未做）。
- P4 的「明顯負」是小樣本（12–15 檔）高估；以 P5 大樣本「微正但遠遜大盤」為準。

## 之後要改回測這塊，怎麼跟我說（給使用者）

- **直接點名功能**：「回測停損改 −10%」「universe 加到 200 重跑」「用這框架比 T16 vs 買入持有」。
- **或指報告**：「portfolio_backtest.md 的 ATR 那組再試 3×」。
- 一律**先同步再動工**；我照 git log 認進度（commit 訊息很詳細）。

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
