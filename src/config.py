"""全域設定與 T11 篩選門檻（可自行調整）。"""
from pathlib import Path

# --- 路徑 ---
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "stock.db"
OUTPUT_DIR = ROOT / "reports" / "screener"

# --- FinMind ---
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# --- T11 法人默默吸貨 篩選門檻（依 2026-07 真實全市場分佈校準）---
class T11:
    LOOKBACK_DAYS = 10          # 觀察最近幾個交易日
    ALLOWED_MISSING_DAYS = 2    # 容許窗口內偶發缺日（抓取失敗/資料未齊）
    MIN_CONSECUTIVE_BUY = 4     # 投信(或外資)最少連續買超天數
    MAX_PRICE_GAIN = 0.12       # 期間股價漲幅上限（還沒噴出）
    MIN_PRICE_GAIN = -0.08      # 跌太多=接刀/套牢，排除（回測證實「抗跌」有效，故收緊）
    MIN_BUY_RATIO = 0.05        # 法人累計買超張數 / 期間總成交張數 下限（實測中位約4.4%）
    MAX_MARGIN_INCREASE = 0.15  # 融資餘額增幅上限（籌碼乾淨，散戶未追）
    MAX_ABOVE_MA20 = 0.15       # 收盤高於 20 日均線超過此比例＝已噴出，排除（單邊）
    MIN_AVG_VOLUME = 500        # 上市日均量下限（張）
    MIN_AVG_VOLUME_TPEX = 1000  # 上櫃加嚴：流動性差、易控盤 → 門檻拉高
    USE_INVESTOR = "Investment_Trust"  # 上市主看投信；可改 "Foreign_Investor"
    TPEX_USE_FOREIGN = True     # 上櫃改看外資（投信少碰上櫃，外資才是主要法人力量）


# --- 長期持有軌：價值+成長+配息 門檻 ---
class LONGTERM:
    MIN_YIELD = 3.0            # 現金殖利率下限(%)
    MAX_PER = 20.0             # 本益比上限（>0 排除虧損）
    MAX_PBR = 3.0              # 股價淨值比上限
    MIN_DIVIDEND_YEARS = 5     # 硬門檻：至少連續配發現金股利年數
    MIN_ROE_EST = 5.0          # 硬門檻：估算 ROE(=PBR/PER) 下限(%)，濾獲利能力太弱者
    DEEP_DIVE_N = 40           # 粗篩後進 FinMind 深掘的檔數上限
    # 營收認列不規律的產業（如營建：完工才認列，YoY 爆高爆低失真）
    # → 評分時 YoY 貢獻歸零，並在輸出標註產業
    LUMPY_REVENUE_INDUSTRIES = ("建材營造",)


# --- 當沖候選（EOD 掃描，非即時）門檻 ---
class DAYTRADE:
    LOOKBACK_DAYS = 20
    MIN_AVG_VOLUME = 3000      # 上市日均量下限(張)：當沖需高流動性
    MIN_AVG_VOLUME_TPEX = 2000 # 上櫃
    MIN_AVG_AMPLITUDE = 3.0    # 平均日振幅下限(%)：要夠波動才有價差可做
    VOL_SURGE = 1.5            # 當日量 / 20日均量 倍數（爆量門檻，用於加分非硬篩）


# --- T16 相對強弱／抗跌強勢股 門檻 ---
class T16:
    LOOKBACK_DAYS = 10
    MIN_RETURN = 0.0            # 至少正報酬（抗跌但續跌者不取）
    MIN_AVG_VOLUME = 500        # 上市日均量下限（張）
    MIN_AVG_VOLUME_TPEX = 1000  # 上櫃加嚴
