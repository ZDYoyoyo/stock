"""昨日精選追蹤 — 每天記錄篩出的波段(T11/T16)與當沖前 N 名，隔日對比今天表現＋找原因。

為什麼要存：要「昨天選的今天表現如何、為什麼」就得先把每天的精選名單留下來。
存 data/history/picks.csv（進 git，一天天累積，也供未來回測「隔日追蹤」策略）。
欄位：date, track, rank, stock_id, name
"""
from __future__ import annotations

import re

import pandas as pd

from .config import DATA_DIR
from .screeners import chip_diagnosis as cd

PICKS_CSV = DATA_DIR / "history" / "picks.csv"
_COLS = ["date", "track", "rank", "stock_id", "name", "主力淨額", "預估賣壓%",
         "全市場黑名單", "本檔黑名單", "黑名單明細"]
# pick 當下才算得出黑名單（要當日分點 + 當時的 broker_profile）→ 存下來，隔日追蹤直接顯示
_EXTRA = ("主力淨額", "預估賣壓%", "全市場黑名單", "本檔黑名單", "黑名單明細")


def _load() -> pd.DataFrame:
    if not PICKS_CSV.exists():
        return pd.DataFrame(columns=_COLS)
    df = pd.read_csv(PICKS_CSV, dtype={"stock_id": str})
    for c in _EXTRA:                          # 舊檔無此欄→補空，向後相容
        if c not in df.columns:
            df[c] = pd.NA
    return df


def save(today: str, tracks: dict, n: int = 15) -> pd.DataFrame:
    """把今天各軌前 n 名存進 picks.csv（同日重跑會覆蓋當日，不重複累積）。

    tracks: {軌名: DataFrame(需含 stock_id, name)}。若 df 有『今主力淨額』欄
    （隔日沖鎖碼軌）一併存下 → 追蹤時可直接顯示『當時鎖碼買了多少』，免重算；
    『預估賣壓佔量%』同理存下 → 隔日可比對「預測 vs 實際」賣壓；
    『全市場黑名單/本檔黑名單』也存下 → 追蹤時看得到當時是誰在鎖碼（隔日重算會失真：
    分點快取會被 prune、broker_profile 也一直在更新，不是 pick 當下那份）；
    『黑名單明細』（逐分點當日買進張數）同理存下 → 隔日可逐點算「這個人賣掉幾張」。
    """
    df = _load()
    df = df[df["date"] != today]  # 同日全清後重寫
    rows = []
    for track, tdf in tracks.items():
        if tdf is None or tdf.empty:
            continue
        for rank, (_, r) in enumerate(tdf.head(n).iterrows(), 1):
            mf = r.get("今主力淨額", pd.NA)
            ep = r.get("預估賣壓佔量%", pd.NA)       # 當時『預測』明日賣壓 → 隔日可對照實際
            rows.append({"date": today, "track": track, "rank": rank,
                         "stock_id": str(r["stock_id"]), "name": r.get("name", ""),
                         "主力淨額": int(mf) if pd.notna(mf) else pd.NA,
                         "預估賣壓%": ep if pd.notna(ep) else pd.NA,
                         "全市場黑名單": r.get("全市場黑名單", pd.NA),
                         "本檔黑名單": r.get("本檔黑名單", pd.NA),
                         "黑名單明細": r.get("黑名單明細", pd.NA)})
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
        if track == "隔日沖鎖碼":          # 有專屬 OHLC 走勢區塊(snipe_ohlc)，不在通用追蹤重複列
            continue
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


_BL_RE = re.compile(r"^(.+)\+(-?\d+)$")


