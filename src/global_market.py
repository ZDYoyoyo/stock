"""全球市場觀察 — 台股連動的外部環境。

台股與美股（尤其費城半導體 SOX）連動極高，隔夜美股常主導隔日台股方向。
用 Yahoo Finance chart API（免費）抓主要指數最新漲跌，作為環境輔助判斷。
"""
from datetime import datetime, timezone

import requests

# symbol -> 中文名（^ 需編碼為 %5E）
_INDICES = [
    ("^SOX", "費城半導體"),
    ("^IXIC", "那斯達克"),
    ("^GSPC", "標普500"),
    ("^DJI", "道瓊"),
    ("^VIX", "VIX恐慌指數"),
    ("^TWII", "台股加權"),
    ("TWD=X", "美元/台幣"),
]
_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _one(sym: str):
    try:
        r = requests.get(_URL.format(sym.replace("^", "%5E")),
                         params={"interval": "1d", "range": "10d"},
                         headers=_HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        # 用日線收盤序列的最後兩根算「單日」漲跌。
        # 注意：不可用 meta.chartPreviousClose——它是「抓取區間之前」的收盤，
        # 會把多日累積漲跌誤當成單日（曾導致費半 5 日跌幅被顯示成單日 -9.97%）。
        # 盤中時最後一根 close 即為即時價；收盤後為當日收盤 → 皆是「最新 vs 前一交易日收盤」
        # 帶著時間戳一起過濾 None，才知道最新那根是「哪一天」的（sox_signal 判斷時序要用）。
        bars = [(t, c) for t, c in zip(res.get("timestamp") or [],
                                       res["indicators"]["quote"][0]["close"]) if c is not None]
        if len(bars) >= 2 and bars[-2][1]:
            (ts, price), (_, prev) = bars[-1], bars[-2]
            return {"price": round(price, 2), "prev": round(prev, 2),
                    "chg_%": round((price - prev) / prev * 100, 2),
                    # 日線時間戳是該市場開盤時刻(美股≈13:30 UTC、台股≈01:00 UTC) → 取 UTC 日期即當地交易日
                    "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")}
    except (requests.RequestException, KeyError, ValueError, TypeError, IndexError):
        return None
    return None


def fetch() -> list[dict]:
    out = []
    for sym, name in _INDICES:
        d = _one(sym)
        if d:
            out.append({"symbol": sym, "name": name, **d})
    return out


def sox_signal(data: list[dict]) -> str:
    """以費半漲跌給台股半導體的環境提示。"""
    sox = next((x for x in data if x["symbol"] == "^SOX"), None)
    if not sox:
        return ""
    c = sox["chg_%"]
    # 時序：盤後(台北晚上)跑時，最新費半收盤是『前一個美股交易日』，台股今天白天已經反應過了，
    # 不能再說「隔日」。只有費半那根比台股最新交易日還新(美股收在台股收盤之後，例如清晨跑)，
    # 才是對下一個台股交易日的預告。2026-11-23 實際踩到：上週五費半 −10.7%，台股週一已收紅，
    # 報告還寫「台股隔日易承壓」。比對基準用同一批抓到的 ^TWII 最新日，免另外傳參數。
    tw = next((x for x in data if x["symbol"] == "^TWII"), None)
    s_day, t_day = sox.get("date"), (tw or {}).get("date")
    stale = bool(s_day and t_day and s_day < t_day)
    if c <= -3:
        head, fwd = f"⚠️ 費半重挫 {c}%", "台股半導體/電子隔日易承壓"
    elif c <= -1:
        head, fwd = f"費半走弱 {c}%", "台股電子偏弱"
    elif c >= 3:
        head, fwd = f"🟢 費半大漲 {c}%", "台股電子隔日有撐"
    elif c >= 1:
        head, fwd = f"費半走強 {c}%", "台股電子偏多"
    else:
        head, fwd = f"費半持平 {c}%", ""
    if stale:
        return f"{head}（{s_day} 美股收盤，台股 {t_day} 已反應過）"
    return f"{head} → {fwd}" if fwd else head


def summary_lines(data: list[dict]) -> list[str]:
    if not data:
        return ["全球市場：無資料"]
    lines = [f"{d['name']}: {d['chg_%']:+.2f}%" for d in data]
    return lines


if __name__ == "__main__":
    d = fetch()
    print(" | ".join(summary_lines(d)))
    print(sox_signal(d))
