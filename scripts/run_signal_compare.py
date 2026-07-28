"""離線訊號對照 — 同一面板、同指標，比 T11 / 買入持有 / 買入持有+regime 擇時。

全離線（讀 backtest_panel.csv.gz），不碰 FinMind 額度。
用法：python -m scripts.run_signal_compare [--panel ...] [--regime-th 45] [--hold 20] [--max-pos 5]
輸出：reports/signal_compare.md
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.portfolio_backtest import (
    _metrics, compute_regime_ok, compute_t11_entries, load_panel_csv, run_portfolio,
)
from src.signals import T11Params
from src.config import T11 as T11CFG

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"


def _ew_index(panel: dict) -> pd.Series:
    """等權買入持有指數：每檔以自身首日收盤正規化，逐日取平均（處理各檔起始日不同）。"""
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
    """買入持有 + regime：昨日環境 OK 才在場（今日吃指數報酬），否則空手(現金)。回 (equity, 曝險天數)。"""
    rets = index_eq.pct_change().fillna(0)
    dates = list(index_eq.index)
    eq, invested_days = [1.0], 0
    for i in range(1, len(dates)):
        invested = reg_ok.get(dates[i - 1], True)
        eq.append(eq[-1] * (1 + (rets.iloc[i] if invested else 0.0)))
        if invested:
            invested_days += 1
    return pd.Series(eq, index=dates), invested_days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--regime-th", type=float, default=45.0)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--max-pos", type=int, default=5)
    args = ap.parse_args()

    panel = load_panel_csv(args.panel)
    n_days = len({d for df in panel.values() for d in df["date"]})
    print(f"離線面板 {len(panel)} 檔、{n_days} 交易日")

    # 1) T11（真正的 T11，含融資）
    t11 = run_portfolio(panel, compute_t11_entries(panel, T11Params.from_config(T11CFG)),
                        max_positions=args.max_pos, hold_days=args.hold)

    # 2) 買入持有（等權全 universe）
    idx = _ew_index(panel)
    bh = _metrics(idx, [], days_with_pos=len(idx), n_days=len(idx))

    # 3) 買入持有 + regime 擇時
    reg = compute_regime_ok(panel, threshold=args.regime_th)
    timed_eq, inv_days = _regime_timed(idx, reg)
    bhr = _metrics(timed_eq, [], days_with_pos=inv_days, n_days=len(timed_eq))

    rows = [("T11 法人吸貨（持5檔/20日）", t11.metrics),
            ("買入持有（等權全部）", bh),
            (f"買入持有 + regime≥{args.regime_th:.0f}%", bhr)]

    print("\n===== 訊號對照 =====")
    for name, m in rows:
        print(f"  {name:26s}｜CAGR {m['CAGR_%']:+6.1f}%｜MaxDD {m['MaxDD_%']:6.1f}%"
              f"｜夏普 {m['Sharpe']:5.2f}｜曝險 {m['exposure_%']:.0f}%")

    today = date.today().isoformat()
    lines = [f"# 離線訊號對照 — {today}\n",
             f"- 面板：{len(panel)} 檔、{n_days} 交易日（離線，不碰額度）",
             "- 同一 universe、同指標。T11＝持5檔/20日；買入持有＝等權全部；"
             "regime＝市場廣度爛時空手\n",
             "## CAGR / 最大回撤 / 夏普",
             "| 策略 | CAGR | 最大回撤 | 夏普 | 年化波動 | 曝險 |",
             "|---|---|---|---|---|---|"]
    for name, m in rows:
        lines.append(f"| {name} | {m['CAGR_%']:+.2f}% | {m['MaxDD_%']:.2f}% | "
                     f"{m['Sharpe']:.2f} | {m['Vol_%']:.1f}% | {m['exposure_%']:.0f}% |")
    lines += ["\n## 判讀",
              "- 夏普最高＝風險調整後最好。買入持有若夏普遠贏 T11 → 再次證明 T11 選股/擇時無加值。",
              "- regime 版若『MaxDD 明顯變淺、夏普升、CAGR 沒差多少』→ 擇時濾網有價值。",
              "\n## 限制：150 檔仍有倖存者偏誤；未計滑價；等權買入持有未計再平衡成本。"]
    out = ROOT / "reports" / "signal_compare.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")


if __name__ == "__main__":
    main()
