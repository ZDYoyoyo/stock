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

### ⚠️ 單位鐵律：DB 裡的 volume／法人／融資融券「已是張」，別再 /1000（老毛病）
`twse_client.py` 抓進來時就已 `/1000` 換成**張**（`volume: int(_num(...)/1000)`、`foreign_net/
trust_net/dealer_net/total_net`、`margin_balance/short_balance` 同理）。所以直接讀 DB 的
`price.volume`、`institutional.*_net`、`margin.*_balance` **就是張數，report 時原樣輸出、不要再除 1000**。
（曾多次把「106,771 張」誤植成「107 張」——差 1000 倍。）
- 比率欄（主導度%、融資佔量%、券資比%、乖離% 等）分子分母同單位相消，**不受影響**；錯的只會是絕對張數。
- 若真要股數，才 `張×1000`。TDCC 千張大戶欄本來就是 %，也不換算。

## 專案是什麼

台股盤後選股系統。抓免費官方資料（TWSE/TPEX/FinMind），跑多軌篩選，輸出**單一整合日報**
（`.md` + `.html` + 各軌完整 `.csv`）到 `reports/screener/`。使用者是**非技術背景**，
用 `一鍵執行/*.bat` 操作，看報告做台股波段／當沖／長期決策。

**五軌**：T11 法人吸貨、T16 抗跌強勢、T12 月營收動能、長期價值、當沖候選。
（＋第6軌 **隔日沖鎖碼候選**：漲停/大漲 + 主力/隔日沖大戶鎖碼進場，實驗性、未回測、見 `daytrade_snipe`。）

## 常用指令

```bash
python -m scripts.run_all                      # 一鍵全部（抓資料+選股+報告）
python -m scripts.run_all --no-update          # 用現有資料，不重抓（測試用）
python -m scripts.run_all --skip-longterm      # 選用：想略過較慢的長期軌時（Sponsor 後已預設五軌日更）
python -m scripts.run_longterm                 # 長期價值軌單獨跑
python -m scripts.run_stock 1303 --days 30     # 個股籌碼深掘（單檔病歷表→reports/stock/）
# 注意：沒有 --no-sync 這個參數
python -m scripts.sync_data load               # 由 CSV 重建 stock.db
```

✅ **已辦 FinMind Sponsor（2026-08，token 在環境變數 `FINMIND_TOKEN`）**：額度 6000/時、解鎖
分點日報(`TaiwanStockTradingDailyReport`)＋千張大戶歷史(`TaiwanStockHoldingSharesPer`)＋借券已全市場落 DB。
升級規劃與實作順序見 `docs/Sponsor升級規劃.md`。⚠️ 別用多帳號多 token 繞額度（違反 ToS，帳號可能被封）。
- ✅**已回歸五軌日更**（2026-08，額度解放[2]）：盤後主按鈕＋`1_盤後選股.bat` 已移除 `--skip-longterm`，
  長期軌併回每日。若日後嫌長期軌拖慢/無 edge，加回 `--skip-longterm` 即可（`run_longterm` 仍可單獨跑）。
- （歷史備註）免費 600/時常不夠：主燒在長期軌逐檔深掘，故曾預設 `--skip-longterm`＋長期軌拆週更。

## 程式地圖（關鍵檔）

- `scripts/run_all.py` — 主流程：跑五軌、併欄、寫 `.md`/`.html`/各軌 `.csv`、推播。
- `src/report_html.py` — HTML 輸出＋`COLUMN_LABELS`（英文欄名→中文顯示名）＋`rename_cn`。
  📌**收盤價上限篩選**（`_pxctrl`/`_PX_JS`）：報告頂端一個輸入框「只看收盤價 ≤ N 元」→純前端 JS 即時跨全部軌隱藏超標列＋顯示「符合 N/M 檔」＋localStorage 記住。
  收盤價欄各軌不一(`_PRICE_COLS`=今日收盤>close>現價>今收 優先序)，`_table` 就地把該欄 `<td>` 標 `data-price` 供 JS 讀。純視圖控制(同 colctrl)、MD 無對應。
  📌**代號→K線圖外連**(`_klink`/`_ID_COLS`/`_KCHART_URL`)：報告裡股票代號做成 Goodinfo K線圖連結(`ShowK_Chart.asp?STOCK_ID=`，新分頁、免分上市櫃故每檔一種網址最穩)。`_table` 代號欄(`stock_id`/`代號`)＋追蹤區/歸因段的代號都套。純外連、MD 無對應。
