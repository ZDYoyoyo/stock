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
    "close": "基準收盤",   # T11 為法人資料基準日收盤（非最新交易日），與旁邊「今日收盤」區分
    "price_gain_%": "區間漲幅%",
    "consec_buy_days": "連買天數",
    "buy_ratio_%": "吃貨比重%",
    "cum_net_lots": "法人累買張",
    "margin_chg_%": "融資增減%",
    "avg_vol_lots": "日均量張",
    "return_%": "區間漲幅%",
    "vs_market_%": "相對大盤%",
    "score": "評分",
    # 三大法人/資券：欄名標明時間窗，避免被誤讀成單日（外資今日=單日、外資=近10日累積）
    "外資": "外資10日",
    "投信": "投信10日",
    "自營": "自營10日",
    "融資增減": "融資增減10日",
    "融券增減": "融券增減10日",
    # 財務面英文縮寫 → 中文（純英文欄給中文；底部另附術語小抄解釋概念）
    "PER": "本益比",
    "PBR": "股價淨值比",
    "YoY%": "年增%",
    "累計YoY%": "累計年增%",
    "MoM%": "月增%",
    "營收YoY%": "營收年增%",
}

# 多時窗合併顯示（省欄）：把 今/10/20/60 日堆進「外資」等同一格，欄數不變。
# key＝anchor(10日欄，df 內欄名、也是 cols 清單裡的欄)；value＝(顯示基名, [(來源欄, 小標)...])。
# 依序堆疊今→10→20→60；HTML 多行各自上色、MD 斜線併排、位置對應小標。
# CSV 不套用（rename_cn 走 COLUMN_LABELS，維持各時窗分開欄，Excel 好篩選）。
MERGE_GROUPS = {
    "外資":     ("外資", [("外資今日", "今"), ("外資", "10日"), ("外資20日", "20日"), ("外資60日", "60日")]),
    "投信":     ("投信", [("投信今日", "今"), ("投信", "10日"), ("投信20日", "20日"), ("投信60日", "60日")]),
    "自營":     ("自營", [("自營今日", "今"), ("自營", "10日"), ("自營20日", "20日"), ("自營60日", "60日")]),
    "融資增減": ("融資", [("融資今日", "今"), ("融資增減", "10日"), ("融資20日", "20日"), ("融資60日", "60日")]),
    "融券增減": ("融券", [("融券今日", "今"), ("融券增減", "10日"), ("融券20日", "20日"), ("融券60日", "60日")]),
}
_GROUP_HEAD = "今/10/20/60日"


def group_source_cols():
    """所有分組來源欄（供 MD 端補進 disp 供堆疊；HTML 端讀整列不需要）。"""
    return [src for _, srcs in MERGE_GROUPS.values() for src, _ in srcs]


def _fmt_signed(v):
    return "—" if pd.isna(v) else f"{v:+,.0f}"


def fmt_group_text(vals) -> str:
    """MD 用：今/10/20/60 依序併成一格文字（純文字表格無法多行，位置對應小標）。"""
    return "／".join(_fmt_signed(v) for v in vals)


GLOSSARY = ("📖 術語小抄：本益比(PER)＝股價÷每股盈餘，數字越低越便宜；"
            "年增(YoY)＝與去年同期比；月增(MoM)＝與上月比；"
            "EPS＝每股盈餘(公司幫每股賺多少)；ROE＝股東權益報酬率(獲利能力，越高越好)；"
            "股價淨值比(PBR)＝股價÷每股淨值。")

# 當沖欄位說明：附在當沖報告，解釋量能倍數/振幅這些當沖特有指標。
DAYTRADE_NOTE = ("量能倍數＝當日量÷近20日均量（>1.5＝爆量有人在玩、<1＝量縮冷清）；"
                 "當日振幅%＝(當日最高−最低)÷昨收，越大越有價差可做；均振幅%＝近20日平均振幅。"
                 "爆量是中性的：爆量+漲＝追價強，爆量+跌＝出貨/賣壓，要配當日漲跌一起看。")

# 多空傾向欄位說明：解釋 開盤前定調 的用途與『非即時』的限制。
BIAS_NOTE = ("多空傾向＝法人淨買/千張大戶週增/融資變化/相對大盤/站上20MA 五項方向投票加總"
             "（🔴偏多找買點、🟢偏空找空點、⚪中性看盤中量價）；與大盤＝此傾向與環境紅綠燈"
             "同向為『順勢』(勝率較高)、反向為『逆勢⚠️』。⚠️籌碼為昨日收盤後資料，是**開盤前"
             "定調方向的偏誤(bias)，非盤中即時訊號**；實際進出點仍須看盤中量價。")


