"""券商分點資料（FinMind Sponsor 付費）— 有 token 且會員等級足夠才啟用。

免費版沒有分點：本模組會自動偵測，抓不到就回「不可用」，讓上層改用免費的
主力籌碼綜合（三大法人+千張大戶+融資）。之後辦了 FinMind Sponsor、把 token
填進 .env 的 FINMIND_TOKEN，這模組就會自動啟用、多出分點欄位，不需改任何程式。

主力分點指標（標準做法）：把當日各分點的買賣超淨額(張)排序，
取「前 N 大買超分點淨額合計」= 主力買超；「前 N 大賣超」= 主力賣超。
淨額越集中在少數分點 = 主力（大戶/特定券商）進出越明顯。
"""
from __future__ import annotations

import os

import requests

from .config import FINMIND_URL

_TOKEN = os.getenv("FINMIND_TOKEN", "")
_DATASET = "TaiwanStockTradingDailyReport"   # Sponsor 付費：券商分點日報
_TOP_BRANCHES = 15                            # 主力=前幾大分點
_available_cache: bool | None = None


def _raw(sid: str, date: str) -> list | None:
    """抓單檔單日分點。回傳 list；若付費牆/無資料回 None。"""
    params = {"dataset": _DATASET, "data_id": sid, "start_date": date, "end_date": date}
    if _TOKEN:
        params["token"] = _TOKEN
    try:
        r = requests.get(FINMIND_URL, params=params, timeout=60)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None                            # 400=免費等級不足 / 其他
    payload = r.json()
    if payload.get("status") != 200:
        return None
    return payload.get("data", [])


def available(probe_sid: str = "2330", probe_date: str = "2026-07-17") -> bool:
    """偵測分點是否可用（有 token 且會員等級足夠）。結果快取。"""
    global _available_cache
    if _available_cache is not None:
        return _available_cache
    if not _TOKEN:
        _available_cache = False
        return False
    data = _raw(probe_sid, probe_date)
    _available_cache = bool(data)
    return _available_cache


def _net_by_branch(rows: list) -> dict:
    """各分點淨買超(張)。FinMind buy/sell 單位為股 → /1000 轉張。"""
    net: dict[str, float] = {}
    for d in rows:
        name = d.get("securities_trader") or d.get("securities_trader_id") or "?"
        buy = (d.get("buy") or 0) / 1000
        sell = (d.get("sell") or 0) / 1000
        net[name] = net.get(name, 0) + (buy - sell)
    return net


def branch_summary(sid: str, date: str) -> dict:
    """單檔單日主力分點摘要。不可用/無資料回 {}。

    回傳：主力買超(前15大買超分點淨額合計，張)、主力賣超、主力淨額、買超/賣超家數。
    """
    if not available():
        return {}
    rows = _raw(sid, date)
    if not rows:
        return {}
    net = _net_by_branch(rows)
    vals = sorted(net.values(), reverse=True)
    top_buy = sum(v for v in vals[:_TOP_BRANCHES] if v > 0)
    top_sell = sum(v for v in vals[-_TOP_BRANCHES:] if v < 0)
    return {
        "主力買超張": round(top_buy),
        "主力賣超張": round(top_sell),
        "主力淨額張": round(top_buy + top_sell),
        "買超分點數": sum(1 for v in net.values() if v > 0),
        "賣超分點數": sum(1 for v in net.values() if v < 0),
    }
