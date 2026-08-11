"""個股深掘：把單一股票各面向歷史拉齊成『籌碼病歷表』(.md + .html)。

決策某一檔時跑一次 → 看主力是誰、何時進出、隔日沖慣性。
分點需 Sponsor（抓不到則分點區塊留白，其餘 DB 區塊照出）。

用法（專案根目錄）：
    python -m scripts.run_stock 1303            # 近 30 交易日
    python -m scripts.run_stock 1303 --days 60
輸出：reports/stock/<代號>_<名稱>_深掘.md / .html
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src import stock_deepdive as dd
from src import report_html as rh
from src import svgchart as sc
from src.config import OUTPUT_DIR

# 圖譜區塊樣式（明暗皆清楚；接在 rh._CSS 後）
_CHART_CSS = """
.chgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin:10px 0 6px}
.chblk{border:1px solid #d5dbe4;border-radius:8px;padding:8px 10px;background:#fff}
.chttl{font-size:13px;font-weight:600;color:#2c3e50;margin-bottom:4px}
.chcap{font-size:11px;color:#777;margin-top:2px}
@media (prefers-color-scheme: dark){.chblk{background:#1e2126;border-color:#333a44}.chttl{color:#cdd6e0}}
"""


def _charts(tl, bt, dtl, ht, mas=None) -> str:
    """組『圖譜』區塊：只放有資料的迷你圖（收盤+均線/主力淨額/隔日沖賣壓%/當沖比%/借券/大戶）。"""
    blocks = []

    def blk(title, svg, cap=""):
        c = f'<div class="chcap">{cap}</div>' if cap else ""
        blocks.append(f'<div class="chblk"><div class="chttl">{title}</div>{svg}{c}</div>')

    d = list(tl["date"])
    # 收盤＋均線疊圖（藍收盤／橙MA5／紫MA20／綠MA60，共用價格軸）
    if mas and mas.get("收盤"):
        series = [("收盤", "#2d7ef7", mas["收盤"]), ("MA5", "#e67e22", mas["MA5"]),
                  ("MA20", "#8e44ad", mas["MA20"]), ("MA60", "#158a4e", mas["MA60"])]
        blk("收盤 + 均線", sc.lines(series, mas["dates"], fmt="{:,.2f}"),
            "🔵收盤 🟠MA5 🟣MA20 🟢MA60；收盤在均線上方＝偏多、均線糾結＝盤整")
    else:
        blk("收盤價", sc.line(list(tl["收盤"]), d, unit="", fmt="{:,.2f}", color="#2d7ef7"),
            "藍線＝收盤，對照下列籌碼變化發生在什麼價位")
    if bt is not None and not bt.empty:
        bd = list(bt["date"])
        blk("主力淨額（逐日·紅買綠賣）",
            sc.bars(list(bt["主力淨額"]), bd, signed=True, unit=" 張"),
            "前15買超＋前15賣超分點淨額：紅=主力淨買、綠=淨賣")
        if "隔日沖賣壓%" in bt.columns:
            blk("隔日沖賣壓%（逐日）", sc.bars(list(bt["隔日沖賣壓%"]), bd, unit="%", fmt="{:,.1f}"),
                "昨日大買家今日倒貨量佔比：柱越高＝隔日沖倒貨越兇（隔天常殺低⚠️）")
    if dtl is not None and not dtl.empty:
        blk("當沖比%（逐日）", sc.line(list(dtl["當沖比%"]), list(dtl["date"]), unit="%",
                                     fmt="{:,.1f}", color="#e67e22"),
            "當沖量÷總量：越高＝當沖客對殺越熱（妖股特徵）")
    if "借券餘額" in tl.columns and tl["借券餘額"].notna().any():
        blk("借券賣出餘額（法人空單）", sc.line(list(tl["借券餘額"]), d, unit=" 張", fmt="{:,.0f}",
                                          color="#8e44ad"),
            "法人真實空單餘額：升＝加空、降＝回補（潛在買盤）")
    if ht is not None and not ht.empty:
        blk("千張大戶%（週頻）", sc.line(list(ht["千張大戶%"]), list(ht["date"]), unit="%",
                                     fmt="{:,.2f}", color="#158a4e"),
            "≥1000張大戶持股比：升＝籌碼沉澱、降＝派發給散戶")
    if not blocks:
        return ""
    return f'<h2>📈 圖譜（滑鼠移到點/柱看數值）</h2><div class="chgrid">{"".join(blocks)}</div>'

# 各表的「有正負、要上色」欄（紅正綠負，台股慣例）
_SIGNED = {"漲跌%", "外資", "投信", "自營", "融資增減", "融券增減", "借券增減",
           "主力淨額", "大戶週增pp", "20MA乖離%", "EPS單季", "EPS年增%", "營收YoY%",
           "營收MoM%", "累計YoY%", "自由現金流億", "營運CF億"}

# 技術面卡要顯示的欄（趨勢/位置導向；籌碼細節見下面時間序列表）
_TECH_CARD = ["定調", "均線排列", "季線年線", "20MA乖離%", "52週位置%", "量能倍數", "成交額億"]


def _tech_card_df(tech: dict) -> pd.DataFrame:
    """技術面 snapshot dict → 單列 DataFrame（只留有值的欄），供 _md_table/_html_table 對齊輸出。"""
    if not tech:
        return pd.DataFrame()
    row = {k: tech.get(k) for k in _TECH_CARD if tech.get(k) not in (None, "", "—")}
    return pd.DataFrame([row]) if row else pd.DataFrame()


def _val_card_df(val: dict, streak: int) -> pd.DataFrame:
    """估值卡：PER/PBR/殖利率%/PER近1年位置%/連續配息年數 → 單列 DataFrame。"""
    if not val and not streak:
        return pd.DataFrame()
    row = {"PER": val.get("PER"), "PBR": val.get("PBR"), "殖利率%": val.get("殖利率%"),
           "PER近1年位置%": val.get("PER近1年位置%"), "連續配息年數": streak or None}
    row = {k: v for k, v in row.items() if v not in (None, "")}
    return pd.DataFrame([row]) if row else pd.DataFrame()


def _fund_charts(rev, prof, val) -> str:
    """基本面迷你圖（HTML）：營收YoY長條／單季EPS長條／PER近1年折線。只放有資料的。"""
    blocks = []

    def blk(title, svg, cap=""):
        c = f'<div class="chcap">{cap}</div>' if cap else ""
        blocks.append(f'<div class="chblk"><div class="chttl">{title}</div>{svg}{c}</div>')

    if rev is not None and not rev.empty and rev["營收YoY%"].notna().any():
        blk("月營收 YoY%（紅增綠減）",
            sc.bars(list(rev["營收YoY%"]), list(rev["月份"]), signed=True, unit="%", fmt="{:,.1f}"),
            "近12月營收年增：紅=成長、綠=衰退")
    if prof is not None and not prof.empty and prof["EPS單季"].notna().any():
        blk("單季 EPS（元）",
            sc.bars(list(prof["EPS單季"]), list(prof["季別"]), signed=True, unit=" 元", fmt="{:,.2f}"),
            "逐季每股盈餘：看獲利趨勢")
    if val and val.get("_per") and len(val["_per"]) >= 2:
        blk("本益比 PER（近1年）",
            sc.line(val["_per"], val.get("_dates"), unit="", fmt="{:,.1f}", color="#8e44ad"),
            "PER 走勢：低=相對便宜、高=相對貴（位置%見估值卡）")
    if not blocks:
        return ""
    return f'<div class="chgrid">{"".join(blocks)}</div>'


def _health_charts(health) -> str:
    """財務體質迷你圖（HTML）：負債比%折線／單季營運CF長條。只放有資料的。"""
    if health is None or health.empty:
        return ""
    blocks = []

    def blk(title, svg, cap=""):
        c = f'<div class="chcap">{cap}</div>' if cap else ""
        blocks.append(f'<div class="chblk"><div class="chttl">{title}</div>{svg}{c}</div>')

    dt = list(health["季別"])
    if health["負債比%"].notna().any():
        blk("負債比%（逐季）", sc.line(list(health["負債比%"]), dt, unit="%", fmt="{:,.1f}",
                                    color="#e67e22"), "總負債÷總資產：越低財務越穩健（產業別看）")
    if health["營運CF億"].notna().any():
        blk("單季營運現金流（億·紅正綠負）",
            sc.bars(list(health["營運CF億"]), dt, signed=True, unit=" 億", fmt="{:,.1f}"),
            "本業收現：持續為正＝獲利有現金撐（已去累計還原單季）")
    if not blocks:
        return ""
    return f'<div class="chgrid">{"".join(blocks)}</div>'


def _md_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "（無資料）\n"
    disp = df.astype(object).where(df.notna(), "—")   # 所有欄的 <NA>/NaN → —（對齊 HTML）
    return disp.to_markdown(index=False) + "\n"


def _html_table(df: pd.DataFrame) -> str:
    """簡易表格（不套 MERGE 群組邏輯）；signed 欄紅正綠負；共用 report_html._CSS 的 sticky。"""
    if df is None or df.empty:
        return "<p>（無資料）</p>"
    head = "".join(f'<th data-col="{c}">{c}</th>' for c in df.columns)
    body = ""
    for _, row in df.iterrows():
        tds = ""
        for c in df.columns:
            v = row[c]
            style = ""
            if c in _SIGNED and isinstance(v, (int, float)) and pd.notna(v):
                style = f"color:{rh._UP};font-weight:600" if v > 0 else (
                    f"color:{rh._DOWN};font-weight:600" if v < 0 else "")
            tds += f'<td data-col="{c}" style="{style}">{rh._fmt(v)}</td>'
        body += f"<tr>{tds}</tr>"
    return f'<div class="tblwrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


_NOTE = ("📖 主力淨額＝當日前15大買超分點淨額＋前15大賣超分點淨額(🔴淨買/🟢淨賣)；"
         "隔日沖賣壓%＝昨日前15大買超分點今日轉淨賣量÷今日量(比率高=昨天大買家今天在跑，隔天常殺低)；"
         "隔日沖常客＝窗內反覆『昨買今賣』的分點(次數多=這檔的隔日沖大戶)；"
         "借券餘額=法人真實空單(增=加空/減=回補)；千張大戶%為週頻。紅漲綠跌為台股慣例，研究用途非投資建議。")


def _summary(sid, meta, tl, bt, reg, tech=None):
    """幾句規則式近況（只講資料看得到的，不過度解讀）。"""
    lines = []
    last = tl.iloc[-1]
    lines.append(f"最新 {last['date']}：收 {last['收盤']}（{last['漲跌%']:+}%）、量 {int(last['量']):,} 張")
    if tech:
        seg = f"綜合定調 {tech.get('定調', '—')}"
        extra = [tech[k] for k in ("均線排列", "季線年線") if tech.get(k) not in (None, "", "—")]
        if extra:
            seg += "（" + "、".join(str(x) for x in extra) + "）"
        lines.append(seg)
    if "借券增減" in tl.columns and pd.notna(last.get("借券增減")):
        d = int(last["借券增減"])
        lines.append(f"借券餘額 {int(last['借券餘額']):,} 張，昨→今 {d:+,}（{'法人加空⚠️' if d>0 else '法人回補'}）")
    if bt is not None and not bt.empty:
        b = bt.iloc[-1]
        mn = int(b["主力淨額"])
        p = b["隔日沖賣壓%"]
        seg = f"主力淨額 {mn:+,} 張"
        if pd.notna(p):
            seg += f"、隔日沖賣壓 {p}%"
        lines.append(seg)
    if reg is not None and not reg.empty:
        top = reg.iloc[0]
        lines.append(f"隔日沖常客首位：{top['分點']}（窗內 {int(top['隔日沖次數'])} 次、累計回吐 {int(top['累計回吐張']):,} 張）")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stock_id")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    sid = args.stock_id.strip()

    # 確保 DB 有資料（換機/新 session）
    try:
        from src import datastore
        if datastore.has_history():
            datastore.load()
    except Exception:
        pass

    meta = dd.stock_meta(sid)
    tl = dd.chip_timeline(sid, args.days)
    if tl.empty:
        raise SystemExit(f"找不到 {sid} 的價量資料（先跑 sync_data load / update_data）")

    print(f"[深掘] {sid} {meta['name']} 近 {len(tl)} 交易日 …")
    tech = dd.tech_snapshot(sid)                    # 技術面卡（DB 免費）
    mas = dd.ma_series(sid, list(tl["date"]))       # 收盤+均線疊圖
    tech_df = _tech_card_df(tech)
    bt = dd.broker_timeline(sid, tl)
    reg = dd.daytrader_regulars(sid, tl)
    day = tl["date"].iloc[-1]
    buy, sell = dd.top_branches(sid, day)
    ht = dd.holder_trend(sid)
    dtl = dd.daytrade_timeline(sid, tl)
    has_broker = not bt.empty
    # 基本面（FinMind 逐檔；抓不到留白）
    try:
        rev = dd.monthly_revenue(sid)
        prof = dd.profitability(sid)
        val = dd.valuation_snapshot(sid)
        divs, div_streak = dd.dividends(sid)
        health = dd.financial_health(sid)
        has_fund = not rev.empty or not prof.empty or bool(val)
    except Exception as e:
        rev = prof = divs = health = pd.DataFrame(); val = {}; div_streak = 0; has_fund = False
        print(f"   ⚠️ 基本面抓取略過（{type(e).__name__}: {e}）")
    print(f"   技術面：{tech.get('定調', '—')}　基本面：{'有' if has_fund else '無'}　"
          f"分點：{'可用' if has_broker else '不可用(需 Sponsor)'}")

    title = f"{sid} {meta['name']}（{meta['market']}{'・'+meta['industry'] if meta['industry'] else ''}）深掘病歷表"
    out_dir = OUTPUT_DIR.parent / "stock"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{sid}_{meta['name']}_深掘"

    # ---- Markdown ----
    md = [f"# {title}", f"\n> 資料截至 {day}　·　研究用途，非投資建議（紅漲綠跌）", ""]
    md.append("## 📌 近況摘要")
    for ln in _summary(sid, meta, tl, bt, reg, tech):
        md.append(f"- {ln}")
    md.append("\n## 📐 技術面（均線趨勢／位置）")
    md.append(_md_table(tech_df) if not tech_df.empty else "（技術面資料不足）\n")
    # 基本面（FinMind）
    val_df = _val_card_df(val, div_streak)
    if has_fund:
        md.append("\n## 🧾 基本面（營收動能／獲利／估值／配息）")
        md.append("**估值＋配息：**\n")
        md.append(_md_table(val_df) if not val_df.empty else "（無估值資料）\n")
        md.append("\n**近12月營收：**\n")
        md.append(_md_table(rev) if not rev.empty else "（無月營收資料）\n")
        md.append("\n**近8季獲利能力：**\n")
        md.append(_md_table(prof) if not prof.empty else "（無損益表資料）\n")
        md.append(f"\n**近年配息**（連續配息 {div_streak} 年）：\n")
        md.append(_md_table(divs) if not divs.empty else "（無配息資料）\n")
    else:
        md.append("\n## 🧾 基本面")
        md.append("（基本面資料不可用；需 FinMind 且該檔有財報/營收）\n")
    # 財務體質（三表健檢）
    if health is not None and not health.empty:
        md.append("\n## 🏥 財務體質（負債／流動／淨值／現金流）")
        md.append("> 負債比=總負債÷總資產(低=穩)；流動比=流動資產÷流動負債(>100%短期無虞)；"
                  "含金量=營運現金流÷稅後淨利(>100%獲利是真金)；營運CF/自由現金流已去累計還原單季。\n")
        md.append(_md_table(health))
    md.append("\n## 📈 圖譜")
    md.append("> 迷你走勢圖（收盤+均線／主力淨額／隔日沖賣壓%／當沖比%／借券／大戶）請見同名 **.html**"
              "（可滑鼠移上去看數值）；下列表格為同資料的逐日明細。\n")
    md.append(f"\n## 📊 近 {len(tl)} 交易日籌碼時間序列")
    md.append(_md_table(tl))
    md.append("## 🏦 分點主力淨額 + 隔日沖賣壓%（逐日）")
    md.append(_md_table(bt) if has_broker else "（分點資料不可用；需 FinMind Sponsor）\n")
    md.append("## 🔁 隔日沖常客名單（窗內昨買今賣的分點）")
    md.append(_md_table(reg) if has_broker and not reg.empty else "（無或分點不可用）\n")
    md.append(f"## 🟥 {day} 主力分點 Top 買超 / 賣超")
    if has_broker:
        md.append("**買超：**\n"); md.append(_md_table(buy))
        md.append("**賣超：**\n"); md.append(_md_table(sell))
    else:
        md.append("（分點不可用）\n")
    md.append("## 🏦 千張大戶週趨勢")
    md.append(_md_table(ht))
    md.append(f"\n> {_NOTE}")
    base.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")

    # ---- HTML（共用 report_html._CSS：sticky 表頭/首欄） ----
    def sec(h, inner):
        return f"<h2>{h}</h2>{inner}"
    sm = "".join(f"<li>{ln}</li>" for ln in _summary(sid, meta, tl, bt, reg, tech))
    body = f'<div class="banner reg-neutral">📌 近況摘要<small><ul class="ft">{sm}</ul></small></div>'
    body += sec("📐 技術面（均線趨勢／位置）",
                _html_table(tech_df) if not tech_df.empty else "<p>（技術面資料不足）</p>")
    # 基本面（FinMind）：估值+配息卡 → 迷你圖 → 營收/獲利/配息表
    if has_fund:
        fh = _html_table(val_df) if not val_df.empty else "<p>（無估值資料）</p>"
        fh += _fund_charts(rev, prof, val)
        fh += "<h3>近12月營收</h3>" + (_html_table(rev) if not rev.empty else "<p>（無）</p>")
        fh += "<h3>近8季獲利能力</h3>" + (_html_table(prof) if not prof.empty else "<p>（無）</p>")
        fh += f"<h3>近年配息（連續配息 {div_streak} 年）</h3>" + (
            _html_table(divs) if not divs.empty else "<p>（無）</p>")
        body += sec("🧾 基本面（營收動能／獲利／估值／配息）", fh)
    else:
        body += sec("🧾 基本面", "<p>（基本面資料不可用；需 FinMind 且該檔有財報/營收）</p>")
    # 財務體質（三表健檢）
    if health is not None and not health.empty:
        hh = ('<p class="note">負債比=總負債÷總資產(低=穩)；流動比=流動資產÷流動負債(>100%短期無虞)；'
              '含金量=營運現金流÷稅後淨利(>100%獲利是真金)；營運CF/自由現金流已去累計還原單季。</p>')
        hh += _health_charts(health) + _html_table(health)
        body += sec("🏥 財務體質（負債／流動／淨值／現金流）", hh)
    body += _charts(tl, bt, dtl, ht, mas)
    body += sec(f"📊 近 {len(tl)} 交易日籌碼時間序列", _html_table(tl))
    body += sec("🏦 分點主力淨額 + 隔日沖賣壓%（逐日）",
                _html_table(bt) if has_broker else "<p>（分點不可用；需 FinMind Sponsor）</p>")
    body += sec("🔁 隔日沖常客名單（窗內昨買今賣的分點）",
                _html_table(reg) if has_broker and not reg.empty else "<p>（無或分點不可用）</p>")
    if has_broker:
        body += sec(f"🟥 {day} 主力分點 Top 買超 / 賣超",
                    _html_table(buy) + _html_table(sell))
    body += sec("🏦 千張大戶週趨勢", _html_table(ht))
    html = (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><style>{rh._CSS}{_CHART_CSS}</style></head><body><div class="wrap">'
            f'<h1>{title}</h1><div class="sub">資料截至 {day}　·　研究用途，非投資建議</div>'
            f'{body}<p class="note" style="margin-top:18px">{_NOTE}</p></div></body></html>')
    base.with_suffix(".html").write_text(html, encoding="utf-8")

    print(f"✅ 病歷表 → {base.with_suffix('.md')}")
    print(f"   HTML → {base.with_suffix('.html')}")


if __name__ == "__main__":
    main()
