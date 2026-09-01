"""T11Q『潛伏吸籌』嚴格樣本外（walk-forward）驗證。

問題：T11 法人吸貨回測無 edge（CAGR −16%），因為它找的是「已經在漲＋法人買」，
那是動能的劣化版。這支試相反的假設：**法人一直買、但價格還在整理**（籌碼領先價格）。
in-sample 初掃看起來不錯（持40~60日夏普 1.2~1.37），但本專案已驗證過這種比較會灌水
（T12 in-sample +34.5% → OOS 只剩 +7.8%），所以直接做 walk-forward 才算數。

方法（與 run_t16_oos / run_t12_oos 同一套，共用 src/walkforward）：
  3 折 expanding-train（切點 25/50/75%），train 挑夏普最高參數 → test 驗證，
  三段 test 資本串接成連續 OOS 曲線；對照「看整段挑最好」上界與同期買入持有。

參數網格：買超佔量{2%,3%}×淨買日數{10,12}×持有{20,40,60}×持股數{8,10}×regime{無,45}。
⚠️ 持有期一起掃是刻意的：潛伏股要時間發動，用 20 日測會「還沒發動就出場」，
   那會是參數的錯不是策略的錯——所以讓 train 自己選，不預設答案。

全離線（讀 backtest_panel.csv.gz），不碰 FinMind 額度。
用法：python -m scripts.run_t11q_oos
輸出：reports/t11q_oos.md
⚠️ 限制：面板 ~200 檔有倖存者偏誤；未計滑價；**千張大戶同步加碼那條沒進回測**
   （面板無週頻大戶資料）。
"""
import argparse
import itertools
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.portfolio_backtest import (
    compute_regime_ok, compute_t11q_entries, load_panel_csv, run_portfolio, slice_panel,
)
from src.walkforward import bh_curve as _bh_curve, curve_metrics as _curve_metrics, make_folds

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"
OUT = ROOT / "reports" / "t11q_oos.md"

GRID_BUYRATIO = [0.02, 0.03]
GRID_MINDAYS = [10, 12]
GRID_HOLD = [20, 40, 60]
GRID_MAXPOS = [8, 10]
GRID_REGIME = [None, 45.0]


def _cfg_label(c) -> str:
    reg = "無regime" if c["regime"] is None else f"regime{int(c['regime'])}"
    return (f"買超{c['buy_ratio']:.0%}·淨買{c['min_days']}日·"
            f"持{c['maxpos']}檔·holding{c['hold']}日·{reg}")


def _grid():
    for br, md, hd, mp, rg in itertools.product(
            GRID_BUYRATIO, GRID_MINDAYS, GRID_HOLD, GRID_MAXPOS, GRID_REGIME):
        yield {"buy_ratio": br, "min_days": md, "hold": hd, "maxpos": mp, "regime": rg}


class _Cache:
    """訊號依 (buy_ratio, min_days) 快取——那是唯一貴的部分，其餘旋鈕不影響訊號。"""
    def __init__(self, panel):
        self.panel = panel
        self._ent, self._reg = {}, {}

    def entries(self, br, md):
        k = (br, md)
        if k not in self._ent:
            self._ent[k] = compute_t11q_entries(self.panel, buy_ratio=br, min_days=md)
        return self._ent[k]

    def regime(self, th):
        if th is None:
            return None
        if th not in self._reg:
            self._reg[th] = compute_regime_ok(self.panel, threshold=th)
        return self._reg[th]

    def evaluate(self, sub_panel, cfg, init_cap=1_000_000):
        return run_portfolio(
            sub_panel, self.entries(cfg["buy_ratio"], cfg["min_days"]),
            max_positions=cfg["maxpos"], hold_days=cfg["hold"], stop=None,
            regime_ok=self.regime(cfg["regime"]), init_capital=init_cap)


