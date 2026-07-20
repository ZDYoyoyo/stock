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
    MAX_LUMPY = 3              # 營建等認列不規律產業，最終清單最多保留幾檔（避免洗版）
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


# --- T12 月營收動能股（全市場官方月營收 OpenAPI）門檻 ---
class T12:
    MIN_YOY = 15.0            # 最新月營收 YoY 下限(%)
    MAX_YOY = 300.0          # YoY 上限：>此值多為基期效應(去年同月近0)雜訊，排除
    MIN_CUM_YOY = 5.0         # 累計營收 YoY 下限(%)：避免單月一次性暴衝
    MAX_CUM_YOY = 200.0      # 累計 YoY 上限：同樣濾基期爆表
    MIN_ACCEL = 0.0          # 加速度=單月YoY−累計YoY，>此值代表近月動能加速
    REQUIRE_ACCEL = True      # 是否硬性要求加速（False 則只當加分）
    MIN_CLOSE = 10.0          # 股價下限，濾雞蛋水餃股
    MIN_AVG_VOLUME = 300      # 日均量下限(張)：要有基本流動性
    ABOVE_MA20_BONUS = True   # 股價站上20MA（市場也認同）加分
    # 營收認列不規律產業（營建完工才認列，YoY 失真）→ 直接排除（不是動能股該用的濾網）
    EXCLUDE_INDUSTRIES = ("建材營造", "營建")
    SCORE_CAP_YOY = 150.0    # 評分時 YoY 貢獻上限，避免極端值洗版排序
    SCORE_CAP_CUM = 100.0
    SCORE_CAP_ACCEL = 100.0
    TOP_N = 25                # 輸出前幾檔


# --- 主力籌碼綜合（法人+千張大戶+融資；分點需 FinMind Sponsor 自動加值）---
class MAINFORCE:
    LOOKBACK_DAYS = 10        # 法人/融資觀察窗
    MIN_CLOSE = 10.0
    MIN_AVG_VOLUME = 500      # 日均量下限(張)
    W_INST = 2.0             # 法人佔量% 權重
    W_HOLDER = 3.0           # 千張大戶週增(pp) 權重（大戶最能代表主力）
    W_MARGIN = 1.0           # 散戶退場(融資減%) 權重
    TOP_N = 25
    BRANCH_ENRICH_N = 10      # 有分點時，對前幾檔加抓分點主力進出


# --- 地雷偵測（財務+籌碼+技術 紅旗）門檻 ---
class LANDMINE:
    # 財務面（FinMind 綜合損益表/月營收）
    REV_YOY_BAD = -20.0        # 近3月營收YoY均 低於此 → 營收動能崩塌
    REV_YOY_CRASH = -30.0      # 最新單月營收YoY 低於此 → 單月急墜
    EPS_TTM_DROP = -50.0       # 近四季EPS年增 低於此(%) → 獲利崩
    MARGIN_DROP_PP = 5.0       # 最新季毛利率 較去年同季 下滑超過幾個百分點 → 毛利惡化
    # 籌碼面（本地 DB）
    INST_SELL_DAYS = 4         # 法人(投信/外資)連續賣超天數 → 法人棄守
    BIG_HOLDER_DROP = 1.0      # 千張大戶比 週減超過幾個百分點 → 大戶出貨
    MARGIN_SURGE = 0.20        # 融資餘額期間增幅超過 → 散戶接刀
    # 技術面（本地 DB）
    MA_LONG = 60               # 季線天數，跌破=中期轉弱
    WEAK_VS_MARKET = -10.0     # 近20日報酬 減 大盤中位 低於此(%) → 相對弱勢
    LOOKBACK_DAYS = 20
    # 風險分級（紅旗加權總分）
    LEVEL_SEVERE = 6           # ≥ → 嚴重
    LEVEL_HIGH = 4             # ≥ → 高
    LEVEL_MID = 2              # ≥ → 中


# --- T16 相對強弱／抗跌強勢股 門檻 ---
class T16:
    LOOKBACK_DAYS = 10
    MIN_RETURN = 0.0            # 至少正報酬（抗跌但續跌者不取）
    MAX_RETURN = 0.30          # 漲幅上限(30%)：超過視為過度延伸/噴出，不宜追，排除
    MIN_AVG_VOLUME = 500        # 上市日均量下限（張）
    MIN_AVG_VOLUME_TPEX = 1000  # 上櫃加嚴