- `src/chip_signal.py` — 籌碼訊號：連買賣天數、法人主導度%、訊號標籤、量能倍數、券資比%、
  資券佔量%（把資券絕對張數相對化，避免「融券−343」被誤判大小）。
- `src/flows.py` — 法人多時窗流向(近10/20日淨買賣超)、資券增減併欄；`institution_flows(days=N)`
  參數化。報告用 `report_html.MERGE_GROUPS` 把**今/昨/10/20** 同格堆疊(欄數不變、版面不爆)。
  ⚠️(2026-08 使用者調整)加「昨日」單日、拿掉「60日」季窗(太遠)。今/昨走 `run_all._day_flows(offset)`(0=今·1=昨)、
  10/20 走 `institution_flows`。改窗只需動 `MERGE_GROUPS`+`_GROUP_HEAD`+run_all 產生對應來源欄，MD 端自動跟(讀 `group_source_cols`)。
- `src/tech_signal.py` — 技術面併欄：均線排列(5/10/20多空)、季線年線(60/240MA中長多空)、
  **半年線(120MA·中期分水嶺)**、20MA乖離%、52週位置%(近1年區間位置)、成交額億(資金權重)。需長歷史→靠滾動窗每日DB。
- **風控/事件欄（2026-08 依使用者投資框架補洞，五軌＋第6軌全套用）**：
  - `src/disposal.py` — **處置股/注意股警示**(免費 TWSE `announcement/punish`＋`notice`、TPEX `tpex_disposal_information`，各1call)。
    處置＝人工分盤撮合(2~5分鐘一次)＋常需預收款券→**當沖/短線做不動，務必避開**。⚠️日期皆民國，TWSE 期間「115/08/10～115/08/14」、
    TPEX「1150813~1150821」兩種格式，`_roc_to_iso`/`_period` 都吃；只標『期間內🚫/未開始⚠️』，已結束不標。
  - `src/exdividend.py` — **除權息預告**(免費 TWSE `TWT48U_ALL`＋TPEX `tpex_exright_prepost`)。除息當天股價扣股利開盤，
    不知情會把「參考價下修」誤判成大跌/跌破均線。只標近45天內。⚠️FinMind `TaiwanStockDividend` 不帶 data_id **只回單日快照**(要逐日打)，故走 TWSE/TPEX 預告表。
  - `src/shareholding.py` — **外資持股%/市值億/周轉率%**(FinMind `TaiwanStockShareholding`，**1 call 全市場2366檔**)。
    外資持股＝**存量**(與「外資今日買賣超」流量互補)；市值＝分大型/中小型股；周轉率＝量÷已發行股數(熱度，當沖比絕對量準)。
    ⚠️單位：DB volume 已是張→分母股數要 /1000 再比。
  - `src/risk.py` `levels()/enrich()` — **ATR 停損價/停損%/目標價**(純DB、一次向量化全市場)。停損＝收盤−2×ATR、目標＝2倍風險→
    **風報比固定1:2故不另列欄**(寫在報告說明)。停損%因股而異＝用ATR而非固定%的理由。⚠️`plan_trade()`(張數/部位)需帶總資金→留在 `scripts.risk_calc`，不進日報。
