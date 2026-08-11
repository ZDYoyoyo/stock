"""個股深掘：把單一股票的各面向歷史拉齊成『籌碼病歷表』資料層。

決策某一檔時跑一次，回答「這檔的主力是誰、何時進出、隔日沖慣性如何」。
資料來源：
  - DB（免費、已累積）：價量／三大法人／融資融券／借券餘額／千張大戶。
  - 分點（Sponsor、on-demand）：逐日主力淨額、隔日沖賣壓%、隔日沖常客名單、最新日 Top 分點。
    分點僅單日查（每日 1 call），單檔 N 日＝N calls，6000/hr 綽綽有餘。

只讀不寫；分點量大不落 DB。渲染見 scripts/run_stock.py。
"""
from __future__ import annotations

import pandas as pd

from .db import connect


def stock_meta(sid: str) -> dict:
    """{name, market, industry}；查不到回空值。"""
    with connect() as c:
        df = pd.read_sql(
            "SELECT stock_name, type, industry FROM stock_info WHERE stock_id=?",
            c, params=(sid,))
    if df.empty:
        return {"name": "", "market": "", "industry": ""}
    r = df.iloc[0]
    return {"name": r["stock_name"] or "",
            "market": "上櫃" if r["type"] == "tpex" else "上市",
            "industry": r["industry"] or ""}


def chip_timeline(sid: str, days: int = 30) -> pd.DataFrame:
    """近 N 交易日籌碼時間序列（DB）：收盤/漲跌%/量/三大法人/資券增減/借券餘額+增減。"""
    with connect() as c:
        px = pd.read_sql(
            "SELECT date, close, volume FROM price WHERE stock_id=? ORDER BY date", c, params=(sid,))
        inst = pd.read_sql(
            "SELECT date, foreign_net, trust_net, dealer_net FROM institutional "
            "WHERE stock_id=? ORDER BY date", c, params=(sid,))
        mg = pd.read_sql(
            "SELECT date, margin_balance, short_balance FROM margin "
            "WHERE stock_id=? ORDER BY date", c, params=(sid,))
        sbl = pd.read_sql(
            "SELECT date, sbl_balance FROM sbl WHERE stock_id=? ORDER BY date", c, params=(sid,))
    if px.empty:
        return pd.DataFrame()

    df = px.rename(columns={"close": "收盤", "volume": "量"})
    df["漲跌%"] = (df["收盤"].pct_change() * 100).round(2)
    df = df.merge(inst.rename(columns={"foreign_net": "外資", "trust_net": "投信",
                                       "dealer_net": "自營"}), on="date", how="left")
    if not mg.empty:
        mg = mg.sort_values("date")
        mg["融資增減"] = mg["margin_balance"].diff()
        mg["融券增減"] = mg["short_balance"].diff()
        df = df.merge(mg[["date", "融資增減", "融券增減"]], on="date", how="left")
    if not sbl.empty:
        sbl = sbl.sort_values("date").rename(columns={"sbl_balance": "借券餘額"})
        sbl["借券增減"] = sbl["借券餘額"].diff()
        df = df.merge(sbl[["date", "借券餘額", "借券增減"]], on="date", how="left")

    for col in ("外資", "投信", "自營", "融資增減", "融券增減", "借券餘額", "借券增減"):
        if col in df.columns:
            df[col] = df[col].astype("Int64")
    return df.tail(days).reset_index(drop=True)


def daytrade_timeline(sid: str, tl: pd.DataFrame) -> pd.DataFrame:
    """近窗當沖比%（DB day_trade 的當沖量 ÷ tl 的量）。無資料回空。"""
    if tl is None or tl.empty or "量" not in tl.columns:
        return pd.DataFrame()
    with connect() as c:
        dt = pd.read_sql("SELECT date, dt_vol FROM day_trade WHERE stock_id=? ORDER BY date",
                         c, params=(sid,))
    if dt.empty:
        return pd.DataFrame()
    m = tl[["date", "量"]].merge(dt, on="date", how="inner")
    m = m[m["量"] > 0]
    if m.empty:
        return pd.DataFrame()
    m["當沖比%"] = (m["dt_vol"] / m["量"] * 100).round(1)
    return m[["date", "當沖比%"]].reset_index(drop=True)


