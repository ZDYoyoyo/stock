"""把每日整合報告輸出成 HTML（表格永遠對齊、台股慣例紅漲綠跌上色）。

與 .md 並存：run_all 會同時寫 .md 和 .html。
"""
import math

import pandas as pd

# 台股慣例：紅=漲/正、綠=跌/負
_UP = "#d63031"
_DOWN = "#158a4e"

# 欄位顯示名（讓表頭一看就懂；內部欄名維持英文供計算/評分用）。
# 重點：把易誤讀成「單日」的欄位標明是「區間」；close 標明是「基準日收盤」。
COLUMN_LABELS = {
    "stock_id": "代號",
    "name": "名稱",
    "market": "市場",
    "investor": "法人",
    "close": "收盤",
    "price_gain_%": "區間漲幅%",
    "consec_buy_days": "連買天數",
    "buy_ratio_%": "吃貨比重%",
    "cum_net_lots": "法人累買張",
    "margin_chg_%": "融資增減%",
    "avg_vol_lots": "日均量張",
    "return_%": "區間漲幅%",
    "vs_market_%": "相對大盤%",
    "score": "評分",
}


def label(col: str) -> str:
    """欄位英文名 → 中文顯示名（沒定義的原樣顯示，已是中文的欄位不受影響）。"""
    return COLUMN_LABELS.get(col, col)

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
  margin: 0; padding: 24px; background: #f5f6f8; color: #1a1a1a; }
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: #666; font-size: 13px; margin-bottom: 16px; }
.banner { padding: 14px 18px; border-radius: 10px; margin: 14px 0; font-weight: 600;
  border-left: 6px solid; }
.banner small { display:block; font-weight: 400; margin-top: 6px; color: #444; }
.reg-bear { background: #fdecea; border-color: #d63031; }
.reg-weak { background: #fff4e5; border-color: #e67e22; }
.reg-neutral { background: #eef2f7; border-color: #6b7a90; }
.reg-bull { background: #e8f7ee; border-color: #158a4e; }
h2 { font-size: 16px; margin: 22px 0 4px; padding-bottom: 6px; border-bottom: 2px solid #e2e5ea; }
.note { color:#777; font-size:12px; margin: 0 0 8px; }
.tblwrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; background: #fff;
  border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
th, td { padding: 7px 10px; text-align: right; white-space: nowrap; }
th { background: #2c3e50; color: #fff; font-weight: 600; }
td:nth-child(-n+3), th:nth-child(-n+3) { text-align: left; }
tbody tr:nth-child(even) { background: #f7f8fa; }
tbody tr:hover { background: #eef3fb; }
.star { background:#fffbe6; border:1px solid #ffe28a; border-radius:8px; padding:10px 14px; }
.disclaimer { color:#999; font-size:12px; margin-top:20px; }
@media (prefers-color-scheme: dark) {
  body { background:#15171b; color:#e6e6e6; } table{ background:#1e2126; }
  tbody tr:nth-child(even){ background:#23272e; } th{ background:#333a44; }
  .sub{color:#aaa;} h2{border-color:#333;}
}
"""


def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def _table(df: pd.DataFrame, cols, signed_cols) -> str:
    cols = [c for c in cols if c in df.columns]
    head = "".join(f"<th>{label(c)}</th>" for c in cols)
    body = ""
    for _, row in df.iterrows():
        tds = ""
        for c in cols:
            v = row[c]
            style = ""
            if c in signed_cols and isinstance(v, (int, float)) and pd.notna(v):
                if v > 0:
                    style = f"color:{_UP};font-weight:600"
                elif v < 0:
                    style = f"color:{_DOWN};font-weight:600"
            tds += f'<td style="{style}">{_fmt(v)}</td>'
        body += f"<tr>{tds}</tr>"
    return f'<div class="tblwrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _regime_class(reg: dict) -> str:
    label = reg.get("regime", "")
    if "偏空" in label:
        return "reg-bear"
    if "偏弱" in label or "震盪" in label:
        return "reg-weak"
    if "偏多" in label and "中性" not in label:
        return "reg-bull"
    return "reg-neutral"


def build(today, reg, glob_lines, sox, blocks, intersection=None) -> str:
    from .regime import summary_line
    banner = (f'<div class="banner {_regime_class(reg)}">🚦 {summary_line(reg)}'
              f'<small>{sox}<br>🌍 {" ｜ ".join(glob_lines)}</small></div>')

    body = ""
    for b in blocks:
        body += f"<h2>{b['title']}</h2>"
        if b.get("note"):
            body += f'<p class="note">{b["note"]}</p>'
        df = b["df"]
        if b.get("skipped"):
            body += "<p>（已略過 --skip-longterm；要看長期軌請跑 <code>python -m scripts.run_longterm</code>）</p>"
        elif df is None or df.empty:
            body += "<p>（今日無符合條件標的）</p>"
        else:
            body += _table(df, b["cols"], b.get("signed", []))
        if b.get("after_intersection") and intersection is not None:
            names = "、".join(intersection) if intersection else "（無）"
            body += f'<div class="star">⭐ 雙訊號交集（法人買且抗跌）：{names}</div>'

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股每日整合報告 {today}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>台股每日整合報告</h1><div class="sub">{today}　·　研究用途，非投資建議</div>
{banner}{body}
<div class="disclaimer">⚠️ 本報告為候選觀察名單，非投資建議。紅漲綠跌為台股慣例。</div>
</div></body></html>"""