- `src/screeners/` — 各軌篩選器 + `landmine`(地雷偵測)。
- `src/screeners/daytrade_snipe.py` — **第6軌 隔日沖鎖碼候選(實驗)**：DB 抓今日漲停/大漲(≥9%)→取成交額前N檔→
  分點算主力淨額(前15買+前15賣)，只對『主力淨買鎖碼』者比對此檔近窗『隔日沖常客』(反覆昨買今賣≥2次)→
  今日大買分點列**兩份黑名單(分開欄、不同訊號)**：`全市場黑名單`＝跨所有股票的隔日沖慣犯(broker_profile，帶隔日沖率%、樣本大最可信)、
  `本檔黑名單`＝專門玩這檔的常客(近窗、專屬但樣本小)；兩邊都上榜＝最該防。
  ＋`預估賣壓張`/`預估賣壓佔量%`(前瞻，已回測單調有效見 broker_profile)。挑明日對殺 arena。
  📊**昨vs今欄(複用 `broker_signal._one`)**：`昨主力淨額`(T-1 前15買賣淨)→`今主力淨額`(T)看鎖碼是持續(昨買今也買)還已在倒(昨買今賣)；
  `隔日沖賣壓%`=昨日前15大買分點今日轉賣的對沖量÷今量＝**『昨進今出』實現驗證**(越高＝昨鎖碼今真在倒)。逐檔昨買今賣名單看個股深掘 `run_stock`。
  📊**判斷欄(run_all 沿用他軌 enrich 補上、不重抓)**：今日量張/量能倍數(爆量撐漲停?)、20MA乖離%/52週位置%(位階·追高風險)、均線排列/季線年線(趨勢)、
  外資今日/投信今日(法人站同邊?·只併今日單日免觸發 MERGE_GROUPS)、券資比%、借券賣出餘額/借券增減(空方)、籌碼訊號。dfsnipe 在 enrich 區塊後才建→就地用已算好的 `_sig`/`_tech`/`_tf` enrich。
  ⚠️方向未定(可能軋空續強/開高走低)、非投資建議；
  需 Sponsor 分點(無則只出漲停清單)、走 broker_net 本機快取。實測🎯精準命中凱基台北/美林/元大等公認隔日沖分點。
  🔬**已回測(`backtest_snipe`，356樣本)**：鎖碼股隔日**普遍開高(gap+1.3%)、盤中開高走低(oc−0.8%/55%跌)**，
  **開越高跌越兇**(開高>7%盤中−2.4%但僅48%跌·有軋空尾部)；**收收cc≈+0.4**(gap蓋過走低)→ 是**當沖空開盤盤中回補**訊號、
  **別抱過夜**；edge 薄(扣成本所剩無幾)。⚠️🎯對報酬無增量預測力(**收緊hits≥3/前5大買也沒用**，全~oc−0.8%)——
  🎯只是正確辨識隔日沖玩家的**資訊**、非更強訊號；**唯一鑑別變數是隔夜開高幅度**(開高>7%盤中−2.4%)。報告 `reports/snipe_signal_backtest.md`。
- `src/twse_client.py` / `src/tpex_client.py` — 官方資料 client（上市／上櫃）；含 `day_trade` 當沖統計。
- `src/day_trade_signal.py` — 當沖比率% 併欄（妖股對殺偵測，TWSE+TPEX 官方當沖÷總量，免費）。
  `fetch_market_day` 全市場單日→DB `day_trade` rows(供 backfill)；`trend(ids,n=5)` 讀 DB 歷史出
  「當沖比均{n}日＋當沖比趨勢(🔥升溫/❄降溫/➖持平＝今日 vs 前n-1日均)」→ 挑正在升溫的妖股 arena(僅當沖軌加)。
- `scripts/backfill_daytrade.py` — 回補當沖量歷史→DB `day_trade` 表(張)。逐日全市場(TWSE+TPEX 各1 call/交易日)、
  逐日 commit 可續跑；`--days N` 增量。run_all `_update` 已自動接(每日累積)。dump 隨 `sync_data` 進 CSV(進 git)。
- `src/sbl_signal.py` — 借券賣出餘額(法人真實空單)：`compute_from_db`(讀 DB sbl 歷史→借券賣出餘額+借券增減趨勢)
  優先；`compute`(逐檔 live 查) fallback；`fetch_market_day`(Sponsor 全市場單日→DB rows)。
- `scripts/backfill_sbl.py` — 回補借券歷史→DB `sbl` 表(股÷1000→張)。逐日全市場(1 call/交易日)、逐日 commit
  可續跑；`--days N` 增量。run_all `_update` 已自動接(每日累積)。dump 隨 `sync_data` 進 CSV(進 git)。
- `scripts/backfill_holders.py` — 回補千張大戶歷史→DB `big_holders` 表(Sponsor `TaiwanStockHoldingSharesPer`)。
  整市場逐週抓(1 call/週)、逐週 commit 可續跑；`aggregate_market` 純函式把分級明細→pct_1000(≥1000張)/pct_400(≥400張)，
  **日期標籤與 pct 標準跟 TDCC 完全一致→無縫併入**。⚠️日更仍走**免費 TDCC**(`update_holders`，掉回免費照跑)；
  這支只補歷史深度。跑後 `sync_data dump` 寫回 CSV(進 git)。用途：`enrich.big_holder_change_map` 千張週增減欄＋個股深掘大戶曲線。
- `src/broker_client.py` — 券商分點日報 client(`TaiwanStockTradingDailyReport`, Sponsor)：`available()` 偵測、
  `branch_summary` 主力買賣超摘要。⚠️分點僅**單日**查(`end_date` 需 none/等於 start)、**原始上萬列/檔不落 DB**。
  ⚠️**必須 `load_dotenv()`**（2026-08 修）：本機 token 寫在專案 `.env`，漏這行則 `_TOKEN=""`→`available()` 誤判「分點不可用(需 Sponsor)」，但抓資料(finmind_client 有 load_dotenv)照常→症狀＝有 Sponsor 卻只有分點失效。凡直接讀 FINMIND_TOKEN 的模組都要 load_dotenv。
