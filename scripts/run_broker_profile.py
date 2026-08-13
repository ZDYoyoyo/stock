"""分點行為檔案（隔日沖黑名單）— 更新累計計數器並輸出排行報告。

用途：看「哪些券商分點是隔日沖慣犯、哪些是偏長線買盤」。
每日盤後選股已自動累積，這支是**手動查看/補累積**用：
  - 把本機分點快取折進累計計數器（冪等，重跑不會重複算）
  - 輸出排行 .md/.html → reports/broker_profile.md

判讀：
  隔日沖率% = 進前15大買超後、**隔日**就轉淨賣的比率（只認相鄰交易日）
  回吐量%   = 隔日實際對沖掉的張數 ÷ 當初買進張數（比次數更看得出倒貨力道）
  樣本數/股票數 = 可信度（跨越越多檔、次數越多，越不可能是巧合）

⚠️ 樣本靠本機累積：跑越多天越準。分點＝券商分公司非個人，
   同分點內不同客戶的買賣會互相抵消（偏保守）。研究用途、非投資建議。

用法：python -m scripts.run_broker_profile [--min-ops 10] [--top 25]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import broker_profile as bp
from src import report_html as rh

OUT = ROOT / "reports" / "broker_profile.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ops", type=int, default=10, help="最少樣本數才列入（預設10）")
    ap.add_argument("--top", type=int, default=25, help="各榜顯示幾名（預設25）")
    args = ap.parse_args()

    # 確保 DB 有資料（換機/容器重置時由 CSV 重建，計數器也會一起回來）
    try:
        from src import datastore
        if datastore.has_history():
            datastore.load()
    except Exception:
        pass

    print("[分點檔案] 折算本機快取 …")
    st = bp.update_from_cache()
    print(f"   新增 {st['新增轉換']} 筆轉換 → 累計 {st['總樣本']} 樣本／{st['分點數']} 分點")

    prof = bp.build(min_ops=args.min_ops)
    if prof.empty:
        raise SystemExit(f"樣本不足（需 ≥{args.min_ops} 次）。多跑幾天盤後選股累積後再看。")

    hot = prof[prof["分點類型"] == "🔥隔日沖大戶"].head(args.top)
    mid = prof[prof["分點類型"] == "⚠️偏隔日沖"].head(args.top)
    lon = prof[prof["分點類型"] == "🏦偏長線"].sort_values(
        ["隔日沖率%", "樣本數"], ascending=[True, False]).head(args.top)

    n = len(prof)
    head = (f"# 分點行為檔案（隔日沖黑名單）\n\n"
            f"> 累計 **{st['總樣本']:,} 筆樣本**、{n} 個分點入榜（樣本≥{args.min_ops}）　·　"
            f"研究用途，非投資建議\n\n"
            f"**判讀**：隔日沖率%＝進前15大買超後**隔日**轉淨賣的比率（只認相鄰交易日）；"
            f"回吐量%＝隔日實際對沖掉的張數÷當初買進張數（倒貨力道）；"
            f"樣本數/股票數＝可信度（跨越越多檔越可信）。\n\n"
            f"⚠️ 樣本靠本機每日累積，跑越多天越準；分點＝券商分公司非個人，"
            f"同分點內不同客戶買賣會互相抵消（偏保守低估）。\n")

    md = [head,
          f"\n## 🔥 隔日沖大戶（隔日沖率 ≥{bp._HOT}%）— 這些人買進＝隔天大機率倒貨\n",
          hot.to_markdown(index=False) if not hot.empty else "（無）",
          f"\n\n## ⚠️ 偏隔日沖（{bp._MID}~{bp._HOT}%）\n",
          mid.to_markdown(index=False) if not mid.empty else "（無）",
          f"\n\n## 🏦 偏長線（<{bp._LONG}%）— 這些人買進＝籌碼較穩\n",
          lon.to_markdown(index=False) if not lon.empty else "（無）",
          "\n\n## 全部分點\n", prof.to_markdown(index=False), "\n"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(md), encoding="utf-8")

    body = (f'<div class="banner reg-neutral">📇 累計 {st["總樣本"]:,} 筆樣本、{n} 個分點入榜'
            f'<small>隔日沖率%＝進前15大買超後隔日轉淨賣的比率（只認相鄰交易日）；'
            f'回吐量%＝隔日對沖張數÷買進張數；樣本數/股票數＝可信度。'
            f'⚠️樣本靠本機每日累積，跑越多天越準。</small></div>'
            f'<h2>🔥 隔日沖大戶（≥{bp._HOT}%）— 買進＝隔天大機率倒貨</h2>'
            + rh._table(hot, list(hot.columns), ["隔日沖率%"])
            + f'<h2>⚠️ 偏隔日沖（{bp._MID}~{bp._HOT}%）</h2>'
            + rh._table(mid, list(mid.columns), ["隔日沖率%"])
            + f'<h2>🏦 偏長線（&lt;{bp._LONG}%）— 買進＝籌碼較穩</h2>'
            + rh._table(lon, list(lon.columns), ["隔日沖率%"])
            + '<h2>📋 全部分點</h2>' + rh._table(prof, list(prof.columns), ["隔日沖率%"]))
    html = (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>分點行為檔案</title><style>{rh._CSS}</style></head><body><div class="wrap">'
            f'<h1>分點行為檔案（隔日沖黑名單）</h1>'
            f'<div class="sub">累計 {st["總樣本"]:,} 筆樣本　·　研究用途，非投資建議</div>'
            f'{body}</div></body></html>')
    OUT.with_suffix(".html").write_text(html, encoding="utf-8")

    print(f"\n🔥 隔日沖大戶 {len(prof[prof['分點類型'] == '🔥隔日沖大戶'])} 家"
          f"／⚠️偏隔日沖 {len(mid)} 家／🏦偏長線 {len(prof[prof['分點類型'] == '🏦偏長線'])} 家")
    if not hot.empty:
        print("\n前 5 大隔日沖分點：")
        for _, r in hot.head(5).iterrows():     # 欄名含 % → 用 iterrows 明確取值
            print(f"   {r['分點']:<10} 隔日沖率 {r['隔日沖率%']:>5.1f}%　"
                  f"回吐量 {r['回吐量%']:>5.1f}%　({r['樣本數']} 樣本／{r['股票數']} 檔)")
    print(f"\n✅ 報告 → {OUT}")
    print(f"   HTML → {OUT.with_suffix('.html')}")


if __name__ == "__main__":
    main()
