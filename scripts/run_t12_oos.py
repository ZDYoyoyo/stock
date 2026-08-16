"""T12 月營收動能 嚴格樣本外（walk-forward out-of-sample）驗證。

痛點：T12 的「持8檔 CAGR +22.5%／夏普 0.91」是**看整段面板**調出來的（in-sample 挑持股數）。
看整段挑最好＝資料窺探(data-snooping)，會高估 edge。這支比照 `run_t16_oos` 做真正的嚴格樣本外：
**只用 train 期挑參數 → 只在沒看過的 test 期驗證**，看基本面動能 edge 撐不撐得住。

方法（expanding-train walk-forward，3 折，共用 `src/walkforward`）：
  切點 25/50/75%。F1 train[0,25%]→test(25,50%]；F2 train[0,50%]→test(50,75%]；
  F3 train[0,75%]→test(75,100%]。每折在 train 對參數網格挑『夏普最高』者，套到 test。
  三段 test 以**帶進帶出的資本串接**成一條連續 OOS 權益曲線 → 算真實 CAGR/回撤/夏普。
  對照：①「看整段 in-sample 挑最好」＝過擬合上界；②同 OOS 期間買入持有。

參數網格：持股數{5,8,10}×停損{無,8%}×regime{無,45}×最低YoY{20,30}，持有 20 日固定。
⚠️ 無前視偏誤：`compute_t12_entries` 只採 **pub_date<=交易日** 的月營收（用實際公布日對齊），
   regime 也是 trailing。

全離線（讀 backtest_panel + revenue_panel），不碰 FinMind 額度。
用法：python -m scripts.run_t12_oos [--hold 20]
輸出：reports/t12_oos.md
⚠️ 限制：面板 ~200 檔仍有倖存者偏誤；未計滑價。
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
    compute_regime_ok, compute_t12_entries, load_panel_csv, load_revenue_panel,
    run_portfolio, slice_panel,
)
from src.walkforward import bh_curve, curve_metrics, make_folds

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"
REV_PANEL = ROOT / "data" / "history" / "revenue_panel.csv.gz"
OUT = ROOT / "reports" / "t12_oos.md"

# 參數網格（旋鈕含意見既有結論：T12 甜蜜點持8檔；in-sample 說 regime 對 T12 每組都變差
# → 仍放進網格讓 train 自己決定，才是誠實的樣本外）
GRID_MAXPOS = [5, 8, 10]
GRID_STOP = [None, ("pct", 0.08)]
GRID_REGIME = [None, 45.0]
GRID_MIN_YOY = [20.0, 30.0]        # 最新月營收 YoY 門檻（live 預設 30）


def _cfg_label(cfg) -> str:
    stop = "無停損" if cfg["stop"] is None else f"停損{int(cfg['stop'][1] * 100)}%"
    reg = "無regime" if cfg["regime"] is None else f"regime{int(cfg['regime'])}"
    return f"持{cfg['maxpos']}·{stop}·{reg}·YoY≥{int(cfg['min_yoy'])}"


def _grid():
    for mp, st, rg, yy in itertools.product(GRID_MAXPOS, GRID_STOP, GRID_REGIME, GRID_MIN_YOY):
        yield {"maxpos": mp, "stop": st, "regime": rg, "min_yoy": yy}


class _Cache:
    """訊號/regime 依 (min_yoy)/(threshold) 快取，避免重算（全 point-in-time）。"""

    def __init__(self, panel, rev_panel, hold):
        self.panel, self.rev_panel, self.hold = panel, rev_panel, hold
        self._ent, self._reg = {}, {}

    def entries(self, min_yoy):
        if min_yoy not in self._ent:
            self._ent[min_yoy] = compute_t12_entries(self.panel, self.rev_panel, min_yoy=min_yoy)
        return self._ent[min_yoy]

    def regime(self, th):
        if th is None:
            return None
        if th not in self._reg:
            self._reg[th] = compute_regime_ok(self.panel, threshold=th)
        return self._reg[th]

    def evaluate(self, sub_panel, cfg, init_cap=1_000_000):
        return run_portfolio(
            sub_panel, self.entries(cfg["min_yoy"]),
            max_positions=cfg["maxpos"], hold_days=self.hold,
            stop=cfg["stop"], regime_ok=self.regime(cfg["regime"]),
            init_capital=init_cap)


def _pick_best(cache, train_panel):
    """在 train 面板對整個網格挑『夏普最高』(同分取 CAGR 高)。回 (cfg, train_metrics)。"""
    best, best_m = None, None
    for cfg in _grid():
        m = cache.evaluate(train_panel, cfg).metrics
        key = (m["Sharpe"], m["CAGR_%"])
        if best is None or key > (best_m["Sharpe"], best_m["CAGR_%"]):
            best, best_m = cfg, m
    return best, best_m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--rev-panel", default=str(REV_PANEL))
    ap.add_argument("--hold", type=int, default=20)
    args = ap.parse_args()

    print(f"載入面板 {args.panel} …")
    panel = load_panel_csv(args.panel)
    rev_panel = load_revenue_panel(args.rev_panel)
    dates = sorted({d for df in panel.values() for d in df["date"]})
    print(f"  {len(panel)} 檔價量、{len(rev_panel)} 檔月營收、{len(dates)} 交易日"
          f"（{dates[0]} ~ {dates[-1]}）")
    cache = _Cache(panel, rev_panel, args.hold)
    folds = make_folds(dates)

    # --- walk-forward：每折 train 選參數 → test 驗證；資本串接成連續 OOS 曲線 ---
    fold_rows, oos_segments = [], []
    cap = 1_000_000
    for name, tr_lo, tr_hi, te_lo, te_hi in folds:
        best, m_tr = _pick_best(cache, slice_panel(panel, lo=tr_lo, hi=tr_hi))
        res = cache.evaluate(slice_panel(panel, lo=te_lo, hi=te_hi), best, init_cap=cap)
        m_te = res.metrics
        oos_segments.append(res.equity)
        cap = m_te.get("final_equity", cap) or cap
        bh = curve_metrics(bh_curve(panel, te_lo, te_hi))
        fold_rows.append({
            "fold": name, "train": f"{tr_lo}~{tr_hi}", "test": f"{te_lo}~{te_hi}",
            "挑中參數": _cfg_label(best), "train夏普": m_tr["Sharpe"],
            "testCAGR%": m_te["CAGR_%"], "test夏普": m_te["Sharpe"],
            "test回撤%": m_te["MaxDD_%"], "同期買入持有CAGR%": bh["CAGR_%"]})
        print(f"  {name} train[{tr_lo}~{tr_hi}] 挑中 {_cfg_label(best)}"
              f"（train夏普{m_tr['Sharpe']:.2f}）→ test CAGR {m_te['CAGR_%']:+.1f}%"
              f"／夏普 {m_te['Sharpe']:.2f}")

    # 串接三段 test 成連續 OOS 曲線
    oos = pd.concat([s for s in oos_segments if s is not None and len(s)]) if oos_segments else None
    oos_m = curve_metrics(oos)
    oos_lo, oos_hi = folds[0][3], folds[-1][4]
    bh_m = curve_metrics(bh_curve(panel, oos_lo, oos_hi))

    # 對照：看整段 in-sample 挑最好（過擬合上界）
    is_best, is_m = _pick_best(cache, slice_panel(panel, lo=oos_lo, hi=oos_hi))

    lines = [
        "# T12 月營收動能 — 嚴格樣本外（walk-forward）驗證", "",
        f"> 面板 {len(panel)} 檔／{len(dates)} 交易日；OOS 區間 {oos_lo} ~ {oos_hi}；"
        f"持有 {args.hold} 日　·　{date.today().isoformat()}", "",
        "**方法**：expanding-train 3 折，每折只用 train 期挑參數（夏普最高）→ 套到沒看過的 test 期，"
        "三段 test 資本串接成連續 OOS 曲線。訊號用**營收公布日**對齊（pub_date≤交易日）→ 無前視偏誤。", "",
        "## 逐折結果", "", pd.DataFrame(fold_rows).to_markdown(index=False), "",
        "## 連續樣本外 vs 對照", "",
        pd.DataFrame([
            {"策略": "T12 walk-forward OOS", "CAGR%": oos_m["CAGR_%"], "夏普": oos_m["Sharpe"],
             "最大回撤%": oos_m["MaxDD_%"], "年化波動%": oos_m["Vol_%"]},
            {"策略": "同期買入持有", "CAGR%": bh_m["CAGR_%"], "夏普": bh_m["Sharpe"],
             "最大回撤%": bh_m["MaxDD_%"], "年化波動%": bh_m["Vol_%"]},
            {"策略": f"看整段挑最好（過擬合上界·{_cfg_label(is_best)}）", "CAGR%": is_m["CAGR_%"],
             "夏普": is_m["Sharpe"], "最大回撤%": is_m["MaxDD_%"], "年化波動%": is_m["Vol_%"]},
        ]).to_markdown(index=False), "",
        "## 判讀", "",
        f"- 連續 OOS：CAGR **{oos_m['CAGR_%']:+.2f}%**／夏普 **{oos_m['Sharpe']:.2f}**／"
        f"回撤 {oos_m['MaxDD_%']:.2f}%；同期買入持有 {bh_m['CAGR_%']:+.2f}%／夏普 {bh_m['Sharpe']:.2f}。",
        f"- 「看整段挑最好」{is_m['CAGR_%']:+.2f}%／夏普 {is_m['Sharpe']:.2f} 是**上界**，"
        "OOS 與它的落差＝資料窺探灌水的幅度。",
        "- 逐折參數是否穩定（各折挑中同一組＝訊號穩；跳來跳去＝參數敏感、實盤難複製）。", "",
        "⚠️ 面板 ~200 檔有倖存者偏誤；未計滑價。研究用途，非投資建議。", ""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {OUT}")
    print(f"   walk-forward OOS：CAGR {oos_m['CAGR_%']:+.2f}%｜回撤 {oos_m['MaxDD_%']:.2f}%"
          f"｜夏普 {oos_m['Sharpe']:.2f}（同期買入持有 CAGR {bh_m['CAGR_%']:+.2f}%"
          f"／夏普 {bh_m['Sharpe']:.2f}）")


if __name__ == "__main__":
    main()