- `src/broker_profile.py` — **跨股票『分點行為檔案』(2026-08)**：把已累積的 `broker_net` 快取**跨所有股票/日期**聚合成
  每個分點的 `隔日沖率%`(進前15大買後隔日轉賣的次數比)＋`回吐量%`(實際對沖張數比)＋樣本數/股票數(可信度)＋
  分類(🔥隔日沖大戶≥65 / ⚠️偏隔日沖≥50 / ➖中性 / 🏦偏長線<30)。**零 API**(只讀本機快取)。
  解決舊做法「單檔8天內≥2次」樣本太小的問題(實測 730 分點建檔；台新松德71.8%/110樣本 vs 大和國泰19.7%/173樣本，分離清楚)。
  `expected_pressure()`＝**前瞻預估賣壓**＝Σ(今日各分點淨買張×該分點歷史回吐量%)，今天就能估明日倒貨量。
  ⚠️`build(before=日期)` 供回測 point-in-time 用(不傳=用全部歷史，日常報告就該這樣)。
  📌**累計計數器持久化(2026-08)**：`update_from_cache()` 把快取折進 DB `broker_profile`(每分點一列:ops/flips/bought/dumped/stocks)
  ＋`broker_profile_seen`(已折算的買進日，**冪等**不重複累加)，**兩張表進 sync_data→CSV→git**(~300KB)。
  為什麼要：`prune_cache(keep_days=60)` 每天刪 60 天前快取、容器重置更是清空 → 只靠快取算則檔案**永遠只有60天且換機歸零**；
  存計數器才能**跨機器/跨月無限累積**。`build()` 日常優先讀持久化計數器，回測(before/cache)才從快取現算。
  ⚠️**run_all `_update` 順序：先 `update_from_cache()` 再 `prune_cache()`**，反了會漏算被剪掉的資料。
  ⚠️**相鄰交易日檢查**(`trading_days()`/`_next_day_map`)：快取稀疏(只抓候選股)常缺日，D1→D5 不是「隔日」不能算(2026-08 修，剔除約4%假配對)。
  📌**本機工具**：`python -m scripts.run_broker_profile`(＝控制台「📇 分點黑名單」鈕＋`一鍵執行/7_分點黑名單.bat`)
  → 更新計數器＋出排行報告 `reports/broker_profile.md/.html`(🔥隔日沖大戶/⚠️偏隔日沖/🏦偏長線三榜)。
  🔬**已回測**(`scripts/backtest_broker_profile.py`，356樣本·point-in-time)：預估賣壓佔量% **最高四分位隔日盤中 −1.64%**
  (跌比56.8%) vs 最低組 −0.72%，相關 −0.10；**鎖碼股子集單調**(低−0.33/中−0.73/高−1.42)。優於舊🎯(各組≈−0.8%、無鑑別力)。
  ⚠️**全體四組非乾淨單調**(Q1比Q2/Q3差)、edge 薄(扣當沖成本有限)、樣本期短、快取偏向報告候選股 → 當**排序/警示連續變數**用。
  ⚠️(2026-08修)原版把快取「缺日」誤當隔日(D1→D5也算隔日沖)→已加**相鄰交易日檢查**；修正後數字比初版弱(初版誤為−1.92%單調)。
  報告 `reports/broker_profile_backtest.md`。
- `src/broker_signal.py` — 分點主力淨額＋隔日沖偵測 enrich(需 Sponsor)：`compute(ids, day, prev, vol_map)` 出
  「主力淨額(前15買超+前15賣超淨額)」＋「隔日沖賣壓%(昨日前15大買超分點今日轉淨賣量÷今日量→抓昨進今出大戶倒貨、
  補當沖比看不到的隔日沖盲區)」。逐檔 on-demand(每檔 T/T-1 各1 call)、僅對顯示候選(head 20/軌)。run_all 已接。
  🚀**分點本機快取**：`_branch_net` 把聚合後 `{分點:淨}` 存 DB `broker_net`(JSON、**全保真、快取＝實算相同**)，
  同檔日重用免重抓(實測 1100x：1.4s→0.001s；今=明的昨、深掘重跑同檔、回測全受益)。⚠️**本機加速用不進 git CSV**
  (全市場一年 ~1GB 太大；雲端無快取有額度重抓即可)。`prune_cache(keep_days=60)` run_all `_update` 每日修剪控大小。
