"""跨股票『分點行為檔案』— 這家分點到底是不是隔日沖玩家（不是只看單一檔股票）。

痛點：原本判斷隔日沖只看「這檔股票近 8 個交易日內、這個分點昨買今賣≥2 次」，
樣本太小、雜訊大（買完隔天剛好賣一次就被歸類）。

作法：把**已累積的分點快取**（DB `broker_net`，run_all/深掘/回測每天自然累積）
跨**所有股票、所有日期**聚合成每個分點的行為統計：
  - 隔日沖率%＝(進前15大買超後、隔日轉淨賣的次數) ÷ (進前15大買超的總次數)
  - 回吐量%＝隔日實際對沖掉的張數 ÷ 當初買進張數（比「次數」更能反映倒貨力道）
  - 樣本數/股票數＝可信度（跨越越多檔越不可能是巧合）

實測分離度很好：台新松德 71.8%(110樣本/76檔)、元大竹科 69.6% vs
大和國泰(外資券商) 19.7%(173樣本/68檔) → 高低分明。

⚠️ 零 API 成本（只讀本機快取）。快取是本機累積的（不進 git），
   新機器/剛裝時樣本少 → 用 min_ops 過濾，並在報告顯示樣本數讓使用者自行判斷。
⚠️ 分點＝券商分公司，不是個人；淨額會被同分點內不同客戶互相抵消（偏保守低估）。
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd

from .db import connect

_TOP = 15          # 「大買超」＝當日前幾大買超分點（對齊 broker_signal._TOP）
_MIN_OPS = 10      # 至少幾次「進前15大買」樣本才納入檔案（低於此不可信）

# 分級門檻（隔日沖率%）
_HOT = 65          # ≥ → 🔥隔日沖大戶
_MID = 50          # ≥ → ⚠️偏隔日沖
_LONG = 30         # < → 🏦偏長線（買了不隔日跑）

_COLS = ["分點", "隔日沖率%", "回吐量%", "樣本數", "股票數", "分點類型"]


def load_cache() -> dict:
    """{stock_id: {date: {分點: 淨買張}}}（讀 DB broker_net 快取）。無快取回 {}。"""
    try:
        with connect() as c:
            rows = c.execute("SELECT date, stock_id, nets FROM broker_net").fetchall()
    except Exception:
        return {}
    by: dict[str, dict] = defaultdict(dict)
    for d, sid, js in rows:
        try:
            by[sid][d] = json.loads(js)
        except Exception:
            continue
    return by


def _label(rate: float) -> str:
    if rate >= _HOT:
        return "🔥隔日沖大戶"
    if rate >= _MID:
        return "⚠️偏隔日沖"
    if rate < _LONG:
        return "🏦偏長線"
    return "➖中性"


def build(min_ops: int = _MIN_OPS, top: int = _TOP, before: str | None = None,
          cache: dict | None = None) -> pd.DataFrame:
    """跨股票聚合分點行為檔案。無快取/樣本不足回空表。

    before＝只用「該日之前」的資料建檔（point-in-time）。**回測必須傳**，
    否則等於拿未來資料判斷過去（前視偏誤）；日常跑報告不用傳（就是要用全部歷史）。
    cache 可預先載入重用（回測逐日重建時避免每次重讀 DB）。
    """
    by = cache if cache is not None else load_cache()
    if not by:
        return pd.DataFrame(columns=_COLS)

    stat: dict[str, dict] = defaultdict(
        lambda: {"ops": 0, "flips": 0, "bought": 0.0, "dumped": 0.0, "stocks": set()})
    for sid, dm in by.items():
        ds = sorted(d for d in dm if before is None or d < before)
        for i in range(len(ds) - 1):
            n0, n1 = dm[ds[i]], dm[ds[i + 1]]
            buyers = sorted(((k, v) for k, v in n0.items() if v > 0),
                            key=lambda z: z[1], reverse=True)[:top]
            for k, v in buyers:
                s = stat[k]
                s["ops"] += 1
                s["bought"] += v
                s["stocks"].add(sid)
                t = n1.get(k, 0)
                if t < 0:                              # 昨買今賣
                    s["flips"] += 1
                    s["dumped"] += min(v, -t)          # 實際對沖掉的量

    rows = []
    for k, s in stat.items():
        if s["ops"] < min_ops:
            continue
        rate = round(s["flips"] / s["ops"] * 100, 1)
        rows.append({"分點": k, "隔日沖率%": rate,
                     "回吐量%": round(s["dumped"] / s["bought"] * 100, 1) if s["bought"] else 0.0,
                     "樣本數": s["ops"], "股票數": len(s["stocks"]), "分點類型": _label(rate)})
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return (pd.DataFrame(rows).sort_values(["隔日沖率%", "樣本數"], ascending=False)
            .reset_index(drop=True))


_cached: pd.DataFrame | None = None


def get(min_ops: int = _MIN_OPS) -> pd.DataFrame:
    """檔案（單次執行內記憶體快取，避免各軌重複掃全快取）。"""
    global _cached
    if _cached is None:
        _cached = build(min_ops=min_ops)
    return _cached


def as_map(profile: pd.DataFrame | None = None) -> dict:
    """{分點: (隔日沖率%, 回吐量%, 樣本數, 類型)}，供逐檔快速查。"""
    p = profile if profile is not None else get()
    if p is None or p.empty:
        return {}
    return {r["分點"]: (r["隔日沖率%"], r["回吐量%"], r["樣本數"], r["分點類型"])
            for _, r in p.iterrows()}


def expected_pressure(net_today: dict, vol, pmap: dict | None = None,
                      top: int = _TOP, min_rate: float = _MID) -> dict:
    """由『今日誰在買』＋分點歷史回吐率 → **預估明日隔日沖賣壓**。

    預估賣壓張 = Σ(今日該分點淨買張 × 該分點歷史回吐量%)，只計隔日沖率≥min_rate 的分點。
    這是**前瞻**估計（今天就能算），與 broker_signal 的「隔日沖賣壓%」不同——
    那個是**回顧**（昨買今賣已經發生）。

    回 {預估賣壓張, 預估賣壓佔量%, 鎖碼隔日沖分點數}；無資料回 {}。
    """
    if not net_today:
        return {}
    pm = pmap if pmap is not None else as_map()
    if not pm:
        return {}
    buyers = sorted(((k, v) for k, v in net_today.items() if v > 0),
                    key=lambda z: z[1], reverse=True)[:top]
    est, n_hot = 0.0, 0
    for k, v in buyers:
        info = pm.get(k)
        if not info:
            continue
        rate, dump_ratio, _, _ = info
        if rate >= min_rate:
            est += v * dump_ratio / 100
            n_hot += 1
    if n_hot == 0:
        return {"預估賣壓張": 0, "預估賣壓佔量%": 0.0, "隔日沖分點數": 0}
    out = {"預估賣壓張": int(round(est)), "隔日沖分點數": n_hot}
    out["預估賣壓佔量%"] = round(est / vol * 100, 1) if vol and vol > 0 else None
    return out


def annotate(names, pmap: dict | None = None) -> list:
    """分點名清單 → 帶檔案標註的字串（供深掘 Top 分點表顯示可信度）。"""
    pm = pmap if pmap is not None else as_map()
    out = []
    for n in names:
        info = pm.get(n)
        out.append(f"{info[3]} {info[0]:.0f}%({info[2]}樣本)" if info else "—")
    return out
