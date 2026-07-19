# 專案交接 / 記憶檔 (PROJECT STATE)

> 給下一個 session 的接手說明。新 session 冷啟動，**先讀這份 + README.md** 即可接續。
> 這份就是本專案的「memory」，可在 GitHub 分支或本機直接開啟。
> 最後更新：2026-07-17

## 介面
- `gui.py`：tkinter 圖形控制台（點按執行所有功能，Windows 內建 tkinter 免安裝）。
- `一鍵執行/*.bat`：啟動控制台 + 盤後選股/盤中守衛/更新千張/更新程式。

## 一、這個專案是什麼
個人台股研究系統：整合盤面/基本面/籌碼面，建立可重複運作的**選股篩選器**、
**每日盤後掃描**與**回測驗證**。⚠️ 全部研究/教育用途，非投資建議。

### 三軌目標（使用者要的）
1. **長期持有軌**：價值+成長+配息+品質，持有數月~數年。✅ **v2 完成**（long_term_value.py）。
   → 連配息≥5年硬門檻、ROE估算(=PBR/PER，因資產負債表免費版被鎖)、EPS近4季/年增、
   營建股YoY歸零+⚠️標註。結果見 reports/2026-07-17_長期價值候選_v2.md。
   → 散戶最有真 edge、免費 EOD 資料充足。
   → 注意：FinMind 免費額度會觸發 402 限流（client 已自動等待重試），深掘檔數勿調太大。
2. **短期波段軌**：籌碼(T11)+相對強弱(T16)，持有天~數週。✅ 選股+風控+擇時完成。
   → 回測顯示 edge 薄（多為 beta），已補：多空紅綠燈(regime)、全球市場(費半)、ATR停損/部位試算。
3. **當沖軌**：✅ **EOD 候選掃描 + 盤中即時監控完成**。
   - day_trade_candidates.py：盤後選高波動+高流動+爆量，縮小要盯的清單。
   - realtime.py + monitor_intraday.py：TWSE MIS 免開戶即時報價(~20秒延遲)，
     盤中盯盤並警示（接近漲跌停/急拉急殺/突破當日高低）。已測通。
   - ⬜ 僅剩**自動下單**未做：需券商 API（永豐 Shioaji / 富邦 Neo / 富果 Fugle 等，
     非限永豐；國泰證券查無成熟公開下單 API）。使用者決定後再接。

