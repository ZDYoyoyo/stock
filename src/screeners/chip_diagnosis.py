"""個股籌碼診斷 — 三大法人(外資/投信/自營)＋融資融券逐日，並比對今vs昨推敲漲跌原因。

回答兩件事：
  1. 這檔的籌碼動向到底誰在買賣（外資？投信？自營？散戶(融資)？空方(融券)？）
  2. 今天為什麼漲/跌 —— 用今日 vs 昨日的籌碼變化 + 量價，做規則式歸因（非因果保證）。

⚠️ 研究用途，非投資建議。法人/融資券為當日收盤後官方數據；歸因為經驗規則推敲，
   不代表唯一原因（消息面/國際盤/權值連動等未納入）。
"""
from __future__ import annotations

import pandas as pd

from ..db import connect


def _fetch(sid: str, days: int) -> pd.DataFrame:
    with connect() as conn:
        px = pd.read_sql(
            "SELECT date, close, volume FROM price WHERE stock_id=? ORDER BY date DESC LIMIT ?",
            conn, params=(sid, days + 1))
        inst = pd.read_sql(
            "SELECT date, foreign_net, trust_net, dealer_net FROM institutional "
            "WHERE stock_id=? ORDER BY date DESC LIMIT ?", conn, params=(sid, days + 1))
        mg = pd.read_sql(
            "SELECT date, margin_balance, short_balance FROM margin "
            "WHERE stock_id=? ORDER BY date DESC LIMIT ?", conn, params=(sid, days + 2))
    if px.empty:
        return pd.DataFrame()
    df = px.sort_values("date").merge(inst, on="date", how="left").merge(mg, on="date", how="left")
    df = df.sort_values("date").reset_index(drop=True)
    df["漲跌%"] = (df["close"].pct_change() * 100).round(2)
    for c in ("foreign_net", "trust_net", "dealer_net"):
        df[c] = df[c].fillna(0).astype(int)
    df["三大法人"] = df["foreign_net"] + df["trust_net"] + df["dealer_net"]
    df["融資增減"] = df["margin_balance"].diff()
    df["融券增減"] = df["short_balance"].diff()
    return df


def daily_table(df: pd.DataFrame) -> pd.DataFrame:
    """整理成好讀的逐日表（中文欄、近 N 日）。"""
    view = df.copy()
    view = view.rename(columns={
        "date": "日期", "close": "收盤", "foreign_net": "外資", "trust_net": "投信",
        "dealer_net": "自營", "margin_balance": "融資餘額", "short_balance": "融券餘額",
        "volume": "量(張)"})
    cols = ["日期", "收盤", "漲跌%", "外資", "投信", "自營", "三大法人",
            "融資餘額", "融資增減", "融券餘額", "融券增減", "量(張)"]
    return view[[c for c in cols if c in view.columns]]


def attribute(df: pd.DataFrame) -> list[str]:
    """今日 vs 昨日的漲跌歸因（規則式）。回傳條列說明。"""
    if len(df) < 2:
        return ["資料不足，無法歸因（需至少 2 個交易日）。"]
    t = df.iloc[-1]
    chg = t["漲跌%"]
    fn, tn, dn = int(t["foreign_net"]), int(t["trust_net"]), int(t["dealer_net"])
    inst = fn + tn + dn
    mg_chg = t["融資增減"]
    sh_chg = t["融券增減"]
    vol, avg_vol = t["volume"], df["volume"].iloc[:-1].mean()
    vratio = vol / avg_vol if avg_vol else 0
    up = chg is not None and chg > 0

    lines = []
    # 三大法人
    flows = [("外資", fn), ("投信", tn), ("自營", dn)]
    who = "、".join(f"{n}{v:+}" for n, v in flows)
    buyer = max(flows, key=lambda x: x[1])
    seller = min(flows, key=lambda x: x[1])
    # 對做偵測：最大買方與最大賣方都遠大於淨額 → 法人分歧對做（例：投信買、外資賣）
    if buyer[1] > 0 and seller[1] < 0 and min(buyer[1], -seller[1]) > max(abs(inst), 1) * 1.5:
        lead = f"{buyer[0]}大買 vs {seller[0]}大賣（法人對做，淨{inst:+}）"
    else:
        big = max(flows, key=lambda x: abs(x[1]))
        lead = f"主導 {big[0]}({big[1]:+})"
    if inst > 0:
        tone = "法人偏買方" + ("，與股價同向（買盤推升）" if up else "，逆勢承接（趁跌接貨）")
    elif inst < 0:
        tone = "法人偏賣方" + ("，但股價仍漲（散戶/隔日沖拉抬，續航存疑）" if up else "，賣壓主導下跌")
    else:
        tone = "法人大致中性"
    lines.append(f"法人：{who}（合計 {inst:+}張）→ {tone}；{lead}")
    # 融資（散戶）
    if pd.notna(mg_chg) and mg_chg != 0:
        if mg_chg > 0:
            s = "散戶追價加碼（過熱留意）" if up else "散戶接刀加碼（套牢賣壓風險）"
        else:
            s = "散戶退場、籌碼換手（漲勢較健康）" if up else "散戶停損/斷頭殺出"
        lines.append(f"融資：{int(mg_chg):+}張 → {s}")
    # 融券（空方）
    if pd.notna(sh_chg) and sh_chg != 0:
        if sh_chg > 0:
            s = "空方進場/避險增加（後續有回補動能）"
        else:
            s = "融券回補（若同步大漲＝軋空助攻）" if up else "空方獲利了結回補"
        lines.append(f"融券：{int(sh_chg):+}張 → {s}")
    # 量能
    if vratio:
        vs = f"爆量 {vratio:.1f} 倍" if vratio >= 1.5 else (f"量縮 {vratio:.1f} 倍" if vratio < 0.7 else f"量能 {vratio:.1f} 倍")
        vt = ("帶量" if vratio >= 1.5 else "無量")
        lines.append(f"量能：{vs} → {vt}{'上漲較實' if up else '下跌'}" if vratio >= 1.5 or vratio < 0.7 else f"量能：{vs}")
    return lines


def one_line(df: pd.DataFrame) -> str:
    """一句話定調今天漲跌的最可能主因。"""
    if len(df) < 2:
        return "資料不足"
    t = df.iloc[-1]
    chg, inst = t["漲跌%"], int(t["foreign_net"] + t["trust_net"] + t["dealer_net"])
    mg_chg = t["融資增減"] if pd.notna(t["融資增減"]) else 0
    up = chg is not None and chg > 0
    if up:
        if inst > 0 and mg_chg <= 0:
            return "法人買、散戶未追 → 籌碼較乾淨的上漲（品質佳）"
        if inst > 0 and mg_chg > 0:
            return "法人+散戶齊買 → 追價盤（留意過熱）"
        if inst <= 0 and mg_chg > 0:
            return "法人沒買、散戶追 → 散戶盤，續航力存疑"
        return "法人未明顯買 → 題材/當沖拉抬為主"
    else:
        if inst < 0 and mg_chg > 0:
            return "法人賣、散戶接刀 → 最危險組合（賣壓＋套牢）"
        if inst < 0:
            return "法人賣壓主導下跌"
        if mg_chg < 0:
            return "散戶停損殺出為主"
        return "量價回檔，法人未明顯出貨"
