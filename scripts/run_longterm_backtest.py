"""長期價值軌回測 — 月頻價值篩選 vs 買入持有 vs +regime（離線、產真實 CAGR/回撤/夏普）。

價值策略：每月首交易日篩 殖利率≥/PER≤/PBR≤/ROE估≥（config.LONGTERM），持 N 檔、長抱 hold 日。
資料：backtest_panel(股價) + valuation_panel(月頻估值)，全離線不碰額度。

⚠️ 限制：只用『核心價值因子』(殖利率/PER/PBR/ROE估，BWIBBU_d 免費)；
   live 的配息年數硬門檻/EPS成長此處略。BWIBBU_d 僅上市 → 回測僅上市股。

用法：python -m scripts.run_longterm_backtest [--hold 60] [--max-pos 10] [--regime-th 45]
輸出：reports/longterm_backtest.md
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.portfolio_backtest import (
    _metrics, compute_longterm_entries, compute_regime_ok,
    load_panel_csv, load_valuation_panel, run_portfolio,
)
from src.config import LONGTERM as L

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"
VALUATION = ROOT / "data" / "history" / "valuation_panel.csv.gz"


def _ew_index(panel: dict) -> pd.Series:
    """等權買入持有指數（各檔以自身首日收盤正規化後逐日取平均）。"""
    norm = {}
    for sid, df in panel.items():
        df = df.sort_values("date")
        c0 = df["close"].iloc[0]
        if c0 > 0:
            norm[sid] = dict(zip(df["date"], df["close"] / c0))
    all_dates = sorted({d for m in norm.values() for d in m})
    vals = {}
    for d in all_dates:
        xs = [m[d] for m in norm.values() if d in m]
        if xs:
            vals[d] = sum(xs) / len(xs)
    return pd.Series(vals)


def _regime_timed(index_eq: pd.Series, reg_ok: dict):
    rets = index_eq.pct_change().fillna(0)
    dates = list(index_eq.index)
    eq, invested = [1.0], 0
    for i in range(1, len(dates)):
        on = reg_ok.get(dates[i - 1], True)
        eq.append(eq[-1] * (1 + (rets.iloc[i] if on else 0.0)))
        if on:
            invested += 1
    return pd.Series(eq, index=dates), invested


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--valuation", default=str(VALUATION))
    ap.add_argument("--hold", type=int, default=60, help="長抱天數（長期軌預設60≈3個月）")
    ap.add_argument("--max-pos", type=int, default=10, help="持股數（價值分散）")
    ap.add_argument("--regime-th", type=float, default=45.0)
    args = ap.parse_args()

    if not Path(args.valuation).exists():
        print(f"無估值面板 {args.valuation}；先跑 python -m scripts.backfill_valuation")
        return

    panel = load_panel_csv(args.panel)
    val = load_valuation_panel(args.valuation)
    n_days = len({d for df in panel.values() for d in df["date"]})
    val_sids = set(val["stock_id"]) & set(panel.keys())
    print(f"面板 {len(panel)} 檔、{n_days} 交易日｜估值 {val['date'].nunique()} 月、"
          f"面板中 {len(val_sids)} 檔上市對得上")

    reg = compute_regime_ok(panel, threshold=args.regime_th)
    entries = compute_longterm_entries(panel, val)
    n_sig = sum(len(v) for v in entries.values())
    print(f"價值訊號：{len(entries)} 個月、共 {n_sig} 筆")

    lt = run_portfolio(panel, entries, max_positions=args.max_pos, hold_days=args.hold)
    ltr = run_portfolio(panel, entries, max_positions=args.max_pos, hold_days=args.hold,
                        regime_ok=reg)
    idx = _ew_index(panel)
    bh = _metrics(idx, [], days_with_pos=len(idx), n_days=len(idx))
    timed_eq, inv = _regime_timed(idx, reg)
    bhr = _metrics(timed_eq, [], days_with_pos=inv, n_days=len(timed_eq))

    rows = [(f"長期價值（持{args.max_pos}檔/{args.hold}日）", lt.metrics),
            (f"長期價值 + regime≥{args.regime_th:.0f}%", ltr.metrics),
            ("買入持有（等權全部）", bh),
            (f"買入持有 + regime≥{args.regime_th:.0f}%", bhr)]

    print("\n===== 長期價值軌回測 =====")
    for name, m in rows:
        print(f"  {name:28s}｜CAGR {m['CAGR_%']:+6.1f}%｜MaxDD {m['MaxDD_%']:6.1f}%"
              f"｜夏普 {m['Sharpe']:5.2f}｜曝險 {m['exposure_%']:.0f}%")

    today = date.today().isoformat()
    lines = [f"# 長期價值軌回測 — {today}\n",
             f"- 面板：{len(panel)} 檔、{n_days} 交易日；估值 {val['date'].nunique()} 月（月頻，離線不碰額度）",
             f"- 策略：每月篩 殖利率≥{L.MIN_YIELD:.0f}%/PER≤{L.MAX_PER:.0f}/PBR≤{L.MAX_PBR:.0f}/"
             f"ROE估≥{L.MIN_ROE_EST:.0f}%，持{args.max_pos}檔/{args.hold}日\n",
             "## CAGR / 最大回撤 / 夏普",
             "| 策略 | CAGR | 最大回撤 | 夏普 | 年化波動 | 曝險 |",
             "|---|---|---|---|---|---|"]
    for name, m in rows:
        lines.append(f"| {name} | {m['CAGR_%']:+.2f}% | {m['MaxDD_%']:.2f}% | "
                     f"{m['Sharpe']:.2f} | {m['Vol_%']:.1f}% | {m['exposure_%']:.0f}% |")
    lines += ["\n## 判讀",
              "- 價值若夏普/CAGR 贏買入持有 → 價值因子(殖利率+低估值+ROE)有效；反之則此 universe 無 edge。",
              "- +regime 若降回撤且夏普升 → 擇時對長期價值也有價值。",
              "\n## 限制",
              f"- 只用核心價值因子(殖利率/PER/PBR/ROE估)；配息年數硬門檻、EPS成長 未納入(live 有)。",
              f"- BWIBBU_d 僅上市 → 回測僅上市股({len(val_sids)}檔)；上櫃價值股未涵蓋。",
              "- 月頻估值 + 200檔流動性面板(偏大型股)、倖存者偏誤；未計滑價。"]
    out = ROOT / "reports" / "longterm_backtest.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")


if __name__ == "__main__":
    main()
