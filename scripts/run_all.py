"""一鍵跑全部：環境紅綠燈 + 三軌選股，輸出單一整合報告。

流程：
  (可選) 增量更新資料
  環境：多空紅綠燈 + 全球市場（費半等）
  波段軌：T11 法人吸貨 + T16 抗跌強勢 + 雙訊號交集
  長期軌：價值+成長+配息（較慢，FinMind 深掘；--skip-longterm 可略過）
  當沖軌：當沖候選（高波動+高流動）

用法（專案根目錄）：
    python -m scripts.run_all                 # 全部
    python -m scripts.run_all --no-update     # 不重抓資料
    python -m scripts.run_all --skip-longterm # 略過較慢的長期軌
"""
import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR
from src import regime as regime_mod, global_market as gm, report_html
from src.screeners import institutional_accumulation as t11
from src.screeners import relative_strength as t16
from src.screeners import day_trade_candidates as daytrade
from src.screeners import long_term_value as lt
from src.screeners import revenue_momentum as t12
from src.screeners import landmine

_T16_SHOW = 15   # T16 抗跌強勢顯示/排雷檔數


def _is_cloud() -> bool:
    """雲端/排程沙箱（另有自身提交流程）→ 盤後不自動 push，避免與其衝突。本機兩者皆無。"""
    return bool(os.getenv("CLAUDE_CODE_REMOTE") or os.getenv("IS_SANDBOX"))


def _update(days: int):
    print(f"[更新] 抓最近 {days} 天資料 …")
    subprocess.run([sys.executable, "-m", "scripts.update_data", "--days", str(days)],
                   cwd=str(ROOT), check=True)


def _asof_note(df, kind):
    """由 screener 回傳的 df.attrs 組出「資料日期」說明，避免區間值被誤讀成單日。"""
    if df is None:
        return None
    asof = df.attrs.get("asof")
    win = df.attrs.get("window")
    if not asof:
        return None
    if kind == "t11":
        return (f"法人資料截至 {asof}（表中「收盤」為該基準日收盤，非最新交易日；"
                f"「區間漲幅%」為近 {win} 交易日累積漲幅，非單日漲跌）")
    if kind == "t16":
        return f"資料截至 {asof}（「區間漲幅%」「相對大盤%」為近 {win} 交易日累積值，非單日）"
    if kind == "daytrade":
        return f"資料截至 {asof}（「當日振幅%」為該日振幅；「均振幅%」為近 {win} 日平均）"
    return f"資料截至 {asof}"


def _today_px():
    """回傳 {sid: (今日收盤, 今日漲跌%)}，取最新交易日 vs 前一日。"""
    import pandas as pd
    from src.db import connect
    with connect() as conn:
        px = pd.read_sql("SELECT date, stock_id, close FROM price", conn)
    if px.empty:
        return {}
    dates = sorted(px["date"].unique())
    last = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None
    cur = px[px["date"] == last].set_index("stock_id")["close"]
    pv = px[px["date"] == prev].set_index("stock_id")["close"] if prev else None
    out = {}
    for sid, c in cur.items():
        chg = None
        if pv is not None and sid in pv.index and pv[sid] > 0:
            chg = round((c - pv[sid]) / pv[sid] * 100, 2)
        out[sid] = (round(c, 2), chg)
    return out


def _today_inst():
    """回傳 {sid: 今日三大法人合計(張，正=買超)}，取 institutional 最新日的單日合計。

    用途：與券商App顯示的「今日三大法人買賣超合計」對帳（旁邊的『外資10日』等是近10日
    累積、看趨勢；此欄是今天單日、對得起券商數字）。
    """
    import pandas as pd
    from src.db import connect
    with connect() as conn:
        try:
            df = pd.read_sql("SELECT date, stock_id, foreign_net, trust_net, dealer_net, "
                             "total_net FROM institutional", conn)
        except Exception:   # 舊 DB 尚無 total_net 欄
            df = pd.read_sql("SELECT date, stock_id, foreign_net, trust_net, dealer_net "
                             "FROM institutional", conn)
            df["total_net"] = pd.NA
    if df.empty:
        return {}
    last = sorted(df["date"].unique())[-1]
    d = df[df["date"] == last].copy()
    comp = d["foreign_net"] + d["trust_net"] + d["dealer_net"]      # 三分項相加（退路）
    tot = pd.to_numeric(d["total_net"], errors="coerce").fillna(comp)  # 優先用官方合計欄
    return dict(zip(d["stock_id"], tot.astype(int)))


