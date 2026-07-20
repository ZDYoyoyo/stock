"""主力籌碼綜合 — 用免費資料找「主力進駐、散戶退場」的股票。

分點是 FinMind 付費資料；沒會員時，用免費的三大法人+千張大戶+融資組出
「主力進駐分數」，抓的是跟分點同樣的意圖：籌碼從散戶手上集中到主力/大戶。

主力進駐分數 = 法人佔量% × W_INST
             + 千張大戶週增(pp) × W_HOLDER   （大戶最能代表主力）
             + 散戶退場(融資期間減少%) × W_MARGIN
分數越高 = 主力越積極進駐、籌碼越集中。

有 FinMind Sponsor（.env 填 token、等級足夠）時，前幾檔自動加抓「分點主力買賣超」。
門檻/權重見 config.MAINFORCE。⚠️ 研究用途，非投資建議。
"""
from __future__ import annotations

import pandas as pd

from ..config import MAINFORCE as M
from ..db import connect


def _holder_weekly_change() -> dict:
    """{sid: (最新千張大戶%, 週增減pp)}；資料不足回空。"""
    with connect() as conn:
        df = pd.read_sql("SELECT date, stock_id, pct_1000 FROM big_holders", conn)
    if df.empty:
        return {}
    dates = sorted(df["date"].unique())
    last = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None
    cur = df[df["date"] == last].set_index("stock_id")["pct_1000"]
    out = {}
    if prev is not None:
        pv = df[df["date"] == prev].set_index("stock_id")["pct_1000"]
        for sid, v in cur.items():
            chg = (v - pv[sid]) if sid in pv.index else None
            out[sid] = (round(v, 2), round(chg, 2) if chg is not None else None)
    else:
        out = {sid: (round(v, 2), None) for sid, v in cur.items()}
    return out


def run() -> pd.DataFrame:
    with connect() as conn:
        price = pd.read_sql("SELECT date, stock_id, close, volume FROM price", conn)
        inst = pd.read_sql(
            "SELECT date, stock_id, foreign_net, trust_net, dealer_net FROM institutional", conn)
        margin = pd.read_sql("SELECT date, stock_id, margin_balance FROM margin", conn)
        info = pd.read_sql("SELECT stock_id, stock_name, type FROM stock_info", conn)
    if price.empty:
        raise SystemExit("資料庫是空的，請先執行：python -m scripts.update_data")

    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    mkt_map = info.set_index("stock_id")["type"].to_dict()
    holders = _holder_weekly_change()

    win = sorted(price["date"].unique())[-M.LOOKBACK_DAYS:]
    price = price[price["date"].isin(win)]
    inst = inst[inst["date"].isin(win)]

    rows = []
    for sid, pg in price.groupby("stock_id"):
        pg = pg.sort_values("date")
        close = pg["close"].iloc[-1]
        avg_vol = pg["volume"].mean()
        total_vol = pg["volume"].sum()
        if close < M.MIN_CLOSE or avg_vol < M.MIN_AVG_VOLUME or total_vol <= 0:
            continue

        ig = inst[inst["stock_id"] == sid]
        inst_net = (ig["foreign_net"].sum() + ig["trust_net"].sum() + ig["dealer_net"].sum())
        inst_pct = inst_net / total_vol * 100      # 法人淨買佔成交量比

        h = holders.get(sid, (None, None))
        holder_pct, holder_chg = h

        mg = margin[margin["stock_id"] == sid].sort_values("date")["margin_balance"].tolist()
        margin_chg = ((mg[-1] - mg[0]) / mg[0] * 100) if len(mg) >= 2 and mg[0] > 0 else 0.0

        score = (inst_pct * M.W_INST
                 + (holder_chg or 0) * M.W_HOLDER
                 + (-margin_chg) * M.W_MARGIN)
        rows.append({
            "stock_id": sid,
            "name": name_map.get(sid, ""),
            "market": "上櫃" if mkt_map.get(sid) == "tpex" else "上市",
            "close": round(close, 2),
            "法人淨買張": int(inst_net),
            "法人佔量%": round(inst_pct, 2),
            "千張大戶%": holder_pct,
            "千張週增pp": holder_chg,
            "融資增減%": round(margin_chg, 1),
            "主力分": round(score, 1),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("主力分", ascending=False).reset_index(drop=True)


def enrich_branches(df: pd.DataFrame, date: str, n: int | None = None) -> pd.DataFrame:
    """對前 n 檔加抓分點主力買賣超（需 FinMind Sponsor；不可用則原樣回傳）。"""
    from .. import broker_client as bc
    if df.empty or not bc.available():
        return df
    n = n or M.BRANCH_ENRICH_N
    cols = {"主力買超張": {}, "主力賣超張": {}, "主力淨額張": {}}
    for sid in df["stock_id"].head(n):
        s = bc.branch_summary(sid, date)
        for k in cols:
            if k in s:
                cols[k][sid] = s[k]
    for k, m in cols.items():
        if m:
            df[k] = df["stock_id"].map(m)
    return df
