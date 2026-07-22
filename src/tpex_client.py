"""TPEX（櫃買中心/上櫃）官方端點 client（免費、全市場、逐日）。

新版 TPEX 端點（www/zh-tw/...），AD 日期 YYYY/MM/DD，回傳 tables 結構。
注意：TPEX 大回應用 curl 會截斷，須用 requests。
欄位對應已用 FinMind 逐檔交叉驗證：外資=col4、投信=col13、自營合計=col22。
"""
import re
import time

import requests

_BASE = "https://www.tpex.org.tw/www/zh-tw"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json,*/*",
    "Referer": "https://www.tpex.org.tw/",
}
_STOCK_RE = re.compile(r"^[1-9]\d{3}$")  # 只留 4 位數普通股，排除 ETF/債券ETF/權證


def _num(s):
    if s is None:
        return 0
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "-", "X", "N/A", "----"):
        return 0
    try:
        return float(s)
    except ValueError:
        return 0


def _ad(ymd: str) -> str:
    """'YYYYMMDD' -> 'YYYY/MM/DD'。"""
    return f"{ymd[:4]}/{ymd[4:6]}/{ymd[6:]}"


def _get(path: str, params: dict, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            r = requests.get(f"{_BASE}/{path}", params=params, headers=_HEADERS, timeout=60)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (i + 1))
    return {}


def _first_table(d: dict) -> dict:
    tables = d.get("tables") or []
    return tables[0] if tables else {}


def price(date: str) -> list[dict]:
    """回傳 [{date,stock_id,open,high,low,close,volume(張)}]。假日回空。"""
    d = _get("afterTrading/dailyQuotes", {"date": _ad(date), "type": "EW", "id": "", "response": "json"})
    t = _first_table(d)
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    out = []
    for row in t.get("data", []):
        sid = row[0].strip()
        if not _STOCK_RE.match(sid):
            continue
        # 代號0 名稱1 收盤2 漲跌3 開盤4 最高5 最低6 均價7 成交股數8
        out.append({
            "date": iso, "stock_id": sid,
            "open": _num(row[4]), "high": _num(row[5]),
            "low": _num(row[6]), "close": _num(row[2]),
            "volume": int(_num(row[8]) / 1000),
        })
    return out


def institutional(date: str) -> list[dict]:
    """回傳 [{date,stock_id,foreign_net,trust_net,dealer_net}]（張）。"""
    d = _get("insti/dailyTrade", {"date": _ad(date), "type": "Daily", "id": "", "response": "json"})
    t = _first_table(d)
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    out = []
    for row in t.get("data", []):
        sid = row[0].strip()
        if not _STOCK_RE.match(sid) or len(row) < 23:
            continue
        out.append({
            "date": iso, "stock_id": sid,
            "foreign_net": int(_num(row[4]) / 1000),   # 外資及陸資(不含外資自營)
            "trust_net": int(_num(row[13]) / 1000),     # 投信
            "dealer_net": int(_num(row[22]) / 1000),    # 自營商合計
        })
    return out


def margin(date: str) -> list[dict]:
    """回傳 [{date,stock_id,margin_balance(張)}]。"""
    d = _get("margin/balance", {"date": _ad(date), "response": "json"})
    t = _first_table(d)
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    out = []
    for row in t.get("data", []):
        sid = row[0].strip()
        if not _STOCK_RE.match(sid):
            continue
        # 代號0 名稱1 前資餘額2 資買3 資賣4 現償5 資餘額6 … 券餘額14
        out.append({"date": iso, "stock_id": sid,
                    "margin_balance": int(_num(row[6])),
                    "short_balance": int(_num(row[14])) if len(row) > 14 else None})
    return out


def stock_names(date: str) -> dict[str, str]:
    d = _get("afterTrading/dailyQuotes", {"date": _ad(date), "type": "EW", "id": "", "response": "json"})
    t = _first_table(d)
    out = {}
    for row in t.get("data", []):
        sid = row[0].strip()
        if _STOCK_RE.match(sid):
            out[sid] = row[1].strip()
    return out
