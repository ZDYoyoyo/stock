"""T16 調校 — 降回撤：試不同持股數 × 停損 × regime（離線，不碰額度）。

用法：python -m scripts.run_t16_tune
輸出：reports/t16_tune.md
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portfolio_backtest import (
    compute_regime_ok, compute_t16_entries, load_panel_csv, run_portfolio,
)

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"


def main():
    panel = load_panel_csv(str(PANEL))
    entries = compute_t16_entries(panel)
    reg = compute_regime_ok(panel, threshold=45.0)
    print(f"面板 {len(panel)} 檔")

    # (名稱, 持股數, 停損, 用regime)
    configs = [
        ("5檔 無停損（基準）", 5, None, False),
        ("10檔 無停損", 10, None, False),
        ("15檔 無停損", 15, None, False),
        ("10檔 +停損-8%", 10, ("pct", 0.08), False),
        ("15檔 +停損-8%", 15, ("pct", 0.08), False),
        ("10檔 +停損-8% +regime", 10, ("pct", 0.08), True),
        ("15檔 +停損-8% +regime", 15, ("pct", 0.08), True),
    ]

    rows = []
    for name, npos, stop, use_reg in configs:
        r = run_portfolio(panel, entries, max_positions=npos, hold_days=20,
                          stop=stop, regime_ok=(reg if use_reg else None))
        rows.append((name, r.metrics))
        m = r.metrics
        print(f"  {name:24s}｜CAGR {m['CAGR_%']:+6.1f}%｜MaxDD {m['MaxDD_%']:6.1f}%"
              f"｜夏普 {m['Sharpe']:5.2f}｜曝險 {m['exposure_%']:.0f}%")

    today = date.today().isoformat()
    lines = [f"# T16 抗跌強勢 調校（降回撤）— {today}\n",
             f"- 面板 {len(panel)} 檔、持有20日；試 持股數 × 停損-8% × regime≥45%\n",
             "| 設定 | CAGR | 最大回撤 | 夏普 | 年化波動 | 勝率 | 曝險 |",
             "|---|---|---|---|---|---|---|"]
    for name, m in rows:
        lines.append(f"| {name} | {m['CAGR_%']:+.2f}% | {m['MaxDD_%']:.2f}% | {m['Sharpe']:.2f} | "
                     f"{m['Vol_%']:.1f}% | {m['win_rate_%']:.0f}% | {m['exposure_%']:.0f}% |")
    lines += ["\n## 判讀",
              "- 多持幾檔＝分散，通常降回撤、降波動（可能小降報酬）。",
              "- 停損砍大賠、regime 躲爛環境，兩者一起最能壓回撤；看夏普找甜蜜點。",
              "\n## 限制：150檔仍有倖存者偏誤（動能策略尤其樂觀）；未計滑價；in-sample。"]
    out = ROOT / "reports" / "t16_tune.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")


if __name__ == "__main__":
    main()