def holder_trend(sid: str) -> pd.DataFrame:
    """千張大戶% 週趨勢（DB big_holders，週頻）。無資料回空。"""
    with connect() as c:
        df = pd.read_sql(
            "SELECT date, pct_1000, pct_400 FROM big_holders WHERE stock_id=? ORDER BY date",
            c, params=(sid,))
    if df.empty:
        return df
    df = df.rename(columns={"pct_1000": "千張大戶%", "pct_400": "400張大戶%"})
    df["大戶週增pp"] = df["千張大戶%"].diff().round(2)
    return df


# ---- 技術面（DB 免費；複用 tech_signal/chip_signal/verdict，公式與五軌單一來源）----

def tech_snapshot(sid: str) -> dict:
    """技術面卡：均線排列/季線年線/20MA乖離%/52週位置%/成交額億＋量能倍數/籌碼訊號/連買賣＋綜合定調。

    全走 DB。複用 tech_signal.compute()＋chip_signal.compute()（全市場算好挑本檔），
    再用 verdict 投票出一句定調。查不到回 {}。
    """
    from . import tech_signal, chip_signal, verdict
    row: dict = {"stock_id": sid}
    for compute in (tech_signal.compute, chip_signal.compute):
        try:
            s = compute()
        except Exception:
            continue
        if s is not None and not s.empty:
            m = s[s["stock_id"] == sid]
            if not m.empty:
                row.update({k: v for k, v in m.iloc[0].to_dict().items() if k != "stock_id"})
    row["定調"] = verdict.label(verdict._vote(pd.Series(row)))
    return row


def ma_series(sid: str, dates: list[str]) -> dict:
    """收盤＋MA5/20/60 對齊 dates（用完整歷史算 MA，才不會窗頭幾根均線缺算）。無資料回 {}。"""
    if not dates:
        return {}
    with connect() as c:
        px = pd.read_sql("SELECT date, close FROM price WHERE stock_id=? ORDER BY date",
                         c, params=(sid,))
    if px.empty:
        return {}
    px = px.sort_values("date").reset_index(drop=True)
    for n in (5, 20, 60):
        px[f"MA{n}"] = px["close"].rolling(n).mean().round(2)
    sub = px[px["date"].isin(dates)].reset_index(drop=True)
    if sub.empty:
        return {}
    return {"dates": sub["date"].tolist(),
            "收盤": sub["close"].round(2).tolist(),
            "MA5": sub["MA5"].tolist(),
            "MA20": sub["MA20"].tolist(),
            "MA60": sub["MA60"].tolist()}


# ---- 基本面（FinMind 逐檔；抓不到回空，上層 graceful 留白）----

def _pct(a, b):
    """(a-b)/b×100，四捨1位；b 缺/0 回 None。"""
    if a is None or b in (None, 0) or pd.isna(a) or pd.isna(b):
        return None
    return round((a - b) / abs(b) * 100, 1)


def monthly_revenue(sid: str, months: int = 12) -> pd.DataFrame:
    """近 N 月營收（億）＋YoY%/MoM%/累計YoY%。FinMind TaiwanStockMonthRevenue。"""
    from .finmind_client import fetch
    data = fetch("TaiwanStockMonthRevenue", start_date="2023-01-01", data_id=sid)
    if not data:
        return pd.DataFrame()
    rev = {(int(d["revenue_year"]), int(d["revenue_month"])): d["revenue"] for d in data}
    rows = []
    for (y, m) in sorted(rev):
        r = rev[(y, m)]
        mom_key = (y, m - 1) if m > 1 else (y - 1, 12)
        cum = sum(rev.get((y, mm), 0) for mm in range(1, m + 1))
        cum_prev = sum(rev.get((y - 1, mm), 0) for mm in range(1, m + 1))
        rows.append({"月份": f"{y}/{m:02d}", "營收億": round(r / 1e8, 2),
                     "營收YoY%": _pct(r, rev.get((y - 1, m))),
                     "營收MoM%": _pct(r, rev.get(mom_key)),
                     "累計YoY%": _pct(cum, cum_prev) if cum_prev else None})
    return pd.DataFrame(rows).tail(months).reset_index(drop=True)