def _add_today(df, tpx):
    """把今日收盤/今日漲跌% 併進 df（依 stock_id）。"""
    if df is None or df.empty or "stock_id" not in df.columns:
        return df
    df["今日收盤"] = df["stock_id"].map(lambda s: tpx.get(s, (None, None))[0])
    df["今日漲跌%"] = df["stock_id"].map(lambda s: tpx.get(s, (None, None))[1])
    return df


def _landmine_warn(f, df, label="T11 候選"):
    """清單若有高風險（🔴/🟠），列出紅旗提醒（選股當下就看到雷）。"""
    if df is None or df.empty or "風險" not in df.columns:
        return
    hi = df[df["風險"].astype(str).str.contains("嚴重|高", na=False)]
    if hi.empty:
        return
    f.write(f"\n> 🧨 **{label}排雷提醒**（財務/籌碼/技術紅旗，建議先避開或查清）：\n")
    for r in hi.itertuples():
        flags = getattr(r, "紅旗", "") or ""
        f.write(f"> - {r.stock_id} {r.name}：{r.風險}　{flags}\n")


def _section(f, title, df, cols, n=15, skipped=False, note=None):
    f.write(f"\n## {title}\n\n")
    if note:
        f.write(f"> 📅 {note}\n\n")
    if skipped:
        f.write("（已略過 --skip-longterm；要看長期軌請跑 `python -m scripts.run_longterm`）\n")
    elif df is None or df.empty:
        f.write("（今日無符合條件標的）\n")
    else:
        keep = [c for c in cols if c in df.columns]
        disp = df[keep].head(n).rename(columns=report_html.COLUMN_LABELS)
        f.write(disp.to_markdown(index=False) + "\n")


def _holdings_attribution(pf_view):
    """對每檔持股做今vs昨籌碼漲跌歸因（一句話）。回傳 [(代號, 名稱, 說明)]。"""
    from src.screeners import chip_diagnosis as cd
    out = []
    for r in pf_view.itertuples():
        sid = str(r.代號)
        try:
            df = cd._fetch(sid, 5)
            if not df.empty and len(df) >= 2:
                out.append((sid, r.名稱, cd.one_line(df)))
        except Exception:
            continue
    return out


