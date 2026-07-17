# 台股投資研究系統 (Taiwan Stock Research System)

個人化的台股研究與選股系統：整合**盤面資料、基本面、籌碼面、技術面**，
建立可重複運作的**選股篩選器**、**每日盤後掃描**與**投資決策框架**。

> ⚠️ **免責聲明**：本專案內容為研究與教育用途，所有分析、篩選結果與範例
> 皆**不構成投資建議**。投資有風險，進出場請自行判斷並自負盈虧。

---

## 目錄結構

```
stock/
├── README.md
├── requirements.txt                # 相依套件（免費資料源）
├── .env.example                    # FinMind token（免費，選填）
├── reports/                        # 個股 / 大盤分析存檔（依日期）
│   └── 2026-07-17_3711_日月光投控.md
├── strategy/
│   ├── 01_投資框架_短中長期.md      # 短 / 中 / 長期操作框架
│   └── 02_分析任務清單_backlog.md    # 要建的篩選器 / 指標 / 資料管線
├── src/
│   ├── config.py                   # 設定與 T11 篩選門檻
│   ├── db.py                       # SQLite 儲存層
│   ├── finmind_client.py           # FinMind 免費 API client
│   └── screeners/
│       └── institutional_accumulation.py   # T11 法人默默吸貨
└── scripts/
    ├── update_data.py              # 抓資料進 SQLite（每日盤後跑）
    └── run_t11.py                  # 執行 T11 篩選、輸出清單
```

## 🚀 本機快速開始

```bash
pip install -r requirements.txt
cp .env.example .env          # 選填 FinMind token（免費註冊額度較高）

# 1) 抓最近 30 天全市場資料 → data/stock.db
python -m scripts.update_data --days 30

# 2) 跑 T11 法人默默吸貨篩選 → 終端機 + reports/screener/ 下 CSV/MD
python -m scripts.run_t11

# 3) ★ 一鍵跑全部（環境紅綠燈 + 波段 + 長期 + 當沖，輸出單一整合報告）
python -m scripts.run_all                 # 全部
python -m scripts.run_all --skip-longterm # 略過較慢的長期軌

# 其他單獨執行
python -m scripts.daily_scan   # 波段（環境+T11+T16+交集）
python -m scripts.run_longterm # 長期價值
python -m scripts.run_daytrade # 當沖候選
python -m scripts.run_t30      # 短名單基本面深掘
python -m scripts.risk_calc --stock 2206 --account 1000000 --risk 1.5  # 風控試算
```

## 盤中即時監控（本機、盤中執行；TWSE MIS 免開戶）

```bash
# 盤中(09:00~13:30)盯一籃股票並警示（接近漲跌停/急拉急殺/突破當日高低）
python -m scripts.monitor_intraday --stocks 2330,2454,6669
python -m scripts.monitor_intraday --from-daytrade 10   # 用當沖候選前10檔
```
⚠️ MIS 約 20 秒延遲，適合監控/警示非搶帽子；本工具只監控不下單，進出自行判斷嚴設停損。

> 篩選門檻（連買天數、漲幅上限、吃貨比例…）都在 `src/config.py`，可自行調鬆/調緊。

## ⏰ 每日自動掃描排程（本機）

`daily_scan` 建議在交易日傍晚（三大法人 T86 公布後，約 18:00~20:00）跑：

```bash
# Linux / macOS：週一~五 19:00
0 19 * * 1-5 cd /path/to/stock && /usr/bin/python3 -m scripts.daily_scan >> logs/scan.log 2>&1
```
Windows 用「工作排程器」設每日 19:00 觸發 `python -m scripts.daily_scan`（起始位置設專案資料夾）。

## 資料源說明
- **批次全市場**：TWSE（上市）＋ TPEX（上櫃）官方端點，免費、無需金鑰。
- **市場自適應**：上市看投信、上櫃看外資（投信極少參與上櫃）；上櫃套用更嚴的量能門檻。
- FinMind（`src/finmind_client.py`）保留供日後對短名單「逐檔深掘」（月營收/EPS/PER/分點）。

## 使用方式

1. **看個股/大盤分析** → 讀 `reports/` 下對應日期檔案
2. **決定操作策略** → 讀 `strategy/01_投資框架_短中長期.md`
3. **開發選股工具** → 依 `strategy/02_分析任務清單_backlog.md` 逐項實作

## ⚠️ 環境限制備註

目前這個雲端 session 的**網路政策封鎖了所有財經資料來源**（TWSE / Yahoo /
鉅亨 / Goodinfo 皆回 403），即時報價與批次資料**無法在本環境抓取**。
資料管線（data pipeline）需在**具備開放網路**的環境執行，例如：

- 本機執行 Python 腳本
- 使用網路政策較寬鬆的 Claude Code session
- 官方開放資料：TWSE OpenAPI、公開資訊觀測站 (MOPS)、TPEX OpenAPI

本專案先建立**方法論、篩選規則與程式骨架**；接上資料源後即可產出實際清單。
