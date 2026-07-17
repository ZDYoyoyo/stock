# 專案交接 / 記憶檔 (PROJECT STATE)

> 給下一個 session 的接手說明。新 session 冷啟動，**先讀這份 + README.md** 即可接續。
> 最後更新：2026-07-17

## 這個專案是什麼
個人台股研究系統：整合盤面/基本面/籌碼面/技術面，建立可重複運作的**選股篩選器**、
**每日盤後掃描**與**投資決策框架**。⚠️ 研究用途，非投資建議。

## 已拍板的決策
1. **資料源：免費** → **改用 TWSE 官方全市場端點**（MI_INDEX/T86/MI_MARGN，免費無 key、可批次）。
   ⚠️ FinMind 免費版**不支援全市場批次**（回 "level is free"），只能逐檔查，故批次改走 TWSE。
   FinMind client 仍保留（`src/finmind_client.py`）供日後逐檔細查。
2. **語言：Python**（純腳本，暫不做網頁 dashboard）
3. **T11 法人吸貨篩選器 ✅ 已完成，並已用真實資料產出首份清單**（見 reports/2026-07-17_T11…）。
4. **執行環境**：本 session 網路**已放行**（可直接抓 TWSE），亦可本機跑。
   目前僅上市(TWSE)，**未含上櫃(TPEX)**。

## 已完成並 commit（分支 claude/taiwan-stock-analysis-asx-mww0s6）
- `reports/2026-07-17_3711_日月光投控.md` — 日月光當日跌停股災分析（大盤級股災、非個股利空、7/23 財報為關鍵）
- `strategy/01_投資框架_短中長期.md` — 短/中/長期操作框架
- `strategy/02_分析任務清單_backlog.md` — 完整任務 backlog（T01~T24）
- `src/`、`scripts/` — FinMind 資料管線 + T11 篩選器（**已用合成資料驗證邏輯正確**）

## 本機執行方式
```bash
pip install -r requirements.txt
cp .env.example .env               # 選填 FinMind token
python -m scripts.update_data --days 30   # 抓資料 → data/stock.db
python -m scripts.run_t11                 # 跑 T11 → reports/screener/ 下 CSV/MD
```
T11 門檻都在 `src/config.py` 可調。

## 進度更新（2026-07-17）
- ✅ **方案A（上櫃 TPEX）完成**：src/tpex_client.py，市場自適應（上市看投信/上櫃看外資）+ 加嚴護欄
- ✅ **T16 抗跌強勢**：src/screeners/relative_strength.py
- ✅ **T22 每日盤後自動掃描**：scripts/daily_scan.py（T11+T16+雙訊號交集，附本機排程說明）
- 首份真實清單見 reports/2026-07-17_T11_法人吸貨清單.md（9上市+4上櫃）

## 下一步 (TODO，依序)
1. **T30 短名單深掘**：對 T11/T16 短名單用 FinMind 逐檔補月營收/EPS/PER 二次驗證
2. **T23 回測引擎**（含手續費 0.1425%、證交稅 0.3%）驗證勝率後才實際投錢
3. 上櫃可再疊加主力券商分點（FinMind）交叉驗證
4. 其他 backlog：見 `strategy/02_分析任務清單_backlog.md`

## 值得優先考慮的延伸題目（腦力激盪，未決定）
- 籌碼：千張大戶持股比、券商分點主力、借券賣出/券資比、董監質押
- 基本面：月營收動能(T12)、盈餘品質/地雷偵測、本益比河流圖
- 大盤：市場廣度 breadth、產業輪動熱力圖（判斷「現在能不能進場」）
- 風控：ATR 停損/移動停利、部位試算、投組績效追蹤（回撤/夏普）
- 事件：除權息/法說/營收行事曆提醒、費半夜盤連動、台幣匯率

## 付費 API 備忘（目前用免費即可，不急）
- FinMind Sponsor / Sponsor Pro：更高額度+券商分點，月費數百元級（詳見官網）
- 永豐 Shioaji：開戶免費、即時報價+下單、市佔近 5 成（要即時/下單時的首選）
- 富果 Fugle：免費+付費即時行情
- TEJ：法人級、偏貴（年費萬元起）；CMoney：付費籌碼資料

## 環境備忘
- Git 分支：`claude/taiwan-stock-analysis-asx-mww0s6`（所有開發都在這）
- Repo：ZDYoyoyo/stock