- `src/stock_deepdive.py` + `scripts/run_stock.py` — **個股深掘([6])**：單檔『深掘病歷表』(.md+.html)，籌碼＋技術＋基本面全含。
  DB 拉齊價量/法人/資券/借券時間序列＋千張大戶週趨勢＋當沖比時間序列(`daytrade_timeline`)；分點(Sponsor)出逐日主力淨額/隔日沖賣壓%＋
  **隔日沖常客名單**(窗內反覆昨買今賣的分點→這檔的隔日沖大戶)＋最新日 Top 買/賣分點。分點逐日單查(N日=N call)、
  **走 broker_net 本機快取**(重跑同檔幾乎免抓)。
  📌**技術面(2026-08)**：`tech_snapshot`(複用 tech_signal/chip_signal/verdict→均線排列/季線年線/20MA乖離/52週位置/量能倍數/成交額＋綜合定調🔴/⚪/🟢)＋`ma_series`(收盤+MA5/20/60 疊圖，用完整歷史算 MA)。純 DB 免費。
  📌**基本面(2026-08，FinMind 逐檔~4~6 call)**：`monthly_revenue`(近12月營收 YoY/MoM/累計YoY)、`profitability`(近8季毛利率/營益率/淨利率/單季EPS/EPS年增，損益表**單季值**)、`valuation_snapshot`(PER/PBR/殖利率＋PER近1年位置%＋序列)、`dividends`(近年配息＋連續配息年數；⚠️FinMind `year`＝『114年第2季』民國+季，自解析民國年**按年彙總**、季配股不誤當年數，故不複用 `enrich.dividend_years`)。
  📌**財務體質(2026-08，三表健檢)**：`financial_health`(負債比/流動比/每股淨值/單季營運CF/含金量%/自由現金流)。⚠️**現金流是累計 YTD**(年內遞增·跨年重置)→`single_q` 同年內 diff **去累計還原單季**、Q1=YTD，不還原含金量會算爆；損益表淨利本就單季直接比。
  📌**真實ROE＋三率三升(2026-08)**：`financial_health` 加 `ROE%`＝**近四季淨利TTM÷期末權益**(取代長期軌 PBR/PER 的粗估；實測2330=32.7%)；
  `three_rates(prof)` 出🔴三率三升/🟢三率三降/⚪n升m降(最新季vs上季)，併入基本面票。
  📌**同業比較+財報紅旗+基本面定調(2026-08)**：`industry_peers`(產業別走 `enrich.industry_map` FinMind——**DB stock_info.industry 是空的**；同業依 DB 成交額排序取前 n)＋`peer_table`(PER/PBR/殖利率/月營收YoY 並排、★本檔)；財報紅旗複用 `landmine._fin_flags`(單一來源)出 callout；綜合定調＝`tech_snapshot._vote`(技術+籌碼)＋run_stock `_fund_vote`(營收YoY/EPS年增/含金量/紅旗)兩票相加→`verdict.label`（**不改共用 `verdict._vote`**，避免影響五軌日報）。
  HTML 有 **📈圖譜([7])**：收盤+均線/主力淨額/隔日沖賣壓%/當沖比%/借券/大戶＋基本面(營收YoY/EPS/PER)＋財務體質(負債比/營運CF)的內嵌 SVG 迷你圖。抓不到(免 Sponsor/無財報)graceful 留白。用法 `python -m scripts.run_stock 1303 --days 30`→`reports/stock/`。GUI「個股籌碼深掘」鈕＋`6_個股深掘.bat`。
- `src/svgchart.py` — 極簡 inline SVG 迷你圖(`bars` 長條/紅正綠負、`line` 折線、`lines` 多序列共用y軸疊圖供均線用)：純 SVG+<title> tooltip、CSP-safe、明暗皆清楚。供深掘圖譜用。

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
- `scripts/run_t16_oos.py` — T16 **嚴格樣本外**（walk-forward）：3 折 expanding-train，train 挑夏普最高
  參數→test 驗證，三段 test 資本串接成連續 OOS 曲線；對照「看整段挑最好」過擬合上界。離線、免額度。
- `scripts/backfill_history.py` — 回補離線面板→`data/history/backtest_panel.csv.gz`。
  `--update`(補新日子)、`--only-new`(只加新股、舊股零API)、`--max-new`(額度保護，600/時→190/次)。
- `scripts/backfill_revenue.py` — 回補月營收面板→`data/history/revenue_panel.csv.gz`（供 T12）。
  每檔1次 FinMind call，對齊股價面板；YoY/累計YoY 自算，公布日用 create_time(或次月10日 fallback)。