def profitability(sid: str, quarters: int = 8) -> pd.DataFrame:
    """近 N 季獲利能力：毛利率%/營益率%/淨利率%/EPS單季/EPS年增%。損益表為單季值。"""
    from .finmind_client import fetch
    data = fetch("TaiwanStockFinancialStatements", start_date="2022-01-01", data_id=sid)
    if not data:
        return pd.DataFrame()
    by: dict[str, dict] = {}
    for d in data:
        by.setdefault(d["date"], {})[d.get("type")] = d.get("value")
    dates = sorted(by)
    eps = {dt: by[dt].get("EPS") for dt in dates}
    rows = []
    for i, dt in enumerate(dates):
        v = by[dt]
        rev = v.get("Revenue")
        mg = lambda x: round(x / rev * 100, 1) if rev and x is not None else None
        e = eps[dt]
        e_yoy = _pct(e, eps.get(dates[i - 4])) if i >= 4 else None   # vs 去年同季
        rows.append({"季別": dt[:7], "毛利率%": mg(v.get("GrossProfit")),
                     "營益率%": mg(v.get("OperatingIncome")), "淨利率%": mg(v.get("IncomeAfterTaxes")),
                     "EPS單季": e, "EPS年增%": e_yoy})
    return pd.DataFrame(rows).tail(quarters).reset_index(drop=True)


def valuation_snapshot(sid: str) -> dict:
    """估值：PER/PBR/殖利率%＋PER 近1年位置%（0=一年最便宜/100=最貴）＋PER 序列供圖。"""
    from datetime import date, timedelta
    from .finmind_client import fetch
    start = (date.today() - timedelta(days=400)).isoformat()
    data = fetch("TaiwanStockPER", start_date=start, data_id=sid)
    if not data:
        return {}
    data = sorted(data, key=lambda d: d["date"])
    cur = data[-1]
    pers = [d["PER"] for d in data if d.get("PER") not in (None, 0)]
    pos = None
    if len(pers) >= 20 and cur.get("PER"):
        lo, hi = min(pers), max(pers)
        pos = round((cur["PER"] - lo) / (hi - lo) * 100) if hi > lo else None
    return {"PER": cur.get("PER"), "PBR": cur.get("PBR"), "殖利率%": cur.get("dividend_yield"),
            "PER近1年位置%": pos, "_per": pers, "_dates": [d["date"] for d in data if d.get("PER") not in (None, 0)]}


def dividends(sid: str, years: int = 6) -> tuple[pd.DataFrame, int]:
    """近 N 年配息（現金＋股票，按民國年彙總）＋連續配息年數。回 (df, streak)。

    ⚠️ FinMind year 欄為『114年第2季』等民國+季字串（季配股會多列）→ 解析民國年、按年加總，
    連續配息年數才不會把『季』誤當『年』。
    """
    import re
    from .finmind_client import fetch
    data = fetch("TaiwanStockDividend", start_date="2015-01-01", data_id=sid)
    if not data:
        return pd.DataFrame(), 0

    def roc_year(s):
        m = re.match(r"\s*(\d+)", str(s))
        if not m:
            return None
        y = int(m.group(1))
        return y + 1911 if y < 1911 else y     # 民國→西元（<1911 視為民國年）

    by: dict[int, list] = {}
    for d in data:
        y = roc_year(d.get("year"))
        if y is None:
            continue
        cash = (d.get("CashEarningsDistribution") or 0) + (d.get("CashStatutorySurplus") or 0)
        stock = (d.get("StockEarningsDistribution") or 0) + (d.get("StockStatutorySurplus") or 0)
        a = by.setdefault(y, [0.0, 0.0])
        a[0] += cash
        a[1] += stock
    yrs = sorted(by)
    streak = 0
    for y in reversed(yrs):
        if by[y][0] > 0:
            streak += 1
        else:
            break
    rows = [{"年度": y, "現金股利": round(by[y][0], 2), "股票股利": round(by[y][1], 2)} for y in yrs]
    return pd.DataFrame(rows).tail(years).reset_index(drop=True), streak


