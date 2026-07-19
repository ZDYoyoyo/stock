"""地雷偵測 — 財務 + 籌碼 + 技術 三面紅旗，買進/續抱前的排雷檢查。

用途：不是選股，是「排除」。對你的持股、或要買的候選、或每日清單，
逐檔檢查是否有踩雷風險（營收崩、轉虧、法人棄守、大戶出貨、跌破季線…），
加權算「風險分」與「風險級（中/高/嚴重）」，把該閃的先標出來。

資料：籌碼/技術面走本地 DB（便宜）；財務面用 FinMind 逐檔（月營收/EPS/損益表，免費）。
門檻見 config.LANDMINE。⚠️ 研究用途，非投資建議。
"""
from __future__ import annotations

import time

import pandas as pd

from ..config import LANDMINE as L
from ..db import connect
from ..finmind_client import fetch


# ---------- 財務面（FinMind） ----------
def _fin_flags(sid: str) -> tuple[list[str], dict]:
    """回傳 (紅旗清單, 指標dict)。逐檔 FinMind：月營收 + 綜合損益表。"""
    flags, info = [], {}

    # 月營收 YoY（動能崩塌 / 單月急墜）
    rev = fetch("TaiwanStockMonthRevenue", start_date="2024-01-01", data_id=sid)
    if rev:
        rmap = {(d["revenue_year"], d["revenue_month"]): d["revenue"] for d in rev}
        yoys = []
        for (y, m) in sorted(rmap.keys()):
            prev = rmap.get((y - 1, m))
            if prev and prev > 0:
                yoys.append((rmap[(y, m)] - prev) / prev * 100)
        if yoys:
            last = yoys[-1]
            avg3 = sum(yoys[-3:]) / len(yoys[-3:])
            info["營收YoY%"] = round(last, 1)
            info["近3月YoY均%"] = round(avg3, 1)
            if avg3 <= L.REV_YOY_BAD:
                flags.append(f"營收動能崩塌(近3月YoY均{avg3:.0f}%)")
            if last <= L.REV_YOY_CRASH:
                flags.append(f"單月營收急墜(YoY{last:.0f}%)")

    # 綜合損益表：EPS 逐季 + 毛利率 + 淨利轉虧
    fs = fetch("TaiwanStockFinancialStatements", start_date="2023-01-01", data_id=sid)
    if fs:
        by_dt: dict[str, dict] = {}
        for d in fs:
            by_dt.setdefault(d["date"], {})[d.get("type")] = d.get("value")
        dates = sorted(by_dt.keys())

        # EPS TTM 年增
        eps = [(dt, by_dt[dt].get("EPS")) for dt in dates if by_dt[dt].get("EPS") is not None]
        if len(eps) >= 8:
            ttm = sum(v for _, v in eps[-4:])
            prev = sum(v for _, v in eps[-8:-4])
            info["EPS近4季"] = round(ttm, 2)
            if prev:
                g = (ttm - prev) / abs(prev) * 100
                info["EPS年增%"] = round(g, 1)
                if g <= L.EPS_TTM_DROP:
                    flags.append(f"獲利崩(EPS年增{g:.0f}%)")
            if ttm < 0:
                flags.append(f"近四季轉虧(EPS{ttm:.2f})")

        # 最新季 淨利轉虧
        last_dt = dates[-1]
        nat = by_dt[last_dt].get("IncomeAfterTaxes")
        if nat is not None and nat < 0:
            flags.append(f"最新季稅後虧損({last_dt})")

        # 毛利率 YoY 惡化（最新季 vs 去年同季）
        def _gm(dt):
            g, r = by_dt[dt].get("GrossProfit"), by_dt[dt].get("Revenue")
            return (g / r * 100) if (g is not None and r) else None
        gm_now = _gm(last_dt)
        yoy_dt = _same_quarter_last_year(dates, last_dt)
        gm_prev = _gm(yoy_dt) if yoy_dt else None
        if gm_now is not None:
            info["毛利率%"] = round(gm_now, 1)
        if gm_now is not None and gm_prev is not None:
            drop = gm_prev - gm_now
            if drop >= L.MARGIN_DROP_PP:
                flags.append(f"毛利率惡化(年減{drop:.1f}pp)")

    return flags, info


def _same_quarter_last_year(dates: list[str], dt: str) -> str | None:
    """在季度日期清單裡找『去年同季』（月份相同、年份少 1）。"""
    y, md = dt[:4], dt[5:]
    target = f"{int(y) - 1}-{md}"
    return target if target in dates else None


# ---------- 籌碼 + 技術面（本地 DB，一次載入） ----------
def _db_context():
    with connect() as conn:
        price = pd.read_sql("SELECT date, stock_id, close, volume FROM price", conn)
        inst = pd.read_sql("SELECT date, stock_id, trust_net, foreign_net FROM institutional", conn)
        margin = pd.read_sql("SELECT date, stock_id, margin_balance FROM margin", conn)
        info = pd.read_sql("SELECT stock_id, stock_name, type FROM stock_info", conn)
    return price, inst, margin, info


