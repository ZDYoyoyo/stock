"""地雷偵測 — 對持股 / 指定股 / 每日候選逐檔排雷。

用法（專案根目錄）：
    python -m scripts.run_landmine --holdings            # 檢查我的持股（預設）
    python -m scripts.run_landmine --stocks 2330,2887    # 檢查指定股
    python -m scripts.run_landmine --from-daily          # 檢查今天 T11/T16/當沖候選
    python -m scripts.run_landmine --stocks 2330 --min-level 中   # 只列風險≥中

檢查財務(營收/EPS/毛利)+籌碼(法人賣/大戶出貨/融資暴增)+技術(破季線/相對弱勢)紅旗。
⚠️ 研究用途，非投資建議；地雷偵測是「排除」不是「放空訊號」。
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR
from src.screeners import landmine
from src import report_html

_LEVEL_ORDER = {"🟢 低": 0, "🟡 中": 1, "🟠 高": 2, "🔴 嚴重": 3}
_LEVEL_ALIAS = {"低": 0, "中": 1, "高": 2, "嚴重": 3}


def _daily_candidates(t16_top: int = 15) -> list:
    """今天『波段』候選代號（去重），排雷用。

    聚焦 T11(法人吸貨，全部) + T16(抗跌強勢，前 t16_top 檔)——這些是要抱天~週的標的，
    財務/籌碼紅旗才有意義。當沖候選是隔日沖、看財務無意義，故不納入。
    """
    from src.screeners import institutional_accumulation as t11
    from src.screeners import relative_strength as t16
    sids = []
    try:
        d11 = t11.run()
        if not d11.empty:
            sids += d11["stock_id"].tolist()
    except Exception:
        pass
    try:
        d16 = t16.run()
        if not d16.empty:
            sids += d16["stock_id"].head(t16_top).tolist()
    except Exception:
        pass
    seen, out = set(), []
    for s in sids:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", help="逗號分隔代號，如 2330,2887")
    ap.add_argument("--holdings", action="store_true", help="檢查我的持股")
    ap.add_argument("--from-daily", action="store_true",
                    help="檢查今天波段候選(T11全部+T16前15，當沖不納入)")
    ap.add_argument("--min-level", default="低", choices=["低", "中", "高", "嚴重"],
                    help="只列出風險≥此級（預設全列）")
    args = ap.parse_args()

    if args.stocks:
        sids = [s.strip() for s in args.stocks.split(",") if s.strip()]
        source = "指定股"
    elif args.from_daily:
        print("彙整今日候選清單 …")
        sids = _daily_candidates()
        source = "今日候選"
    else:  # 預設檢查持股
        from src import portfolio as pf
        sids = pf.load()["stock_id"].astype(str).tolist()
        source = "我的持股"

    if not sids:
        print(f"（{source}為空，無可檢查標的）")
        if source == "我的持股":
            print("先記錄持股：python -m scripts.portfolio add 2330 --lots 1 --price 1000")
        return

    print(f"🧨 地雷偵測（{source}，共 {len(sids)} 檔）…")
    df = landmine.scan(sids)
    if df.empty:
        print("無資料")
        return

    floor = _LEVEL_ALIAS[args.min_level]
    view = df[df["風險級"].map(_LEVEL_ORDER) >= floor]

    cols = [c for c in ["stock_id", "name", "風險級", "風險分", "收盤", "營收YoY%",
                        "EPS年增%", "毛利率%", "相對大盤%", "千張大戶%", "紅旗"]
            if c in view.columns]

    print(f"\n=== 地雷偵測結果（{source}，風險高→低）===")
    if view.empty:
        print(f"（無風險≥{args.min_level}的標的，清單相對乾淨）")
    else:
        print(report_html.rename_cn(view[cols]).to_string(index=False))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = OUTPUT_DIR / f"{today}_地雷偵測.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 地雷偵測 — {source}（{today}）\n\n")
        f.write("> ⚠️ 研究用途，非投資建議。地雷偵測用於『排除』，非放空訊號。\n")
        f.write("> 紅旗分財務(營收/EPS/毛利)、籌碼(法人賣/大戶出貨/融資)、技術(破季線/相對弱勢)三面。\n\n")
        f.write(report_html.rename_cn(df[cols]).to_markdown(index=False) + "\n")
        f.write(f"\n> {report_html.GLOSSARY}\n")
    print(f"\n✅ 報告 → {path}")

    severe = df[df["風險級"].isin(["🔴 嚴重", "🟠 高"])]
    if not severe.empty:
        print(f"\n🚨 高風險 {len(severe)} 檔：" +
              "、".join(f"{r.stock_id} {r.name}" for r in severe.itertuples()))


if __name__ == "__main__":
    main()