## ⚙️ 維護慣例（重要，每次都要遵守）
- **只要新增/修改「要打指令執行」的功能（scripts/*.py 或新 CLI 參數），
  一定要同步更新 `docs/指令手冊.md`（怎麼跑）與 `docs/功能說明.md`（做什麼）。**
- 新增模組要更新本檔第三節的架構樹。

## 二、已拍板的決策
1. **資料源（免費）**
   - 批次全市場 → **TWSE(上市) + TPEX(上櫃) 官方端點**（免金鑰）。
   - 逐檔深掘 → **FinMind**（免費逐檔，月營收/EPS/PER；免費版不支援全市場批次）。
2. **語言：Python 純腳本**（暫不做網頁 dashboard）。
3. **市場自適應**：上市看投信、上櫃看外資（投信極少碰上櫃）；上櫃套更嚴量能門檻。
4. **執行環境**：本 session 網路已放行，可直接抓；亦可本機跑。

## 三、系統架構（全部 .py）
```
src/
  config.py            # 所有門檻參數（T11/T16 等）
  db.py                # SQLite 儲存層
  twse_client.py       # 上市全市場（MI_INDEX/T86/MI_MARGN）
  tpex_client.py       # 上櫃全市場（欄位經 FinMind 交叉驗證）
  finmind_client.py    # FinMind 逐檔
  enrich.py            # T30 基本面深掘 + 連續配息年數 + 千張大戶
  tdcc_client.py       # 集保股權分散（千張大戶，免費全市場）
  backtest.py          # T23 回測引擎
  regime.py            # 多空紅綠燈（建議部位水位）
  global_market.py     # 全球市場（費半/那指/VIX/台幣，Yahoo）
  risk.py              # ATR 停損 + 部位試算
  screeners/
    institutional_accumulation.py  # T11 法人吸貨（波段）
    relative_strength.py           # T16 抗跌強勢（波段）
    market_breadth.py              # 市場廣度
    long_term_value.py             # 長期：價值+成長+配息
    day_trade_candidates.py        # 當沖候選（EOD，高波動+高流動）
    landmine.py                    # 🧨 地雷偵測（財務+籌碼+技術紅旗，排雷用）
scripts/
  update_data.py       # 抓資料進 DB（--market all/twse/tpex, --days N）
  run_all.py           # ★ 一鍵跑全部：環境+三軌，輸出單一整合報告
  daily_scan.py        # 波段：環境紅綠燈+全球 + T11 + T16 + 交集
  run_daytrade.py      # 當沖候選
  run_longterm.py      # 長期軌選股
  update_holders.py    # 千張大戶更新（每週跑，累積趨勢）
  risk_calc.py         # 風控試算（停損/停利/張數）
  portfolio.py         # 持股追蹤（記錄實單/即時損益/停損警示）
  monitor_portfolio.py # 盤中持股守衛（觸停損/停利自動推 Telegram）
  run_landmine.py      # 🧨 地雷偵測（--holdings/--stocks/--from-daily，買進前排雷）
  monitor_intraday.py  # 盤中盯觀察清單（終端機警示）
  notify.py 用於推播；run_all --notify 盤後推播
docs/
  功能說明.md          # 所有模組白話解釋
  指令手冊.md          # 所有指令怎麼跑
  run_t11.py / run_t30.py / run_backtest.py / run_validation.py / run_edge_check.py
```

## 四、模組完成度
| 模組 | 功能 | 狀態 |
|---|---|---|
| 資料管線 | TWSE+TPEX 全市場逐日 | ✅ |
| T11 | 法人吸貨（上市投信/上櫃外資）+ 加嚴護欄 | ✅ |
| T16 | 抗跌強勢股（相對大盤強弱） | ✅ |
| 市場廣度 | 漲跌家數 + 站上20MA% → 判斷進場時機 | ✅ |
| T22 | 每日盤後一鍵掃描 | ✅ |
| T30 | 短名單基本面深掘（FinMind） | ✅ |
| T23 | 回測引擎（事件研究，已測 1~2 檔） | ✅ 引擎完成 |

## 五、本機執行
```bash
pip install -r requirements.txt
python -m scripts.update_data --days 40     # 抓資料
python -m scripts.daily_scan                # 每日掃描（推薦，含市場廣度）
python -m scripts.run_t30                   # 短名單補基本面
python -m scripts.run_backtest --stocks 2206,6669   # 回測測試
```
門檻都在 `src/config.py`。排程說明見 README「每日自動掃描排程」。

## ★ 回測關鍵發現（2026-07-19 更新，很重要）
**正式回測**：DB 流動性前 **100 檔**、2022-01-01~2026-07（含成本，run_full_backtest）：

| 持有 | 基準(任意日) | A法人連買 | D組合(連買+抗跌+營收) | A超額 | D超額 |
|---|---|---|---|---|---|
| 5天 | -0.22% | -0.48% | -0.29% | -0.26% | -0.07% |
| 10天 | +0.11% | -0.07% | +0.48% | -0.18% | +0.37% |
| 20天 | +1.00% | +0.60% | +1.77% | -0.40% | +0.77% |
| 40天 | +2.25% | +2.11% | +3.81% | -0.14% | +1.56% |

**結論（100檔比 60/36 檔更穩，方向完全一致）：**
- **「法人連買」單獨用沒有 edge，且每個天期都『負超額』**（-0.14~-0.40%，略輸隨機進場 → 法人常追高）。
  **不可當機械買訊。**
- **「組合訊號」（法人連買+抗跌+營收不衰退）在 10/20/40 天穩定贏基準，且持有越久贏越多**
  （超額 +0.37% → +0.77% → +1.56%，單調放大 = alpha 特徵，非雜訊）。
- **短持有(5天)全負**，任何訊號都一樣 → 別想訊號進場幾天就跑。
- 勝率都 <50%，正期望靠少數大贏 → **嚴設停損、讓對的單續抱**（風控是報酬關鍵，非可有可無）。
→ 意義：**用組合條件（籌碼+抗跌+基本面），不單看法人**；**持有數週**；擇時(regime)+風控必備；
   限制：2022~ 非樣本外、未計滑價，屬方向性驗證。

## 六、下一步 (TODO)
1. ✅ **T23 完整回測（已完成 2026-07-19）**：run_full_backtest 用 FinMind 逐檔抓 2022~ 歷史
   （不需回補全市場 DB），100 檔驗證完畢，結論見上「回測關鍵發現」。
   已定論：單靠「法人連買」負超額，需疊加抗跌+基本面且持有數週才有 edge。
2. 其他 backlog（未做，可挑）：
   - 籌碼：千張大戶持股、券商分點主力、借券賣出
   - 基本面：月營收動能股(T12)、盈餘品質/地雷偵測、本益比河流圖
   - 風控：ATR 停損/移動停利、部位試算、投組績效追蹤
   - 事件：除權息/法說/營收行事曆提醒
   - 詳見 `strategy/02_分析任務清單_backlog.md`

## 七、重要提醒 / 已知限制
- 三大法人 T86 傍晚才公布；上市常比上櫃晚一天（篩選器已自動對齊共同最後法人日）。
- 目前只做上市+上櫃「普通股」（4 位數代號），排除 ETF/權證。
- 每日輸出在 `reports/screener/`（gitignore）；里程碑報告在 `reports/`（入庫）。
- Git 分支：`claude/taiwan-stock-analysis-asx-mww0s6`；Repo：ZDYoyoyo/stock。

## 八、付費 API 備忘（目前免費即可）
FinMind Sponsor（更高額度+分點，月費數百元）／永豐 Shioaji（開戶免費、即時+下單）／
富果 Fugle（即時行情）／TEJ（法人級、偏貴）。
