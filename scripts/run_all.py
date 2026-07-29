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
    from src.db import read_table
    px = read_table("price", use_cache=True)[["date", "stock_id", "close"]]
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


def _today_flows():
    """回傳每檔『今日單日』法人分項＋資券變化 DataFrame（供各軌 merge，依 stock_id）。

    欄位：外資今日/投信今日/自營今日（張，正=買超，取 institutional 最新日）、
          融資今日/融券今日（張，正=增加，= 最新日餘額 − 前一日餘額）。
    看「今天誰在動」；旁邊的 外資10日 等看近10日趨勢。
    """
    import pandas as pd
    from src.db import read_table
    inst = read_table("institutional", use_cache=True)[
        ["date", "stock_id", "foreign_net", "trust_net", "dealer_net"]]
    mg = read_table("margin", use_cache=True)[
        ["date", "stock_id", "margin_balance", "short_balance"]]

    out = None
    if not inst.empty:
        last = sorted(inst["date"].unique())[-1]
        out = (inst[inst["date"] == last][["stock_id", "foreign_net", "trust_net", "dealer_net"]]
               .rename(columns={"foreign_net": "外資今日", "trust_net": "投信今日",
                                "dealer_net": "自營今日"}))
    if not mg.empty:
        dts = sorted(mg["date"].unique())
        cur = mg[mg["date"] == dts[-1]].set_index("stock_id")
        chg = pd.DataFrame({"stock_id": cur.index})
        if len(dts) >= 2:
            pv = mg[mg["date"] == dts[-2]].set_index("stock_id")
            chg["融資今日"] = (cur["margin_balance"] - pv["margin_balance"].reindex(cur.index)).values
            chg["融券今日"] = (cur["short_balance"] - pv["short_balance"].reindex(cur.index)).values
        else:
            chg["融資今日"] = pd.NA
            chg["融券今日"] = pd.NA
        out = chg if out is None else out.merge(chg, on="stock_id", how="outer")
    return out if out is not None else pd.DataFrame(columns=["stock_id"])


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
        ind = getattr(r, "產業", None)
        ind = f"（{ind}）" if isinstance(ind, str) and ind else ""
        f.write(f"> - {r.stock_id} {r.name}{ind}：{r.風險}　{flags}\n")