def _summary(today, reg, glob, df11, df16, inter, dflt, df12=None, pf_view=None,
             pf_summary=None, dfdt=None, ft=None, ftstats=None):
    """給推播用的精簡摘要（純文字，含 HTML 粗體）。"""
    lines = [f"<b>📈 台股每日報告 {today}</b>",
             f"🚦 {regime_mod.summary_line(reg)}",
             gm.sox_signal(glob), ""]
    # 持股狀況擺最前（觸停損最該立刻知道）
    if pf_view is not None and not pf_view.empty:
        alerts = pf_view[pf_view["狀態"].str.contains("觸停損|停利", na=False)]
        for r in alerts.itertuples():
            lines.append(f"🔔 <b>{r.代號} {r.名稱}：{r.狀態}</b>（現價 {r.現價}）")
        lines.append(f"📋 持股總損益 {pf_summary['總損益']:+,}（{pf_summary['總報酬%']:+.2f}%）")
        for sid, name, line in _holdings_attribution(pf_view):
            lines.append(f"　└ {sid} {name}：{line}")
        lines.append("")
    if inter:
        lines.append("⭐ <b>雙訊號交集(法人買且抗跌)</b>：" + "、".join(inter))
    if not df11.empty:
        top = df11.head(3)
        lines.append("🟡 波段T11 前3：" +
                     "、".join(f"{r.stock_id} {r.name}" for r in top.itertuples()))
    # 波段候選（T11+T16）若有高風險地雷，合併提醒
    import pandas as _pd
    parts = [d[["stock_id", "name", "風險"]] for d in (df11, df16)
             if d is not None and not d.empty and "風險" in d.columns]
    if parts:
        allrisk = _pd.concat(parts).drop_duplicates("stock_id")
        hi = allrisk[allrisk["風險"].astype(str).str.contains("嚴重|高", na=False)]
        if not hi.empty:
            lines.append("🧨 <b>波段排雷警示</b>：" +
                         "、".join(f"{r.stock_id} {r.name}({r.風險})" for r in hi.itertuples()))
    if df12 is not None and not df12.empty:
        top = df12.head(3)
        lines.append("🚀 營收動能T12 前3：" +
                     "、".join(f"{r.stock_id} {r.name}" for r in top.itertuples()))
    if dflt is not None and not dflt.empty:
        top = dflt.head(3)
        lines.append("🟢 長期 前3：" +
                     "、".join(f"{r.stock_id} {r.name}" for r in top.itertuples()))
    # 當沖：優先報「順勢」候選（與大盤同向、勝率較高）；中性盤則報前3並帶傾向
    if dfdt is not None and not dfdt.empty and "多空傾向" in dfdt.columns:
        trend = dfdt[dfdt["與大盤"] == "順勢"].head(4)
        if not trend.empty:
            dir_tag = str(trend.iloc[0]["多空傾向"])
            action = "找空" if "偏空" in dir_tag else "找買"
            names = "、".join(f"{r.stock_id} {r.name}" for r in trend.itertuples())
            lines.append(f"⚡ <b>當沖順勢({dir_tag}{action})</b>：{names}")
        else:
            top = dfdt.head(3)
            names = "、".join(f"{r.stock_id} {r.name}{r.多空傾向}" for r in top.itertuples())
            lines.append(f"⚡ 當沖前3：{names}")
    # 昨日精選今日追蹤：各軌隔日平均表現 + 最強一檔
    if ft and ft.get("tracks"):
        lines.append(f"\n📈 <b>昨日精選今日追蹤（{ft['date']}選）</b>")
        for track, rows in ft["tracks"].items():
            st = (ftstats or {}).get(track, {})
            best = max(rows, key=lambda r: (r["chg"] if r["chg"] == r["chg"] else -999))
            head = f"　{track}：隔日均 {st.get('avg', 0):+.2f}%（漲{st.get('up', 0)}/{st.get('n', 0)}）" if st else f"　{track}："
            lines.append(f"{head}｜最強 {best['stock_id']} {best['name']} {best['chg']:+.2f}%")
    lines.append("\n完整報告見 reports/screener/（.html 用瀏覽器開）")
    return "\n".join(x for x in lines if x is not None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--skip-longterm", action="store_true")
    ap.add_argument("--notify", action="store_true", help="把摘要推播到手機（Telegram/Email，需設 .env）")
    ap.add_argument("--skip-landmine", action="store_true", help="略過波段候選(T11+T16)排雷（省 FinMind 呼叫、加快）")
    ap.add_argument("--days", type=int, default=12)
    ap.add_argument("--no-sync", action="store_true",
                    help="盤後不自動把累積資料 commit+push 到 GitHub（本機預設會自動同步）")
    args = ap.parse_args()

    # 換電腦/新環境：先用 data/history CSV 把歷史載回 DB（累積歷史的關鍵）。
    # 同機為冪等 upsert（CSV==DB → 無變動）；新機才真正把整段歷史種回空 DB。
    try:
        from src import datastore
        if datastore.has_history():
            res = datastore.load()
            print("[資料] 由 CSV 載回 DB（累積歷史）：" + "，".join(f"{k} {v}" for k, v in res.items()))
    except Exception as e:
        print(f"[資料] 載回 DB 略過：{e}")

    if not args.no_update:
        _update(args.days)

    print("[環境] 多空紅綠燈 + 全球市場 …")
    reg = regime_mod.assess()
    glob = gm.fetch()
    print("   " + regime_mod.summary_line(reg))
    print("   " + gm.sox_signal(glob))

    print("[波段] T11 + T16 …")
    df11, df16 = t11.run(), t16.run()
    # 併入千張大戶%（來自每週 update_holders；沒資料則欄位空白）
    from src.enrich import big_holders_map
    bh = big_holders_map()
    if not df11.empty:
        df11["千張大戶%"] = df11["stock_id"].map(bh)
    # 對波段候選就地排雷：T11 全部 + T16 前 15（地雷常躲在強勢榜=價強地雷）
    # 掃聯集一次，成本低，再把風險標回兩張表
    if not args.skip_landmine:
        ids = list(df11["stock_id"]) if not df11.empty else []
        if not df16.empty:
            ids += df16["stock_id"].head(_T16_SHOW).tolist()
        ids = list(dict.fromkeys(ids))   # 去重保序
        if ids:
            print(f"[排雷] 掃波段候選 {len(ids)} 檔（T11+T16強勢）…")
            lm = landmine.scan(ids, verbose=False)
            if not lm.empty:
                rmap = lm.set_index("stock_id")["風險級"].to_dict()
                fmap = lm.set_index("stock_id")["紅旗"].to_dict()
                for d in (df11, df16):
                    if not d.empty:
                        d["風險"] = d["stock_id"].map(rmap)
                        d["紅旗"] = d["stock_id"].map(fmap)
    print("[成長] T12 月營收動能 …")
    try:
        df12 = t12.run()
    except SystemExit:
        df12 = None
    print("[當沖] 候選掃描 …")
    dfdt = daytrade.run()
    dfdt = daytrade.add_bias(dfdt, reg)   # 加多空傾向+與大盤（開盤前定調方向）
    dflt = None
    if not args.skip_longterm:
        print("[長期] 價值+成長+配息（較慢）…")
        try:
            dflt = lt.run(verbose=False)
        except SystemExit as e:
            # 長期軌資料源（TWSE 估值）一時抓不到不該讓整份日報產不出來 → 優雅降級
            print(f"   ⚠️ 長期軌略過（{e}）")
            dflt = None

    from src import portfolio as pf
    pf_view, pf_summary = pf.status()

    # 五個清單統一併入「今日收盤 + 今日漲跌%」（波段判斷：一眼看今天在漲還在殺）
    tpx = _today_px()
    for d in (df11, df16, df12, dflt, dfdt):
        _add_today(d, tpx)

    # 併入三大法人(外資/投信/自營近10日淨買賣超) + 融資/融券增減，讓各軌看得到「誰在買賣」
    from src import flows as flows_mod
    _flows = flows_mod.institution_flows(days=10)
    df11 = flows_mod.enrich(df11, flows=_flows)
    df16 = flows_mod.enrich(df16, flows=_flows)
    dfdt = flows_mod.enrich(dfdt, flows=_flows)
    df12 = flows_mod.enrich(df12, flows=_flows)   # 五軌一致：成長軌也看法人/資券
    dflt = flows_mod.enrich(dflt, flows=_flows)   # 長期軌也看法人/資券

    # 併入「今日法人」= 今日三大法人合計(單日)，讓各軌能直接對帳券商App今日數
    _tinst = _today_inst()
    for d in (df11, df16, df12, dflt, dfdt):
        if d is not None and not d.empty and _tinst:
            d["今日法人"] = d["stock_id"].map(_tinst).astype("Int64")

    # 併入產業別（對齊 T12/長期軌；FinMind 一次 call 全市場，抓不到則欄位留白，不影響其他欄）
    from src import enrich as enrich_mod
    try:
        _ind = enrich_mod.industry_map()
    except Exception as e:
        print(f"   ⚠️ 產業別略過（{e}）")
        _ind = {}
    for d in (df11, df16, dfdt):
        if d is not None and not d.empty and _ind:
            d["產業"] = d["stock_id"].map(_ind)

    # 資料日期說明（避免區間值/基準日收盤被誤讀成單日/最新日）
    _flownote = ("　今日法人＝今日三大法人買賣超合計(張，正=買超；此欄對得起券商App今日數)；"
                 "外資10日/投信10日/自營10日＝近10日淨買賣超(張，正=買超，看趨勢非今日)；"
                 "融資增減10日／融券增減10日＝近10日融資／融券餘額變化(張，正=增加)。")
    note11 = (_asof_note(df11, "t11") or "") + _flownote
    note16 = (_asof_note(df16, "t16") or "") + _flownote
    notedt = ((_asof_note(dfdt, "daytrade") or "") + "。" + report_html.DAYTRADE_NOTE
              + " " + report_html.BIAS_NOTE + _flownote)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    # 昨日精選今日追蹤：先算昨日精選對今天的表現，再存今天的精選（供明天追蹤）
    import pandas as pd
    from src import picks_tracker as pt
    ft = pt.followthrough(today)
    ftstats = pt.summary_stats(ft) if ft else {}
    pt.save(today, {"波段T11": df11, "波段T16": df16, "當沖": dfdt}, n=15)
    # 持股籌碼歸因算一次，.md 與 .html 共用（確保兩份內容一致）
    pf_attr = _holdings_attribution(pf_view) if not pf_view.empty else []

    path = OUTPUT_DIR / f"{today}_run_all.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 台股每日整合報告 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。先看環境紅綠燈決定要不要出手。\n\n")
        f.write("## 🚦 環境紅綠燈\n\n")
        f.write(f"- **{regime_mod.summary_line(reg)}**\n")
        f.write(f"- {gm.sox_signal(glob)}\n")
        f.write(f"- 全球：{' ｜ '.join(gm.summary_lines(glob))}\n")

        _section(f, "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", df11,
                 ["stock_id", "name", "market", "產業", "investor", "close", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "風險", "紅旗", "score"],
                 note=note11)
        _landmine_warn(f, df11)
        _section(f, "🟡 波段｜T16 抗跌強勢", df16,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減",
                  "return_%", "vs_market_%", "風險", "紅旗"], note=note16)
        _landmine_warn(f, df16, "T16 強勢榜")
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n### ⭐ 波段雙訊號交集（法人買且抗跌）\n\n")
            nm = df11.set_index("stock_id")["name"].to_dict()
            f.write("".join(f"- {s} {nm.get(s,'')}\n" for s in both) if both else "（無）\n")

        _section(f, "🚀 成長｜T12 月營收動能（YoY強+近月加速）", df12,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"], n=20, note=_flownote.strip())
        _section(f, "🟢 長期｜價值+成長+配息", dflt,
                 ["stock_id", "name", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "殖利率%", "PER",
                  "ROE估%", "營收YoY%", "連配息年", "score"], skipped=args.skip_longterm,
                 note=_flownote.strip())
        _section(f, "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", dfdt,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "多空傾向", "與大盤",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "當日振幅%", "均振幅%", "量能倍數"],
                 note=notedt)
        if not pf_view.empty:
            f.write(f"\n## 📋 我的持股（總損益 {pf_summary['總損益']:+,}"
                    f"｜{pf_summary['總報酬%']:+.2f}%）\n\n")
            f.write(pf_view.to_markdown(index=False) + "\n")
            attr = pf_attr
            if attr:
                f.write("\n### 📊 持股今日籌碼歸因（今 vs 昨，自動）\n\n")
                for sid, name, line in attr:
                    f.write(f"- **{sid} {name}**：{line}\n")
        # 昨日精選今日追蹤（波段T11/T16 + 當沖前15 → 今天表現+原因）
        if ft.get("tracks"):
            f.write(f"\n## 📈 昨日精選今日追蹤（{ft['date']} 精選 → 今日表現＋原因）\n\n")
            for track, rows in ft["tracks"].items():
                st = ftstats.get(track, {})
                head = f"### {track}"
                if st:
                    head += f"（隔日均 {st['avg']:+.2f}%，上漲 {st['up']}/{st['n']}）"
                f.write(head + "\n\n")
                for r in rows:
                    c = r["chg"]
                    cs = f"{c:+.2f}%" if pd.notna(c) else "—"
                    f.write(f"- #{r['rank']} **{r['stock_id']} {r['name']}** 今日 {cs} → {r['one_line']}\n")
                f.write("\n")
        f.write(f"\n---\n\n> {report_html.GLOSSARY}\n")

    # 同步輸出 HTML（表格永遠對齊、紅漲綠跌上色）
    both = (set(df11["stock_id"]) & set(df16["stock_id"])) if not df11.empty and not df16.empty else set()
    nm = df11.set_index("stock_id")["name"].to_dict() if not df11.empty else {}
    blocks = [
        {"title": "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", "df": df11, "note": note11,
         "cols": ["stock_id", "name", "market", "產業", "investor", "close", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "風險", "紅旗", "score"],
         "signed": ["今日漲跌%", "price_gain_%", "今日法人", "外資", "投信", "自營", "融資增減", "融券增減"],
         "landmine": True, "landmine_label": "T11 候選",
         "after_intersection": True},
        {"title": "🟡 波段｜T16 抗跌強勢", "df": df16, "note": note16, "n": 15,
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減",
                  "return_%", "vs_market_%", "風險", "紅旗"],
         "signed": ["今日漲跌%", "return_%", "vs_market_%", "今日法人", "外資", "投信", "自營", "融資增減", "融券增減"],
         "landmine": True, "landmine_label": "T16 強勢榜"},
        {"title": "🚀 成長｜T12 月營收動能（YoY強+近月加速）", "df": df12, "n": 20, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"],
         "signed": ["今日漲跌%", "今日法人", "外資", "投信", "自營", "融資增減", "融券增減",
                    "YoY%", "累計YoY%", "MoM%", "加速度"]},
        {"title": "🟢 長期｜價值+成長+配息", "df": dflt, "skipped": args.skip_longterm, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "產業", "今日收盤", "今日漲跌%",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "殖利率%", "PER", "ROE估%",
                  "營收YoY%", "連配息年", "score"],
         "signed": ["今日漲跌%", "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "營收YoY%"]},
        {"title": "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", "df": dfdt, "note": notedt, "n": 20,
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "多空傾向", "與大盤",
                  "今日法人", "外資", "投信", "自營", "融資增減", "融券增減", "當日振幅%", "均振幅%", "量能倍數"],
         "signed": ["今日漲跌%", "今日法人", "外資", "投信", "自營", "融資增減", "融券增減"]},
    ]
    if not pf_view.empty:
        blocks.append({
            "title": f"📋 我的持股（總損益 {pf_summary['總損益']:+,}｜{pf_summary['總報酬%']:+.2f}%）",
            "df": pf_view,
            "cols": ["代號", "名稱", "張數", "成本", "現價", "損益%", "損益金額", "停損", "狀態"],
            "signed": ["損益%", "損益金額"],
            "attribution": pf_attr})
    inter = [f"{s} {nm.get(s,'')}" for s in both]
    html = report_html.build(today, reg, gm.summary_lines(glob), gm.sox_signal(glob),
                             blocks, intersection=inter, followthrough=ft, ftstats=ftstats)
    html_path = OUTPUT_DIR / f"{today}_run_all.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\n✅ 整合報告 → {path}")
    print(f"   HTML（瀏覽器開、表格對齊）→ {html_path}")
    print(f"   波段T11 {len(df11)} / T16 {len(df16)} ｜ T12 {0 if df12 is None else len(df12)}"
          f" ｜ 當沖 {len(dfdt)}"
          + (f" ｜ 長期 {len(dflt)}" if dflt is not None else " ｜ 長期(略過)"))

    if args.notify:
        from src.notify import notify
        ok, ch, detail = notify(_summary(today, reg, glob, df11, df16, inter, dflt, df12, pf_view, pf_summary, dfdt, ft, ftstats),
                                subject=f"台股每日報告 {today}", file_path=str(html_path))
        print(f"   📲 推播（{ch}）：{'成功' if ok else '失敗 - ' + detail}")

    # 盤後自動把累積資料（DB→CSV）commit+push 到 GitHub，讓歷史一天天累積、可跨電腦。
    # 本機才做；雲端/排程有自身提交流程，跳過以免衝突。--no-sync 可關閉。
    if not args.no_sync and not _is_cloud():
        print("[同步] 上傳累積資料到 GitHub …")
        try:
            from scripts import commit_data
            commit_data.sync()
        except Exception as e:
            print(f"[同步] 略過（{e}）；資料仍在本機，下次會再嘗試上傳。")


if __name__ == "__main__":
    main()
