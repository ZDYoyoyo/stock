"""每檔『今日 vs 近期』籌碼訊號合成，供日報一眼判斷「誰在狂買/狂賣、今天是否背離、法人是否主導」。

產出三樣（單位：張，正=買超/增加）：
  - 外資連/投信連/自營連：當前連續同向天數（正=連買N天、負=連賣N天、0=最新為平盤）
      → 三大法人「分開」看趨勢，數字越大代表越「狂買」、越負代表越「狂賣」。
  - 主導度%：今日三大法人淨買賣超合計 ÷ 今日成交量 ×100
      → 法人買賣佔當日成交多少，比絕對張數更能看出「法人是否在主導這檔」。
  - 量能倍數：今日量 ÷ 近20日均量（>1.5＝爆量、<1＝量縮），一眼判斷今日量是否放大。
  - 券資比%：融券餘額 ÷ 融資餘額 ×100，看空方相對多方力道／軋空空間。
  - 融資佔量%／融券佔量%：今日資券增減 ÷ 今日量 ×100 —— 把資券絕對張數『相對化』，
      避免「融券−343」這種絕對數字被誤判大小（相對當日量才知是杯水車薪或真力道）。
  - 籌碼訊號：把上面濃縮成一句 emoji 標籤（今日共識／今昨背離／連買連賣強度）。

一次查全表、groupby 批次算，不逐檔打 DB，很快。
"""
from __future__ import annotations

import pandas as pd

from .db import read_table

_COLS = ["stock_id", "外資連", "投信連", "自營連", "主導度%",
         "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號"]
_STREAK_STRONG = 5   # 連買/連賣 ≥ 幾天算「狂買/狂賣」，才進訊號標籤


def _streak(nets) -> int:
    """給一串『由新到舊』的每日淨額，回傳當前連續同向天數。
    正=連買、負=連賣、0=最新為平盤或無資料。（共用 signals.consecutive_net_days，
    該函式吃時間順序 舊→新，故傳入前反轉。）"""
    from .signals import consecutive_net_days
    return consecutive_net_days(list(nets)[::-1])


def _label(ft, tt, dt, f10, fstk, tstk, dstk) -> str:
    """合成籌碼訊號標籤（最多兩段，只留高訊號：今日共識 + 連買/連賣強度）。
    ft/tt/dt=外資/投信/自營今日；f10=外資近10日；fstk/tstk/dstk=外資/投信/自營連。

    刻意不放「今日vs近10日方向相反」的背離：那在單日層級太常發生（雜訊），
    且今日/10日兩組欄位已並排、看得到；標籤專注在真正少見、可行動的訊號。"""
    tags = []
    # 1) 今日外投共識（外資+投信同向，最值得注意）
    if ft and tt:
        if ft > 0 and tt > 0:
            tags.append("🔥外投同買")
        elif ft < 0 and tt < 0:
            tags.append("❄️外投同賣")
    # 2) 連買/連賣強度（狂買狂賣，≥門檻天數才報）
    for name, stk in (("外資", fstk), ("投信", tstk), ("自營", dstk)):
        if stk >= _STREAK_STRONG:
            tags.append(f"💪{name}連買{stk}")
        elif stk <= -_STREAK_STRONG:
            tags.append(f"🩸{name}連賣{abs(stk)}")
    return "｜".join(tags[:2]) if tags else "—"


