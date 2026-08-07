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