def financial_health(sid: str, quarters: int = 6) -> pd.DataFrame:
    """財務體質健檢（近 N 季）：負債比%/流動比%/每股淨值/單季營運CF億/獲利含金量%/自由現金流億。

    資料：資產負債表(時點值)＋現金流量表(累計YTD→去累計還原單季)＋損益表(單季淨利，算含金量)。
    ⚠️ 現金流是**累計**(年內遞增、跨年重置)，同年內 diff 還原單季、Q1=YTD；不還原會把含金量算爆。
    """
    from .finmind_client import fetch
    bs = fetch("TaiwanStockBalanceSheet", start_date="2022-01-01", data_id=sid)
    cf = fetch("TaiwanStockCashFlowsStatement", start_date="2022-01-01", data_id=sid)
    fs = fetch("TaiwanStockFinancialStatements", start_date="2022-01-01", data_id=sid)
    if not bs and not cf:
        return pd.DataFrame()

    def by_date(data):
        out: dict[str, dict] = {}
        for d in data:
            out.setdefault(d["date"], {})[d.get("type")] = d.get("value")
        return out

    B, C, F = by_date(bs), by_date(cf), by_date(fs)

    def single_q(store, key):
        """累計YTD → 單季值：{date: 單季}。同年內減前一季、Q1(≤3月)＝YTD。"""
        out, dates = {}, sorted(store)
        for dt in dates:
            v = store[dt].get(key)
            if v is None:
                out[dt] = None
                continue
            y, m = int(dt[:4]), int(dt[5:7])
            if m <= 3:
                out[dt] = v
            else:
                prev = [d for d in dates if d < dt and int(d[:4]) == y]
                pv = store[prev[-1]].get(key) if prev else None
                out[dt] = v - pv if pv is not None else None
        return out

    ocf_q = single_q(C, "CashFlowsFromOperatingActivities")
    capex_q = single_q(C, "PropertyAndPlantAndEquipment")   # 已為負(現金流出)

    rows = []
    for dt in sorted(set(B) | set(C)):
        b = B.get(dt, {})
        ta, li = b.get("TotalAssets"), b.get("Liabilities")
        ca, cl = b.get("CurrentAssets"), b.get("CurrentLiabilities")
        eq, cap = b.get("EquityAttributableToOwnersOfParent"), b.get("CapitalStock")
        ocf, capex = ocf_q.get(dt), capex_q.get(dt)
        ni = F.get(dt, {}).get("IncomeAfterTaxes")
        rows.append({
            "季別": dt[:7],
            "負債比%": round(li / ta * 100, 1) if ta and li is not None else None,
            "流動比%": round(ca / cl * 100) if cl and ca is not None else None,
            "每股淨值": round(eq / (cap / 10), 2) if eq and cap else None,   # 權益÷(股本/面額10)
            "營運CF億": round(ocf / 1e8, 1) if ocf is not None else None,
            "含金量%": round(ocf / ni * 100) if ocf is not None and ni not in (None, 0) else None,
            "自由現金流億": round((ocf + capex) / 1e8, 1) if ocf is not None and capex is not None else None,
        })
    return pd.DataFrame(rows).tail(quarters).reset_index(drop=True)


# ---- 分點（需 Sponsor）----

