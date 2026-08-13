"""隔日沖鎖碼候選（EOD 掃描）— 前日漲停/大漲 + 今日主力/隔日沖大戶鎖碼進場。

思路（散戶『隔日沖鎖碼股』打法）：某檔今日漲停/大漲，若有主力(前15分點)集中淨買
『鎖碼』，且今日大買分點正是此檔近期『隔日沖常客』(反覆昨買今賣者)→ 隔日(T+1)這些
大戶常倒貨 → 開高走低/對殺舞台。挑明日當沖要盯的 arena。

⚠️ 觀察名單、未經回測驗證有 edge、非投資建議。分點需 FinMind Sponsor；
   無 Sponsor 時仍出『漲停清單』(主力/隔日沖欄留白)。

由 DB 價量抓漲停/大漲 → 取成交額前 N 檔(流動性+控分點 call) → 分點算主力淨額+今日大買分點
→ 只對『主力淨買鎖碼』者列兩份黑名單：
  **全市場黑名單**＝跨所有股票的隔日沖慣犯(broker_profile，樣本大、帶隔日沖率%)
  **本檔黑名單**＝專門在這檔反覆昨買今賣的分點(近窗、專屬但樣本小)
＋**預估賣壓張/佔量%**＝今日這些人買的量 × 各自歷史回吐率(前瞻，今天就能算)。
"""
from __future__ import annotations

import pandas as pd

from ..db import connect

_GAIN_TH = 9.0     # 漲停 proxy：台股漲停 +10%，≥9% 捕捉漲停＋逼近漲停
_TOP_N = 15        # 只取成交額前 N 檔（當沖要流動性；也控分點 call 數）
_LOOKBACK = 8      # 隔日沖常客回看交易日數
_MIN_HITS = 2      # 窗內『昨買今賣』≥此次數才算此檔的隔日沖常客（本檔黑名單）
_MID_RATE = 50     # 跨股票隔日沖率≥此%才進全市場黑名單（對齊 broker_profile._MID）
_TOP = 15          # 主力＝前幾大分點（對齊 broker_signal）

_COLS = ["stock_id", "name", "market", "產業", "close", "漲跌%", "成交額億",
         "當沖比率%", "昨主力淨額", "今主力淨額", "隔日沖賣壓%",
         "預估賣壓張", "預估賣壓佔量%", "全市場黑名單", "本檔黑名單"]


def _regulars(sid: str, dates: list[str]) -> set:
    """此檔近窗『隔日沖常客』：窗內反覆(≥_MIN_HITS 次)昨買今賣的分點名。無分點回空。"""
    from .. import broker_signal as bs
    nets = {}
    for d in dates:
        n = bs._branch_net(sid, d)     # 走本機快取
        if n:
            nets[d] = n
    ds = [d for d in dates if d in nets]
    hits: dict[str, int] = {}
    for i in range(1, len(ds)):
        ny, nt = nets[ds[i - 1]], nets[ds[i]]
        buyers = sorted(((k, v) for k, v in ny.items() if v > 0),
                        key=lambda z: z[1], reverse=True)[:_TOP]
        for k, _ in buyers:
            if nt.get(k, 0) < 0:       # 昨買今賣
                hits[k] = hits.get(k, 0) + 1
    return {k for k, c in hits.items() if c >= _MIN_HITS}


