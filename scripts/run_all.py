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
import io
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
from src.screeners import daytrade_snipe
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
    # 借券賣出餘額（法人真實空單）逐日累積入 DB（Sponsor 一次抓全市場，1 call/日）。
    # 缺的日子才抓；抓不到不擋主流程（enrich 欄留白即可）。
    try:
        subprocess.run([sys.executable, "-m", "scripts.backfill_sbl", "--days", str(days)],
                       cwd=str(ROOT), check=False)
    except Exception as e:
        print(f"[更新] 借券累積略過：{e}")
    # 當沖量逐日累積入 DB（免費 TWSE+TPEX，各 1 call/日）→ 供當沖比熱度趨勢欄。
    try:
        subprocess.run([sys.executable, "-m", "scripts.backfill_daytrade", "--days", str(days)],
                       cwd=str(ROOT), check=False)
    except Exception as e:
        print(f"[更新] 當沖累積略過：{e}")
    # ⚠️順序重要：先把快取折進『分點行為累計計數器』(進 git、永久累積)，再修剪快取。
    # 反過來的話，被剪掉的 60 天前資料就永遠沒被計入了。
    try:
        from src import broker_profile
        st = broker_profile.update_from_cache()
        if st.get("新增轉換"):
            print(f"[更新] 分點檔案 +{st['新增轉換']} 筆 → 累計 {st['總樣本']} 樣本／{st['分點數']} 分點")
    except Exception as e:
        print(f"[更新] 分點檔案累積略過：{e}")
    # 修剪分點本機快取，控制大小（只留近 60 交易日；不影響雲端無快取的重抓）
    try:
        from src.broker_signal import prune_cache
        prune_cache(keep_days=60)
    except Exception as e:
        print(f"[更新] 分點快取修剪略過：{e}")


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


def _day_flows(offset: int = 0, suffix: str = "今日"):
    """回傳每檔『單日』法人分項＋資券變化 DataFrame（供各軌 merge，依 stock_id）。

    offset=0＝最新交易日(今日)、offset=1＝前一交易日(昨日)。欄位帶 suffix：
      外資{suffix}/投信{suffix}/自營{suffix}（張，正=買超，取 institutional 該日）、
      融資{suffix}/融券{suffix}（張，正=增加，= 該日餘額 − 前一日餘額）。
    看「今天/昨天誰在動」；旁邊的 外資10日 等看近10/20日趨勢。
    """
    import pandas as pd
    from src.db import read_table
    inst = read_table("institutional", use_cache=True)[
        ["date", "stock_id", "foreign_net", "trust_net", "dealer_net"]]
    mg = read_table("margin", use_cache=True)[
        ["date", "stock_id", "margin_balance", "short_balance"]]

    out = None
    if not inst.empty:
        idts = sorted(inst["date"].unique())
        if len(idts) > offset:
            day = idts[-1 - offset]
            out = (inst[inst["date"] == day][["stock_id", "foreign_net", "trust_net", "dealer_net"]]
                   .rename(columns={"foreign_net": f"外資{suffix}", "trust_net": f"投信{suffix}",
                                    "dealer_net": f"自營{suffix}"}))
    if not mg.empty:
        dts = sorted(mg["date"].unique())
        if len(dts) > offset:
            cur = mg[mg["date"] == dts[-1 - offset]].set_index("stock_id")
            chg = pd.DataFrame({"stock_id": cur.index})
            if len(dts) >= offset + 2:
                pv = mg[mg["date"] == dts[-2 - offset]].set_index("stock_id")
                chg[f"融資{suffix}"] = (cur["margin_balance"] - pv["margin_balance"].reindex(cur.index)).values
                chg[f"融券{suffix}"] = (cur["short_balance"] - pv["short_balance"].reindex(cur.index)).values
            else:
                chg[f"融資{suffix}"] = pd.NA
                chg[f"融券{suffix}"] = pd.NA
            out = chg if out is None else out.merge(chg, on="stock_id", how="outer")
    return out if out is not None else pd.DataFrame(columns=["stock_id"])


def _today_flows():
    """今日單日（＝_day_flows(0)）；保留舊名供既有呼叫點。"""
    return _day_flows(0, "今日")


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