def _branch_nets(sid: str, dates: list[str]) -> dict:
    """{date: {分點: 淨買張}}；逐日單查（每日 1 call）。不可用回 {}。"""
    from . import broker_client as bc
    from .broker_signal import _branch_net
    if not bc.available():
        return {}
    out = {}
    for d in dates:
        net = _branch_net(sid, d)
        if net:
            out[d] = net
    return out


def broker_timeline(sid: str, tl: pd.DataFrame, top: int = 15) -> pd.DataFrame:
    """逐日主力淨額 + 隔日沖賣壓%（分點）。tl＝chip_timeline（取其 date/量）。不可用回空。"""
    if tl is None or tl.empty:
        return pd.DataFrame()
    dates = list(tl["date"])
    nets = _branch_nets(sid, dates)
    if not nets:
        return pd.DataFrame()
    vol = dict(zip(tl["date"], tl["量"]))
    rows = []
    for i, d in enumerate(dates):
        net_t = nets.get(d)
        if not net_t:
            continue
        vals = sorted(net_t.values(), reverse=True)
        main_net = round(sum(v for v in vals[:top] if v > 0) + sum(v for v in vals[-top:] if v < 0))
        pressure = pd.NA
        prev = dates[i - 1] if i > 0 else None
        net_y = nets.get(prev) if prev else None
        v = vol.get(d)
        if net_y and v and v > 0:
            buyers = sorted(((k, x) for k, x in net_y.items() if x > 0),
                            key=lambda z: z[1], reverse=True)[:top]
            overlap = sum(min(x, -net_t.get(k, 0)) for k, x in buyers if net_t.get(k, 0) < 0)
            pressure = round(overlap / v * 100, 1)
        rows.append({"date": d, "主力淨額": main_net, "隔日沖賣壓%": pressure})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["主力淨額"] = df["主力淨額"].astype("Int64")
    return df


def daytrader_regulars(sid: str, tl: pd.DataFrame, top: int = 15, n: int = 15) -> pd.DataFrame:
    """隔日沖常客名單：窗內反覆『昨買今賣』的分點 → 出現次數＋累計回吐張。不可用回空。"""
    if tl is None or tl.empty:
        return pd.DataFrame()
    dates = list(tl["date"])
    nets = _branch_nets(sid, dates)
    if not nets:
        return pd.DataFrame()
    hits: dict[str, list] = {}
    for i in range(1, len(dates)):
        net_y, net_t = nets.get(dates[i - 1]), nets.get(dates[i])
        if not net_y or not net_t:
            continue
        buyers = sorted(((k, x) for k, x in net_y.items() if x > 0),
                        key=lambda z: z[1], reverse=True)[:top]
        for k, x in buyers:
            t = net_t.get(k, 0)
            if t < 0:                       # 昨買今賣
                ov = min(x, -t)
                h = hits.setdefault(k, [0, 0.0])
                h[0] += 1
                h[1] += ov
    if not hits:
        return pd.DataFrame()
    rows = [{"分點": k, "隔日沖次數": v[0], "累計回吐張": int(round(v[1]))}
            for k, v in hits.items()]
    return (pd.DataFrame(rows).sort_values(["隔日沖次數", "累計回吐張"], ascending=False)
            .head(n).reset_index(drop=True))


def top_branches(sid: str, day: str, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    """最新一日 Top n 買超 / 賣超 分點（分點）。不可用回 (空, 空)。"""
    from . import broker_client as bc
    from .broker_signal import _branch_net
    if not bc.available():
        return pd.DataFrame(), pd.DataFrame()
    net = _branch_net(sid, day)
    if not net:
        return pd.DataFrame(), pd.DataFrame()
    s = sorted(net.items(), key=lambda z: z[1], reverse=True)
    buy = pd.DataFrame([{"分點": k, "淨買張": int(round(v))} for k, v in s[:n] if v > 0])
    sell = pd.DataFrame([{"分點": k, "淨賣張": int(round(v))} for k, v in s[::-1][:n] if v < 0])
    return buy, sell
