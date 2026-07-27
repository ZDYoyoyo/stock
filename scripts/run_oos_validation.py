"""P4 — 樣本外/分期穩健度 + 參數敏感度分析。

目的：確認 P2/P3 的（負）結論不是單一期間或單一參數的巧合。
  ① 分期穩健度：同一組態在 全期 / 前半 / 後半 的績效是否一致（時間穩健）。
  ② 參數敏感度：逐一掃 T11 主要門檻，看 CAGR/夏普對參數有多敏感——
     若小幅動參數績效就大幅跳動 = 脆弱/過擬合訊號。

面板只抓一次（FinMind），之後全在記憶體重算，很快。
用法：python -m scripts.run_oos_validation --universe 15 --start 2022-01-01
輸出：reports/oos_validation.md
"""
import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import T11 as T11CFG
from src.portfolio_backtest import (
    compute_t11_entries, run_portfolio, slice_panel,
)
from src.signals import T11Params
# 復用 P2 的 universe 選取與面板載入（DRY）
from scripts.run_portfolio_backtest import _load_panel_stock, _pick_universe


def _bt(panel, params, max_pos, hold):
    entries = compute_t11_entries(panel, params)
    res = run_portfolio(panel, entries, max_positions=max_pos, hold_days=hold)
    return res.metrics


def _bh(panel):
    r = [c[-1] / c[0] - 1 for c in (df["close"].tolist() for df in panel.values()) if c and c[0] > 0]
    return sum(r) / len(r) * 100 if r else 0


def _mid_date(panel):
    ds = sorted({d for df in panel.values() for d in df["date"]})
    return ds[len(ds) // 2] if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=15)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--max-pos", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"載入 {len(universe)} 檔（起始 {args.start}）…")
    panel = {}
    for sid, market in universe:
        try:
            df = _load_panel_stock(sid, market, args.start, with_margin=False)
            if df is not None and len(df) >= 60:
                panel[sid] = df
                print(f"  ✓ {sid} {len(df)} 日")
        except Exception as e:
            print(f"  ✗ {sid} {e}")
    if len(panel) < 3:
        print("有效檔數不足，結束")
        return

    base = T11Params.from_config(T11CFG)
    mid = _mid_date(panel)

    # ① 分期穩健度
    periods = [("全期", panel), ("前半", slice_panel(panel, hi=mid)),
               ("後半", slice_panel(panel, lo=mid))]
    period_rows = []
    for name, pnl in periods:
        m = _bt(pnl, base, args.max_pos, args.hold)
        period_rows.append((name, m, _bh(pnl)))

    # ② 參數敏感度（逐一動一個旋鈕，其餘固定）
    sweeps = [
        ("min_consec 連買門檻", "min_consec", [3, 4, 5, 6]),
        ("max_gain 漲幅上限", "max_gain", [0.08, 0.12, 0.16, 0.20]),
        ("min_buy_ratio 吃貨比重", "min_buy_ratio", [0.03, 0.05, 0.08]),
    ]
    sens_blocks = []
    for title, field, values in sweeps:
        rows = []
        for v in values:
            params = replace(base, **{field: v})
            m = _bt(panel, params, args.max_pos, args.hold)
            rows.append((v, m))
        sens_blocks.append((title, field, rows))
    # 持有天數 / 同時持股數（組合層級，不改訊號）
    hold_rows = [(h, _bt(panel, base, args.max_pos, h)) for h in (10, 20, 40)]
    pos_rows = [(p, _bt(panel, base, p, args.hold)) for p in (3, 5, 8)]

    print("\n===== 分期穩健度 =====")
    for name, m, bh in period_rows:
        print(f"  {name}：CAGR {m['CAGR_%']:+.1f}%｜MaxDD {m['MaxDD_%']:.1f}%｜"
              f"夏普 {m['Sharpe']:.2f}（買入持有 {bh:+.0f}%）")

    # --- 報告 ---
    today = date.today().isoformat()

    def _hdr():
        return ["| 設定 | CAGR | 最大回撤 | 夏普 | 勝率 | 交易數 |", "|---|---|---|---|---|---|"]

    def _row(label, m):
        return (f"| {label} | {m['CAGR_%']:+.2f}% | {m['MaxDD_%']:.2f}% | {m['Sharpe']:.2f} | "
                f"{m['win_rate_%']:.0f}% | {m['n_trades']} |")

    lines = [f"# 樣本外/分期穩健度 + 參數敏感度 — {today}\n",
             f"- universe {len(panel)} 檔（⚠️倖存者偏誤）；期間起始 {args.start}；"
             f"基準組態＝config.T11，最多持 {args.max_pos} 檔、持有 {args.hold} 日\n",
             "## ① 分期穩健度（同組態換期間）",
             "| 期間 | CAGR | 最大回撤 | 夏普 | 勝率 | 交易數 | 買入持有 |",
             "|---|---|---|---|---|---|---|"]
    for name, m, bh in period_rows:
        lines.append(_row(name, m).rstrip(" |") + f" | {bh:+.0f}% |")
    lines.append("\n> 判讀：前半/後半若方向一致（都負或都正）→ 結論穩健，非單一期間巧合。")

    lines.append("\n## ② 參數敏感度（逐一動一個旋鈕）")
    for title, field, rows in sens_blocks:
        lines.append(f"\n**{title}**（基準 {getattr(base, field)}）")
        lines += _hdr()
        for v, m in rows:
            star = "　★基準" if v == getattr(base, field) else ""
            lines.append(_row(f"{field}={v}{star}", m))
    lines.append("\n**持有天數（組合層級）**")
    lines += _hdr()
    for h, m in hold_rows:
        lines.append(_row(f"hold={h}{'　★基準' if h == args.hold else ''}", m))
    lines.append("\n**同時持股數**")
    lines += _hdr()
    for p, m in pos_rows:
        lines.append(_row(f"max_pos={p}{'　★基準' if p == args.max_pos else ''}", m))

    lines += ["\n## 判讀",
              "- 敏感度表若『每個參數怎麼調都是負 CAGR/夏普』→ 不是沒調好，是訊號本身無 edge。",
              "- 若某參數一動績效就從正跳到負（不連續、無單調趨勢）→ 該參數是過擬合的雜訊旋鈕，應固定或移除。",
              "\n## 限制：小 universe + 倖存者偏誤（P5）；未計滑價；分期為單次切半非完整 walk-forward。"]
    out = ROOT / "reports" / "oos_validation.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")


if __name__ == "__main__":
    main()
