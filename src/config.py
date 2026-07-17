"""全域設定與 T11 篩選門檻（可自行調整）。"""
from pathlib import Path

# --- 路徑 ---
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "stock.db"
OUTPUT_DIR = ROOT / "reports" / "screener"

# --- FinMind ---
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# --- T11 法人默默吸貨 篩選門檻 ---
class T11:
    LOOKBACK_DAYS = 10          # 觀察最近幾個交易日
    MIN_CONSECUTIVE_BUY = 5     # 投信(或外資)最少連續買超天數
    MAX_PRICE_GAIN = 0.10       # 期間股價漲幅上限（<10% 才算「還沒噴」）
    MIN_BUY_RATIO = 0.15        # 法人累計買超張數 / 期間總成交張數 下限
    MAX_MARGIN_INCREASE = 0.10  # 融資餘額增幅上限（籌碼乾淨，散戶未追）
    MA_NEAR_PCT = 0.08          # 收盤距 20 日均線 ±% 內視為「均線附近打底」
    MIN_AVG_VOLUME = 500        # 期間日均量下限（張），濾掉冷門股
    USE_INVESTOR = "Investment_Trust"  # 主看投信；可改 "Foreign_Investor"