- `scripts/backfill_valuation.py` — 回補估值面板→`data/history/valuation_panel.csv.gz`（供長期價值軌）。
  月頻(每月首交易日)抓 TWSE BWIBBU_d 殖利率/PER/PBR，僅上市；`compute_longterm_entries` 用之。
- `scripts/run_longterm_backtest.py` — 長期價值軌回測（月頻價值篩選 vs 買入持有/+regime）。
- `tests/` — 97 個單元測試（signals／portfolio／T12前視／長期價值篩選／db 快取／定調7面向／借券enrich＋歷史增減／分點主力淨額＋隔日沖賣壓／分點本機快取／當沖比熱度趨勢／隔日沖鎖碼軌／個股深掘時間序列＋隔日沖常客／SVG迷你圖／T16 OOS純函式／千張大戶分級聚合）；`python -m pytest tests/ -q`。
- 報告：`reports/signal_compare.md`、`reports/t16_tune.md`、`reports/portfolio_backtest.md`、`reports/oos_validation.md`、`reports/longterm_backtest.md`、`reports/t16_oos.md`、`reports/daytrade_signal_backtest.md`、`reports/snipe_signal_backtest.md`。

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
python -m scripts.run_t16_oos                          # T16 嚴格樣本外 walk-forward（train挑參數→test驗證）
```

**關鍵結論（離線 200 檔面板實測，2026-07）**：
- **T11 法人吸貨＝沒 edge**（CAGR −16%、回撤 −63%）→ 只當選股情報，別當進出場策略。
- **T16 抗跌強勢＝真有 edge**（動能）：10 檔無停損 CAGR **+32%**、夏普 **0.99**、回撤 −37%。
  ⚠️ **停損反而傷 T16**（把動能股在低點洗出場）；降回撤靠「多持到 ~10 檔分散」，不是停損。
  🔬 **嚴格樣本外驗證（walk-forward，run_t16_oos）＝edge 存活但打折且脆弱**：連續 OOS
  CAGR **+37.9%**／夏普 **1.11**，僅小勝同期買入持有(+26.5%/1.07)，且**回撤更深 −44%**。
  逐折高度分散：F1 +14%、**F2 −22%（動能失效窗）**、F3 +195%（強多頭+倖存者偏誤灌水）。
  選參數不穩（各折挑中組態跳動）。結論：**T16 有真 edge 但非穩定印鈔**，實盤須順 regime、
  分散、控回撤、抓對市況；別把 in-sample +32% 當保證。
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
- 未計滑價；✅ T16 已做嚴格樣本外（walk-forward，run_t16_oos）；其餘軌仍 in-sample。

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

- ✅迷你趨勢圖（sparkline）—— 已於個股深掘 HTML 圖譜([7])做（`svgchart`）；顏色深淺熱力圖仍擱置。
- MD/HTML **顯示列數**落差（T11/當沖：HTML 19–20 列 vs MD 15 列）；欄位一致、僅列數不同。

### 🎯 下次可做（2026-08 盤點給使用者的選單，未動工；使用者傾向先實戰觀察）
1. **全軌命中追蹤＋累積戰績表**（推薦）：`picks_tracker` 追 T11/T16/當沖/**隔日沖鎖碼**(已加,2026-08)，
   還缺 T12/長期；可再累積各軌實際命中率/報酬做戰績表 → 用實戰數據判斷哪軌真有用（比回測更誠實）。
   ⚠️隔日沖鎖碼軌的追蹤：`daytrade_snipe.run(asof=日期)` 可重建舊日精選回填 picks.csv(見 2026-08 收尾)。
   ✅**隔日沖鎖碼有專屬追蹤區塊**(`picks_tracker.snipe_ohlc`)：昨日鎖碼候選→今日開/高/低/收＋跳空%(隔夜高開)/盤中%(開高走低)，
   report_html `_snipe_ohlc_html`+run_all MD 段對齊；已從通用 followthrough 排除(不重複)。基準日用『≤pick標籤的最後交易日』
   查昨收→對非交易日標籤(週末跑/回填)健壯。
2. **T16 驗證贏法變「今日建議籃子」**：回測結論 T16+分散~10檔+順regime 才是真 edge；把它落成
   「今天該買這籃/環境紅燈空手」的具體行動（橋接回測→實作）。
3. **T12 嚴格樣本外**（walk-forward，比照 `run_t16_oos`）：唯一剩的回測嚴謹缺口、框架現成、低成本。
- 更大工/邊際遞減：盤中即時(Shioaji VWAP/五檔/內外盤，另一個專案)、價值軌納配息年數/EPS、上櫃估值。
- **使用者當前態度＝先每天跑 `1_盤後選股.bat` 觀察一兩週，讓 picks_tracker 累積真實戰績再決定。**

### 📌 2026-08 Sponsor 升級 session 收尾（下次接手看這裡）
FinMind Sponsor 已辦，規劃見 `docs/Sponsor升級規劃.md`。本 session 完成 [0][2][4][5][6][8]：
- [0] 借券歷史落 DB＋借券增減趨勢欄；[2] 額度解放(五軌回歸日更)；[4] 主力淨額欄；
  [5] 隔日沖賣壓%欄；[6] 個股深掘 `run_stock`；[8] 隔日沖回測(單獨≈無 edge，見結論)。
- 另：HTML 報告可隱藏欄位＋表頭/首欄固定（`report_html._colctrl`/sticky）。
- **狀態＝先實戰跑幾天觀察**（新欄＋個股深掘實用性）。
- **[7] 深掘進階已完成**（2026-08 後續 session）：當沖比熱度趨勢欄＋個股深掘 📈圖譜(SVG 迷你圖)。
  唯一還可延伸：特定分點追蹤、顏色熱力圖——皆錦上添花、非必要，roadmap 主線已清空。
- 若嫌長期軌拖慢：加回 `--skip-longterm`（gui 主按鈕＋`1_盤後選股.bat`）即可。

### 📌 2026-08 後續 session 收尾（清理＋加強當沖＋第6軌，下次接手看這裡）
本 session（接續 Sponsor 升級）完成，全部已 push、97 單元測試全綠：
1. **清理**：刪掉與正式流程脫鉤、方法已被組合框架取代的 6 隻舊事件回測腳本＋`src/backtest.py`
   ＋3 個舊報告（~813 行下架），docs 同步。
2. **[3] 千張大戶歷史回補**（`backfill_holders`，51週）＋ run_all T11 加「千張週增減」欄。
3. **分點本機快取**（DB `broker_net`，1100x 提速；深掘/回測/每日重用免重抓；不進 git）。
4. **加強當沖**：當沖量落 DB（`backfill_daytrade`）＋當沖軌「當沖比均5日/趨勢(🔥升溫/❄降溫)」；
   個股深掘 HTML 加 📈圖譜（`svgchart` 六張 SVG 迷你圖）。
5. **第6軌 隔日沖鎖碼候選**（`daytrade_snipe`）：漲停/大漲＋主力/隔日沖大戶鎖碼，🎯 精準命中
   凱基台北/美林等公認隔日沖分點（純資料推導）。**已回測(`backtest_snipe`,356樣本)**：
   開高走低成立但 edge 薄、方向不穩（開高>7%有軋空尾部）、🎯對報酬無增量預測力（收緊也沒用）、
   唯一鑑別是隔夜開高幅度；定位＝當沖空開盤盤中回補的**情境舞台**、非提款機、別抱過夜。
- **狀態＝roadmap 全清空，功能面已完整；下一步見上「🎯 下次可做」選單，但使用者傾向先實戰觀察。**

### 📌 2026-08 第6軌強化 + 健壯性 session 收尾（下次接手看這裡）
本 session 全部已 push、103 單元測試全綠。圍繞使用者實戰第6軌的一連串需求：
1. **HTML 表頭固定真生效**（`report_html._CSS`）：原 `thead sticky` 被 `.tblwrap overflow-x`＋
   `table overflow:hidden`(圓角)兩層攔截失效→改 `.tblwrap max-height:82vh overflow:auto`＋圓角/陰影搬到 tblwrap、
   table 去 overflow:hidden。長表框內捲動、欄名黏頂；Playwright 實測。
2. **第6軌加昨/今主力淨額 + 隔日沖賣壓%**（`daytrade_snipe`＋複用 `broker_signal._one`）：昨vs今看鎖碼持續/在倒。
3. **昨日鎖碼候選→今日走勢專屬區塊**（`picks_tracker.snipe_ohlc`＋`report_html._snipe_ohlc_html`＋run_all MD）：
   昨日精選今日 開/高/低/收＋跳空%(隔夜高開)/盤中%(開高走低)＋**鎖碼淨額(當時主力買多少,存 picks 時記下)**；
   從通用 followthrough 排除。⚠️基準日用『≤pick標籤最後交易日』(對週末/非交易日標籤健壯——曾把08-07誤標週日08-09)。
4. **第6軌每日表加判斷欄**（run_all 就地 enrich dfsnipe）：量能倍數/今日量張、20MA乖離%/52週位置%、均線排列/季年、
   外資今日/投信今日、券資比%/借券/籌碼訊號。⚠️只併今日法人單日、**不加10日「外資」欄**（否則 MERGE_GROUPS 把單日欄吃掉）。
5. **502 健壯性**（`finmind_client.fetch` 5xx 退避重試＋run_all 長期軌 except 放寬為 Exception）：
   一次 FinMind 抖動不再炸掉整份日報。
- **狀態＝第6軌功能已很完整(昨vs今/走勢追蹤/判斷欄)；使用者持續實戰觀察中。**

### 報告增強 roadmap（2026-07 盤點，報告欄位已很完整，瓶頸在「把資料變行動」）
- **① 綜合定調**：✅已完成。波段/成長/長期軌皆有 `verdict.add_verdict`，7面向投票
  （法人主導/融資散戶/均線排列/季線年線/20MA乖離/52週位置/券資比軋空）→ 每檔一句
  「🔴偏多/⚪觀望/🟢偏空」，門檻 ±3（須≥3方向一致）。當沖軌另有 intraday 專用「多空傾向」。
- **② 資料受限籌碼欄**：✅當沖比率（妖股對殺，day_trade_signal，5328~79%驗證）；
  ✅借券賣出餘額＋**借券增減趨勢**（法人真實空單，`sbl_signal`：TaiwanDailyShortSaleBalances 的
  SBLShortSalesCurrentDayBalance；**已辦 Sponsor→改全市場落 DB `sbl` 表**，`backfill_sbl` 回補歷史、
  run_all 每日累積，`compute_from_db` 出「借券賣出餘額(最新)＋借券增減(vs前日)」，+=法人加空/−=回補）；
  ✅**主力分點＋隔日沖賣壓%**（`broker_signal`：分點主力淨額＋昨日大買家今日倒貨的隔日沖偵測，補當沖比盲區；
  Sponsor 分點、逐檔 on-demand 僅對顯示候選、量大不落 DB；5 軌報告已加「主力淨額/隔日沖賣壓%」欄）。
  ✅**千張大戶歷史回補([3]，`backfill_holders`)**：Sponsor `TaiwanStockHoldingSharesPer` 整市場逐週回補
  DB `big_holders`(已補 51 週)，日期/pct 與 TDCC 一致無縫併入；日更仍免費 TDCC。run_all T11 加「**千張週增減**」欄
  (`enrich.big_holder_change_map`，🔴+加碼/🟢−減碼)＋個股深掘大戶曲線變深。⚠️run_all 只顯示最新快照%，週增減靠此回補才穩。
  ✅個股深掘([6]，`run_stock`：分點歷史時間軸/隔日沖常客名單/主力Top分點/借券大戶趨勢)；
  ✅**隔日沖訊號回測([8]，`backtest_daytrade`)**：1540樣本實測「隔日沖賣壓%」**單獨≈無 edge**（相關+0.002、
  分組不單調、最高組甚至微正）→ 只當情境警示欄，別單獨做隔日方向；**只有極端≥20%(次日−2.2%)或搭配當日走弱
  (次日−1.6%)才預告偏空**；對照主力淨額方向性略強(淨買隔日+0.81%/淨賣−0.60%)但仍弱。報告 `reports/daytrade_signal_backtest.md`。
  ✅**當沖比熱度趨勢([7]一部分，`backfill_daytrade`+`day_trade_signal.trend`)**：當沖量落 DB `day_trade`
  (免費 TWSE+TPEX、每日累積)，當沖軌加「當沖比均5日＋當沖比趨勢(🔥升溫/❄降溫)」→ 挑正在升溫的妖股 arena
  (熱度訊號、非多空方向、非 alpha)。
  ✅**個股深掘圖譜視覺化([7]完成)**：深掘 HTML 加 📈圖譜區塊(`svgchart` 內嵌 SVG 迷你圖)——收盤/主力淨額(紅買綠賣)/
  隔日沖賣壓%/當沖比%/借券餘額/千張大戶% 六張走勢圖，自足無外部庫、tooltip 看數值。**[7] 全數完成、roadmap 清空**。
- **③ 回測驗證缺口**：✅長期價值軌已回測（無 edge）；✅T16 嚴格樣本外（walk-forward，
  run_t16_oos：OOS +37.9%/夏普1.11 小勝買入持有但回撤更深、逐折分散、edge 存活但脆弱）；
  待做：價值軌納配息年數/EPS 因子、上櫃估值(BWIBBU_d 僅上市)、T12 樣本外。

## 提醒使用者的常見事項

- 最新籌碼數值與各軌完整 CSV，需**實際跑一次 `一鍵執行/1_盤後選股.bat`** 才會產生當日檔。