def _pick_best(cache, train_panel):
    best, best_m = None, None
    for cfg in _grid():
        m = cache.evaluate(train_panel, cfg).metrics
        if best is None or (m["Sharpe"], m["CAGR_%"]) > (best_m["Sharpe"], best_m["CAGR_%"]):
            best, best_m = cfg, m
    return best, best_m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    args = ap.parse_args()

    print(f"載入面板 {args.panel} …")
    panel = load_panel_csv(args.panel)
    dates = sorted({d for df in panel.values() for d in df["date"]})
    print(f"  {len(panel)} 檔、{len(dates)} 交易日（{dates[0]} ~ {dates[-1]}）")
    cache = _Cache(panel)

    folds = make_folds(dates)
    fold_rows, oos_segments = [], []
    cap = 1_000_000
    for name, tr_lo, tr_hi, te_lo, te_hi in folds:
        best, m_tr = _pick_best(cache, slice_panel(panel, lo=tr_lo, hi=tr_hi))
        res = cache.evaluate(slice_panel(panel, lo=te_lo, hi=te_hi), best, init_cap=cap)
        m_te = res.metrics
        oos_segments.append(res.equity)
        cap = m_te.get("final_equity", cap) or cap
        fold_rows.append({"fold": name, "train": f"{tr_lo}~{tr_hi}", "test": f"{te_lo}~{te_hi}",
                          "cfg_label": _cfg_label(best), "m_train": m_tr, "m_test": m_te,
                          "bh_test": _curve_metrics(_bh_curve(panel, te_lo, te_hi))})
        print(f"  {name} 挑中 {_cfg_label(best)}（train夏普 {m_tr['Sharpe']:.2f}）"
              f"→ test CAGR {m_te['CAGR_%']:+.1f}%／夏普 {m_te['Sharpe']:.2f}")

    oos_equity = pd.concat(oos_segments)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="first")].sort_index()
    oos_m = _curve_metrics(oos_equity)
    oos_trades = sum(fr["m_test"]["n_trades"] for fr in fold_rows)
    oos_bh = _curve_metrics(_bh_curve(panel, folds[0][3], folds[-1][4]))
    is_best, is_m = _pick_best(cache, panel)

    print(f"\n連續 OOS：CAGR {oos_m['CAGR_%']:+.1f}%／夏普 {oos_m['Sharpe']:.2f}"
          f"／回撤 {oos_m['MaxDD_%']:.1f}%（{oos_trades} 筆）")
    print(f"同期買入持有：CAGR {oos_bh['CAGR_%']:+.1f}%／夏普 {oos_bh['Sharpe']:.2f}"
          f"／回撤 {oos_bh['MaxDD_%']:.1f}%")
    print(f"看整段挑最好(上界)：CAGR {is_m['CAGR_%']:+.1f}%／夏普 {is_m['Sharpe']:.2f}"
          f"　{_cfg_label(is_best)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        f.write("# T11Q 潛伏吸籌 — 嚴格樣本外（walk-forward）\n\n")
        f.write(f"> 產生於 {date.today()}　·　離線面板 {len(panel)} 檔"
                f"（{dates[0]} ~ {dates[-1]}）　·　3 折 expanding-train\n\n")
        f.write("**假設**：法人持續買、但價格還在整理 → 籌碼領先價格。與 T11（已經在漲＋法人買，"
                "回測 −16%）的差別在價格條件相反。\n\n")
        f.write("## 逐折（train 挑參數 → test 驗證）\n\n")
        f.write("| 折 | train | test | 挑中參數 | train夏普 | test CAGR% | test夏普 | test回撤% | 同期買入持有CAGR% |\n")
        f.write("|:--|:--|:--|:--|--:|--:|--:|--:|--:|\n")
        for r in fold_rows:
            f.write(f"| {r['fold']} | {r['train']} | {r['test']} | {r['cfg_label']} "
                    f"| {r['m_train']['Sharpe']:.2f} | {r['m_test']['CAGR_%']:+.1f} "
                    f"| {r['m_test']['Sharpe']:.2f} | {r['m_test']['MaxDD_%']:.1f} "
                    f"| {r['bh_test']['CAGR_%']:+.1f} |\n")
        f.write("\n## 結論\n\n")
        f.write("| 指標 | 連續 OOS | 同期買入持有 | 看整段挑最好（上界） |\n|:--|--:|--:|--:|\n")
        f.write(f"| CAGR% | **{oos_m['CAGR_%']:+.1f}** | {oos_bh['CAGR_%']:+.1f} "
                f"| {is_m['CAGR_%']:+.1f} |\n")
        f.write(f"| 夏普 | **{oos_m['Sharpe']:.2f}** | {oos_bh['Sharpe']:.2f} "
                f"| {is_m['Sharpe']:.2f} |\n")
        f.write(f"| 最大回撤% | {oos_m['MaxDD_%']:.1f} | {oos_bh['MaxDD_%']:.1f} "
                f"| {is_m['MaxDD_%']:.1f} |\n")
        f.write(f"\nOOS 交易 {oos_trades} 筆；上界挑中 `{_cfg_label(is_best)}`。\n")
        f.write("\n⚠️ 限制：面板 ~200 檔有倖存者偏誤；未計滑價；**『千張大戶同步加碼』那條"
                "沒進回測**（面板只有價量+法人，大戶是週頻另存 DB）——實盤加那條的效果未經驗證。\n")
    print(f"\n✅ 報告 → {OUT}")


if __name__ == "__main__":
    main()
