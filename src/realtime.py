"""TWSE MIS 即時報價 client（免開戶、免費、約 20 秒延遲）。

盤中(09:00~13:30)才有即時資料；盤後回傳最後一筆快照。
上市用 tse_ 前綴、上櫃用 otc_。
"""
import requests

from .db import connect

_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/"}


def _market_map() -> dict:
    try:
        with connect() as conn:
            import pandas as pd
            info = pd.read_sql("SELECT stock_id, type FROM stock_info", conn)
        return dict(zip(info["stock_id"], info["type"]))
    except Exception:
        return {}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def quote(stock_ids, market_map=None) -> list[dict]:
    """回傳每檔即時報價 dict。stock_ids: list[str]。"""
    mm = market_map if market_map is not None else _market_map()
    chans = []
    for sid in stock_ids:
        prefix = "otc_" if mm.get(sid) == "tpex" else "tse_"
        chans.append(f"{prefix}{sid}.tw")
    try:
        r = requests.get(_URL, params={"ex_ch": "|".join(chans), "json": "1", "delay": "0"},
                         headers=_HEADERS, timeout=20)
        arr = r.json().get("msgArray", [])
    except (requests.RequestException, ValueError):
        return []

    out = []
    for s in arr:
        z, y = _num(s.get("z")), _num(s.get("y"))
        if z is None:  # 尚無成交，用最佳買價暫代
            z = _num((s.get("b") or "").split("_")[0])
        chg = round((z - y) / y * 100, 2) if (z and y) else None
        bid = _num((s.get("b") or "").split("_")[0])   # 最佳買價
        ask = _num((s.get("a") or "").split("_")[0])   # 最佳賣價
        out.append({
            "stock_id": s.get("c"), "name": s.get("n"),
            "price": z, "chg_%": chg,
            "open": _num(s.get("o")), "high": _num(s.get("h")),
            "low": _num(s.get("l")), "prev_close": y,
            "limit_up": _num(s.get("u")), "limit_down": _num(s.get("w")),
            "volume": _num(s.get("v")), "bid": bid, "ask": ask,
            "time": s.get("t"),
        })
    return out
