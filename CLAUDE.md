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
- 每日DB歷史採**滾動窗**：`run_all --keep-days 260`（預設）只留近260交易日的CSV，
  檔案大小封頂、不壓縮(git delta友善)；260≥年線240，夠算季線/年線/52週位置%。
  一次性回補更多歷史：`python -m scripts.update_data --days 400`（免費端點、不燒token）。

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
- `src/chip_signal.py` — 籌碼訊號：連買賣天數、法人主導度%、訊號標籤、量能倍數、券資比%、
  資券佔量%（把資券絕對張數相對化，避免「融券−343」被誤判大小）。
- `src/flows.py` — 法人近10日流向、資券增減併欄。
- `src/tech_signal.py` — 技術面併欄：均線排列(5/10/20多空)、季線年線(60/240MA中長多空)、
  20MA乖離%、52週位置%(近1年區間位置)、成交額億(資金權重)。需長歷史→靠滾動窗每日DB。
- `src/screeners/` — 各軌篩選器 + `landmine`(地雷偵測)。
- `src/twse_client.py` / `src/tpex_client.py` — 官方資料 client（上市／上櫃）；含 `day_trade` 當沖統計。
- `src/day_trade_signal.py` — 當沖比率% 併欄（妖股對殺偵測，TWSE+TPEX 官方當沖÷總量，免費）。

## 回測框架（P1–P6，2026-07 量化顧問專案已建）

把「每日選股系統」補上**可驗證的回測**。核心：`src/signals.py`（回測與實際**共用**的訊號原語）
＋`src/portfolio_backtest.py`（組合層級回測，產真實 CAGR／最大回撤／夏普）。

**關鍵檔**：
- `src/signals.py` — 交易成本、法人連買天數、T11 point-in-time 判斷 `t11_pass`（回測＝實際同一份）。
- `src/portfolio_backtest.py` — 引擎：`compute_t11_entries`、`compute_t16_entries`、
  `compute_t12_entries`（月營收 point-in-time）、`run_portfolio`（含停損/regime）、
  `compute_regime_ok`、`slice_panel`、`load_panel_csv`、`load_revenue_panel`（離線面板）。
- `scripts/run_portfolio_backtest.py` — 執行；`--compare` 四組風控對照；`--offline` 用離線面板秒跑。
- `scripts/run_oos_validation.py` — 樣本外分期＋參數敏感度。
- `scripts/run_signal_compare.py` — 離線比 T11／T16／買入持有／各+regime（`compute_t16_entries` 抗跌強勢）。
- `scripts/run_t16_tune.py` — T16 調校：持股數×停損×regime 降回撤。
- `scripts/backfill_history.py` — 回補離線面板→`data/history/backtest_panel.csv.gz`。
  `--update`(補新日子)、`--only-new`(只加新股、舊股零API)、`--max-new`(額度保護，600/時→190/次)。
- `scripts/backfill_revenue.py` — 回補月營收面板→`data/history/revenue_panel.csv.gz`（供 T12）。
  每檔1次 FinMind call，對齊股價面板；YoY/累計YoY 自算，公布日用 create_time(或次月10日 fallback)。
- `scripts/backfill_valuation.py` — 回補估值面板→`data/history/valuation_panel.csv.gz`（供長期價值軌）。
  月頻(每月首交易日)抓 TWSE BWIBBU_d 殖利率/PER/PBR，僅上市；`compute_longterm_entries` 用之。
- `scripts/run_longterm_backtest.py` — 長期價值軌回測（月頻價值篩選 vs 買入持有/+regime）。
- `tests/` — 47 個單元測試（signals／portfolio／T12前視／長期價值篩選／db 快取）；`python -m pytest tests/ -q`。
- 報告：`reports/signal_compare.md`、`reports/t16_tune.md`、`reports/portfolio_backtest.md`、`reports/oos_validation.md`、`reports/longterm_backtest.md`。

**指令**：
```bash
python -m scripts.run_signal_compare      # 離線比訊號（不碰額度、秒出）
python -m scripts.run_t16_tune            # T16 降回撤調校
python -m scripts.backfill_history --update           # 補回測面板新日子
python -m scripts.backfill_history --universe 300 --only-new   # 加檔數（只抓新股）
python -m scripts.backfill_revenue                    # 回補月營收面板（對齊股價面板，供 T12）
python -m scripts.backfill_revenue --update           # 增量：只補面板新股的月營收
python -m scripts.backfill_valuation                  # 回補月頻估值面板（殖利率/PER/PBR，供長期價值軌）
python -m scripts.run_longterm_backtest               # 長期價值軌回測（vs 買入持有/+regime）
```

