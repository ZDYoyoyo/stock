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

# 多時窗合併顯示（省欄）：把 今/昨/10/20 日堆進「外資」等同一格，欄數不變。
# key＝anchor(10日欄，df 內欄名、也是 cols 清單裡的欄)；value＝(顯示基名, [(來源欄, 小標)...])。
# 依序堆疊今→昨→10→20；HTML 多行各自上色、MD 斜線併排、位置對應小標。
# （2026-08 使用者調整：加「昨日」單日、拿掉「60日」——季窗太遠參考性低。）
# CSV 不套用（rename_cn 走 COLUMN_LABELS，維持各時窗分開欄，Excel 好篩選）。
MERGE_GROUPS = {
    "外資":     ("外資", [("外資今日", "今"), ("外資昨日", "昨"), ("外資", "10日"), ("外資20日", "20日")]),
    "投信":     ("投信", [("投信今日", "今"), ("投信昨日", "昨"), ("投信", "10日"), ("投信20日", "20日")]),
    "自營":     ("自營", [("自營今日", "今"), ("自營昨日", "昨"), ("自營", "10日"), ("自營20日", "20日")]),
    "融資增減": ("融資", [("融資今日", "今"), ("融資昨日", "昨"), ("融資增減", "10日"), ("融資20日", "20日")]),
    "融券增減": ("融券", [("融券今日", "今"), ("融券昨日", "昨"), ("融券增減", "10日"), ("融券20日", "20日")]),
}
_GROUP_HEAD = "今/昨/10/20日"

# 收盤價篩選：哪一欄當「收盤價」（依優先序取第一個存在的）。各軌欄名不一：
# 多數軌＝今日收盤、第6軌＝close、追蹤區＝今收、持股表＝現價。標到 <td data-price> 供 JS 篩選。
_PRICE_COLS = ["今日收盤", "close", "現價", "今收"]

# 代號欄（各表第一欄）：多數軌＝stock_id、持股表＝代號。這格做成 K 線圖外連。
_ID_COLS = ["stock_id", "代號"]
# 點代號開該檔 K 線圖（Goodinfo，免分上市/上櫃、每檔一種網址最穩，新分頁開啟）。
_KCHART_URL = "https://goodinfo.tw/tw/ShowK_Chart.asp?STOCK_ID={sid}"


def _klink(sid, disp=None) -> str:
    """把代號包成 Goodinfo K 線圖連結（新分頁）。disp 為顯示文字（預設＝代號）。"""
    s = str(sid).strip()
    d = disp if disp is not None else s
    if not s or s in ("—", "nan", "None"):
        return d
    return (f'<a class="klink" href="{_KCHART_URL.format(sid=s)}" target="_blank" '
            f'rel="noopener" title="開 {s} K線圖（Goodinfo，新分頁）">{d}</a>')


def group_source_cols():
    """所有分組來源欄（供 MD 端補進 disp 供堆疊；HTML 端讀整列不需要）。"""
    return [src for _, srcs in MERGE_GROUPS.values() for src, _ in srcs]


def _fmt_signed(v):
    return "—" if pd.isna(v) else f"{v:+,.0f}"


def fmt_group_text(vals) -> str:
    """MD 用：今/昨/10/20 依序併成一格文字（純文字表格無法多行，位置對應小標）。"""
    return "／".join(_fmt_signed(v) for v in vals)


