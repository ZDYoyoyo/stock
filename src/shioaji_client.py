"""永豐金 Shioaji 即時行情 client（選配升級）— 有裝套件且填了金鑰才啟用。

定位：比免費的 TWSE MIS（src/realtime.py，約 20 秒延遲）更快更準的盤中即時報價。
本模組**只用「行情」功能，完全不碰下單/帳務**，所以申請與設定都是最輕量的一條：
  - 只需 API Key / Secret（登入驗證）即可，**不需要電子憑證、不需簽下單同意書**。
  - 永豐金申請時開「**正式環境 + 行情**」就夠，「下單」不用開。

啟用方式（三步，之後全自動）：
  1. pip install shioaji
  2. 在 .env（本機）或環境變數（雲端）填：
       SHIOAJI_API_KEY=...
       SHIOAJI_SECRET_KEY=...
  3. 完成後 available() 自動回 True，src.realtime.quote() 會自動改用 Shioaji。
  沒安裝或沒金鑰 → available()=False，系統自動退回 TWSE MIS，**不需改任何程式**。

限制（永豐金官方）：訂閱上限 200 檔、每身分證最多 5 連線、每 24h 需重新登入；
行情流量額度依「下單量」而定 → 適合盯**小清單**（持股＋當沖候選），別大量抓歷史。

⚠️ 欄位對應依 Shioaji 官方 snapshot 文件撰寫，尚未對真實帳戶實測；取得金鑰後
   請跑一次 `python -m src.shioaji_client` 核對數值（任何錯誤都會自動退回 MIS）。
"""
from __future__ import annotations

import atexit
import os

from dotenv import load_dotenv

load_dotenv()  # 讀取專案根目錄的 .env（否則獨立執行本模組時抓不到金鑰）
_API_KEY = os.getenv("SHIOAJI_API_KEY", "")
_SECRET_KEY = os.getenv("SHIOAJI_SECRET_KEY", "")

_api = None            # 已登入的 Shioaji 實例（模組級單例，避免重複登入撞連線上限）
_login_failed = False  # 登入/匯入失敗過就不再重試，避免每次呼叫都卡住


def _get_api():
    """取得已登入的 Shioaji 實例；沒裝套件／沒金鑰／登入失敗回 None。"""
    global _api, _login_failed
    if _api is not None:
        return _api
    if _login_failed or not (_API_KEY and _SECRET_KEY):
        return None
    try:
        import shioaji as sj
    except ImportError:
        _login_failed = True
        return None
    try:
        api = sj.Shioaji()  # 正式環境（行情）
        api.login(api_key=_API_KEY, secret_key=_SECRET_KEY)
        _api = api
        return _api
    except Exception:
        _login_failed = True
        return None


def available() -> bool:
    """Shioaji 行情是否可用（有裝套件＋金鑰＋登入成功）。"""
    return _get_api() is not None


def diagnose() -> str:
    """回傳人看得懂的狀態字串，用於排錯（區分：沒金鑰／沒套件／登入失敗）。"""
    if not _API_KEY or not _SECRET_KEY:
        miss = [n for n, v in [("SHIOAJI_API_KEY", _API_KEY),
                               ("SHIOAJI_SECRET_KEY", _SECRET_KEY)] if not v]
        return ("❌ 金鑰未讀到：" + "、".join(miss) +
                "\n   檢查：①.env 是否在執行目錄 ②存成 UTF-8（非 ANSI）③等號後無空格/引號"
                " ④或用系統環境變數則需重開終端機")
    try:
        import shioaji as sj  # noqa: F401
    except ImportError:
        return "❌ 已讀到金鑰，但沒裝 shioaji 套件 → 用同一個 Python 跑：python -m pip install shioaji"
    try:
        api = sj.Shioaji()
        api.login(api_key=_API_KEY, secret_key=_SECRET_KEY)
        global _api
        _api = api
        return "✅ 金鑰＋套件＋登入都成功"
    except Exception as e:
        return (f"❌ 金鑰與套件都在，但登入失敗：{type(e).__name__}: {e}"
                "\n   多半是：API 尚未審核通過 / 金鑰打錯 / 帳戶未開通行情")


def _contract(api, sid: str):
    """依股票代號取合約（Shioaji 自動判斷上市/上櫃）。找不到回 None。"""
    try:
        return api.Contracts.Stocks[sid]
    except Exception:
        return None


def quote(stock_ids, market_map=None) -> list[dict]:
    """回傳與 src.realtime.quote 相同格式的即時報價 list。不可用/失敗回 []。

    market_map 參數僅為與 realtime.quote 介面相容（Shioaji 自動判斷市場，此處不用）。
    任何例外都回 []，讓上層 dispatcher 自動退回 TWSE MIS。
    """
    api = _get_api()
    if api is None:
        return []
    pairs = [(s, _contract(api, s)) for s in stock_ids]
    pairs = [(s, c) for s, c in pairs if c is not None]
    if not pairs:
        return []
    try:
        snaps = api.snapshots([c for _, c in pairs])
    except Exception:
        return []

    out = []
    for (sid, ct), sn in zip(pairs, snaps):
        close = getattr(sn, "close", None)
        chg_price = getattr(sn, "change_price", None)
        prev = round(close - chg_price, 2) if (close is not None and chg_price is not None) else None
        out.append({
            "stock_id": getattr(sn, "code", sid),
            "name": getattr(ct, "name", None),
            "price": close,
            "chg_%": getattr(sn, "change_rate", None),
            "open": getattr(sn, "open", None),
            "high": getattr(sn, "high", None),
            "low": getattr(sn, "low", None),
            "prev_close": prev,
            "limit_up": getattr(ct, "limit_up", None),
            "limit_down": getattr(ct, "limit_down", None),
            "volume": getattr(sn, "total_volume", None),
            "bid": getattr(sn, "buy_price", None),
            "ask": getattr(sn, "sell_price", None),
            "time": getattr(sn, "ts", None),
        })
    return out


@atexit.register
def _logout():
    """程式結束時登出，釋放連線（永豐金限每身分證最多 5 連線）。"""
    global _api
    if _api is not None:
        try:
            _api.logout()
        except Exception:
            pass
        _api = None


if __name__ == "__main__":
    # 取得金鑰後跑這個核對：python -m src.shioaji_client
    status = diagnose()
    print(status)
    if status.startswith("✅"):
        print("測試 2330/2317/6243：")
        for q in quote(["2330", "2317", "6243"]):
            print(f"  {q['stock_id']} {q['name']}　現價 {q['price']}　"
                  f"漲跌 {q['chg_%']}%　量 {q['volume']}　買/賣 {q['bid']}/{q['ask']}")
