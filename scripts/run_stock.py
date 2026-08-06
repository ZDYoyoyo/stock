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
from src.config import OUTPUT_DIR

# 各表的「有正負、要上色」欄（紅正綠負，台股慣例）
_SIGNED = {"漲跌%", "外資", "投信", "自營", "融資增減", "融券增減", "借券增減",
           "主力淨額", "大戶週增pp"}


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


def _summary(sid, meta, tl, bt, reg):
    """幾句規則式近況（只講資料看得到的，不過度解讀）。"""
    lines = []
    last = tl.iloc[-1]
    lines.append(f"最新 {last['date']}：收 {last['收盤']}（{last['漲跌%']:+}%）、量 {int(last['量']):,} 張")
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
    bt = dd.broker_timeline(sid, tl)
    reg = dd.daytrader_regulars(sid, tl)
    day = tl["date"].iloc[-1]
    buy, sell = dd.top_branches(sid, day)
    ht = dd.holder_trend(sid)
    has_broker = not bt.empty
    print(f"   分點：{'可用' if has_broker else '不可用(需 Sponsor)'}")

    title = f"{sid} {meta['name']}（{meta['market']}{'・'+meta['industry'] if meta['industry'] else ''}）籌碼病歷表"
    out_dir = OUTPUT_DIR.parent / "stock"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{sid}_{meta['name']}_深掘"

    # ---- Markdown ----
    md = [f"# {title}", f"\n> 資料截至 {day}　·　研究用途，非投資建議（紅漲綠跌）", ""]
    md.append("## 📌 近況摘要")
    for ln in _summary(sid, meta, tl, bt, reg):
        md.append(f"- {ln}")
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
    sm = "".join(f"<li>{ln}</li>" for ln in _summary(sid, meta, tl, bt, reg))
    body = f'<div class="banner reg-neutral">📌 近況摘要<small><ul class="ft">{sm}</ul></small></div>'
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
            f'<title>{title}</title><style>{rh._CSS}</style></head><body><div class="wrap">'
            f'<h1>{title}</h1><div class="sub">資料截至 {day}　·　研究用途，非投資建議</div>'
            f'{body}<p class="note" style="margin-top:18px">{_NOTE}</p></div></body></html>')
    base.with_suffix(".html").write_text(html, encoding="utf-8")

    print(f"✅ 病歷表 → {base.with_suffix('.md')}")
    print(f"   HTML → {base.with_suffix('.html')}")


if __name__ == "__main__":
    main()