# 各軌的「回測驗證結果」——直接標在標題旁，避免看到清單就當成保證會賺。
# 來源＝本專案實測（組合回測／walk-forward 樣本外），數字見 reports/*.md。
# (等級, 短標, 詳述)；等級 ok=通過樣本外／no=無 edge／warn=未驗證或 edge 薄。
BACKTEST_VERDICTS = {
    "T11": ("no", "回測無 edge",
            "組合回測 CAGR −16%／最大回撤 −63%，遠輸買入持有 → **只當選股情報，別當進出場策略**。"),
    "T16": ("ok", "唯一通過嚴格樣本外",
            "walk-forward OOS CAGR +37.9%／夏普 1.11，小勝同期買入持有(+26.5%／1.07)。"
            "⚠️但回撤更深(−44%)、逐折分散(F2 −22%)、選中參數不穩 → **有真 edge 但脆弱**，"
            "須順 regime＋分散到 ~10 檔；停損反而傷動能。"),
    "T12": ("no", "嚴格樣本外失效",
            "walk-forward OOS 僅 CAGR +7.8%／夏普 0.41，**大輸同期買入持有(+26.5%／1.07)**；"
            "「看整段挑最好」+34.5% 是資料窺探灌水約 4 倍 → **降級為選股情報**。"),
    "長期": ("no", "此期間無 edge",
             "月頻價值篩選 CAGR −0.5%／夏普 0.08，慘輸買入持有(+14.9%)。"
             "主因 2022~2026 為成長股主導、價值落後 → **不代表價值投資無效**，是此面板×此期間沒 edge。"),
    "當沖": ("warn", "未回測（本質是舞台篩選）",
             "篩的是「流動性＋波動度」的對殺舞台，不是進出場訊號 → 不適用組合回測；"
             "實際進出仍看盤中量價。"),
    "隔日沖鎖碼": ("warn", "已回測·edge 薄",
                "356樣本：鎖碼股隔日多**開高走低**(盤中 −0.8%)，但 EOD 欄位無法穩定預測方向"
                "（🎯本身無增量預測力）；只有『預估賣壓佔量%』最高組 −1.64%(跌比56.8%) 略有鑑別 → "
                "**情境舞台、非提款機，別抱過夜**。"),
}
_V_ICON = {"ok": "✅", "no": "❌", "warn": "⚠️"}


def verdict_md(key) -> str:
    """MD 用：一行『回測驗證』說明。無此軌回空字串。"""
    v = BACKTEST_VERDICTS.get(key)
    if not v:
        return ""
    lvl, short, detail = v
    return f"🔬 **回測驗證：{_V_ICON[lvl]} {short}** — {detail}"