def compute(streak_days: int = 30, flow_days: int = 10) -> pd.DataFrame:
    """回傳每檔 [外資連,投信連,自營連,主導度%,籌碼訊號]（依 stock_id）。無資料回空表。"""
    inst = read_table("institutional", use_cache=True)[
        ["date", "stock_id", "foreign_net", "trust_net", "dealer_net"]]
    px = read_table("price", use_cache=True)[["date", "stock_id", "volume"]]
    if inst.empty:
        return pd.DataFrame(columns=_COLS)

    dates = sorted(inst["date"].unique())
    last = dates[-1]
    swin = dates[-streak_days:]
    sdf = inst[inst["date"].isin(swin)].sort_values("date", ascending=False)

    # 連買/連賣天數（三大法人分開）
    rows = []
    for sid, g in sdf.groupby("stock_id"):
        rows.append((sid,
                     _streak(g["foreign_net"].tolist()),
                     _streak(g["trust_net"].tolist()),
                     _streak(g["dealer_net"].tolist())))
    out = pd.DataFrame(rows, columns=["stock_id", "外資連", "投信連", "自營連"])

    # 今日分項 + 近10日外資（給訊號用）
    today = inst[inst["date"] == last].set_index("stock_id")
    fwin = dates[-flow_days:]
    f10 = inst[inst["date"].isin(fwin)].groupby("stock_id")["foreign_net"].sum()

    # 主導度% = 今日三大法人淨額 / 今日成交量
    tot_today = today["foreign_net"] + today["trust_net"] + today["dealer_net"]
    vol = px[px["date"] == last].set_index("stock_id")["volume"] if not px.empty else pd.Series(dtype=float)

    def _dom(sid):
        v = vol.get(sid)
        t = tot_today.get(sid)
        return round(t / v * 100, 1) if (v and v > 0 and t is not None) else None

    out["主導度%"] = out["stock_id"].map(_dom)

    # --- 量能／資券脈絡：把絕對張數相對化，供一眼判斷大小 ---
    # 量能倍數＝今日量÷近20日均量（用 price 自己的最新日，資券／量的基準日一致）
    pdates = sorted(px["date"].unique()) if not px.empty else []
    plast = pdates[-1] if pdates else None
    vol_today = px[px["date"] == plast].set_index("stock_id")["volume"] if plast else pd.Series(dtype=float)
    vol20 = (px[px["date"].isin(pdates[-20:])].groupby("stock_id")["volume"].mean()
             if pdates else pd.Series(dtype=float))

    def _volr(sid):
        vt, va = vol_today.get(sid), vol20.get(sid)
        return round(vt / va, 2) if (va and va > 0 and vt is not None) else None

    out["量能倍數"] = out["stock_id"].map(_volr)

    # 券資比%＝融券餘額÷融資餘額；融資／融券佔量%＝今日增減(今−昨餘額)÷今日量
    mg = read_table("margin", use_cache=True)[["date", "stock_id", "margin_balance", "short_balance"]]
    mb_cur = sb_cur = mb_prev = sb_prev = pd.Series(dtype=float)
    if not mg.empty:
        mdts = sorted(mg["date"].unique())
        mcur = mg[mg["date"] == mdts[-1]].set_index("stock_id")
        mb_cur, sb_cur = mcur["margin_balance"], mcur["short_balance"]
        if len(mdts) >= 2:
            mprev = mg[mg["date"] == mdts[-2]].set_index("stock_id")
            mb_prev, sb_prev = mprev["margin_balance"], mprev["short_balance"]

    def _short_margin_ratio(sid):
        mb, sb = mb_cur.get(sid), sb_cur.get(sid)
        return round(sb / mb * 100, 1) if (mb and mb > 0 and sb is not None) else None

    def _chg_over_vol(sid, cur, prev):
        vt, c, p = vol_today.get(sid), cur.get(sid), prev.get(sid)
        return round((c - p) / vt * 100, 1) if (vt and vt > 0 and c is not None and p is not None) else None

    out["券資比%"] = out["stock_id"].map(_short_margin_ratio)
    out["融資佔量%"] = out["stock_id"].map(lambda s: _chg_over_vol(s, mb_cur, mb_prev))
    out["融券佔量%"] = out["stock_id"].map(lambda s: _chg_over_vol(s, sb_cur, sb_prev))

    stk = out.set_index("stock_id")
    out["籌碼訊號"] = out["stock_id"].map(lambda s: _label(
        today["foreign_net"].get(s), today["trust_net"].get(s), today["dealer_net"].get(s),
        f10.get(s), int(stk.loc[s, "外資連"]), int(stk.loc[s, "投信連"]), int(stk.loc[s, "自營連"])))
    return out[_COLS]


def enrich(df: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """把籌碼訊號欄併進 df（依 stock_id）。signals 可預先算好重用。"""
    if df is None or df.empty:
        return df
    s = signals if signals is not None else compute()
    if s is None or s.empty:
        return df
    # 目標 df 已有的同名欄（如當沖軌自算的「量能倍數」）不覆蓋 → 先從 s 移除再併，避免 _x/_y 衝突
    dup = [c for c in s.columns if c != "stock_id" and c in df.columns]
    df = df.merge(s.drop(columns=dup) if dup else s, on="stock_id", how="left")
    for c in ("外資連", "投信連", "自營連"):
        if c in df.columns:
            df[c] = df[c].astype("Int64")
    return df
