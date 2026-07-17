"""全球市場觀察 — 台股連動的外部環境。

台股與美股（尤其費城半導體 SOX）連動極高，隔夜美股常主導隔日台股方向。
用 Yahoo Finance chart API（免費）抓主要指數最新漲跌，作為環境輔助判斷。
"""
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
                         params={"interval": "1d", "range": "5d"},
                         headers=_HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        m = r.json()["chart"]["result"][0]["meta"]
        price, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose")
        if price and prev:
            return {"price": round(price, 2), "prev": round(prev, 2),
                    "chg_%": round((price - prev) / prev * 100, 2)}
    except (requests.RequestException, KeyError, ValueError, TypeError):
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
    if c <= -3:
        return f"⚠️ 費半重挫 {c}% → 台股半導體/電子隔日易承壓"
    if c <= -1:
        return f"費半走弱 {c}% → 台股電子偏弱"
    if c >= 3:
        return f"🟢 費半大漲 {c}% → 台股電子隔日有撐"
    if c >= 1:
        return f"費半走強 {c}% → 台股電子偏多"
    return f"費半持平 {c}%"


def summary_lines(data: list[dict]) -> list[str]:
    if not data:
        return ["全球市場：無資料"]
    lines = [f"{d['name']}: {d['chg_%']:+.2f}%" for d in data]
    return lines


if __name__ == "__main__":
    d = fetch()
    print(" | ".join(summary_lines(d)))
    print(sox_signal(d))
