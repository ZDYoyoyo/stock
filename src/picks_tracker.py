"""昨日精選追蹤 — 每天記錄篩出的波段(T11/T16)與當沖前 N 名，隔日對比今天表現＋找原因。

為什麼要存：要「昨天選的今天表現如何、為什麼」就得先把每天的精選名單留下來。
存 data/history/picks.csv（進 git，一天天累積，也供未來回測「隔日追蹤」策略）。
欄位：date, track, rank, stock_id, name
"""
from __future__ import annotations

import pandas as pd

from .config import DATA_DIR
from .screeners import chip_diagnosis as cd

PICKS_CSV = DATA_DIR / "history" / "picks.csv"
_COLS = ["date", "track", "rank", "stock_id", "name"]


def _load() -> pd.DataFrame:
    if not PICKS_CSV.exists():
        return pd.DataFrame(columns=_COLS)
    return pd.read_csv(PICKS_CSV, dtype={"stock_id": str})


def save(today: str, tracks: dict, n: int = 15) -> pd.DataFrame:
    """把今天各軌前 n 名存進 picks.csv（同日重跑會覆蓋當日，不重複累積）。

    tracks: {軌名: DataFrame(需含 stock_id, name)}。
    """
    df = _load()
    df = df[df["date"] != today]  # 同日全清後重寫
    rows = []
    for track, tdf in tracks.items():
        if tdf is None or tdf.empty:
            continue
        for rank, (_, r) in enumerate(tdf.head(n).iterrows(), 1):
            rows.append({"date": today, "track": track, "rank": rank,
                         "stock_id": str(r["stock_id"]), "name": r.get("name", "")})
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    df = df.sort_values(["date", "track", "rank"]).reset_index(drop=True)
    PICKS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(PICKS_CSV, index=False, encoding="utf-8")
    return df


def prior_date(today: str) -> str | None:
    """picks.csv 中比 today 早的最後一個有紀錄日（=上一個交易日的精選）。"""
    df = _load()
    past = sorted(d for d in df["date"].unique() if str(d) < today)
    return past[-1] if past else None


def followthrough(today: str) -> dict:
    """對『上一個有紀錄日』的精選，逐檔算今天漲跌＋籌碼歸因（今 vs 昨）。

    回傳 {"date": 昨日, "tracks": {軌名: [{rank, stock_id, name, chg, one_line}, ...]}}；
    無前一日紀錄回 {}。
    """
    df = _load()
    pdate = prior_date(today)
    if not pdate:
        return {}
    prev = df[df["date"] == pdate]
    out = {"date": pdate, "tracks": {}}
    for track in prev["track"].unique():
        rows = []
        for r in prev[prev["track"] == track].sort_values("rank").itertuples():
            diag = cd._fetch(str(r.stock_id), 5)
            if diag.empty or len(diag) < 2:
                continue
            rows.append({"rank": int(r.rank), "stock_id": str(r.stock_id), "name": r.name,
                         "chg": diag.iloc[-1]["漲跌%"], "one_line": cd.one_line(diag)})
        if rows:
            out["tracks"][track] = rows
    return out


def summary_stats(ft: dict) -> dict:
    """各軌隔日表現統計：平均漲跌%、上漲家數/總數。"""
    stats = {}
    for track, rows in ft.get("tracks", {}).items():
        chgs = [r["chg"] for r in rows if pd.notna(r["chg"])]
        if not chgs:
            continue
        stats[track] = {"avg": round(sum(chgs) / len(chgs), 2),
                        "up": sum(1 for c in chgs if c > 0), "n": len(chgs)}
    return stats
