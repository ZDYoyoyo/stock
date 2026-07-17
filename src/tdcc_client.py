"""集保結算所 TDCC 股權分散表 client（免費、全市場、每週更新）。

千張大戶籌碼：持股分級 15 = 1,000,001股以上（≥1000張）；分級 12~15 = ≥400張。
大戶持股比上升 = 籌碼往大戶集中（沉澱）；下降 = 大戶出貨給散戶。

注意：TDCC 開放資料只給「最新一週」快照。趨勢需每週跑 update_holders 累積。
"""
import re

import requests

_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
_STOCK_RE = re.compile(r"^[1-9]\d{3}$")  # 4 位數普通股


def fetch() -> list[dict]:
    """回傳最新一週 [{date, stock_id, pct_1000, pct_400}]（%）。"""
    r = requests.get(_URL, params={"id": "1-5"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    r.raise_for_status()
    lines = r.text.splitlines()

    agg = {}  # sid -> {"date":, "pct_1000":, "pct_400":}
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) < 6:
            continue
        sid = p[1].strip()
        if not _STOCK_RE.match(sid):
            continue
        try:
            tier = int(p[2])
            pct = float(p[5])
        except ValueError:
            continue
        d = agg.setdefault(sid, {"date": _iso(p[0]), "stock_id": sid,
                                 "pct_1000": 0.0, "pct_400": 0.0})
        if tier == 15:            # ≥1000張（千張大戶）
            d["pct_1000"] += pct
        if 12 <= tier <= 15:      # ≥400張（大戶）
            d["pct_400"] += pct
    return [{"date": v["date"], "stock_id": k,
             "pct_1000": round(v["pct_1000"], 2), "pct_400": round(v["pct_400"], 2)}
            for k, v in agg.items()]


def _iso(ymd: str) -> str:
    ymd = ymd.strip()
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
