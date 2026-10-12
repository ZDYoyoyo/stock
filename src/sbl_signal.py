"""借券賣出餘額 enrich — 法人真實空單（SBL, Securities Borrowing & Lending）。

借券賣出餘額＝機構／外資向券商借股票放空、尚未回補的在外空單張數。
與『融券』的差別（很重要）：
  - 融券＝散戶信用交易放空 → 散戶指標（已有欄：融券餘額／券資比%／融券佔量%）。
  - 借券賣出＝法人／外資真實看空部位 → 法人指標，此欄補的就是這塊。
餘額高＝法人空方壓力大；後續回補（餘額下降）對股價是潛在買盤。
⚠️ 為絕對張數：大型股天生較高，判讀請對照股本／成交量，別只看大小（同券資比%的提醒）。

資料：FinMind TaiwanDailyShortSaleBalances 的 SBLShortSalesCurrentDayBalance（單位股，÷1000→張）。
📌 **兩種查法的權限不同**（2026-10 Sponsor 到期後實際踩到）：
  - 全市場單日（不帶 data_id，`fetch_market_day`）＝**Sponsor 專屬**，到期後回 400
    「Your level is register」→ `backfill_sbl` 靜默抓 0 筆，DB sbl 表從此停止累積。
  - 逐檔（帶 data_id，`fetch_stock_days`）＝**免費層仍可用**，1 call/檔。
而 `compute_from_db` 只取「該檔表裡最後一筆」→ 停更後會把幾天前的舊餘額當成今天的值輸出，
報告上跟「今天真的是這個數」長得一模一樣（又一次靜默降級，2026-10-07~10-12 實際發生）。
故日常請走 `compute_latest()`：它會比對資料日，不夠新就逐檔補抓，補不到就**標資料日或留白**。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .db import read_table
from .finmind_client import fetch

_DATASET = "TaiwanDailyShortSaleBalances"
_COLS = ["stock_id", "借券賣出餘額"]
_DATE_COL = "借券資料日"
_DB_COLS = ["stock_id", "借券賣出餘額", "借券增減", _DATE_COL]
_BAL_FIELD = "SBLShortSalesCurrentDayBalance"   # 借券賣出當日餘額（股）
_MAX_STALE_DAYS = 5      # 資料落後超過幾個交易日 → 整檔留白（寧可沒有，也不給看起來像今天的舊值）
_LIVE_DAYS = 14          # 逐檔補抓的回看天數（日曆日；要夠涵蓋連假）


def fetch_market_day(day_iso: str) -> list[dict]:
    """抓某交易日『全市場』借券賣出餘額 → DB sbl 表 rows（股÷1000→張）。

    ⚠️ **需 Sponsor**（不帶 data_id 的全市場查詢）；免費層回 400 → 這裡回空。
    回傳 [{date, stock_id, sbl_balance}]；假日/無資料回空。
    """
    try:
        data = fetch(_DATASET, start_date=day_iso, end_date=day_iso)
    except Exception:
        return []
    rows = []
    for d in data:
        sid = str(d.get("stock_id", "")).strip()
        bal = d.get(_BAL_FIELD)
        if not sid or bal is None:
            continue
        rows.append({"date": day_iso, "stock_id": sid,
                     "sbl_balance": int(round(bal / 1000))})   # 股 → 張
    return rows


def fetch_stock_days(stock_id, start_iso: str, end_iso: str) -> list[dict]:
    """逐檔抓一段期間的借券賣出餘額 → DB sbl 表 rows（股÷1000→張）。**免費層可用**。

    1 call/檔，故只對當日要顯示的候選股呼叫（見 compute_latest 的 live_ids）。
    """
    sid = str(stock_id).strip()
    try:
        data = fetch(_DATASET, start_date=start_iso, end_date=end_iso, data_id=sid)
    except Exception:
        return []
    rows = []
    for d in data:
        day = str(d.get("date", "")).strip()[:10]
        bal = d.get(_BAL_FIELD)
        if not day or bal is None:
            continue
        rows.append({"date": day, "stock_id": sid,
                     "sbl_balance": int(round(bal / 1000))})   # 股 → 張
    return rows


def backfill_live(stock_ids, end_iso: str | None = None, days: int = _LIVE_DAYS) -> int:
    """逐檔補抓近 days 天借券 → upsert DB sbl；回傳寫入筆數。

    Sponsor 到期後的日常補資料路徑：全市場抓不到，就只替『今天會顯示的候選股』補。
    寫回 DB 而不是只回傳當日值，是為了讓『借券增減』(vs 前一交易日) 仍算得出來，
    個股深掘的借券曲線也跟著接上。
    """
    ids = sorted({str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()})
    if not ids:
        return 0
    end = end_iso or _latest_date()
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    from .db import connect, upsert
    n = 0
    with connect() as conn:
        for sid in ids:
            rows = fetch_stock_days(sid, start, end)
            if rows:
                n += upsert(conn, "sbl", rows)
    return n


def latest_db_date() -> str | None:
    """DB sbl 表最新資料日（ISO）；無表/無資料回 None。"""
    from .db import connect
    with connect() as conn:
        try:
            v = conn.execute("SELECT MAX(date) FROM sbl").fetchone()[0]
        except Exception:
            return None
    return v or None


def _price_dates() -> list[str]:
    px = read_table("price", use_cache=True)
    if px is None or px.empty:
        return []
    return sorted(px["date"].unique())


def _latest_date() -> str:
    """本地 price 表最新交易日 ISO（YYYY-MM-DD）；無資料回今日。"""
    ds = _price_dates()
    return ds[-1] if ds else date.today().isoformat()


def _stale_days(data_date: str, asof: str) -> int:
    """data_date 到 asof 之間隔了幾個交易日（0＝就是當日）。用 price 表的交易日，不算假日。"""
    return len([d for d in _price_dates() if data_date < d <= asof])


def compute_from_db(stock_ids, asof: str | None = None) -> pd.DataFrame:
    """由 DB sbl 歷史算 [借券賣出餘額(最新), 借券增減(vs 前一交易日), 借券資料日]（張）。

    一次讀表、不打 API。⚠️`借券資料日`＝**該檔自己**最後一筆的日期——表停更時各檔可能
    新舊不一（有補抓的是今天、沒補到的是上週），故逐檔標而非整批標。
    無歷史/表不存在回空表。
    """
    ids = {str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()}
    if not ids:
        return pd.DataFrame(columns=_DB_COLS)
    from .db import connect
    with connect() as conn:
        try:
            df = pd.read_sql("SELECT date, stock_id, sbl_balance FROM sbl", conn)
        except Exception:
            return pd.DataFrame(columns=_DB_COLS)
    if df.empty:
        return pd.DataFrame(columns=_DB_COLS)
    df = df[df["stock_id"].isin(ids)]
    if asof:
        df = df[df["date"] <= asof]
    df = df.sort_values("date")
    rows = []
    for sid, g in df.groupby("stock_id"):
        bals = g["sbl_balance"].tolist()
        delta = (bals[-1] - bals[-2]) if len(bals) >= 2 else None
        rows.append((sid, int(bals[-1]), int(delta) if delta is not None else pd.NA,
                     g["date"].iloc[-1]))
    out = pd.DataFrame(rows, columns=_DB_COLS)
    out["借券增減"] = out["借券增減"].astype("Int64")
    return out


def compute_latest(stock_ids, live_ids=None, asof: str | None = None) -> pd.DataFrame:
    """**日常取用口**：DB 夠新就直接用（零 API）；落後就逐檔補抓，補不到就標資料日或留白。

    stock_ids＝要輸出的全部候選（讀 DB 不花額度）；live_ids＝值得花 API 補抓的子集
    （＝報告真的會顯示的那幾檔，預設同 stock_ids）。每檔 1 call，免費層 600/時。
    輸出：資料日全都是 asof → 不帶『借券資料日』欄（常態，版面不變）；
    有任一檔落後 → 整欄帶上各自資料日；落後超過 _MAX_STALE_DAYS 交易日的檔**整列剔除**，
    不讓舊餘額假裝成今天的。
    """
    ids = {str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()}
    if not ids:
        return pd.DataFrame(columns=_DB_COLS)
    asof = asof or _latest_date()

    if latest_db_date() != asof:        # 全市場抓不到（Sponsor 到期）→ 只替會顯示的候選逐檔補
        targets = ids if live_ids is None else (
            ids & {str(s).strip() for s in live_ids if pd.notna(s) and str(s).strip()})
        if targets:
            backfill_live(targets, end_iso=asof)

    out = compute_from_db(ids, asof=asof)
    if out.empty:
        return out
    stale = {d: _stale_days(d, asof) for d in out[_DATE_COL].unique()}
    out = out[out[_DATE_COL].map(stale) <= _MAX_STALE_DAYS].reset_index(drop=True)
    if out.empty:
        return pd.DataFrame(columns=_DB_COLS)
    if (out[_DATE_COL] == asof).all():
        out = out.drop(columns=[_DATE_COL])
    return out


def compute(stock_ids, start_date: str | None = None) -> pd.DataFrame:
    """回傳每檔 [借券賣出餘額]（張，依 stock_id）。只查傳入的候選股；抓不到回空表。

    逐檔 live 查（免費層可用）、不落 DB 的輕量版；日常請用 compute_latest()。
    """
    ids = sorted({str(s).strip() for s in stock_ids if pd.notna(s) and str(s).strip()})
    if not ids:
        return pd.DataFrame(columns=_COLS)

    end_iso = _latest_date()
    start = start_date or (date.fromisoformat(end_iso) - timedelta(days=_LIVE_DAYS)).isoformat()

    rows = []
    for sid in ids:
        data = fetch_stock_days(sid, start, end_iso)
        if not data:
            continue
        latest = max(data, key=lambda r: r["date"])
        rows.append((sid, latest["sbl_balance"]))
    return pd.DataFrame(rows, columns=_COLS)


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把『借券賣出餘額』併進 df（依 stock_id）。signals 可預先算好重用。

    未傳 signals 時，只查 df 自身的 stock_id（避免逐檔掃全市場）。
    """
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute_latest(df["stock_id"].tolist())
    if s is None or s.empty:
        return df
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    return df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