def label(col: str) -> str:
    """欄位英文名 → 中文顯示名（沒定義的原樣顯示，已是中文的欄位不受影響）。"""
    return COLUMN_LABELS.get(col, col)


def rename_cn(df):
    """把 DataFrame 的英文欄名換成中文顯示名（供 to_markdown/to_string 直接輸出的獨立報告用）。"""
    return df.rename(columns=COLUMN_LABELS)

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
h3 { font-size: 14px; margin: 14px 0 4px; }
.note { color:#777; font-size:12px; margin: 0 0 8px; }
.warn { background:#fff4f4; border-left:4px solid #d63031; border-radius:6px;
  padding:8px 14px; margin:8px 0; font-size:13px; }
.warn ul { margin:6px 0 0; padding-left:20px; }
ul.ft { margin:4px 0 12px; padding-left:20px; font-size:13px; line-height:1.7; }
.tblwrap { overflow-x: auto; }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; background: #fff;
  border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
th, td { padding: 7px 10px; text-align: right; white-space: nowrap; }
th { background: #2c3e50; color: #fff; font-weight: 600; }
th small { display:block; font-weight:400; font-size:10px; opacity:.8; margin-top:1px; }
td.pair { line-height: 1.25; font-variant-numeric: tabular-nums; }
td.pair .tv { display:block; font-weight:600; }
td.pair .dv { display:block; font-size:11px; opacity:.62; }
td:nth-child(-n+3), th:nth-child(-n+3) { text-align: left; }
tbody tr:nth-child(even) { background: #f7f8fa; }
tbody tr:hover { background: #eef3fb; }
/* 表頭固定：直向捲動時欄位標題不消失 */
thead th { position: sticky; top: 0; z-index: 3; }
/* 首欄(代號)固定：橫向捲動時看得到是哪一檔 */
th:first-child, td:first-child { position: sticky; left: 0; }
thead th:first-child { z-index: 4; }
td:first-child { z-index: 1; background: #fff; }
tbody tr:nth-child(even) td:first-child { background: #f7f8fa; }
tbody tr:hover td:first-child { background: #eef3fb; }
/* 欄位顯示開關面板 */
.colctrl { position: sticky; top: 0; z-index: 5; background:#eef2f7; border:1px solid #d5dbe4;
  border-radius:8px; padding:8px 12px; margin:12px 0; font-size:13px; }
.colctrl summary { cursor:pointer; font-weight:600; color:#2c3e50; }
.colctrl .cols { display:flex; flex-wrap:wrap; gap:4px 14px; margin-top:8px; }
.colctrl label { display:inline-flex; align-items:center; gap:4px; white-space:nowrap; font-weight:400; }
.colctrl .btns { margin-top:8px; display:flex; gap:8px; }
.colctrl button { font-size:12px; padding:3px 10px; border:1px solid #b8c0cc; border-radius:6px;
  background:#fff; cursor:pointer; }
.star { background:#fffbe6; border:1px solid #ffe28a; border-radius:8px; padding:10px 14px; }
.disclaimer { color:#999; font-size:12px; margin-top:20px; }
@media (prefers-color-scheme: dark) {
  body { background:#15171b; color:#e6e6e6; } table{ background:#1e2126; }
  tbody tr:nth-child(even){ background:#23272e; } th{ background:#333a44; }
  .sub{color:#aaa;} h2{border-color:#333;}
  .warn{ background:#2a1d1d; }
  td:first-child { background:#1e2126; }
  tbody tr:nth-child(even) td:first-child { background:#23272e; }
  tbody tr:hover td:first-child { background:#2b3340; }
  .colctrl { background:#20242b; border-color:#333a44; }
  .colctrl summary { color:#cdd6e2; }
  .colctrl button { background:#2a2f37; color:#e6e6e6; border-color:#444; }
}
"""


def _fmt(v):
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def _group_cell(row, srcs, anchor) -> str:
    """多時窗合併格：今(粗)→10→20→60(小灰)依序堆疊，各自紅正綠負；缺值顯示—。"""
    def span(col, cls):
        v = row[col] if col in row.index else None
        if v is None or pd.isna(v):
            return f'<span class="{cls}">—</span>'
        color = _UP if v > 0 else (_DOWN if v < 0 else "#888")
        return f'<span class="{cls}" style="color:{color}">{v:+,.0f}</span>'
    parts = [span(col, "tv" if i == 0 else "dv") for i, (col, _) in enumerate(srcs)]
    return f'<td class="pair" data-col="{anchor}">{"".join(parts)}</td>'


def _shown_cols(df, cols):
    """一張表實際會顯示的欄（套用 MERGE 隱藏來源欄邏輯）；供欄位開關面板列舉。"""
    hide = set()
    for anchor, (_, srcs) in MERGE_GROUPS.items():
        if anchor in df.columns:
            hide.update(col for col, _ in srcs if col != anchor)
    return [c for c in cols if c in df.columns and c not in hide]


def _table(df: pd.DataFrame, cols, signed_cols) -> str:
    # 多時窗來源欄併入 anchor 同格後隱藏（僅當 anchor 存在時；今/20/60日等不獨立成欄）
    hide = set()
    for anchor, (_, srcs) in MERGE_GROUPS.items():
        if anchor in df.columns:
            hide.update(col for col, _ in srcs if col != anchor)
    cols = [c for c in cols if c in df.columns and c not in hide]
    head = ""
    for c in cols:
        if c in MERGE_GROUPS and c in df.columns:
            head += f'<th data-col="{c}">{MERGE_GROUPS[c][0]}<small>{_GROUP_HEAD}</small></th>'
        else:
            head += f'<th data-col="{c}">{label(c)}</th>'
    body = ""
    for _, row in df.iterrows():
        tds = ""
        for c in cols:
            if c in MERGE_GROUPS and c in df.columns:
                tds += _group_cell(row, MERGE_GROUPS[c][1], c)
                continue
            v = row[c]
            style = ""
            if c in signed_cols and isinstance(v, (int, float)) and pd.notna(v):
                if v > 0:
                    style = f"color:{_UP};font-weight:600"
                elif v < 0:
                    style = f"color:{_DOWN};font-weight:600"
            tds += f'<td data-col="{c}" style="{style}">{_fmt(v)}</td>'
        body += f"<tr>{tds}</tr>"
    return f'<div class="tblwrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _landmine_html(df, label="T11 候選") -> str:
    """清單若有高風險(🔴/🟠)，輸出紅旗排雷提醒 callout（對齊 .md 的 _landmine_warn）。"""
    if df is None or df.empty or "風險" not in df.columns:
        return ""
    hi = df[df["風險"].astype(str).str.contains("嚴重|高", na=False)]
    if hi.empty:
        return ""
    items = ""
    for r in hi.itertuples():
        flags = getattr(r, "紅旗", "") or ""
        ind = getattr(r, "產業", None)
        ind = f"（{ind}）" if isinstance(ind, str) and ind else ""
        items += f"<li>{r.stock_id} {r.name}{ind}：{r.風險}　{flags}</li>"
    return (f'<div class="warn">🧨 <b>{label}排雷提醒</b>'
            f'（財務/籌碼/技術紅旗，建議先避開或查清）：<ul>{items}</ul></div>')


def _attr_html(attr) -> str:
    """持股今日籌碼歸因（今 vs 昨）— 對齊 .md 的『持股今日籌碼歸因』段。"""
    if not attr:
        return ""
    lis = "".join(f"<li><b>{sid} {name}</b>：{line}</li>" for sid, name, line in attr)
    return ('<h3>📊 持股今日籌碼歸因（今 vs 昨，自動）</h3>'
            f'<ul class="ft">{lis}</ul>')


def _followthrough_html(ft, ftstats) -> str:
    """昨日精選今日追蹤 — 對齊 .md 的同名段（各軌隔日表現＋逐檔原因）。"""
    if not ft or not ft.get("tracks"):
        return ""
    h = (f'<h2>📈 昨日精選今日追蹤（{ft["date"]} 精選 → 今日表現＋原因）</h2>')
    for track, rows in ft["tracks"].items():
        st = (ftstats or {}).get(track, {})
        sub = track + (f"（隔日均 {st['avg']:+.2f}%，上漲 {st['up']}/{st['n']}）" if st else "")
        h += f"<h3>{sub}</h3><ul class='ft'>"
        for r in rows:
            c = r["chg"]
            if pd.notna(c):
                color = _UP if c > 0 else (_DOWN if c < 0 else "#666")
                cs = f'<span style="color:{color};font-weight:600">{c:+.2f}%</span>'
            else:
                cs = "—"
            h += (f"<li>#{r['rank']} <b>{r['stock_id']} {r['name']}</b> "
                  f"今日 {cs} → {r['one_line']}</li>")
        h += "</ul>"
    return h


def _regime_class(reg: dict) -> str:
    label = reg.get("regime", "")
    if "偏空" in label:
        return "reg-bear"
    if "偏弱" in label or "震盪" in label:
        return "reg-weak"
    if "偏多" in label and "中性" not in label:
        return "reg-bull"
    return "reg-neutral"


def _colctrl(blocks) -> str:
    """欄位顯示開關面板：列出全報告會出現的欄，勾掉即隱藏（localStorage 記住跨日）。

    代號/名稱固定不列（不讓使用者不小心把辨識欄關掉）。
    """
    seen, items = set(), []
    for b in blocks:
        df = b.get("df")
        if df is None or getattr(df, "empty", True):
            continue
        for c in _shown_cols(df, b["cols"]):
            if c in ("stock_id", "name") or c in seen:
                continue
            seen.add(c)
            txt = (MERGE_GROUPS[c][0] + "今/10/20/60") if c in MERGE_GROUPS else label(c)
            items.append(f'<label><input type="checkbox" checked data-col="{c}" '
                         f'onchange="tc(this)">{txt}</label>')
    if not items:
        return ""
    return ('<details class="colctrl"><summary>🔧 欄位顯示（勾掉不想看的欄，全報告即時套用、下次開報告會記住）</summary>'
            f'<div class="cols">{"".join(items)}</div>'
            '<div class="btns"><button onclick="allCols(true)">全部顯示</button>'
            '<button onclick="allCols(false)">全部隱藏</button></div></details>')


_COLCTRL_JS = """
<script>
var LSK='twreport_hiddenCols';
function _apply(name,show){document.querySelectorAll('[data-col="'+name+'"]').forEach(function(e){e.style.display=show?'':'none';});}
function _save(){var h=[];document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){if(!cb.checked)h.push(cb.dataset.col);});try{localStorage.setItem(LSK,JSON.stringify(h));}catch(e){}}
function tc(cb){_apply(cb.dataset.col,cb.checked);_save();}
function allCols(show){document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){cb.checked=show;_apply(cb.dataset.col,show);});_save();}
(function(){var h=[];try{h=JSON.parse(localStorage.getItem(LSK)||'[]');}catch(e){}
 document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){if(h.indexOf(cb.dataset.col)>=0){cb.checked=false;_apply(cb.dataset.col,false);}});})();
</script>"""


def build(today, reg, glob_lines, sox, blocks, intersection=None,
          followthrough=None, ftstats=None) -> str:
    from .regime import summary_line
    banner = (f'<div class="banner {_regime_class(reg)}">🚦 {summary_line(reg)}'
              f'<small>{sox}<br>🌍 {" ｜ ".join(glob_lines)}</small></div>')

    body = ""
    for b in blocks:
        body += f"<h2>{b['title']}</h2>"
        if b.get("note"):
            note_html = "<br>".join(ln for ln in b["note"].split("\n") if ln.strip())
            body += f'<p class="note">{note_html}</p>'
        df = b["df"]
        if b.get("skipped"):
            body += "<p>（已略過 --skip-longterm；要看長期軌請跑 <code>python -m scripts.run_longterm</code>）</p>"
        elif df is None or df.empty:
            body += "<p>（今日無符合條件標的）</p>"
        else:
            n = b.get("n")
            shown = df.head(n) if n else df
            body += _table(shown, b["cols"], b.get("signed", []))
            if n and len(df) > n:
                csv = b.get("csv_name")
                where = (f'完整清單見同資料夾 <code>{csv}</code>（可用 Excel 開）'
                         if csv else "完整清單見同資料夾 CSV")
                body += f'<p class="note">（僅顯示前 {n} 名，共 {len(df)} 檔符合；{where}）</p>'
        # 排雷提醒 callout（對齊 .md）：df 內有高風險則列紅旗
        if b.get("landmine"):
            body += _landmine_html(df, b.get("landmine_label", "T11 候選"))
        # 持股籌碼歸因（對齊 .md）：接在持股表後
        if b.get("attribution"):
            body += _attr_html(b["attribution"])
        if b.get("after_intersection") and intersection is not None:
            names = "、".join(intersection) if intersection else "（無）"
            body += f'<div class="star">⭐ 雙訊號交集（法人買且抗跌）：{names}</div>'

    # 昨日精選今日追蹤（對齊 .md）：擺在各軌之後、術語小抄之前
    body += _followthrough_html(followthrough, ftstats)

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股每日整合報告 {today}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>台股每日整合報告</h1><div class="sub">{today}　·　研究用途，非投資建議</div>
{banner}{_colctrl(blocks)}{body}
<p class="note" style="margin-top:18px">{GLOSSARY}</p>
<div class="disclaimer">⚠️ 本報告為候選觀察名單，非投資建議。紅漲綠跌為台股慣例。</div>
</div>{_COLCTRL_JS}</body></html>"""
