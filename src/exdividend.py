"""除權息預告（免費 TWSE + TPEX OpenAPI）— 事件風控欄。

為什麼要看：除權息當天股價會**扣掉股利**開盤（參考價下修），看盤軟體上像「大跌」
但其實沒虧；反之若不知情，容易把技術面訊號誤判（跌破均線/停損被觸發）。
短線更要注意：除息前後常有搶息/棄息賣壓。

資料源（各 1 call/日、免費）：
  - TWSE 上市除權除息預告表：openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL
  - TPEX 上櫃除權息預告：tpex.org.tw/openapi/v1/tpex_exright_prepost
⚠️ 日期皆民國（1150812）。抓不到一律 graceful 回空，不讓日報掛掉。
"""
from __future__ import annotations

import re
from datetime import date

import requests

_TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
_TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
_TIMEOUT = 25


def _roc_to_iso(s) -> str | None:
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) != 7:
        return None
    try:
        return date(int(d[:3]) + 1911, int(d[3:5]), int(d[5:7])).isoformat()
    except ValueError:
        return None


def _num(v):
    try:
        f = float(str(v).replace(",", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _get(url: str) -> list:
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_events() -> dict:
    """{stock_id: {除權息日, 現金股利, 股票股利, 類型}}；同檔取最近一次（日期最小）。"""
    out: dict[str, dict] = {}

    def put(sid, rec):
        sid = str(sid or "").strip()
        if not sid or not sid.isdigit() or not rec.get("除權息日"):
            return
        old = out.get(sid)
        if old is None or rec["除權息日"] < old["除權息日"]:
            out[sid] = rec

    for d in _get(_TWSE):
        put(d.get("Code"), {"除權息日": _roc_to_iso(d.get("Date")),
                            "現金股利": _num(d.get("CashDividend")),
                            "股票股利": _num(d.get("StockDividendRatio")),
                            "類型": (d.get("Exdividend") or "").strip()})
    for d in _get(_TPEX):
        put(d.get("SecuritiesCompanyCode"), {
            "除權息日": _roc_to_iso(d.get("ExRrightsExDividendDate")),
            "現金股利": _num(d.get("CashDividend")),
            "股票股利": _num(d.get("StockDividendRatio")),
            "類型": (d.get("ExRrightsExDividend") or "").strip()})
    return out


def _label(rec: dict, today: str) -> str | None:
    """→「📅除息08-14(配2.5)」；已過的不標。當天標『今日除息』。"""
    d = rec.get("除權息日")
    if not d or d < today:
        return None
    kind = "除權息" if (rec.get("現金股利") and rec.get("股票股利")) else (
        "除權" if rec.get("股票股利") else "除息")
    amt = rec.get("現金股利") or rec.get("股票股利")
    amt_s = f"(配{amt:g})" if amt else ""
    if d == today:
        return f"📅今日{kind}{amt_s}"
    return f"📅{kind}{d[5:]}{amt_s}"


def compute(today: str | None = None, within_days: int = 45) -> dict:
    """{stock_id: 除權息標籤}；只留今天起 within_days 內的（太遠沒行動意義）。"""
    from datetime import datetime, timedelta
    today = today or date.today().isoformat()
    try:
        limit = (datetime.fromisoformat(today) + timedelta(days=within_days)).date().isoformat()
    except ValueError:
        limit = "9999-12-31"
    out = {}
    for sid, rec in fetch_events().items():
        if rec.get("除權息日", "") > limit:
            continue
        lb = _label(rec, today)
        if lb:
            out[sid] = lb
    return out


def enrich(df, ex_map: dict | None = None, col: str = "除權息"):
    """把除權息欄併進 df（依 stock_id）。無事件留空字串。"""
    if df is None or getattr(df, "empty", True) or "stock_id" not in df.columns:
        return df
    m = ex_map if ex_map is not None else compute()
    if not m:
        return df
    df = df.copy()
    df[col] = df["stock_id"].astype(str).map(lambda s: m.get(s, ""))
    return df