def _parse_bl(s) -> dict:
    """『凱基台北+1234、美林+890』→ {分點: 當日買進張}。空/NA/格式不符回 {}。"""
    if s is None or (not isinstance(s, str) and pd.isna(s)):
        return {}
    out = {}
    for part in str(s).split("、"):
        m = _BL_RE.match(part.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def snipe_ohlc(today: str, track: str = "隔日沖鎖碼") -> dict:
    """昨日『隔日沖鎖碼候選』→ 今日開高低收走勢（具體看『開高走低』有沒有發生）。

    回傳 {"date": 昨日, "trade_date": 今交易日,
          "rows": [{rank, stock_id, name, 鎖碼淨額, 全市場黑名單, 本檔黑名單,
                    黑名單買張, 黑名單賣張, 倒貨%, 黑名單逐點,
                    預估賣壓%, 實際賣壓%, 今主力淨額,
                    昨收, 今開, 今高, 今低, 今收, 漲跌%, 跳空%, 盤中%, 高檔回落%, 振幅%,
                    量能倍數, 當沖比%}]}。
      鎖碼淨額＝pick 當日主力淨額(前15買+前15賣)＝『當時鎖碼買了多少』(存檔時記下、免重算)。
      全市場/本檔黑名單＝昨天列出的隔日沖分點(存檔時記下)→ 可直接對照「這些人今天倒了沒」。
      **黑名單買張/賣張/倒貨%/逐點**＝昨天上榜分點各買了幾張、今天各倒了幾張(倒貨%＝賣÷買)；
      逐點如「凱基台北 買1234→賣1100(89%)」，今日反手續買則標『今再買』。需 Sponsor 分點。
      跳空%＝(今開−昨收)/昨收（隔夜高開幅度）；盤中%＝(今收−今開)/今開（開高走低幅度，負=走低）。
      **解釋漲跌原因的欄**：預估賣壓%(昨天預測)vs實際賣壓%(今天真的倒多少)＝預測有沒有兌現；
      今主力淨額(今天主力續買撐盤/在倒)；高檔回落%(衝高後被倒 vs 守住高檔)；
      振幅%/量能倍數/當沖比%(對殺熱度)。分點欄需 Sponsor，抓不到留白。
    無前一日鎖碼紀錄 / 無今日價量 → 回 {}。
    """
    df = _load()
    picks = df[df["track"] == track]
    past = sorted(d for d in picks["date"].unique() if str(d) < today)
    pdate = past[-1] if past else None
    if not pdate:
        return {}
    from .db import connect
    with connect() as conn:
        px = pd.read_sql("SELECT date, stock_id, open, high, low, close, volume FROM price", conn)
    if px.empty:
        return {}
    trading = sorted(px["date"].unique())
    # 基準交易日＝pick 當時的最後交易日(≤pdate)：pdate 可能落在週末/假日(非交易日標籤)，
    # 直接用 pdate 查昨收會撈空 → 退到 ≤pdate 的最後一個真交易日。今交易日＝最新。
    ref = next((d for d in reversed(trading) if d <= pdate), None)
    tdate = trading[-1]
    if ref is None or tdate <= ref:
        return {}
    cur = px[px["date"] == tdate].set_index("stock_id")
    yclose = px[px["date"] == ref].set_index("stock_id")["close"]
    sub = picks[picks["date"] == pdate].sort_values("rank")
    mf_map = dict(zip(sub["stock_id"].astype(str), sub["主力淨額"]))   # 存檔時記下的當日鎖碼淨額
    ep_map = dict(zip(sub["stock_id"].astype(str), sub["預估賣壓%"]))  # 當時預測的明日賣壓
    # 昨天列出的兩份黑名單（存檔時記下＝pick 當下那一份，不重算）
    mb_map = dict(zip(sub["stock_id"].astype(str), sub["全市場黑名單"]))
    sb_map = dict(zip(sub["stock_id"].astype(str), sub["本檔黑名單"]))
    # 逐分點「昨天各買幾張」→ 今日比對得出「各賣幾張」
    bl_map = {str(k): _parse_bl(v) for k, v in zip(sub["stock_id"], sub["黑名單明細"])}

    # ---- 解釋今天為何漲/跌的欄位（都對照 ref→tdate 這一天）----
    ids = [str(x) for x in sub["stock_id"]]
    volmap = cur["volume"].to_dict() if "volume" in cur.columns else {}
    # 量能倍數＝今日量÷前20日均量（爆量=換手/對殺、量縮=沒人接）
    vr_map = {}
    with connect() as conn:
        vhist = pd.read_sql("SELECT date, stock_id, volume FROM price WHERE stock_id IN "
                            f"({','.join('?' * len(ids))})", conn, params=ids) if ids else pd.DataFrame()
        dtv = pd.read_sql("SELECT stock_id, dt_vol FROM day_trade WHERE date=?",
                          conn, params=(tdate,))
    if not vhist.empty:
        for sid, g in vhist.sort_values("date").groupby("stock_id"):
            prev20 = g[g["date"] < tdate]["volume"].tail(20)
            v = volmap.get(sid)
            if len(prev20) >= 5 and prev20.mean() > 0 and v:
                vr_map[sid] = round(v / prev20.mean(), 2)
    dt_map = {r.stock_id: round(r.dt_vol / volmap[r.stock_id] * 100, 1)
              for r in dtv.itertuples() if volmap.get(r.stock_id, 0) > 0}

    # 實際隔日沖賣壓%＝昨日前15大買分點今日轉賣的對沖量÷今量（＝昨天鎖碼的人今天真的倒了沒）
    # ＋今主力淨額（今天主力是續買撐盤還是在倒）。需 Sponsor 分點；走本機快取多半免重抓。
    real_map, tnet_map = {}, {}
    bl_buy, bl_sell, bl_line = {}, {}, {}
    try:
        from . import broker_client as bc
        if bc.available():
            from . import broker_signal as bs
            for sid in ids:
                one = bs._one(sid, tdate, ref, volmap.get(sid))
                if one:
                    tnet_map[sid] = one.get("主力淨額")
                    if "隔日沖賣壓%" in one:
                        real_map[sid] = one["隔日沖賣壓%"]
                # 昨天上黑名單的分點，今天各自倒了幾張（net_t 已是張，不再換算）
                bl = bl_map.get(sid) or {}
                net_t = bs._branch_net(sid, tdate) if bl else {}
                if bl and net_t:
                    parts, sold = [], 0
                    for k, b in sorted(bl.items(), key=lambda z: -z[1]):
                        v = net_t.get(k)
                        if v is None or round(v) == 0:
                            parts.append(f"{k} 買{b}→今無進出")
                            continue
                        v = int(round(v))
                        if v < 0:
                            sold += -v
                            parts.append(f"{k} 買{b}→賣{-v}"
                                         + (f"({round(-v / b * 100)}%)" if b > 0 else ""))
                        else:
                            parts.append(f"{k} 買{b}→今再買+{v}")
                    bl_buy[sid] = sum(bl.values())
                    bl_sell[sid] = sold
                    bl_line[sid] = "、".join(parts)
    except Exception:
        pass

    rows = []
    for r in sub.itertuples():
        sid = str(r.stock_id)
        if sid not in cur.index or sid not in yclose.index:
            continue
        o, h, l, c = (cur.loc[sid, "open"], cur.loc[sid, "high"],
                      cur.loc[sid, "low"], cur.loc[sid, "close"])
        yc = yclose[sid]
        if not yc or yc <= 0 or not o or o <= 0 or pd.isna(o) or pd.isna(c):
            continue
        mf, ep = mf_map.get(sid), ep_map.get(sid)
        tn = tnet_map.get(sid)
        bb, bs_ = bl_buy.get(sid), bl_sell.get(sid)
        rows.append({"rank": int(r.rank), "stock_id": sid, "name": r.name,
                     "鎖碼淨額": int(mf) if pd.notna(mf) else pd.NA,
                     "全市場黑名單": mb_map.get(sid) if pd.notna(mb_map.get(sid)) else "",
                     "本檔黑名單": sb_map.get(sid) if pd.notna(sb_map.get(sid)) else "",
                     "黑名單買張": bb if bb is not None else pd.NA,
                     "黑名單賣張": bs_ if bs_ is not None else pd.NA,
                     "倒貨%": round(bs_ / bb * 100, 1) if bb else pd.NA,
                     "黑名單逐點": bl_line.get(sid, ""),
                     "預估賣壓%": ep if pd.notna(ep) else pd.NA,
                     "實際賣壓%": real_map.get(sid, pd.NA),
                     "今主力淨額": int(tn) if tn is not None and pd.notna(tn) else pd.NA,
                     "昨收": round(yc, 2), "今開": round(o, 2), "今高": round(h, 2),
                     "今低": round(l, 2), "今收": round(c, 2),
                     "漲跌%": round((c - yc) / yc * 100, 2),
                     "跳空%": round((o - yc) / yc * 100, 2),
                     "盤中%": round((c - o) / o * 100, 2),
                     # 高檔回落%＝今收 vs 今高：跌深＝衝高後被倒貨；接近0＝守在高檔
                     "高檔回落%": round((c - h) / h * 100, 2) if h and h > 0 else pd.NA,
                     "振幅%": round((h - l) / yc * 100, 2) if h and l else pd.NA,
                     "量能倍數": vr_map.get(sid, pd.NA),
                     "當沖比%": dt_map.get(sid, pd.NA)})
    return {"date": ref, "trade_date": tdate, "rows": rows} if rows else {}


def snipe_ohlc_stats(so: dict) -> dict:
    """今日走勢彙總：均跳空%、均盤中%、盤中走低家數/總數（驗證開高走低）。"""
    rows = so.get("rows", []) if so else []
    if not rows:
        return {}
    gap = [r["跳空%"] for r in rows]
    oc = [r["盤中%"] for r in rows]
    return {"gap": round(sum(gap) / len(gap), 2), "oc": round(sum(oc) / len(oc), 2),
            "oc_down": sum(1 for x in oc if x < 0), "n": len(rows)}


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
