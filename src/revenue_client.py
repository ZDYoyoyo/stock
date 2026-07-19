"""月營收全市場資料源（上市 TWSE + 上櫃 TPEX 官方 OpenAPI，免金鑰）。

一次抓全市場最新月營收，含：當月營收、去年同月增減%(YoY)、上月增減%(MoM)、
累計營收與累計增減%(累計YoY)。用於 T12 月營收動能篩選。

端點：
  上市 https://openapi.twse.com.tw/v1/opendata/t187ap05_L
  上櫃 https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O
兩者欄位相同（中文鍵）。
"""
from __future__ import annotations

import requests

_TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _roc_ym(roc: str) -> str | None:
    """民國年月 11506 → '2026-06'。"""
    s = str(roc).strip()
    if len(s) < 5:
        return None
    try:
        y = int(s[:-2]) + 1911
        m = int(s[-2:])
        return f"{y}-{m:02d}"
    except ValueError:
        return None


def _parse(rows: list, market: str) -> list:
    out = []
    for x in rows:
        sid = str(x.get("公司代號", "")).strip()
        if len(sid) != 4 or not sid.isdigit():   # 只保留普通股（排除 ETF/權證/KY特殊碼）
            continue
        out.append({
            "stock_id": sid,
            "name": str(x.get("公司名稱", "")).strip(),
            "industry": str(x.get("產業別", "")).strip(),
            "ym": _roc_ym(x.get("資料年月")),
            "revenue": _to_float(x.get("營業收入-當月營收")),
            "yoy": _to_float(x.get("營業收入-去年同月增減(%)")),
            "mom": _to_float(x.get("營業收入-上月比較增減(%)")),
            "cum_yoy": _to_float(x.get("累計營業收入-前期比較增減(%)")),
            "market": market,
        })
    return out


def _get(url: str, retries: int = 3) -> list:
    for i in range(retries):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=30, verify=True)
            if r.status_code == 200 and r.text.strip().startswith("["):
                return r.json()
        except requests.RequestException:
            pass
    return []


def fetch_all() -> list:
    """回傳上市+上櫃全市場最新月營收（已解析、只留普通股）。"""
    rows = _parse(_get(_TWSE_URL), "twse") + _parse(_get(_TPEX_URL), "tpex")
    return rows