def run(gain_th: float = _GAIN_TH, top_n: int = _TOP_N, lookback: int = _LOOKBACK,
        asof: str | None = None) -> pd.DataFrame:
    """asof=None → 用 DB 最新交易日；給日期則以該日為『今日』(供回填舊日精選/回測)。"""
    with connect() as conn:
        price = pd.read_sql("SELECT date, stock_id, close, volume FROM price", conn)
        info = pd.read_sql("SELECT stock_id, stock_name, type, industry FROM stock_info", conn)
    if price.empty:
        return pd.DataFrame(columns=_COLS)
    dates = sorted(price["date"].unique())
    if asof:
        dates = [d for d in dates if d <= asof]      # 以 asof 為『今日』(含)，模擬當時盤後
    if len(dates) < 2:
        return pd.DataFrame(columns=_COLS)
    today, prev = dates[-1], dates[-2]
    cur = price[price["date"] == today].set_index("stock_id")
    pv = price[price["date"] == prev].set_index("stock_id")["close"]
    name_map = info.set_index("stock_id")["stock_name"].to_dict()
    mkt_map = info.set_index("stock_id")["type"].to_dict()
    ind_map = info.set_index("stock_id")["industry"].to_dict()

    # 1) 漲停/大漲 + 流動性
    rows = []
    for sid, r in cur.iterrows():
        c, v = r["close"], r["volume"]
        if sid not in pv.index or pv[sid] <= 0 or not v or v <= 0:
            continue
        chg = (c - pv[sid]) / pv[sid] * 100
        if chg < gain_th:
            continue
        rows.append({"stock_id": sid, "name": name_map.get(sid, ""),
                     "market": "上櫃" if mkt_map.get(sid) == "tpex" else "上市",
                     "產業": ind_map.get(sid) or "",
                     "close": round(c, 2), "漲跌%": round(chg, 2),
                     "成交額億": round(c * v / 1e5, 2)})
    if not rows:
        df = pd.DataFrame(columns=_COLS)
        df.attrs["asof"] = today
        return df
    df = pd.DataFrame(rows).sort_values("成交額億", ascending=False).head(top_n).reset_index(drop=True)

    # 2) 當沖比率%（DB day_trade，選配）
    try:
        from .. import day_trade_signal as dts
        _dt = dts.trend  # noqa: F401  (確保模組載入)
        with connect() as conn:
            ddf = pd.read_sql("SELECT stock_id, dt_vol FROM day_trade WHERE date=?", conn, params=(today,))
        volmap = cur["volume"].to_dict()
        dtr = {r.stock_id: round(r.dt_vol / volmap[r.stock_id] * 100, 1)
               for r in ddf.itertuples() if volmap.get(r.stock_id, 0) > 0}
        df["當沖比率%"] = df["stock_id"].map(dtr)
    except Exception:
        df["當沖比率%"] = pd.NA

    # 3) 分點：昨/今主力淨額 + 隔日沖賣壓%(昨買今賣實現) + 兩份黑名單 + 預估賣壓（需 Sponsor；否則留白）
    from .. import broker_client as bc
    prev_net, main_net, sell_pressure = {}, {}, {}
    market_bl, stock_bl, est_lots, est_pct = {}, {}, {}, {}
    if bc.available():
        from .. import broker_signal as bs
        from .. import broker_profile as bp
        pmap = bp.as_map()                       # 跨股票分點檔案（讀本機快取、零 API）
        win = dates[-(lookback + 1):]
        volmap = cur["volume"].to_dict()
        for sid in df["stock_id"]:
            one = bs._one(sid, today, prev, volmap.get(sid))   # 今主力淨額 + 隔日沖賣壓%（複用同一公式）
            if not one:
                continue
            main_net[sid] = one["主力淨額"]
            if "隔日沖賣壓%" in one:
                sell_pressure[sid] = one["隔日沖賣壓%"]
            net_y = bs._branch_net(sid, prev)                  # 昨主力淨額（快取，_one 已抓過 T-1）
            if net_y:
                vy = sorted(net_y.values(), reverse=True)
                prev_net[sid] = int(round(sum(x for x in vy[:_TOP] if x > 0)
                                          + sum(x for x in vy[-_TOP:] if x < 0)))
            if one["主力淨額"] > 0:                             # 只對『今日主力淨買鎖碼』者查隔日沖名單（省 call）
                net_t = bs._branch_net(sid, today)             # 快取，_one 已抓過 T
                top_buyers = {k for k, v in sorted(net_t.items(), key=lambda z: z[1], reverse=True)[:_TOP]
                              if v > 0}
                # 兩份黑名單分開列（不同訊號，別混）：
                #  ①全市場＝跨所有股票的慣犯(樣本大、可信)  ②本檔＝專門玩這檔的(專屬、樣本小)
                mkt = sorted((k for k in top_buyers if (pmap.get(k) or (0,))[0] >= _MID_RATE),
                             key=lambda k: -pmap[k][0])
                if mkt:
                    market_bl[sid] = "、".join(f"{k}{int(pmap[k][0])}%" for k in mkt[:2]) + \
                                     (f" +{len(mkt) - 2}" if len(mkt) > 2 else "")
                own = sorted(_regulars(sid, win) & top_buyers)
                if own:
                    stock_bl[sid] = "、".join(own[:2]) + (f" +{len(own) - 2}" if len(own) > 2 else "")
                # 前瞻預估：今日這些人買的量 × 各自歷史回吐率 → 明日潛在賣壓
                est = bp.expected_pressure(net_t, volmap.get(sid), pmap)
                if est:
                    est_lots[sid] = est.get("預估賣壓張")
                    est_pct[sid] = est.get("預估賣壓佔量%")
    df["昨主力淨額"] = df["stock_id"].map(prev_net).astype("Int64")
    df["今主力淨額"] = df["stock_id"].map(main_net).astype("Int64")
    df["隔日沖賣壓%"] = df["stock_id"].map(sell_pressure)
    df["預估賣壓張"] = df["stock_id"].map(est_lots).astype("Int64")
    df["預估賣壓佔量%"] = df["stock_id"].map(est_pct)
    df["全市場黑名單"] = df["stock_id"].map(market_bl)
    df["本檔黑名單"] = df["stock_id"].map(stock_bl)

    df = df[_COLS]
    df.attrs["asof"] = today
    df.attrs["window"] = lookback
    return df