def _consec_sell(series: pd.Series) -> int:
    """從最近往回數，連續 < 0（賣超）的天數。"""
    n = 0
    for v in reversed(series.tolist()):
        if v < 0:
            n += 1
        else:
            break
    return n


def _chip_tech_flags(sid: str, ctx, market_ret_median: float) -> tuple[list[str], dict, bool]:
    """回傳 (紅旗, 指標, 是否有DB資料)。"""
    price, inst, margin, mkt_map = ctx
    flags, info = [], {}

    pg = price[price["stock_id"] == sid].sort_values("date")
    if pg.empty:
        return flags, info, False
    close = pg["close"].tolist()
    info["收盤"] = round(close[-1], 2)

    # 跌破季線
    if len(close) >= L.MA_LONG:
        ma = sum(close[-L.MA_LONG:]) / L.MA_LONG
        if close[-1] < ma:
            flags.append(f"跌破季線({L.MA_LONG}MA)")

    # 相對大盤弱勢（近 LOOKBACK 報酬 vs 全市場中位）
    win = min(L.LOOKBACK_DAYS, len(close) - 1)
    if win >= 5 and close[-win - 1] > 0:
        r = (close[-1] - close[-win - 1]) / close[-win - 1] * 100
        rel = r - market_ret_median
        info["相對大盤%"] = round(rel, 1)
        if rel <= L.WEAK_VS_MARKET:
            flags.append(f"相對大盤弱勢({rel:.0f}%)")

    # 法人連賣（上市看投信、上櫃看外資）
    is_tpex = mkt_map.get(sid) == "tpex"
    col = "foreign_net" if is_tpex else "trust_net"
    ig = inst[inst["stock_id"] == sid].sort_values("date")
    if not ig.empty:
        sell_days = _consec_sell(ig[col])
        if sell_days >= L.INST_SELL_DAYS:
            who = "外資" if is_tpex else "投信"
            flags.append(f"{who}連賣{sell_days}天")

    # 融資大增（散戶接刀）
    mg = margin[margin["stock_id"] == sid].sort_values("date")
    mb = mg["margin_balance"].tolist()
    if len(mb) >= 2 and mb[0] > 0:
        chg = (mb[-1] - mb[0]) / mb[0]
        if chg >= L.MARGIN_SURGE:
            flags.append(f"融資暴增(+{chg * 100:.0f}%)")

    # 千張大戶週減
    from ..enrich import big_holder_info
    bh = big_holder_info(sid)
    if bh.get("千張週增減") is not None:
        info["千張大戶%"] = bh.get("千張大戶%")
        if bh["千張週增減"] <= -L.BIG_HOLDER_DROP:
            flags.append(f"大戶出貨(千張週減{bh['千張週增減']:.1f}pp)")

    return flags, info, True


def _market_median_return(price: pd.DataFrame) -> float:
    """全市場近 LOOKBACK 報酬中位數，當『大盤』基準。"""
    dates = sorted(price["date"].unique())[-(L.LOOKBACK_DAYS + 1):]
    sub = price[price["date"].isin(dates)]
    rets = []
    for _, g in sub.groupby("stock_id"):
        c = g.sort_values("date")["close"].tolist()
        if len(c) >= 2 and c[0] > 0:
            rets.append((c[-1] - c[0]) / c[0] * 100)
    return float(pd.Series(rets).median()) if rets else 0.0


def _level(score: int) -> str:
    if score >= L.LEVEL_SEVERE:
        return "🔴 嚴重"
    if score >= L.LEVEL_HIGH:
        return "🟠 高"
    if score >= L.LEVEL_MID:
        return "🟡 中"
    return "🟢 低"


def scan(stock_ids, sleep: float = 0.35, verbose: bool = True) -> pd.DataFrame:
    """對一籃股票逐檔排雷。回傳依風險分排序的 DataFrame。"""
    ctx_full = _db_context()
    price, _, _, info = ctx_full
    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    mkt_map = info.set_index("stock_id")["type"].to_dict()
    ctx = (price, ctx_full[1], ctx_full[2], mkt_map)
    mret = _market_median_return(price)

    rows = []
    for sid in stock_ids:
        ct_flags, ct_info, has_db = _chip_tech_flags(sid, ctx, mret)
        fin_flags, fin_info = _fin_flags(sid)
        flags = ct_flags + fin_flags
        score = len(ct_flags) + len(fin_flags)
        row = {"stock_id": sid, "name": name_map.get(sid, ""),
               "風險分": score, "風險級": _level(score),
               "紅旗": "；".join(flags) if flags else "—"}
        row.update({k: v for k, v in {**ct_info, **fin_info}.items()})
        rows.append(row)
        if verbose:
            print(f"  {sid} {name_map.get(sid,'')}  {_level(score)}（{score}）"
                  f"  {'；'.join(flags) if flags else '無明顯紅旗'}")
        time.sleep(sleep)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("風險分", ascending=False).reset_index(drop=True)
