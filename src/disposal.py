"""處置股／注意股警示（免費 TWSE + TPEX OpenAPI）— 買賣前的硬風控。

為什麼重要：處置股會改成「人工管制撮合」（約每 2~5 分鐘才撮合一次＝分盤交易），
且常要求預收款券。當沖/短線在分盤標的上幾乎做不動（掛單成交不確定、滑價大），
**必須先避開**。這支把「今天正在處置／即將處置」標成一欄，五軌報告直接紅字警示。

資料源（皆免費、每日各 1 call）：
  - TWSE 處置：https://openapi.twse.com.tw/v1/announcement/punish
  - TWSE 注意：https://openapi.twse.com.tw/v1/announcement/notice
  - TPEX 處置：https://www.tpex.org.tw/openapi/v1/tpex_disposal_information
⚠️ 日期皆為民國：TWSE 期間「115/08/10～115/08/14」、TPEX 期間「1150813~1150821」。
抓不到（斷網/改版）一律 graceful 回空，不讓日報掛掉。
"""
from __future__ import annotations

import re
from datetime import date

import requests

_TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"
_TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"
_TPEX_DISPOSAL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
_TIMEOUT = 25


def _roc_to_iso(s: str) -> str | None:
    """民國日期 → ISO。吃「115/08/10」「1150810」「115-08-10」三種；失敗回 None。"""
    if not s:
        return None
    digits = re.sub(r"\D", "", str(s))
    if len(digits) == 7:                       # 1150810
        y, m, d = int(digits[:3]), int(digits[3:5]), int(digits[5:7])
    elif len(digits) == 6:                     # 罕見 3+1+2 不足位，保守放棄
        return None
    else:
        return None
    try:
        return date(y + 1911, m, d).isoformat()
    except ValueError:
        return None


def _period(s: str) -> tuple[str | None, str | None]:
    """處置期間字串 → (起ISO, 迄ISO)。分隔符可能是 ～ ~ 或 －。"""
    if not s:
        return None, None
    parts = re.split(r"[～~\-—－]", str(s).strip())
    parts = [p for p in parts if re.search(r"\d", p)]
    if len(parts) >= 2:
        return _roc_to_iso(parts[0]), _roc_to_iso(parts[-1])
    if len(parts) == 1:
        one = _roc_to_iso(parts[0])
        return one, one
    return None, None


def _get(url: str) -> list:
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []                              # 斷網/改版/JSON 壞掉 → 靜默略過


def fetch_disposals() -> dict:
    """回傳 {stock_id: {market, 原因, 起, 迄, 措施, 類型}}；同檔多筆取迄日最晚者。"""
    out: dict[str, dict] = {}

    def put(sid, rec):
        sid = str(sid).strip()
        if not sid or not sid.isdigit():
            return
        old = out.get(sid)
        if old is None or (rec.get("迄") or "") > (old.get("迄") or ""):
            out[sid] = rec

    for d in _get(_TWSE_PUNISH):
        st, ed = _period(d.get("DispositionPeriod"))
        put(d.get("Code"), {"market": "上市", "原因": (d.get("ReasonsOfDisposition") or "").strip(),
                            "起": st, "迄": ed,
                            "措施": (d.get("DispositionMeasures") or "").strip(), "類型": "處置"})
    for d in _get(_TPEX_DISPOSAL):
        st, ed = _period(d.get("DispositionPeriod"))
        put(d.get("SecuritiesCompanyCode"), {
            "market": "上櫃", "原因": (d.get("DispositionReasons") or "").strip(),
            "起": st, "迄": ed, "措施": "", "類型": "處置"})
    return out


def fetch_notices() -> dict:
    """注意股（TWSE，當日公布）→ {stock_id: 注意原因}。空資料/失敗回 {}。"""
    out = {}
    for d in _get(_TWSE_NOTICE):
        sid = str(d.get("Code") or "").strip()
        if sid and sid.isdigit():
            out[sid] = (d.get("TradingInfoForAttention") or "注意股").strip() or "注意股"
    return out


def _label(rec: dict, today: str) -> str | None:
    """依今天位置給標籤：處置中🚫／即將處置⚠️／已結束(不標)。"""
    st, ed = rec.get("起"), rec.get("迄")
    if ed and ed < today:
        return None                            # 已結束
    tail = f"至{ed[5:]}" if ed else ""
    if st and st > today:
        return f"⚠️將處置({st[5:]}起{('·' + tail) if tail else ''})"
    return f"🚫處置中({tail}·分盤)" if tail else "🚫處置中(分盤)"


def compute(today: str | None = None) -> dict:
    """{stock_id: 警示標籤}。處置優先於注意；已結束的處置不標。抓不到回 {}。"""
    today = today or date.today().isoformat()
    out = {}
    for sid, rec in fetch_disposals().items():
        lb = _label(rec, today)
        if lb:
            out[sid] = lb
    for sid, why in fetch_notices().items():
        out.setdefault(sid, f"⚠️注意股({why[:12]})" if why != "注意股" else "⚠️注意股")
    return out


def enrich(df, warn_map: dict | None = None, col: str = "處置警示"):
    """把警示欄併進 df（依 stock_id）。無警示留空字串（不用「—」，避免整欄雜訊）。"""
    if df is None or getattr(df, "empty", True) or "stock_id" not in df.columns:
        return df
    m = warn_map if warn_map is not None else compute()
    if not m:
        return df
    df = df.copy()
    df[col] = df["stock_id"].astype(str).map(lambda s: m.get(s, ""))
    return df
