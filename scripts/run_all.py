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


def _summary(today, reg, glob, df11, df16, inter, dflt, pf_view=None, pf_summary=None):
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
        lines.append("")
    if inter:
        lines.append("⭐ <b>雙訊號交集(法人買且抗跌)</b>：" + "、".join(inter))
    if not df11.empty:
        top = df11.head(3)
        lines.append("🟡 波段T11 前3：" +
                     "、".join(f"{r.stock_id} {r.name}" for r in top.itertuples()))
    if dflt is not None and not dflt.empty:
        top = dflt.head(3)
        lines.append("🟢 長期 前3：" +
                     "、".join(f"{r.stock_id} {r.name}" for r in top.itertuples()))
    lines.append("\n完整報告見 reports/screener/（.html 用瀏覽器開）")
    return "\n".join(x for x in lines if x is not None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--skip-longterm", action="store_true")
    ap.add_argument("--notify", action="store_true", help="把摘要推播到手機（Telegram/Email，需設 .env）")
    ap.add_argument("--days", type=int, default=12)
    args = ap.parse_args()

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
    print("[當沖] 候選掃描 …")
    dfdt = daytrade.run()
    dflt = None
    if not args.skip_longterm:
        print("[長期] 價值+成長+配息（較慢）…")
        dflt = lt.run(verbose=False)

    from src import portfolio as pf
    pf_view, pf_summary = pf.status()

    # 資料日期說明（避免區間值/基準日收盤被誤讀成單日/最新日）
    note11 = _asof_note(df11, "t11")
    note16 = _asof_note(df16, "t16")
    notedt = _asof_note(dfdt, "daytrade")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_run_all.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 台股每日整合報告 — {today}\n\n")
        f.write("> ⚠️ 候選觀察名單，非投資建議。先看環境紅綠燈決定要不要出手。\n\n")
        f.write("## 🚦 環境紅綠燈\n\n")
        f.write(f"- **{regime_mod.summary_line(reg)}**\n")
        f.write(f"- {gm.sox_signal(glob)}\n")
        f.write(f"- 全球：{' ｜ '.join(gm.summary_lines(glob))}\n")

        _section(f, "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", df11,
                 ["stock_id", "name", "market", "investor", "close",
                  "price_gain_%", "consec_buy_days", "buy_ratio_%", "千張大戶%", "score"],
                 note=note11)
        _section(f, "🟡 波段｜T16 抗跌強勢", df16,
                 ["stock_id", "name", "market", "return_%", "vs_market_%"], note=note16)
        if not df11.empty and not df16.empty:
            both = set(df11["stock_id"]) & set(df16["stock_id"])
            f.write("\n### ⭐ 波段雙訊號交集（法人買且抗跌）\n\n")
            nm = df11.set_index("stock_id")["name"].to_dict()
            f.write("".join(f"- {s} {nm.get(s,'')}\n" for s in both) if both else "（無）\n")

        _section(f, "🟢 長期｜價值+成長+配息", dflt,
                 ["stock_id", "name", "產業", "close", "殖利率%", "PER",
                  "ROE估%", "營收YoY%", "連配息年", "score"], skipped=args.skip_longterm)
        _section(f, "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", dfdt,
                 ["stock_id", "name", "market", "close", "當日振幅%", "均振幅%", "量能倍數"],
                 note=notedt)
        if not pf_view.empty:
            f.write(f"\n## 📋 我的持股（總損益 {pf_summary['總損益']:+,}"
                    f"｜{pf_summary['總報酬%']:+.2f}%）\n\n")
            f.write(pf_view.to_markdown(index=False) + "\n")

    # 同步輸出 HTML（表格永遠對齊、紅漲綠跌上色）
    both = (set(df11["stock_id"]) & set(df16["stock_id"])) if not df11.empty and not df16.empty else set()
    nm = df11.set_index("stock_id")["name"].to_dict() if not df11.empty else {}
    blocks = [
        {"title": "🟡 波段｜T11 法人吸貨（上市投信/上櫃外資）", "df": df11, "note": note11,
         "cols": ["stock_id", "name", "market", "investor", "close", "price_gain_%",
                  "consec_buy_days", "buy_ratio_%", "千張大戶%", "score"],
         "signed": ["price_gain_%"], "after_intersection": True},
        {"title": "🟡 波段｜T16 抗跌強勢", "df": df16, "note": note16,
         "cols": ["stock_id", "name", "market", "return_%", "vs_market_%"],
         "signed": ["return_%", "vs_market_%"]},
        {"title": "🟢 長期｜價值+成長+配息", "df": dflt, "skipped": args.skip_longterm,
         "cols": ["stock_id", "name", "產業", "close", "殖利率%", "PER", "ROE估%",
                  "營收YoY%", "連配息年", "score"], "signed": ["營收YoY%"]},
        {"title": "🔴 當沖候選｜高波動+高流動（盤中盯，非即時訊號）", "df": dfdt, "note": notedt,
         "cols": ["stock_id", "name", "market", "close", "當日振幅%", "均振幅%", "量能倍數"],
         "signed": []},
    ]
    if not pf_view.empty:
        blocks.append({
            "title": f"📋 我的持股（總損益 {pf_summary['總損益']:+,}｜{pf_summary['總報酬%']:+.2f}%）",
            "df": pf_view,
            "cols": ["代號", "名稱", "張數", "成本", "現價", "損益%", "損益金額", "停損", "狀態"],
            "signed": ["損益%", "損益金額"]})
    inter = [f"{s} {nm.get(s,'')}" for s in both]
    html = report_html.build(today, reg, gm.summary_lines(glob), gm.sox_signal(glob),
                             blocks, intersection=inter)
    html_path = OUTPUT_DIR / f"{today}_run_all.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\n✅ 整合報告 → {path}")
    print(f"   HTML（瀏覽器開、表格對齊）→ {html_path}")
    print(f"   波段T11 {len(df11)} / T16 {len(df16)} ｜ 當沖 {len(dfdt)}"
          + (f" ｜ 長期 {len(dflt)}" if dflt is not None else " ｜ 長期(略過)"))

    if args.notify:
        from src.notify import notify
        ok, ch, detail = notify(_summary(today, reg, glob, df11, df16, inter, dflt, pf_view, pf_summary),
                                subject=f"台股每日報告 {today}", file_path=str(html_path))
        print(f"   📲 推播（{ch}）：{'成功' if ok else '失敗 - ' + detail}")


if __name__ == "__main__":
    main()
