"""T16 排序方式對照 — RS（原版）vs 低波動優先 vs 風險調整（RS÷波動率）。

問題：T16 現在依『相對強弱』排序，改成『風險低→高』會更好嗎？
作法：篩選條件完全不動，只換 run_portfolio 的 score（＝同日多檔誰先進場），
其餘照回測驗證過的贏法（持 10 檔、不停損、可選 regime）。全離線、不碰額度。

用法：python -m scripts.run_t16_rank_compare [--max-pos 10] [--hold 20]
輸出：reports/t16_rank_compare.md
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portfolio_backtest import (
    compute_regime_ok, compute_t16_entries, load_panel_csv, run_portfolio,
)

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"
OUT = ROOT / "reports" / "t16_rank_compare.md"

RANKS = [("rs", "RS 相對強弱（原版·已驗證）"),
         ("lowvol", "低波動優先（風險低→高）"),
         ("riskadj", "風險調整（RS÷波動率）")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--max-pos", type=int, default=10)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--vol-win", type=int, default=20)
    ap.add_argument("--regime-th", type=float, default=45)
    args = ap.parse_args()

    panel = load_panel_csv(args.panel)
    reg_ok = compute_regime_ok(panel, threshold=args.regime_th)
    print(f"面板 {len(panel)} 檔｜持 {args.max_pos} 檔／持有 {args.hold} 日／無停損")

    rows = []
    for mode, label in RANKS:
        entries = compute_t16_entries(panel, rank=mode, vol_win=args.vol_win)
        for reg_label, reg in (("—", None), ("+regime", reg_ok)):
            r = run_portfolio(panel, entries, max_positions=args.max_pos,
                              hold_days=args.hold, stop=None, regime_ok=reg)
            m = r.metrics
            rows.append({"排序": label, "regime": reg_label, "CAGR%": m["CAGR_%"],
                         "夏普": m["Sharpe"], "最大回撤%": m["MaxDD_%"],
                         "交易數": m["n_trades"], "勝率%": m["win_rate_%"],
                         "曝險%": m["exposure_%"]})
            print(f"  {label:24s} {reg_label:8s} CAGR {m['CAGR_%']:+6.1f}%"
                  f"　夏普 {m['Sharpe']:5.2f}　回撤 {m['MaxDD_%']:6.1f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        f.write("# T16 排序方式對照（RS vs 低波動 vs 風險調整）\n\n")
        f.write(f"> 產生於 {date.today()}　·　離線面板 {len(panel)} 檔"
                f"　·　持 {args.max_pos} 檔／持有 {args.hold} 日／無停損"
                f"　·　波動率窗 {args.vol_win} 日\n\n")
        f.write("**篩選條件完全相同**（近10日報酬 0~30%、成交量門檻），只換『同日多檔"
                "誰先進場』的排序分數。\n\n")
        f.write("| 排序 | regime | CAGR% | 夏普 | 最大回撤% | 交易數 | 勝率% | 曝險% |\n")
        f.write("|:--|:--|--:|--:|--:|--:|--:|--:|\n")
        for r in rows:
            f.write(f"| {r['排序']} | {r['regime']} | {r['CAGR%']:+.1f} | {r['夏普']:.2f} "
                    f"| {r['最大回撤%']:.1f} | {r['交易數']} | {r['勝率%']:.0f} "
                    f"| {r['曝險%']:.0f} |\n")
        f.write("\n⚠️ 這是 **in-sample** 比較（同一段歷史挑出最好的），本專案已驗證過這種"
                "比較會灌水（T12 in-sample +34.5% → 樣本外只剩 +7.8%）。要換排序方式前，"
                "勝出者必須再跑一次嚴格樣本外（walk-forward）才算數。\n")
    print(f"\n✅ 報告 → {OUT}")


if __name__ == "__main__":
    main()
