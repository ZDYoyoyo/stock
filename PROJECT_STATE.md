# 專案交接 / 記憶檔 (PROJECT STATE)

> 給下一個 session 的接手說明。新 session 冷啟動，**先讀這份 + README.md** 即可接續。
> 這份就是本專案的「memory」，可在 GitHub 分支或本機直接開啟。
> 最後更新：2026-07-17

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
3. **當沖軌**：✅ **EOD 候選掃描完成**（day_trade_candidates.py，高波動+高流動+爆量）。
   → 盤後縮小盤中要盯的清單，使用者盤中人工判斷。
   → 即時/自動化仍需接資料+下單：免費免開戶 **TWSE MIS**（~20秒延遲，已測通，
   mis.twse.com.tw/stock/api/getStockInfo.jsp）做監控；**自動下單**必須透過券商 API
   （永豐 Shioaji / 富果 Fugle / 元大…任一家，非限永豐）。使用者決定後再接。

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
  enrich.py            # T30 基本面深掘 + 連續配息年數
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
scripts/
  update_data.py       # 抓資料進 DB（--market all/twse/tpex, --days N）
  daily_scan.py        # 每日盤後：環境紅綠燈+全球 + T11 + T16 + 交集
  run_longterm.py      # 長期軌選股
  risk_calc.py         # 風控試算（停損/停利/張數）
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

## ★ 回測關鍵發現（2026-07-17，很重要）
用 2023–2026、~36 檔代表股驗證（scripts/run_validation.py、run_edge_check.py）：

| 持有 | 基準(任意日進場) | 法人連買 | 組合(抗跌+營收) | 法人連買超額 |
|---|---|---|---|---|
| 10天 | +0.34% | +0.48% | +0.53% | +0.14% |
| 20天 | +2.29% | +2.01% | +2.58% | -0.29% |
| 40天 | +4.66% | +4.89% | +6.05% | +0.23% |

**結論：法人連買/組合訊號幾乎沒有穩定 alpha，報酬主要來自大盤漂移(beta)。**
→ 意義：**T11/T16/T30 當研究注意力清單，不是機械買賣訊號**；
   擇時(市場廣度)與風控可能比選股更關鍵；真 edge 需更嚴謹（分樣本外、含空頭期）。

## 六、下一步 (TODO)
1. **T23 完整回測**：需先**回補 1~2 年全市場歷史**（目前 DB 僅約 28 天）。
   已驗證：單靠「法人連買」期望值接近 0，需疊加基本面/抗跌條件才有 edge。
2. **回補歷史腳本**：TWSE/TPEX 逐日抓長區間（背景跑）。
3. 其他 backlog（未做，可挑）：
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