def _section(f, title, df, cols, n=15, skipped=False, note=None, bt=None, csv_on=True):
    f.write(f"\n## {title}\n\n")
    vline = report_html.verdict_md(bt)          # 回測驗證（各軌是否真的有 edge）
    if vline:
        f.write(f"> {vline}\n\n")
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
        # 補上分組來源欄（今/昨/20日等雖不在 cols，仍需讀來堆疊同格）
        keep += [c for c in report_html.group_source_cols()
                 if c in df.columns and c not in keep]
        disp = df[keep].head(n).copy()
        # 多時窗合併同格（MD 用「今／10／20／60」斜線併排），來源欄併入後移除
        merged_labels, drop = {}, []
        for anchor, (base, srcs) in report_html.MERGE_GROUPS.items():
            if anchor not in disp.columns:
                continue
            cols_present = [(c, disp[c] if c in disp.columns else None) for c, _ in srcs]
            disp[anchor] = [report_html.fmt_group_text(
                [(s.iloc[i] if s is not None else pd.NA) for _, s in cols_present])
                for i in range(len(disp))]
            merged_labels[anchor] = base + report_html._GROUP_HEAD
            drop += [c for c, _ in srcs if c != anchor and c in disp.columns]
        disp = disp.drop(columns=drop).rename(
            columns={**report_html.COLUMN_LABELS, **merged_labels})
        # 數值欄(Int64 借券增減／float 千張週增減等)的缺值在 MD 顯示為 —（對齊 HTML 的 _fmt）
        for c in disp.columns:
            if str(disp[c].dtype) in ("Int64", "float64", "Float64"):
                disp[c] = disp[c].astype(object).where(disp[c].notna(), "—")
        f.write(disp.to_markdown(index=False) + "\n")
        if len(df) > n:
            where = ("完整清單見同資料夾的同名 CSV 檔（可用 Excel 開）。"
                     if csv_on else "完整清單需在控制台勾選『輸出 CSV』後重跑。")
            f.write(f"\n> 📄 僅顯示前 {n} 名，共 {len(df)} 檔符合；{where}\n")


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
    ap.add_argument("--md", action="store_true",
                    help="同時輸出 Markdown 報告（預設只出 HTML）")
    ap.add_argument("--csv", action="store_true",
                    help="同時輸出各軌完整清單 CSV（Excel 可開；預設只出 HTML）")
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
    # 併入千張大戶%＋週增減（每週 update_holders 累積／backfill_holders 回補；沒資料則欄位空白）
    from src.enrich import big_holders_map, big_holder_change_map
    bh = big_holders_map()
    bhc = big_holder_change_map()
    if not df11.empty:
        df11["千張大戶%"] = df11["stock_id"].map(bh)
        df11["千張週增減"] = df11["stock_id"].map(bhc)
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
        except (Exception, SystemExit) as e:
            # 長期軌資料源（TWSE 估值/FinMind 財報）一時抓不到（含 502 等網路錯誤）
            # 不該讓整份日報產不出來 → 優雅降級略過本軌，其餘照常輸出
            print(f"   ⚠️ 長期軌略過（{type(e).__name__}: {e}）")
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

    # 追加 20日 累積窗（月，補10日不足）：同格堆疊今/昨/10/20，不新增欄
    # （60日季窗 2026-08 已移除——太遠參考性低，使用者要求）
    for _w in (20,):
        _fw = flows_mod.institution_flows(days=_w).rename(columns={
            "外資": f"外資{_w}日", "投信": f"投信{_w}日", "自營": f"自營{_w}日",
            "融資增減": f"融資{_w}日", "融券增減": f"融券{_w}日"})
        if _fw is None or _fw.empty:
            continue
        _wcols = [c for c in _fw.columns if c != "stock_id"]
        def _mw(d):
            if d is None or d.empty:
                return d
            d = d.merge(_fw, on="stock_id", how="left")
            for c in _wcols:
                d[c] = d[c].astype("Int64")   # 保留 NA、整數顯示
            return d
        df11, df16, dfdt, df12, dflt = _mw(df11), _mw(df16), _mw(dfdt), _mw(df12), _mw(dflt)

    # 併入「今日/昨日單日」法人分項(外資/投信/自營)＋資券增減，看「今天/昨天誰在動」
    # 同格堆疊順序＝今/昨/10/20（旁邊 10/20日看趨勢）
    _tf = _today_flows()
    _yf = _day_flows(1, "昨日")
    _tcols = ["外資今日", "投信今日", "自營今日", "融資今日", "融券今日"]
    _ycols = ["外資昨日", "投信昨日", "自營昨日", "融資昨日", "融券昨日"]
    for _sf, _scols in ((_tf, _tcols), (_yf, _ycols)):
        if _sf is None or _sf.empty:
            continue
        df11 = df11.merge(_sf, on="stock_id", how="left") if not df11.empty else df11
        df16 = df16.merge(_sf, on="stock_id", how="left") if not df16.empty else df16
        dfdt = dfdt.merge(_sf, on="stock_id", how="left") if not dfdt.empty else dfdt
        df12 = df12.merge(_sf, on="stock_id", how="left") if df12 is not None and not df12.empty else df12
        dflt = dflt.merge(_sf, on="stock_id", how="left") if dflt is not None and not dflt.empty else dflt
        for d in (df11, df16, df12, dflt, dfdt):
            if d is not None and not d.empty:
                for c in _scols:
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
        # 當沖比熱度趨勢（升溫/降溫）僅加在當沖軌：讀 DB day_trade 歷史，挑正在升溫的妖股 arena
        if dfdt is not None and not dfdt.empty:
            _dtrend = dts.trend(dfdt["stock_id"].tolist())
            if _dtrend is not None and not _dtrend.empty:
                dfdt = dfdt.merge(_dtrend, on="stock_id", how="left")
    except Exception as e:
        print(f"   ⚠️ 當沖比率略過（{e}）")

    # 併入借券賣出餘額（法人真實空單，SBL）：FinMind 免費層逐檔查 → 只抓當日候選股聯集省額度；
    # 抓不到/額度不足則欄留白，不影響其他欄。
    try:
        from src import sbl_signal
        _ids = set()
        for d in (df11, df16, dfdt, df12, dflt):
            if d is not None and not d.empty:
                _ids.update(d["stock_id"].tolist())
        # 優先用回補的 DB 歷史（含『借券增減』趨勢、不打 API）；無歷史才 fallback 逐檔 live 查
        _sbl = sbl_signal.compute_from_db(_ids)
        if _sbl is None or _sbl.empty:
            _sbl = sbl_signal.compute(_ids)
        df11 = sbl_signal.enrich(df11, _sbl)
        df16 = sbl_signal.enrich(df16, _sbl)
        dfdt = sbl_signal.enrich(dfdt, _sbl)
        df12 = sbl_signal.enrich(df12, _sbl)
        dflt = sbl_signal.enrich(dflt, _sbl)
    except Exception as e:
        print(f"   ⚠️ 借券賣出餘額略過（{e}）")

    _sh = None
    # 併入外資持股%/市值億/周轉率%（FinMind 1 call 全市場）：
    # 外資持股＝存量(與「外資今日買賣超」流量互補)、市值＝分大型/中小型股、周轉率＝熱度(當沖用)。
    try:
        from src import shareholding as _shold
        _sh = _shold.compute()
        if _sh is not None and not _sh.empty:
            df11, df16, dfdt = _shold.enrich(df11, _sh), _shold.enrich(df16, _sh), _shold.enrich(dfdt, _sh)
            df12, dflt = _shold.enrich(df12, _sh), _shold.enrich(dflt, _sh)
    except Exception as e:
        print(f"   ⚠️ 外資持股/市值/周轉率略過（{e}）")

    # 併入 ATR 風控價位（停損價/停損%/目標價）：純 DB 免費、一次向量化算全市場。
    # 進場價＝今日收盤；停損＝收盤−2×ATR；目標＝2倍風險 → 風報比 1:2（固定故不另列欄）。
    try:
        from src import risk as _risk
        _lv = _risk.levels()
        df11, df16, dfdt = _risk.enrich(df11, _lv), _risk.enrich(df16, _lv), _risk.enrich(dfdt, _lv)
        df12, dflt = _risk.enrich(df12, _lv), _risk.enrich(dflt, _lv)
    except Exception as e:
        print(f"   ⚠️ ATR 風控價位略過（{e}）")

    # 併入處置股/注意股警示（免費 TWSE+TPEX OpenAPI，各 1 call）：處置＝人工分盤撮合，
    # 當沖/短線幾乎做不動，必須先避開 → 全軌加「處置警示」欄。抓不到則欄留白。
    _warn = {}
    try:
        from src import disposal
        _warn = disposal.compute(date.today().isoformat())
        if _warn:
            df11 = disposal.enrich(df11, _warn)
            df16 = disposal.enrich(df16, _warn)
            dfdt = disposal.enrich(dfdt, _warn)
            df12 = disposal.enrich(df12, _warn)
            dflt = disposal.enrich(dflt, _warn)
            print(f"   🚫 處置/注意股警示 {len(_warn)} 檔（處置＝分盤撮合，短線避開）")
    except Exception as e:
        print(f"   ⚠️ 處置股警示略過（{e}）")

    _ex = {}
    # 併入除權息預告（免費 TWSE+TPEX，各 1 call）：除息當天股價扣股利開盤，
    # 不知情會把「參考價下修」誤判成大跌/跌破均線 → 只標近 45 天內的。
    try:
        from src import exdividend
        _ex = exdividend.compute(date.today().isoformat())
        if _ex:
            df11 = exdividend.enrich(df11, _ex)
            df16 = exdividend.enrich(df16, _ex)
            dfdt = exdividend.enrich(dfdt, _ex)
            df12 = exdividend.enrich(df12, _ex)
            dflt = exdividend.enrich(dflt, _ex)
            print(f"   📅 除權息預告 {len(_ex)} 檔（近 45 天）")
    except Exception as e:
        print(f"   ⚠️ 除權息預告略過（{e}）")

    # 併入分點主力淨額 + 隔日沖賣壓%（需 Sponsor 分點；不可用則欄留白）。
    # 分點量大→逐檔 on-demand(每檔 T/T-1 各 1 call)，僅對『顯示』的候選(head 20/軌)控制 call 數。
    try:
        from src import broker_signal
        from src.db import read_table
        _px = read_table("price", use_cache=True)[["date", "stock_id", "volume"]]
        _dts = sorted(_px["date"].unique())
        _day = _dts[-1]
        _prev = _dts[-2] if len(_dts) >= 2 else None
        _vol = _px[_px["date"] == _day].set_index("stock_id")["volume"].to_dict()
        _bids = set()
        for d in (df11, df16, dfdt, df12, dflt):
            if d is not None and not d.empty:
                _bids.update(d["stock_id"].head(20).tolist())
        _brk = broker_signal.compute(_bids, _day, _prev, _vol)
        if _brk is not None and not _brk.empty:
            df11 = broker_signal.enrich(df11, _brk)
            df16 = broker_signal.enrich(df16, _brk)
            dfdt = broker_signal.enrich(dfdt, _brk)
            df12 = broker_signal.enrich(df12, _brk)
            dflt = broker_signal.enrich(dflt, _brk)
    except Exception as e:
        print(f"   ⚠️ 分點主力/隔日沖略過（{e}）")

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
        "📊 法人/資券欄｜每格四個數＝今日／昨日／近10日／近20日(月)累積(HTML 由上而下四行、MD 以／分隔，位置對應)；"
        "外資·投信·自營＝買賣超(🔴買超/🟢賣超)，融資·融券＝餘額增減(🔴增/🟢減)；短窗看轉折、長窗看主力方向"
        "\n　↳ 融資增＝散戶借錢追價⚠️籌碼較不安定；融券增＝空單多·股價若強有軋空機會(未來須回補)——顏色只表增減，好壞配股價方向看"
        "\n🔁 連續｜外資連·投信連·自營連＝當前連續同向天數(正=連買／負=連賣，看誰在狂買狂賣)"
        "\n🎯 主導度%＝今日法人淨額÷成交量(法人是否主導)；💬 籌碼訊號＝今日共識／連買連賣強度一句話合成"
        "\n🛡️ 風控價位｜停損價＝今日收盤−2×ATR(近14日平均波動)、目標價＝2倍風險 → **風報比固定 1:2**(故不另列欄)；"
        "停損%＝停損距離佔股價%，**因股而異**(波動大的股本來就該把停損放寬，固定5%會被雜訊掃出場)。"
        "⚠️進場價假設＝今日收盤，實際進場價不同要自己按比例調整；張數/部位配置需帶入你的總資金，跑 `python -m scripts.risk_calc`"
        "\n🚫 處置警示｜處置股＝人工分盤撮合(約2~5分鐘一次)＋常需預收款券 → **當沖/短線幾乎做不動，務必避開**；⚠️將處置＝公告了但還沒開始"
        "\n📏 今日量張＝今日成交總量(張)；量能倍數＝今日量÷近20日均量(>1.5爆量·<1量縮)；券資比%＝融券÷融資餘額(空方相對力道)"
        "\n　↳ 融資·融券佔量%＝今日增減÷今日量：把絕對張數『相對化』，例『融券−343』佔量僅0.9%＝杯水車薪，別被絕對數字誤導"
        "\n　↳ 借券賣出餘額(張)＝法人/外資向券商借股放空、尚未回補的在外空單；⚠️與融券不同(融券=散戶)，這是**法人真實空單**。餘額高=法人空方壓力大，後續回補是潛在買盤。絕對張數大型股天生高，判讀對照股本/成交量"
        "\n　↳ 借券增減(張)＝借券賣出餘額 vs 前一交易日：🔴+=法人空單加碼(偏空)／🟢−=法人回補(潛在買盤)；看的是法人空方『今天在加碼還是收手』(顏色只表增減方向，好壞配股價看)"
        "\n🏦 主力分點｜主力淨額(張)＝今日前15大買超分點淨額＋前15大賣超分點淨額(🔴淨買/🟢淨賣)：籌碼是否集中在少數主力進出(比三大法人更廣，含特定券商/大戶)"
        "\n　↳ 隔日沖賣壓%＝昨日『前15大買超分點』今日轉淨賣量÷今日量：抓當沖比看不到的隔日沖(昨進今出的大戶倒貨)——比率高=昨天的大買家今天在跑(隔天常殺低)⚠️；只算昨日大買家、濾掉正常換手；需 Sponsor 分點，抓不到留白"
        "\n📈 技術面｜均線排列＝5/10/20MA短多空(🔴多頭5>10>20順勢·🟢空頭·⚪糾結待變盤)；季線年線＝股價vs60/240MA中長多空(🔴站上季年最強·🟢跌破季年最弱)"
        "\n　↳ 20MA乖離%＝股價偏離20MA(正=偏強·過大追高風險·負=跌破)；52週位置%＝在近1年區間位置(0=年內最低·100=年內最高·波段高低檔)"
        "\n　↳ 成交額億＝今日量×收盤(資金權重，張數不分價位會誤判：2000元股vs20元股同100張差百倍)"
        "\n🔥 當沖比率%＝當日沖銷成交量÷總成交量：>50%＝當沖客『對殺』盤(妖股特徵·暴漲跌·法人不玩)，"
        "資券數字多為帳面沖銷別當真；例5328華容~79%＝純對殺，融券-343佔量僅0.9%就是這原因"
    )
    def _dated(asof, base):  # 有資料日期才加「📅 …」首行，避免空日期留下光禿的一行
        return (f"📅 {asof}\n" if asof else "") + base
    # 千張大戶欄僅 T11 有 → 說明只掛 note11，避免其他軌顯示描述不存在欄位的註記
    _holder_note = ("\n💎 千張大戶%＝持股≥1000張大戶占股本比(集保週結算·週頻)：高=籌碼沉澱在大戶手中(較穩)；"
                    "千張週增減(pp)＝本週 vs 上週：🔴+=大戶加碼(吸籌)／🟢−=大戶減碼(派發給散戶)——看大戶『這週在進還是出』")
    note11 = _dated(_asof_note(df11, "t11"), _flownote + _holder_note)
    note16 = _dated(_asof_note(df16, "t16"), _flownote)
    _dt_asof = _asof_note(dfdt, "daytrade")
    _dt_trend_note = ("\n🔥 當沖比均5日＝近5日當沖比%均(熱度基準)；當沖比趨勢＝今日 vs 前4日均："
                      "🔥升溫(今>均×1.2·資金/妖股湧入·波動放大)／❄降溫(今<均×0.8·退燒)／➖持平"
                      "——只表當沖熱度升降，非多空方向；升溫股盤中振幅大，當沖機會與風險同step。")
    notedt = ("📅 " + (f"{_dt_asof}。" if _dt_asof else "") + report_html.DAYTRADE_NOTE
              + " " + report_html.BIAS_NOTE + "\n" + _flownote + _dt_trend_note)

    # 第6軌：隔日沖鎖碼候選（漲停/大漲 + 主力/隔日沖大戶鎖碼進場）——自足(自抓分點)
    try:
        dfsnipe = daytrade_snipe.run()
    except Exception as e:
        print(f"   ⚠️ 隔日沖鎖碼軌略過（{e}）")
        dfsnipe = None
    # 沿用已算好的全市場 enrich（不重抓）補上判斷欄：量能/技術面/法人今日/借券——
    # 讓漲停鎖碼股也看得到爆量、位階(乖離/52週)、趨勢(均線/季年)、法人站哪邊、空方壓力。
    if dfsnipe is not None and not dfsnipe.empty:
        # 只併『今日』法人單日(外資/投信今日)：不加 10日「外資」欄，免觸發 report_html 的
        # 外資/投信 多時窗合併(MERGE_GROUPS)把單日欄吃掉（snipe 只看今天法人站哪邊）。
        if _tf is not None and not _tf.empty:
            dfsnipe = dfsnipe.merge(_tf, on="stock_id", how="left")
            for c in _tcols:
                if c in dfsnipe.columns:
                    dfsnipe[c] = dfsnipe[c].astype("Int64")
        dfsnipe = chip_signal.enrich(dfsnipe, _sig)         # 今日量張/量能倍數/券資比%/籌碼訊號
        dfsnipe = tech_signal.enrich(dfsnipe, _tech)        # 均線排列/季年/20MA乖離%/52週位置%
        try:                                                # 借券：snipe 股不在其他軌 _ids，逐檔讀 DB(免 API)
            from src import sbl_signal as _sbls
            dfsnipe = _sbls.enrich(dfsnipe, _sbls.compute_from_db(dfsnipe["stock_id"].tolist()))
        except Exception:
            pass
        # 風控/事件欄：處置警示對這軌最關鍵（漲停鎖碼股最常被處置＝分盤，隔日根本沖不動）。
        # 全部重用前面算好的 map/表，不重抓。
        try:
            from src import disposal as _dsp, exdividend as _exd, shareholding as _shd
            if _warn:
                dfsnipe = _dsp.enrich(dfsnipe, _warn)
            if _ex:
                dfsnipe = _exd.enrich(dfsnipe, _ex)
            if _sh is not None and not _sh.empty:
                dfsnipe = _shd.enrich(dfsnipe, _sh)
        except Exception:
            pass
    _snipe_asof = _asof_note(dfsnipe, "snipe") if dfsnipe is not None else ""
    notesnipe = ("📅 " + (f"{_snipe_asof}。" if _snipe_asof else "")
                 + "🎯 隔日沖鎖碼候選＝今日漲停/大漲(≥9%) + 主力(前15分點)集中淨買『鎖碼』"
                 "，且今日大買分點正是此檔近期『隔日沖常客』(反覆昨買今賣)→ 隔日(T+1)這些大戶常倒貨"
                 "、開高走低/對殺，明日當沖 arena。"
                 "\n　↳ 昨主力淨額→今主力淨額(昨vs今)：昨買今也買=持續鎖碼(明日更防倒貨)、昨買今賣=大戶已在倒；"
                 "隔日沖賣壓%=昨日前15大買分點今日轉賣的對沖量÷今量(『昨進今出』的實現驗證，越高＝昨天鎖碼的今天真的在倒)；"
                 "\n　↳ **兩份黑名單分開看**：全市場黑名單＝跨所有股票統計的隔日沖慣犯(帶隔日沖率%，樣本大最可信)；"
                 "本檔黑名單＝專門在這檔反覆昨買今賣的分點(專屬但樣本小)。兩邊都上榜＝最該防。"
                 "名字後的 **+N張**＝該分點今天買了幾張(黑名單買張＝合計)；明日追蹤區會逐點列出他們各倒了幾張。"
                 "\n　↳ **預估賣壓張/佔量%（前瞻·今天就能算）**＝Σ(今日各分點淨買張×該分點歷史回吐量%)，"
                 "估明日這些人可能倒多少。🔬已回測(356樣本·point-in-time無前視)：**最高四分位隔日盤中 −1.64%"
                 "(跌比56.8%) vs 最低組 −0.72%**，相關 −0.10；鎖碼股子集單調(−0.33/−0.73/−1.42)。"
                 "⚠️全體四組**非乾淨單調**(Q1比Q2/Q3差)、edge 薄、扣當沖成本有限 → 當**排序/警示**用，非提款機。"
                 "\n　↳ 判斷欄(沿用他軌)：量能倍數(>1.5爆量撐漲停·<1量縮虛漲)、20MA乖離%/52週位置%(位階·過高追高風險)、"
                 "均線排列/季線年線(趨勢)、外資/投信今日(法人站同邊否)、借券增減(空方加碼/回補)、券資比%(空方力道)。"
                 "\n⚠️ 散戶『隔日沖鎖碼股』打法、**未回測驗證有 edge**、非投資建議；需 Sponsor 分點，"
                 "無則只出漲停清單。方向未定(可能軋空續強，也可能開高走低)——回測結論 EOD 欄位皆無法穩定預測隔日方向，"
                 "僅隔日沖賣壓%極端(≥20%)或預估賣壓佔量%偏高才弱預告次日偏空。僅圈定舞台、非多空訊號。"
                 "\n　↳ 想看『哪些分點昨買今賣』的逐檔名單，跑 python -m scripts.run_stock <代號>（個股深掘）。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    # pick 標籤用『資料日』(DB 最新交易日)而非執行日：兩者常不同(盤前跑、跨時區、補跑舊日)，
    # 用執行日會把不同資料日的精選標成同一天而互相覆蓋 → 追蹤直接斷掉(2026-08 實際踩過)。
    from src.db import connect as _connect
    with _connect() as _c:
        _dd = _c.execute("SELECT MAX(date) FROM price").fetchone()[0]
    data_day = _dd or today

    # 昨日精選今日追蹤：先算昨日精選對今天的表現，再存今天的精選（供明天追蹤）
    import pandas as pd
    from src import picks_tracker as pt
    ft = pt.followthrough(data_day)
    ftstats = pt.summary_stats(ft) if ft else {}
    pt.save(data_day, {"波段T11": df11, "波段T16": df16, "當沖": dfdt,
                       "隔日沖鎖碼": dfsnipe}, n=15)
    # 昨日隔日沖鎖碼候選 → 今日開高低收（專屬區塊，具體驗證開高走低）
    so = pt.snipe_ohlc(data_day)
    sostats = pt.snipe_ohlc_stats(so)
    # 持股籌碼歸因算一次，.md 與 .html 共用（確保兩份內容一致）
    pf_attr = _holdings_attribution(pf_view) if not pf_view.empty else []

    # MD 為選用輸出（預設只出 HTML）：未勾選時寫進記憶體緩衝後丟棄，
    # 這樣整段 f.write 排版邏輯不必改，也保證 MD/HTML 內容一致（同一份程式產生）。
    path = OUTPUT_DIR / f"{today}_run_all.md"
    _md_sink = open(path, "w", encoding="utf-8") if args.md else io.StringIO()
    with _md_sink as f:
        f.write(f"# 台股每日整合報告 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。先看環境紅綠燈決定要不要出手。\n\n")
        f.write("## 🚦 環境紅綠燈\n\n")
        f.write(f"- **{regime_mod.summary_line(reg)}**\n")
        f.write(f"- {gm.sox_signal(glob)}\n")
        f.write(f"- 全球：{' ｜ '.join(gm.summary_lines(glob))}\n")

        _section(f, "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", df11,
                 ["stock_id", "name", "market", "產業", "處置警示", "除權息", "investor", "close", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "千張週增減", "風險", "紅旗", "score"],
                 note=note11, bt="T11", csv_on=args.csv)
        _landmine_warn(f, df11)
        _section(f, "🟡 波段｜T16 抗跌強勢", df16,
                 ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號",
                  "return_%", "vs_market_%", "風險", "紅旗"], note=note16, bt="T16", csv_on=args.csv)
        _landmine_warn(f, df16, "T16 強勢榜")
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n### ⭐ 波段雙訊號交集（法人買且抗跌）\n\n")
            nm = df11.set_index("stock_id")["name"].to_dict()
            f.write("".join(f"- {s} {nm.get(s,'')}\n" for s in both) if both else "（無）\n")

        _section(f, "🚀 成長｜T12 月營收動能（YoY強+近月加速）", df12,
                 ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"], n=20, note=_flownote.strip(), bt="T12", csv_on=args.csv)
        _section(f, "🟢 長期｜價值+成長+配息", dflt,
                 ["stock_id", "name", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "殖利率%", "PER",
                  "ROE估%", "營收YoY%", "連配息年", "score"], skipped=args.skip_longterm,
                 note=_flownote.strip(), bt="長期", csv_on=args.csv)
        _section(f, "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", dfdt,
                 ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "多空傾向", "與大盤", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%", "當沖比均5日", "當沖比趨勢",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "當日振幅%", "均振幅%", "量能倍數"],
                 note=notedt, bt="當沖", csv_on=args.csv)
        _section(f, "🎯 隔日沖鎖碼候選｜漲停/大漲 + 主力/隔日沖大戶鎖碼（明日對殺 arena，非即時訊號）", dfsnipe,
                 ["stock_id", "name", "market", "產業", "處置警示", "除權息", "close", "漲跌%", "成交額億", "周轉率%", "今日量張", "量能倍數", "當沖比率%",
                  "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "外資今日", "投信今日", "券資比%", "借券賣出餘額", "借券增減",
                  "昨主力淨額", "今主力淨額", "隔日沖賣壓%", "預估賣壓張", "預估賣壓佔量%", "全市場黑名單", "本檔黑名單", "黑名單買張", "籌碼訊號", "黑名單明細"],
                 note=notesnipe, bt="隔日沖鎖碼", csv_on=args.csv)
        if not pf_view.empty:
            f.write(f"\n## 📋 我的持股（總損益 {pf_summary['總損益']:+,}"
                    f"｜{pf_summary['總報酬%']:+.2f}%）\n\n")
            f.write(pf_view.to_markdown(index=False) + "\n")
            attr = pf_attr
            if attr:
                f.write("\n### 📊 持股今日籌碼歸因（今 vs 昨，自動）\n\n")
                for sid, name, line in attr:
                    f.write(f"- **{sid} {name}**：{line}\n")
        # 昨日隔日沖鎖碼候選 → 今日走勢（開/高/低/收，具體驗證開高走低）
        if so.get("rows"):
            f.write(f"\n## 🎯 昨日隔日沖鎖碼候選 → 今日走勢"
                    f"（{so['date']} 精選 → {so.get('trade_date', '今日')} 開高低收）\n\n")
            sline = ""
            if sostats:
                sline = (f"　今日均跳空 {sostats['gap']:+.2f}%、均盤中 {sostats['oc']:+.2f}%"
                         f"（盤中走低 {sostats['oc_down']}/{sostats['n']} 檔）")
            f.write("> 鎖碼淨額＝當時主力買了多少(pick 當日前15買+前15賣淨額)。"
                    "具體驗證『開高走低』：跳空%＝隔夜高開(今開vs昨收)、"
                    f"盤中%＝開盤後走勢(今收vs今開，正=守住/走高、負=開高走低)。{sline}\n")
            f.write("> 🚩 **昨天列出的黑名單**(pick 當下記下、非事後重算)：全市場黑名單＝跨所有股票的"
                    "隔日沖慣犯(附隔日沖率%，樣本大最可信)、本檔黑名單＝專門玩這檔的常客(樣本小)；"
                    "兩邊都上榜＝最該防。名字後的 **+N張**＝那天各買了幾張。\n")
            f.write("> 💣 **他們倒了幾張**：黑名單買張(昨天合計買) → 黑名單賣張(今天合計倒)、"
                    "倒貨%＝賣÷買(越接近100%＝昨天鎖的今天倒光；**>100%＝賣得比昨天買的還多**，"
                    "手上有更早的貨或反手加空)；最右『黑名單逐點』逐一列「分點 買X→賣Y(Z%)」，"
                    "今日反手續買則標『今再買』(＝還沒跑、甚至加碼，常是續攻/軋空的一方)。需 Sponsor 分點。\n")
            f.write("> 📊 **為何漲/跌**：預估賣壓%(昨天預測明日會倒多少) vs 實際賣壓%"
                    "(今天昨日大買分點真的倒了多少÷今量)——預測兌現則多半走弱；"
                    "今主力淨額(正=今天主力續買撐盤/負=在倒)；"
                    "高檔回落%=今收vs今高(接近0=守在高檔、跌深=衝高被倒)；"
                    "振幅%/量能倍數/當沖比%=對殺熱度。分點欄需 Sponsor，抓不到留白。\n")
            f.write("> ⚠️ 方向不穩(可能軋空續強)、edge 薄、非投資建議。\n\n")
            sdf = pd.DataFrame(so["rows"])
            sdf = sdf[[c for c in ["stock_id", "name", "鎖碼淨額", "全市場黑名單", "本檔黑名單",
                                   "黑名單買張", "黑名單賣張", "倒貨%",
                                   "預估賣壓%", "實際賣壓%", "今主力淨額", "昨收", "今開",
                                   "今高", "今低", "今收", "漲跌%", "跳空%", "盤中%",
                                   "高檔回落%", "振幅%", "量能倍數", "當沖比%",
                                   "黑名單逐點"] if c in sdf.columns]]
            sdf = sdf.rename(columns={"stock_id": "代號", "name": "名稱"})
            f.write(sdf.to_markdown(index=False) + "\n")
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
        {"bt": "T11", "title": "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", "df": df11, "note": note11,
         "cols": ["stock_id", "name", "market", "產業", "處置警示", "除權息", "investor", "close", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "千張週增減", "風險", "紅旗", "score"],
         "signed": ["今日漲跌%", "20MA乖離%", "停損%","price_gain_%", "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "借券增減", "主力淨額", "千張週增減", "籌碼訊號"],
         "landmine": True, "landmine_label": "T11 候選",
         "after_intersection": True},
        {"bt": "T16", "title": "🟡 波段｜T16 抗跌強勢", "df": df16, "note": note16, "n": 15,
         "cols": ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號",
                  "return_%", "vs_market_%", "風險", "紅旗"],
         "signed": ["今日漲跌%", "20MA乖離%", "停損%","return_%", "vs_market_%", "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "借券增減", "主力淨額", "籌碼訊號"],
         "landmine": True, "landmine_label": "T16 強勢榜"},
        {"bt": "T12", "title": "🚀 成長｜T12 月營收動能（YoY強+近月加速）", "df": df12, "n": 20, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "YoY%",
                  "累計YoY%", "MoM%", "加速度", "站上20MA", "score"],
         "signed": ["今日漲跌%", "20MA乖離%", "停損%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "借券增減", "主力淨額", "籌碼訊號",
                    "YoY%", "累計YoY%", "MoM%", "加速度"]},
        {"bt": "長期", "title": "🟢 長期｜價值+成長+配息", "df": dflt, "skipped": args.skip_longterm, "note": _flownote.strip(),
         "cols": ["stock_id", "name", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "定調", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "量能倍數", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "殖利率%", "PER", "ROE估%",
                  "營收YoY%", "連配息年", "score"],
         "signed": ["今日漲跌%", "20MA乖離%", "停損%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "借券增減", "主力淨額", "籌碼訊號", "營收YoY%"]},
        {"bt": "當沖", "title": "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", "df": dfdt, "note": notedt, "n": 20,
         "cols": ["stock_id", "name", "market", "產業", "處置警示", "除權息", "今日收盤", "今日漲跌%", "多空傾向", "與大盤", "停損價", "停損%", "目標價", "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "市值億", "外資持股%", "成交額億", "周轉率%", "當沖比率%", "當沖比均5日", "當沖比趨勢",
                  "外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "今日量張", "券資比%", "融資佔量%", "融券佔量%", "借券賣出餘額", "借券增減", "主力淨額", "隔日沖賣壓%", "籌碼訊號", "當日振幅%", "均振幅%", "量能倍數"],
         "signed": ["今日漲跌%", "20MA乖離%", "停損%","外資今日", "投信今日", "自營今日", "融資今日", "融券今日", "外資", "投信", "自營", "融資增減", "融券增減", "外資連", "投信連", "自營連", "主導度%", "融資佔量%", "融券佔量%", "借券增減", "主力淨額", "籌碼訊號"]},
        {"bt": "隔日沖鎖碼", "title": "🎯 隔日沖鎖碼候選｜漲停/大漲 + 主力/隔日沖大戶鎖碼（明日對殺 arena，非即時訊號）",
         "df": dfsnipe, "note": notesnipe, "n": 15,
         "cols": ["stock_id", "name", "market", "產業", "處置警示", "除權息", "close", "漲跌%", "成交額億", "周轉率%", "今日量張", "量能倍數", "當沖比率%",
                  "均線排列", "季線年線", "半年線", "20MA乖離%", "52週位置%", "外資今日", "投信今日", "券資比%", "借券賣出餘額", "借券增減",
                  "昨主力淨額", "今主力淨額", "隔日沖賣壓%", "預估賣壓張", "預估賣壓佔量%", "全市場黑名單", "本檔黑名單", "黑名單買張", "籌碼訊號", "黑名單明細"],
         "signed": ["漲跌%", "20MA乖離%", "停損%", "外資今日", "投信今日", "借券增減", "昨主力淨額", "今主力淨額", "籌碼訊號"]},
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
    if args.csv:
        for b in blocks:
            d = b.get("df")
            if d is None or d.empty:
                continue
            slug = next((v for k, v in _slugs.items() if k in b["title"]), "清單")
            name = f"{today}_{slug}.csv"
            cols = [c for c in b["cols"] if c in d.columns]
            report_html.rename_cn(d[cols]).to_csv(OUTPUT_DIR / name, index=False,
                                                  encoding="utf-8-sig")
            b["csv_name"] = name          # 有寫檔才設，HTML 註記才不會開空頭支票
            csv_written.append(name)

    inter = [f"{s} {nm.get(s,'')}" for s in both]
    html = report_html.build(today, reg, gm.summary_lines(glob), gm.sox_signal(glob),
                             blocks, intersection=inter, followthrough=ft, ftstats=ftstats,
                             snipe_ohlc=so, snipe_ohlc_stats=sostats)
    html_path = OUTPUT_DIR / f"{today}_run_all.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\n✅ 整合報告（HTML，瀏覽器開）→ {html_path}")
    if args.md:
        print(f"   Markdown → {path}")
    if csv_written:
        print(f"   完整清單 CSV（Excel 可開）→ {OUTPUT_DIR}/ 內：{'、'.join(csv_written)}")
    if not args.md and not args.csv:
        print("   （MD／CSV 未輸出＝預設；要的話在控制台勾選『輸出 MD』『輸出 CSV』）")
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
