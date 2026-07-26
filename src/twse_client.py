"""TWSE 官方 RWD 端點 client（免費、全市場、逐日）。

免費取得上市全市場資料：
  - 個股 OHLCV：MI_INDEX (type=ALLBUT0999)
  - 三大法人買賣超：T86
  - 融資餘額：MI_MARGN
僅限上市(TWSE)；上櫃需另接 TPEX（本版先做上市，已涵蓋日月光等權值股）。
"""
import re
import time

import requests

_BASE = "https://www.twse.com.tw/rwd/zh"
_HEADERS = {"User-Agent": "Mozilla/5.0 (research; stock-screener)"}
_STOCK_RE = re.compile(r"^[1-9]\d{3}$")  # 只留 4 位數普通股，排除 ETF/權證/TDR


def _num(s):
    if s is None:
        return 0
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "-", "X", "N/A"):
        return 0
    try:
        return float(s)
    except ValueError:
        return 0


def _get(path: str, params: dict, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            r = requests.get(f"{_BASE}/{path}", params=params, headers=_HEADERS, timeout=60)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 * (i + 1))
    return {}


def price(date: str) -> list[dict]:
    """date='YYYYMMDD'。回傳 [{date,stock_id,open,high,low,close,volume(張)}]。假日回空。"""
    d = _get("afterTrading/MI_INDEX", {"date": date, "type": "ALLBUT0999", "response": "json"})
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    for t in d.get("tables", []):
        fields = t.get("fields") or []
        if "收盤價" in fields and "證券代號" in fields:
            idx = {name: fields.index(name) for name in
                   ["證券代號", "成交股數", "開盤價", "最高價", "最低價", "收盤價"]}
            out = []
            for row in t.get("data", []):
                sid = row[idx["證券代號"]].strip()
                if not _STOCK_RE.match(sid):
                    continue
                out.append({
                    "date": iso, "stock_id": sid,
                    "open": _num(row[idx["開盤價"]]), "high": _num(row[idx["最高價"]]),
                    "low": _num(row[idx["最低價"]]), "close": _num(row[idx["收盤價"]]),
                    "volume": int(_num(row[idx["成交股數"]]) / 1000),
                })
            return out
    return []


def valuation(date: str) -> list[dict]:
    """全市場估值：回傳 [{stock_id,name,close,yield,per,pbr}]（BWIBBU_d，免費）。"""
    d = _get("afterTrading/BWIBBU_d", {"date": date, "selectType": "ALL", "response": "json"})
    if d.get("stat") != "OK":
        return []
    fields = d.get("fields") or []
    try:
        i_sid, i_name = fields.index("證券代號"), fields.index("證券名稱")
        i_close, i_yield = fields.index("收盤價"), fields.index("殖利率(%)")
        i_per, i_pbr = fields.index("本益比"), fields.index("股價淨值比")
    except ValueError:
        return []
    out = []
    for row in d.get("data", []):
        sid = row[i_sid].strip()
        if not _STOCK_RE.match(sid):
            continue
        out.append({
            "stock_id": sid, "name": row[i_name].strip(),
            "close": _num(row[i_close]), "yield": _num(row[i_yield]),
            "per": _num(row[i_per]), "pbr": _num(row[i_pbr]),
        })
    return out


def stock_names(date: str) -> dict[str, str]:
    """回傳 {stock_id: 中文名}（取自當日 MI_INDEX）。"""
    d = _get("afterTrading/MI_INDEX", {"date": date, "type": "ALLBUT0999", "response": "json"})
    for t in d.get("tables", []):
        fields = t.get("fields") or []
        if "收盤價" in fields and "證券名稱" in fields:
            i_sid, i_name = fields.index("證券代號"), fields.index("證券名稱")
            out = {}
            for row in t.get("data", []):
                sid = row[i_sid].strip()
                if _STOCK_RE.match(sid):
                    out[sid] = row[i_name].strip()
            return out
    return {}


def institutional(date: str) -> list[dict]:
    """回傳 [{date,stock_id,foreign_net,trust_net,dealer_net}]（張）。當日未公布回空。"""
    d = _get("fund/T86", {"date": date, "selectType": "ALLBUT0999", "response": "json"})
    if d.get("stat") != "OK":
        return []
    fields = d.get("fields") or []
    try:
        i_sid = fields.index("證券代號")
        i_foreign = fields.index("外陸資買賣超股數(不含外資自營商)")
        i_trust = fields.index("投信買賣超股數")
        i_dealer = fields.index("自營商買賣超股數")
    except ValueError:
        return []
    # 外資＝外陸資(不含外資自營商)＋外資自營商，才等於官方「三大法人合計」定義。
    # （外資自營商欄近年多為 0，但仍併入以保證 外資+投信+自營=合計 恆等）
    i_fdealer = fields.index("外資自營商買賣超股數") if "外資自營商買賣超股數" in fields else None
    i_total = fields.index("三大法人買賣超股數") if "三大法人買賣超股數" in fields else None
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    out = []
    for row in d.get("data", []):
        sid = row[i_sid].strip()
        if not _STOCK_RE.match(sid):
            continue
        foreign = _num(row[i_foreign]) + (_num(row[i_fdealer]) if i_fdealer is not None else 0)
        # total_net 取官方「三大法人買賣超股數」合計欄（整段股數先加總再/1000，才對得起券商）
        total_shares = (_num(row[i_total]) if i_total is not None
                        else foreign + _num(row[i_trust]) + _num(row[i_dealer]))
        out.append({
            "date": iso, "stock_id": sid,
            "foreign_net": int(foreign / 1000),
            "trust_net": int(_num(row[i_trust]) / 1000),
            "dealer_net": int(_num(row[i_dealer]) / 1000),
            "total_net": int(total_shares / 1000),
        })
    return out


def margin(date: str) -> list[dict]:
    """回傳 [{date,stock_id,margin_balance(張)}]。"""
    d = _get("marginTrading/MI_MARGN", {"date": date, "selectType": "ALL", "response": "json"})
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    for t in d.get("tables", []):
        fields = t.get("fields") or []
        # 融資融券段：…,融資今日餘額(idx6),…,融券今日餘額(idx12),…（兩個「今日餘額」）
        if fields[:2] == ["代號", "名稱"] and "今日餘額" in fields:
            bal_idx = [i for i, x in enumerate(fields) if x == "今日餘額"]
            i_margin = bal_idx[0]                      # 第一個＝融資
            i_short = bal_idx[1] if len(bal_idx) > 1 else None  # 第二個＝融券
            out = []
            for row in t.get("data", []):
                sid = row[0].strip()
                if not _STOCK_RE.match(sid):
                    continue
                out.append({"date": iso, "stock_id": sid,
                            "margin_balance": int(_num(row[i_margin])),
                            "short_balance": int(_num(row[i_short])) if i_short is not None else None})
            return out
    return []