**關鍵結論（離線 200 檔面板實測，2026-07）**：
- **T11 法人吸貨＝沒 edge**（CAGR −16%、回撤 −63%）→ 只當選股情報，別當進出場策略。
- **T16 抗跌強勢＝真有 edge**（動能）：10 檔無停損 CAGR **+32%**、夏普 **0.99**、回撤 −37%。
  ⚠️ **停損反而傷 T16**（把動能股在低點洗出場）；降回撤靠「多持到 ~10 檔分散」，不是停損。
- **regime（市場廣度紅綠燈）普遍有用**：買入持有+regime 回撤 −33%→−18.6%、夏普升。
- **T12 月營收動能＝有 edge 但不如 T16**（199 檔月營收面板實測）：持5檔/20日 CAGR **+21.5%**、
  夏普 **0.82**、回撤 −42.7%。贏買入持有(+14.9%/0.71)→ 營收成長是有效因子；但輸 T16(+31.4%/0.90)
  → 價的動能比基本面反應更快。用公布日(pub_date≤交易日)對齊 → 無前視偏誤。
  ✅ **T12 甜蜜點＝持8檔**：5→8 檔分散把回撤 **−42.7%→−38.2%**、夏普 **0.82→0.91**、CAGR 不減
  (+22.5%)；8→10 檔過度分散(CAGR 掉)。⚠️ **regime 對 T12 每組都變差**（加深回撤）→ 別加。
- **長期價值軌＝此 universe/期間無 edge**（169檔上市、月頻估值實測）：持10檔/60日 CAGR **−0.5%**、
  夏普 **0.08**，慘輸買入持有(+14.9%/0.71)；+regime 也僅救到 +2.0%。⚠️ 主因：2022–2026 是
  半導體/AI 成長股主導的市場，價值(高息低估)股大幅落後(價值陷阱)；且 200 檔面板偏大型流動股、
  對價值不利。**不代表價值投資無效**，是「此流動性面板×此成長期間」價值沒 edge。
  用核心價值因子(殖利率/PER/PBR/ROE估)；配息年數/EPS 未納入(見報告)。
- **實用結論**：波段用 **T16+分散+順 regime**（攻）；長期用 **買入持有+regime**（守，勝過價值選股）；
  T12 可當「有基本面撐腰的動能」次選（**持8檔分散**、弱於 T16、別加 regime）；T11、長期價值選股 丟。

**已知限制／可延伸**：
- 離線面板 ~200 檔、仍有倖存者偏誤（動能策略尤其樂觀，實際 edge 會小些）。
- 未計滑價；in-sample（T16 尚未做嚴格樣本外，可用 run_oos 概念延伸）。

## 之後要改回測這塊，怎麼跟我說（給使用者）

- **直接點名功能**：「T16 試 10 檔+regime」「universe 加到 300」「回補基本面測 T12」。
- **或指報告**：「t16_tune.md 再試 8 檔」。
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

### 報告增強 roadmap（2026-07 盤點，報告欄位已很完整，瓶頸在「把資料變行動」）
- **① 綜合定調**：✅已完成。波段/成長/長期軌皆有 `verdict.add_verdict`，7面向投票
  （法人主導/融資散戶/均線排列/季線年線/20MA乖離/52週位置/券資比軋空）→ 每檔一句
  「🔴偏多/⚪觀望/🟢偏空」，門檻 ±3（須≥3方向一致）。當沖軌另有 intraday 專用「多空傾向」。
- **② 資料受限籌碼欄**：✅當沖比率（妖股對殺，day_trade_signal，5328~79%驗證）；
  ✅借券賣出餘額（法人真實空單，`sbl_signal`：FinMind TaiwanDailyShortSaleBalances 的
  SBLShortSalesCurrentDayBalance，免費層逐檔查當日候選股、股÷1000→張，不落 DB）；
  待做：主力分點（要 FinMind Sponsor 付費）。
- **③ 回測驗證缺口**：✅長期價值軌已回測（無 edge，見上結論）；待做：T16 嚴格樣本外、
  價值軌納配息年數/EPS 因子、上櫃估值(BWIBBU_d 僅上市)。

## 提醒使用者的常見事項

- 最新籌碼數值與各軌完整 CSV，需**實際跑一次 `一鍵執行/1_盤後選股.bat`** 才會產生當日檔。