def _section(f, title, df, cols, n=15, skipped=False, note=None):
    f.write(f"\n## {title}\n\n")
    if note:
        for ln in note.split("\n"):
            if ln.strip():
                f.write(f"> {ln}\n")
        f.write("\n")
    if skipped:
        f.write("（已略過 --skip-longterm；要看長期軌請跑 `python -m scripts.run_longterm`）\n")
    elif df is None or df.empty:
        f.write("（今日無符合條件標的）\n")
    else:
        keep = [c for c in cols if c in df.columns]
        disp = df[keep].head(n).copy()
        # 今日+10日 合併同格（MD 用「今／10日」斜線併排），今日欄併入後移除
        merged_labels = {}
        for anchor, today_col in report_html.MERGE_PAIRS.items():
            if anchor in disp.columns and today_col in disp.columns:
                disp[anchor] = [report_html.fmt_pair_text(t, d)
                                for t, d in zip(disp[today_col], disp[anchor])]
                merged_labels[anchor] = report_html.MERGE_BASE[anchor] + "今/10日"
        drop = [t for a, t in report_html.MERGE_PAIRS.items()
                if a in disp.columns and t in disp.columns]
        disp = disp.drop(columns=drop).rename(
            columns={**report_html.COLUMN_LABELS, **merged_labels})
        f.write(disp.to_markdown(index=False) + "\n")
        if len(df) > n:
            f.write(f"\n> 📄 僅顯示前 {n} 名，共 {len(df)} 檔符合；"
                    "完整清單見同資料夾的同名 CSV 檔（可用 Excel 開）。\n")


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
    ap.add_argument("--keep-days", type=int, default=260,
                    help="上傳CSV只保留近N交易日(滾動窗，控制檔案大小)；260≥年線240，夠算季/年線。0=全留")
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
    from src.db import clear_cache
    clear_cache()   # 更新完清整表快取，確保各 screener 讀到最新資料（之後同進程只讀一次）

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

    # 併入「今日單日」法人分項(外資/投信/自營今日)＋資券今日增減，看「今天誰在動」
    _tf = _today_flows()
    _tcols = ["外資今日", "投信今日", "自營今日", "融資今日", "融券今日"]
    if _tf is not None and not _tf.empty:
        df11 = df11.merge(_tf, on="stock_id", how="left") if not df11.empty else df11
        df16 = df16.merge(_tf, on="stock_id", how="left") if not df16.empty else df16
        dfdt = dfdt.merge(_tf, on="stock_id", how="left") if not dfdt.empty else dfdt
        df12 = df12.merge(_tf, on="stock_id", how="left") if df12 is not None and not df12.empty else df12
        dflt = dflt.merge(_tf, on="stock_id", how="left") if dflt is not None and not dflt.empty else dflt
        for d in (df11, df16, df12, dflt, dfdt):
            if d is not None and not d.empty:
                for c in _tcols:
                    if c in d.columns:
                        d[c] = d[c].astype("Int64")

    # 併入籌碼訊號：連買/連賣天數(三大法人分開,看誰狂買誰狂賣)、法人主導度%、一句 emoji 訊號
    from src import chip_signal
    _sig = chip_signal.compute()
    df11 = chip_signal.enrich(df11, _sig)
    df16 = chip_signal.enrich(df16, _sig)
    dfdt = chip_signal.enrich(dfdt, _sig)
    df12 = chip_signal.enrich(df12, _sig)
    dflt = chip_signal.enrich(dflt, _sig)

    # 併入技術面：均線排列(5/10/20多空)、20MA乖離%、成交額億(資金權重)，補「趨勢位置」
    from src import tech_signal
    _tech = tech_signal.compute()
    df11 = tech_signal.enrich(df11, _tech)
    df16 = tech_signal.enrich(df16, _tech)
    dfdt = tech_signal.enrich(dfdt, _tech)
    df12 = tech_signal.enrich(df12, _tech)
    dflt = tech_signal.enrich(dflt, _tech)

    # 綜合定調：把已併欄的籌碼＋技術投票成一句『🔴偏多/⚪觀望/🟢偏空』（波段/成長/長期軌；
    # 當沖軌另有 intraday 專用「多空傾向」，不重複）。放最後，確保吃得到所有 enrich 欄。
    from src import verdict
    df11 = verdict.add_verdict(df11)
    df16 = verdict.add_verdict(df16)
    df12 = verdict.add_verdict(df12)
    dflt = verdict.add_verdict(dflt)

    # 併入當沖比率%（妖股對殺偵測：TWSE+TPEX 官方當沖統計，各1呼叫；抓不到則欄留白）
    try:
        from src import day_trade_signal as dts
        _dt = dts.compute()
        df11 = dts.enrich(df11, _dt)
        df16 = dts.enrich(df16, _dt)
        dfdt = dts.enrich(dfdt, _dt)
        df12 = dts.enrich(df12, _dt)
        dflt = dts.enrich(dflt, _dt)
    except Exception as e:
        print(f"   ⚠️ 當沖比率略過（{e}）")

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
    # 分組多行說明（\n 分隔；MD 逐行加 >、HTML 換 <br>，避免擠成一長段）
    _flownote = (
        "🧭 定調＝籌碼＋技術 7面向投票的一句話方向(🔴偏多找買點/⚪觀望/🟢偏空，須≥3方向一致)：法人主導·融資散戶·"
        "均線排列·季線年線·20MA乖離·52週位置·券資比軋空。⚠️昨日收盤後資料的**方向偏誤(bias)非盤中即時**，進出仍看盤中量價\n"
        "📊 法人/資券欄｜每格兩個數＝今日單日／近10日累積(HTML 上下兩行、MD 以／分隔)；"
        "外資·投信·自營＝買賣超(🔴買超/🟢賣超)，融資·融券＝餘額增減(🔴增/🟢減)"
        "\n　↳ 融資增＝散戶借錢追價⚠️籌碼較不安定；融券增＝空單多·股價若強有軋空機會(未來須回補)——顏色只表增減，好壞配股價方向看"
        "\n🔁 連續｜外資連·投信連·自營連＝當前連續同向天數(正=連買／負=連賣，看誰在狂買狂賣)"
        "\n🎯 主導度%＝今日法人淨額÷成交量(法人是否主導)；💬 籌碼訊號＝今日共識／連買連賣強度一句話合成"
        "\n📏 今日量張＝今日成交總量(張)；量能倍數＝今日量÷近20日均量(>1.5爆量·<1量縮)；券資比%＝融券÷融資餘額(空方相對力道)"
        "\n　↳ 融資·融券佔量%＝今日增減÷今日量：把絕對張數『相對化』，例『融券−343』佔量僅0.9%＝杯水車薪，別被絕對數字誤導"
        "\n📈 技術面｜均線排列＝5/10/20MA短多空(🔴多頭5>10>20順勢·🟢空頭·⚪糾結待變盤)；季線年線＝股價vs60/240MA中長多空(🔴站上季年最強·🟢跌破季年最弱)"
        "\n　↳ 20MA乖離%＝股價偏離20MA(正=偏強·過大追高風險·負=跌破)；52週位置%＝在近1年區間位置(0=年內最低·100=年內最高·波段高低檔)"
        "\n　↳ 成交額億＝今日量×收盤(資金權重，張數不分價位會誤判：2000元股vs20元股同100張差百倍)"
        "\n🔥 當沖比率%＝當日沖銷成交量÷總成交量：>50%＝當沖客『對殺』盤(妖股特徵·暴漲跌·法人不玩)，"
        "資券數字多為帳面沖銷別當真；例5328華容~79%＝純對殺，融券-343佔量僅0.9%就是這原因"
    )
    def _dated(asof, base):  # 有資料日期才加「📅 …」首行，避免空日期留下光禿的一行
        return (f"📅 {asof}\n" if asof else "") + base
    note11 = _dated(_asof_note(df11, "t11"), _flownote)
    note16 = _dated(_asof_note(df16, "t16"), _flownote)
    _dt_asof = _asof_note(dfdt, "daytrade")
    notedt = ("📅 " + (f"{_dt_asof}。" if _dt_asof else "") + report_html.DAYTRADE_NOTE
              + " " + report_html.BIAS_NOTE + "\n" + _flownote)

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
                 ["stock_id", "name", "market", "產業", "investor", "close", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "風險", "紅旗", "score"],
                 note=note11)
        _landmine_warn(f, df11)
        _section(f, "🟡 波段｜T16 抗跌強勢", df16,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號",
                  "return_%", "vs_market_%", "風險", "紅旗"], note=note16)
        _landmine_warn(f, df16, "T16 強勢榜")
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n### ⭐ 波段雙訊號交集（法人買且抗跌）\n\n")
            nm = df11.set_index("stock_id")["name"].to_dict()
            f.write("".join(f"- {s} {nm.get(s,'')}\n" for s in both) if both else "（無）\n")

        _section(f, "🚀 成長｜T12 月營收動能（YoY強+近月加速）", df12,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"], n=20, note=_flownote.strip())
        _section(f, "🟢 長期｜價值+成長+配息", dflt,
                 ["stock_id", "name", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "殖利率%", "PER",
                  "ROE估%", "營收YoY%", "連配息年", "score"], skipped=args.skip_longterm,
                 note=_flownote.strip())
        _section(f, "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", dfdt,
                 ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "多空傾向", "與大盤", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "當日振幅%", "均振幅%", "量能倍數"],
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
         "cols": ["stock_id", "name", "market", "產業", "investor", "close", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "風險", "紅旗", "score"],
         "signed": ["今日漲跌%", "20MA乖離%","price_gain_%", "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "籌碼訊號"],
         "landmine": True, "landmine_label": "T11 候選",
         "after_intersection": True},
        {"title": "🟡 波段｜T16 抗跌強勢", "df": df16, "note": note16, "n": 15,
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號",
                  "return_%", "vs_market_%", "風險", "紅旗"],
         "signed": ["今日漲跌%", "20MA乖離%","return_%", "vs_market_%", "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "籌碼訊號"],
         "landmine": True, "landmine_label": "T16 強勢榜"},
        {"title": "🚀 成長｜T12 月營收動能（YoY強+近月加速）", "df": df12, "n": 20, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"],
         "signed": ["今日漲跌%", "20MA乖離%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "籌碼訊號",
                    "YoY%", "累計YoY%", "MoM%", "加速度"]},
        {"title": "🟢 長期｜價值+成長+配息", "df": dflt, "skipped": args.skip_longterm, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "產業", "今日收盤", "今日漲跌%", "定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "殖利率%", "PER", "ROE估%",
                  "營收YoY%", "連配息年", "score"],
         "signed": ["今日漲跌%", "20MA乖離%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "籌碼訊號", "營收YoY%"]},
        {"title": "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", "df": dfdt, "note": notedt, "n": 20,
         "cols": ["stock_id", "name", "market", "產業", "今日收盤", "今日漲跌%", "多空傾向", "與大盤", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "成交額億", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "券資比%", "融資佔量%", "融券佔量%", "籌碼訊號", "當日振幅%", "均振幅%", "量能倍數"],
         "signed": ["今日漲跌%", "20MA乖離%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "籌碼訊號"]},
    ]
    if not pf_view.empty:
        blocks.append({
            "title": f"📋 我的持股（總損益 {pf_summary['總損益']:+,}｜{pf_summary['總報酬%']:+.2f}%）",
            "df": pf_view,
            "cols": ["代號", "名稱", "張數", "成本", "現價", "損益%", "損益金額", "停損", "狀態"],
            "signed": ["損益%", "損益金額"],
            "attribution": pf_attr})
    # 各軌「完整清單」另存 CSV（Excel 可開，utf-8-sig 免亂碼）：報告只列前N名，全部見 CSV
    _slugs = {"T11": "波段T11", "T16": "波段T16", "T12": "成長T12",
              "長期": "長期", "當沖": "當沖", "持股": "我的持股"}
    csv_written = []
    for b in blocks:
        d = b.get("df")
        if d is None or d.empty:
            continue
        slug = next((v for k, v in _slugs.items() if k in b["title"]), "清單")
        name = f"{today}_{slug}.csv"
        cols = [c for c in b["cols"] if c in d.columns]
        report_html.rename_cn(d[cols]).to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")
        b["csv_name"] = name
        csv_written.append(name)

    inter = [f"{s} {nm.get(s,'')}" for s in both]
    html = report_html.build(today, reg, gm.summary_lines(glob), gm.sox_signal(glob),
                             blocks, intersection=inter, followthrough=ft, ftstats=ftstats)
    html_path = OUTPUT_DIR / f"{today}_run_all.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\n✅ 整合報告 → {path}")
    print(f"   HTML（瀏覽器開、表格對齊）→ {html_path}")
    if csv_written:
        print(f"   完整清單 CSV（Excel 可開）→ {OUTPUT_DIR}/ 內：{'、'.join(csv_written)}")
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
            commit_data.sync(keep_days=args.keep_days or None)
        except Exception as e:
            print(f"[同步] 略過（{e}）；資料仍在本機，下次會再嘗試上傳。")


if __name__ == "__main__":
    main()