def _verdict_badge(key) -> str:
    """HTML 用：標題旁的小徽章＋下方一行詳述。"""
    v = BACKTEST_VERDICTS.get(key)
    if not v:
        return ""
    lvl, short, detail = v
    return (f'<span class="bt bt-{lvl}">{_V_ICON[lvl]} {short}</span>'
            f'<p class="btnote">🔬 回測驗證：{detail}</p>')


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
.sec h2 { cursor: pointer; user-select: none; }
.sec h2:hover { color: #2c3e50; }
.sec h2 .arw { display: inline-block; width: 1em; font-size: 12px; opacity: .65; }
.note { color:#777; font-size:12px; margin: 0 0 8px; }
.warn { background:#fff4f4; border-left:4px solid #d63031; border-radius:6px;
  padding:8px 14px; margin:8px 0; font-size:13px; }
.warn ul { margin:6px 0 0; padding-left:20px; }
ul.ft { margin:4px 0 12px; padding-left:20px; font-size:13px; line-height:1.7; }
/* 表格框：限高→框內捲動，sticky 表頭/首欄才會相對這個框固定(不隨頁面滑走) */
.tblwrap { overflow: auto; max-height: 82vh; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; background: #fff; }
th, td { padding: 7px 10px; text-align: right; white-space: nowrap; }
th { background: #2c3e50; color: #fff; font-weight: 600; cursor: pointer; user-select: none; }
th:hover { background: #3a5169; }
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
/* 收盤價篩選列 */
.pxctrl { background:#eef2f7; border:1px solid #d5dbe4; border-radius:8px;
  padding:8px 12px; margin:12px 0; font-size:13px; font-weight:600; color:#2c3e50; }
.pxctrl input { width:90px; font-size:13px; padding:3px 8px; border:1px solid #b8c0cc;
  border-radius:6px; margin:0 4px; text-align:right; }
.pxctrl button { font-size:12px; padding:3px 10px; border:1px solid #b8c0cc; border-radius:6px;
  background:#fff; cursor:pointer; margin-left:6px; }
.pxctrl .pxhint { font-weight:400; color:#158a4e; }
/* 代號→K線圖外連：看起來像代號、虛線底線暗示可點 */
a.klink { color:inherit; text-decoration:none; border-bottom:1px dotted #8896a8; cursor:pointer; }
a.klink:hover { color:#1e63d0; border-bottom-color:#1e63d0; }
/* 回測驗證徽章：貼在各軌標題旁，一眼看出這軌到底驗證過沒有 */
.bt { display:inline-block; font-size:11px; font-weight:600; padding:2px 8px;
  border-radius:10px; margin-left:8px; vertical-align:middle; white-space:nowrap; }
.bt-ok { background:#e8f7ee; color:#0f6b3c; border:1px solid #9dd9b8; }
.bt-no { background:#fdecea; color:#a02020; border:1px solid #f0b0aa; }
.bt-warn { background:#fff4e5; color:#8a5200; border:1px solid #f3cf95; }
.btnote { color:#666; font-size:11.5px; margin:2px 0 8px; line-height:1.6; }
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
  .bt-ok { background:#12331f; color:#7fd6a2; border-color:#2c6b45; }
  .bt-no { background:#3a1a1a; color:#f0938c; border-color:#7a3a34; }
  .bt-warn { background:#3a2c14; color:#f0c078; border-color:#7a5a24; }
  .btnote { color:#9aa0a6; }
  .pxctrl { background:#20242b; border-color:#333a44; color:#cdd6e2; }
  .pxctrl input, .pxctrl button { background:#2a2f37; color:#e6e6e6; border-color:#444; }
  a.klink:hover { color:#6db3ff; border-bottom-color:#6db3ff; }
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
    # 多時窗來源欄併入 anchor 同格後隱藏（僅當 anchor 存在時；今/昨/20日等不獨立成欄）
    hide = set()
    for anchor, (_, srcs) in MERGE_GROUPS.items():
        if anchor in df.columns:
            hide.update(col for col, _ in srcs if col != anchor)
    cols = [c for c in cols if c in df.columns and c not in hide]
    price_col = next((c for c in _PRICE_COLS if c in cols), None)  # 這張表用哪欄當收盤價
    id_col = next((c for c in _ID_COLS if c in cols), None)        # 這張表哪欄是代號（做 K 線外連）
    head = ""
    for c in cols:
        # 表頭可點排序（純視圖，不動各軌本身的排序邏輯——那是回測驗證過的策略的一部分）
        if c in MERGE_GROUPS and c in df.columns:
            head += (f'<th data-col="{c}" onclick="sortT(this)">{MERGE_GROUPS[c][0]}'
                     f'<small>{_GROUP_HEAD}</small></th>')
        else:
            head += f'<th data-col="{c}" onclick="sortT(this)">{label(c)}</th>'
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
            px = f' data-price="{v}"' if c == price_col and isinstance(v, (int, float)) and pd.notna(v) else ""
            disp = _klink(v) if c == id_col else _fmt(v)  # 代號格→K線外連
            tds += f'<td data-col="{c}"{px} style="{style}">{disp}</td>'
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
    lis = "".join(f"<li><b>{_klink(sid, f'{sid} {name}')}</b>：{line}</li>" for sid, name, line in attr)
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
            lbl = f"{r['stock_id']} {r['name']}"
            h += (f"<li>#{r['rank']} <b>{_klink(r['stock_id'], lbl)}</b> "
                  f"今日 {cs} → {r['one_line']}</li>")
        h += "</ul>"
    return h


def _snipe_ohlc_html(so, sostats) -> str:
    """昨日隔日沖鎖碼候選 → 今日開高低收（專屬區塊，具體驗證開高走低）。對齊 .md 同名段。"""
    if not so or not so.get("rows"):
        return ""
    df = pd.DataFrame(so["rows"])
    cols = ["stock_id", "name", "鎖碼淨額", "全市場黑名單", "本檔黑名單",
            "黑名單買張", "黑名單賣張", "倒貨%",
            "預估賣壓%", "實際賣壓%", "今主力淨額",
            "昨收", "今開", "今高", "今低", "今收", "漲跌%", "跳空%", "盤中%",
            "高檔回落%", "振幅%", "量能倍數", "當沖比%", "黑名單逐點"]
    cols = [c for c in cols if c in df.columns]
    sub = ""
    if sostats:
        sub = (f"　今日均跳空 {sostats['gap']:+.2f}%、均盤中 {sostats['oc']:+.2f}%"
               f"（盤中走低 {sostats['oc_down']}/{sostats['n']} 檔）")
    h = (f'<h2>🎯 昨日隔日沖鎖碼候選 → 今日走勢（{so["date"]} 精選 → {so.get("trade_date", "今日")} 開高低收）</h2>'
         f'<p class="note">鎖碼淨額＝當時主力買了多少(pick 當日前15買+前15賣淨額，🔴淨買/🟢淨賣)。'
         f'具體驗證『開高走低』：跳空%＝隔夜高開幅度(今開 vs 昨收)、'
         f'盤中%＝開盤後走勢(今收 vs 今開，🔴正=開低走高/守住、🟢負=開高走低)。{sub}'
         f'<br>🚩 <b>昨天列出的黑名單</b>（pick 當下記下、非事後重算）：<b>全市場黑名單</b>＝跨所有股票的'
         f'隔日沖慣犯(附隔日沖率%，樣本大最可信)、<b>本檔黑名單</b>＝專門玩這檔的常客(樣本小)；'
         f'兩邊都上榜＝最該防。名字後的<b>+N張</b>＝那天各買了幾張。'
         f'<br>💣 <b>他們倒了幾張</b>：<b>黑名單買張</b>(昨天合計買) → <b>黑名單賣張</b>(今天合計倒)、'
         f'<b>倒貨%</b>＝賣÷買(越接近100%＝昨天鎖的今天倒光；<b>&gt;100%＝賣得比昨天買的還多</b>，'
         f'手上有更早的貨或反手加空)；最右<b>黑名單逐點</b>逐一列「分點 買X→賣Y(Z%)」，'
         f'今日反手續買則標『今再買』(＝還沒跑、甚至加碼，常是續攻/軋空的一方)。需 Sponsor 分點。'
         f'<br>📊 <b>為何漲/跌</b>：<b>預估賣壓%</b>(昨天預測明日會倒多少) vs <b>實際賣壓%</b>'
         f'(今天昨日大買分點真的倒了多少÷今量)——預測兌現則多半走弱；'
         f'<b>今主力淨額</b>🔴正=今天主力續買撐盤/🟢負=在倒；'
         f'<b>高檔回落%</b>=今收vs今高(接近0=守在高檔、跌深=衝高被倒)；'
         f'振幅%/量能倍數/當沖比%=對殺熱度。分點欄需 Sponsor，抓不到留白。'
         f'<br>⚠️ 方向不穩(可能軋空續強)、edge 薄、非投資建議。</p>')
    h += _table(df, cols, ["鎖碼淨額", "今主力淨額", "漲跌%", "跳空%", "盤中%", "高檔回落%"])
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


# 「精簡模式」保留的核心欄：做一次買賣決策真正會看的。其餘一鍵收起（資料仍在，隨時可展開）。
# 每軌 45~51 欄對非技術使用者太多 → 給一個乾淨的預設視圖。
_CORE_COLS = {
    "market", "產業", "處置警示",                       # 識別＋硬風控
    "今日收盤", "今日漲跌%", "close", "漲跌%",            # 價
    "定調", "多空傾向", "與大盤",                        # 一句話方向
    "停損價", "目標價",                                 # 風控價位（停損%屬細節）
    "均線排列", "季線年線", "20MA乖離%", "52週位置%",      # 趨勢/位階
    "成交額億", "量能倍數", "當沖比率%", "當日振幅%",       # 量能/熱度
    "外資", "投信", "主導度%", "籌碼訊號",                # 籌碼（外資/投信為堆疊格）
    "主力淨額", "今主力淨額", "隔日沖賣壓%",               # 分點
    "全市場黑名單", "本檔黑名單", "黑名單買張", "預估賣壓佔量%",   # 第6軌核心（黑名單＝防倒貨主訊號）
    "風險",                                            # 排雷
}


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
            txt = (MERGE_GROUPS[c][0] + "今/昨/10/20") if c in MERGE_GROUPS else label(c)
            core = ' data-core="1"' if c in _CORE_COLS else ""
            items.append(f'<label><input type="checkbox" checked data-col="{c}"{core} '
                         f'onchange="tc(this)">{txt}</label>')
    if not items:
        return ""
    return ('<details class="colctrl"><summary>🔧 欄位顯示（勾掉不想看的欄，全報告即時套用、下次開報告會記住）</summary>'
            f'<div class="cols">{"".join(items)}</div>'
            '<div class="btns"><button onclick="coreCols()">⭐ 精簡（只留常用）</button>'
            '<button onclick="allCols(true)">全部顯示</button>'
            '<button onclick="allCols(false)">全部隱藏</button></div></details>')


_COLCTRL_JS = """
<script>
var LSK='twreport_hiddenCols';
function _apply(name,show){document.querySelectorAll('[data-col="'+name+'"]').forEach(function(e){e.style.display=show?'':'none';});}
function _save(){var h=[];document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){if(!cb.checked)h.push(cb.dataset.col);});try{localStorage.setItem(LSK,JSON.stringify(h));}catch(e){}}
function tc(cb){_apply(cb.dataset.col,cb.checked);_save();}
function allCols(show){document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){cb.checked=show;_apply(cb.dataset.col,show);});_save();}
function coreCols(){document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){var k=cb.dataset.core==='1';cb.checked=k;_apply(cb.dataset.col,k);});_save();}
(function(){var h=[];try{h=JSON.parse(localStorage.getItem(LSK)||'[]');}catch(e){}
 document.querySelectorAll('.colctrl input[type=checkbox]').forEach(function(cb){if(h.indexOf(cb.dataset.col)>=0){cb.checked=false;_apply(cb.dataset.col,false);}});})();
</script>"""


def _pxctrl() -> str:
    """收盤價上限篩選列：輸入一個價格，全報告即時只留收盤價 ≤ 該值的列（跨所有軌）。"""
    return ('<div class="pxctrl">💰 只看收盤價 ≤ '
            '<input type="number" id="pxmax" min="0" step="1" placeholder="不限" '
            'inputmode="decimal" oninput="pxApply()"> 元'
            '<button onclick="pxClear()">清除</button>'
            '<span class="pxhint" id="pxhint"></span></div>')


_PX_JS = """
<script>
var PXK='twreport_pxmax';
function pxApply(){
 var el=document.getElementById('pxmax');var v=(el.value||'').trim();
 var max=(v==='')?null:parseFloat(v);var shown=0,total=0;
 document.querySelectorAll('table tbody tr').forEach(function(tr){
  var pc=tr.querySelector('[data-price]');if(!pc)return;
  var p=parseFloat(pc.getAttribute('data-price'));if(isNaN(p))return;
  total++;var hide=(max!==null)&&!isNaN(max)&&p>max;tr.style.display=hide?'none':'';if(!hide)shown++;});
 var hint=document.getElementById('pxhint');
 hint.textContent=(max===null||isNaN(max))?'':('　符合 '+shown+' / '+total+' 檔');
 try{if(v==='')localStorage.removeItem(PXK);else localStorage.setItem(PXK,v);}catch(e){}
}
function pxClear(){document.getElementById('pxmax').value='';pxApply();}
(function(){try{var s=localStorage.getItem(PXK);if(s){document.getElementById('pxmax').value=s;pxApply();}}catch(e){}})();
</script>"""


_SORT_JS = """
<script>
// 點表頭排序（純視圖）。第一下由「差→好」還是「大→小」不猜，一律先降序、再點切升序。
// 數字欄自動辨識：去掉 % 逗號 + 張 倍 等修飾後能 parseFloat 就當數字，否則按文字。
function _num(td){
 var t=(td.textContent||'').replace(/[,%+張倍億元]/g,'').trim();
 if(t===''||t==='—'||t==='-')return null;
 var v=parseFloat(t);return isNaN(v)?null:v;
}
function sortT(th){
 var tb=th.closest('table'),body=tb.tBodies[0];
 var idx=Array.prototype.indexOf.call(th.parentNode.children,th);
 var desc=th.getAttribute('data-sort')!=='desc';
 Array.prototype.forEach.call(th.parentNode.children,function(o){
  o.removeAttribute('data-sort');o.textContent=o.textContent.replace(/[▲▼]$/,'');});
 th.setAttribute('data-sort',desc?'desc':'asc');
 th.insertAdjacentHTML('beforeend',desc?'▼':'▲');
 var rows=Array.prototype.slice.call(body.rows);
 rows.sort(function(a,b){
  var x=a.cells[idx],y=b.cells[idx];if(!x||!y)return 0;
  var nx=_num(x),ny=_num(y);
  if(nx===null&&ny===null)return (x.textContent||'').localeCompare(y.textContent||'','zh-Hant');
  if(nx===null)return 1;              // 空值一律沉底，不論升降序
  if(ny===null)return -1;
  return desc?(ny-nx):(nx-ny);
 });
 rows.forEach(function(r){body.appendChild(r);});
}
</script>"""


def _sec(key: str, head: str, inner: str) -> str:
    """一個可收合的區塊：點標題收合內容。表格很長，不想看的軌收起來就不用一直滑。"""
    return (f'<section class="sec" data-sec="{key}">'
            f'<h2 onclick="secToggle(this)"><span class="arw">\u25be</span>{head}</h2>'
            f'<div class="secbody">{inner}</div></section>')


def _sec_wrap(key: str, html: str) -> str:
    """把既成的『<h2>…</h2>＋內容』區塊（追蹤區）包成可收合，不必改那些函式本身。"""
    if not html or not html.startswith("<h2>"):
        return html
    head, _, rest = html[4:].partition("</h2>")
    return _sec(key, head, rest)


def _secctrl() -> str:
    """區塊收合快捷列（沿用 pxctrl 樣式，跟欄位/價格控制排在一起）。"""
    return ('<div class="pxctrl">\U0001f4c2 區塊：'
            '<button onclick="secAll(false)">全部收合</button>'
            '<button onclick="secAll(true)">全部展開</button>'
            '<span class="pxhint">　點各區塊標題可單獨收合／展開，下次開報告會記住</span></div>')


_SEC_JS = """
<script>
var SECK='twreport_collapsedSecs';
function _secApply(s,open){var b=s.querySelector('.secbody');if(b)b.style.display=open?'':'none';
 var a=s.querySelector('.arw');if(a)a.textContent=open?'\u25be':'\u25b8';}
function _secSave(){var c=[];document.querySelectorAll('.sec').forEach(function(s){
 if(s.dataset.open==='0')c.push(s.dataset.sec);});
 try{localStorage.setItem(SECK,JSON.stringify(c));}catch(e){}}
function secToggle(h){var s=h.closest('.sec');var open=s.dataset.open!=='0';
 s.dataset.open=open?'0':'1';_secApply(s,!open);_secSave();}
function secAll(open){document.querySelectorAll('.sec').forEach(function(s){
 s.dataset.open=open?'1':'0';_secApply(s,open);});_secSave();}
(function(){var c=[];try{c=JSON.parse(localStorage.getItem(SECK)||'[]');}catch(e){}
 document.querySelectorAll('.sec').forEach(function(s){
  var open=c.indexOf(s.dataset.sec)<0;s.dataset.open=open?'1':'0';_secApply(s,open);});})();
</script>"""


def _warn_banner(msg) -> str:
    """資料不完整警示（整批抓取失敗時），擺最頂端——殘缺報告不可以看起來像正常的。"""
    if not msg:
        return ""
    return ('<div style="background:#7f1d1d;color:#fff;padding:12px 14px;border-radius:8px;'
            f'margin:10px 0;font-weight:700">⚠️ {msg}</div>')


def build(today, reg, glob_lines, sox, blocks, intersection=None,
          followthrough=None, ftstats=None, snipe_ohlc=None, snipe_ohlc_stats=None,
          data_warn=None) -> str:
    from .regime import summary_line
    banner = (f'<div class="banner {_regime_class(reg)}">🚦 {summary_line(reg)}'
              f'<small>{sox}<br>🌍 {" ｜ ".join(glob_lines)}</small></div>')

    body = ""
    for i, b in enumerate(blocks):
        head = f"{b['title']}{_verdict_badge(b.get('bt'))}"
        inner = ""
        if b.get("note"):
            note_html = "<br>".join(ln for ln in b["note"].split("\n") if ln.strip())
            inner += f'<p class="note">{note_html}</p>'
        df = b["df"]
        if b.get("skipped"):
            inner += "<p>（已略過 --skip-longterm；要看長期軌請跑 <code>python -m scripts.run_longterm</code>）</p>"
        elif df is None or df.empty:
            inner += "<p>（今日無符合條件標的）</p>"
        else:
            n = b.get("n")
            shown = df.head(n) if n else df
            inner += _table(shown, b["cols"], b.get("signed", []))
            if n and len(df) > n:
                csv = b.get("csv_name")
                where = (f'完整清單見同資料夾 <code>{csv}</code>（可用 Excel 開）' if csv
                         else "完整清單需在控制台勾選「輸出 CSV」後重跑")
                inner += f'<p class="note">（僅顯示前 {n} 名，共 {len(df)} 檔符合；{where}）</p>'
        # 排雷提醒 callout（對齊 .md）：df 內有高風險則列紅旗
        if b.get("landmine"):
            inner += _landmine_html(df, b.get("landmine_label", "T11 候選"))
        # 持股籌碼歸因（對齊 .md）：接在持股表後
        if b.get("attribution"):
            inner += _attr_html(b["attribution"])
        if b.get("after_intersection") and intersection is not None:
            names = "、".join(intersection) if intersection else "（無）"
            inner += f'<div class="star">⭐ 雙訊號交集（法人買且抗跌）：{names}</div>'
        body += _sec(b.get("key") or b.get("bt") or f"sec{i}", head, inner)

    # 昨日精選今日追蹤（對齊 .md）：擺在各軌之後、術語小抄之前
    body += _sec_wrap("鎖碼追蹤", _snipe_ohlc_html(snipe_ohlc, snipe_ohlc_stats))
    body += _sec_wrap("昨日追蹤", _followthrough_html(followthrough, ftstats))

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股每日整合報告 {today}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>台股每日整合報告</h1><div class="sub">{today}　·　研究用途，非投資建議</div>
{_warn_banner(data_warn)}{banner}
<p class="note">💡 點<b>股票代號</b>開 K 線圖（Goodinfo，新分頁）　·　點<b>表頭</b>可依該欄排序（再點一次換升／降序）</p>
{_pxctrl()}{_secctrl()}{_colctrl(blocks)}{body}
<p class="note" style="margin-top:18px">{GLOSSARY}</p>
<div class="disclaimer">⚠️ 本報告為候選觀察名單，非投資建議。紅漲綠跌為台股慣例。</div>
</div>{_COLCTRL_JS}{_PX_JS}{_SORT_JS}{_SEC_JS}</body></html>"""
