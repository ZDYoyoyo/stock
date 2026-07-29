"""③ T16 嚴格樣本外（walk-forward out-of-sample）驗證。

痛點：T16 的 +32% CAGR／夏普 0.99 是**看整段面板**調出來的（run_t16_tune 於 in-sample
挑持股數/停損/regime）。看整段挑最好＝資料窺探(data-snooping)，會高估 edge。這支做真正的
嚴格樣本外：**只用 train 期挑參數 → 只在沒看過的 test 期驗證**，看動能 edge 是否撐得住。

方法（expanding-train walk-forward，3 折）：
  切點 25/50/75%。F1 train[0,25%]→test(25,50%]；F2 train[0,50%]→test(50,75%]；
  F3 train[0,75%]→test(75,100%]。每折在 train 對參數網格挑『夏普最高』者，套到 test。
  三段 test 以**帶進帶出的資本串接**成一條連續 OOS 權益曲線 → 算真實 CAGR/回撤/夏普。
  對照：①「看整段 in-sample 挑最好」= 過擬合上界；②同 OOS 期間買入持有。

參數網格：持股數{5,8,10}×停損{無,8%}×regime{無,45}×lookback{10,20}，持有 20 日固定。
訊號/regime 皆 point-in-time（compute_t16_entries 用 trailing，無前視）。

全離線（讀 backtest_panel.csv.gz），不碰 FinMind 額度。
用法：python -m scripts.run_t16_oos [--panel ...] [--hold 20]
輸出：reports/t16_oos.md
⚠️ 限制：面板 ~200 檔仍有倖存者偏誤（動能策略尤其樂觀）；未計滑價。
"""
import argparse
import itertools
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.portfolio_backtest import (
    TRADING_DAYS, compute_regime_ok, compute_t16_entries,
    load_panel_csv, run_portfolio, slice_panel,
)

PANEL = ROOT / "data" / "history" / "backtest_panel.csv.gz"

# 參數網格（旋鈕含意見 t16_tune：停損傷動能、分散降回撤、regime 有用）
GRID_MAXPOS = [5, 8, 10]
GRID_STOP = [None, ("pct", 0.08)]
GRID_REGIME = [None, 45.0]        # None＝不濾；45＝市場廣度≥45% 才開新倉
GRID_LOOKBACK = [10, 20]


def _cfg_label(cfg) -> str:
    stop = "無停損" if cfg["stop"] is None else f"停損{int(cfg['stop'][1]*100)}%"
    reg = "無regime" if cfg["regime"] is None else f"regime{int(cfg['regime'])}"
    return f"持{cfg['maxpos']}·{stop}·{reg}·LB{cfg['lookback']}"


def _grid():
    for mp, st, rg, lb in itertools.product(GRID_MAXPOS, GRID_STOP, GRID_REGIME, GRID_LOOKBACK):
        yield {"maxpos": mp, "stop": st, "regime": rg, "lookback": lb}


def _curve_metrics(equity: pd.Series) -> dict:
    """由連續權益曲線算 CAGR/最大回撤/夏普/年化波動（串接 OOS 曲線用）。"""
    if equity is None or len(equity) < 2:
        return {"CAGR_%": 0.0, "MaxDD_%": 0.0, "Sharpe": 0.0, "Vol_%": 0.0}
    rets = equity.pct_change().dropna()
    years = len(equity) / TRADING_DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    vol = rets.std() * math.sqrt(TRADING_DAYS)
    sharpe = (rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0
    dd = (equity / equity.cummax() - 1).min()
    return {"CAGR_%": round(cagr * 100, 2), "MaxDD_%": round(dd * 100, 2),
            "Sharpe": round(sharpe, 2), "Vol_%": round(vol * 100, 2)}


class _Cache:
    """訊號/regime 依 (lookback)/(threshold) 快取，避免重算（全 point-in-time）。"""
    def __init__(self, panel, hold):
        self.panel, self.hold = panel, hold
        self._ent, self._reg = {}, {}

    def entries(self, lookback):
        if lookback not in self._ent:
            self._ent[lookback] = compute_t16_entries(self.panel, lookback=lookback)
        return self._ent[lookback]

    def regime(self, th):
        if th is None:
            return None
        if th not in self._reg:
            self._reg[th] = compute_regime_ok(self.panel, threshold=th)
        return self._reg[th]

    def evaluate(self, sub_panel, cfg, init_cap=1_000_000):
        return run_portfolio(
            sub_panel, self.entries(cfg["lookback"]),
            max_positions=cfg["maxpos"], hold_days=self.hold,
            stop=cfg["stop"], regime_ok=self.regime(cfg["regime"]),
            init_capital=init_cap)


def _bh_curve(panel: dict, lo: str, hi: str, init_cap=1_000_000) -> pd.Series:
    """等權買入持有權益曲線（區間 [lo,hi]），供 OOS 基準對照。"""
    sub = slice_panel(panel, lo=lo, hi=hi)
    norm = {}
    for sid, df in sub.items():
        df = df.sort_values("date")
        c0 = df["close"].iloc[0] if len(df) else 0
        if c0 > 0:
            norm[sid] = dict(zip(df["date"], df["close"] / c0))
    all_dates = sorted({d for m in norm.values() for d in m})
    vals = []
    for d in all_dates:
        xs = [m[d] for m in norm.values() if d in m]
        vals.append(sum(xs) / len(xs) if xs else 1.0)
    return pd.Series([v * init_cap for v in vals], index=all_dates)


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
    ap.add_argument("--hold", type=int, default=20)
    args = ap.parse_args()

    print(f"載入面板 {args.panel} …")
    panel = load_panel_csv(args.panel)
    dates = sorted({d for df in panel.values() for d in df["date"]})
    n = len(dates)
    print(f"  {len(panel)} 檔、{n} 交易日（{dates[0]} ~ {dates[-1]}）")
    cache = _Cache(panel, args.hold)

    # 切點（25/50/75%）
    i1, i2, i3 = n // 4, n // 2, 3 * n // 4
    folds = [
        ("F1", dates[0], dates[i1], dates[i1 + 1], dates[i2]),
        ("F2", dates[0], dates[i2], dates[i2 + 1], dates[i3]),
        ("F3", dates[0], dates[i3], dates[i3 + 1], dates[-1]),
    ]

    # --- walk-forward：每折 train 選參數 → test 驗證；資本串接成連續 OOS 曲線 ---
    fold_rows, oos_segments = [], []
    cap = 1_000_000
    for name, tr_lo, tr_hi, te_lo, te_hi in folds:
        train_panel = slice_panel(panel, lo=tr_lo, hi=tr_hi)
        test_panel = slice_panel(panel, lo=te_lo, hi=te_hi)
        best, m_tr = _pick_best(cache, train_panel)
        res = cache.evaluate(test_panel, best, init_cap=cap)
        m_te = res.metrics
        oos_segments.append(res.equity)
        cap = m_te.get("final_equity", cap) or cap
        bh = _curve_metrics(_bh_curve(panel, te_lo, te_hi))
        fold_rows.append({
            "fold": name, "train": f"{tr_lo}~{tr_hi}", "test": f"{te_lo}~{te_hi}",
            "cfg": best, "cfg_label": _cfg_label(best), "m_train": m_tr,
            "m_test": m_te, "bh_test": bh})
        print(f"  {name} train[{tr_lo}~{tr_hi}] 挑中 {_cfg_label(best)}"
              f"（train夏普{m_tr['Sharpe']:.2f}）→ test CAGR {m_te['CAGR_%']:+.1f}%"
              f"／夏普 {m_te['Sharpe']:.2f}")

    # 串接三段 test → 連續 OOS 權益曲線
    oos_equity = pd.concat(oos_segments)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="first")].sort_index()
    oos_m = _curve_metrics(oos_equity)
    oos_trades = sum(fr["m_test"]["n_trades"] for fr in fold_rows)
    # OOS 全期買入持有基準
    oos_bh = _curve_metrics(_bh_curve(panel, folds[0][3], folds[-1][4]))

    # --- 對照：看整段 in-sample 挑最好（過擬合上界）---
    is_best, is_m = _pick_best(cache, panel)

    # --- 報告 ---
    today = date.today().isoformat()

    def _hdr(extra=""):
        return [f"| 設定 | CAGR | 最大回撤 | 夏普 | 勝率 | 交易數 |{extra}",
                "|---|---|---|---|---|---|" + ("---|" if extra else "")]

    def _row(label, m, extra=None):
        base = (f"| {label} | {m['CAGR_%']:+.2f}% | {m['MaxDD_%']:.2f}% | {m['Sharpe']:.2f} | "
                f"{m.get('win_rate_%', 0):.0f}% | {m.get('n_trades', 0)} |")
        return base + (f" {extra} |" if extra is not None else "")

    L = [f"# ③ T16 嚴格樣本外（walk-forward OOS）驗證 — {today}\n",
         f"- 面板 {len(panel)} 檔、{n} 交易日（{dates[0]} ~ {dates[-1]}）；持有 {args.hold} 日固定"
         "（⚠️ 倖存者偏誤，動能策略尤其樂觀；未計滑價）",
         "- 方法：expanding-train walk-forward 3 折，**train 挑夏普最高參數 → test 驗證**，"
         "三段 test 資本串接成連續 OOS 曲線。網格：持股{5,8,10}×停損{無,8%}×regime{無,45}×lookback{10,20}。\n",

         "## 🎯 頭條：連續 walk-forward OOS（沒看過的資料）",
         "| 策略 | CAGR | 最大回撤 | 夏普 | 年化波動 |",
         "|---|---|---|---|---|",
         f"| **T16 walk-forward OOS** | {oos_m['CAGR_%']:+.2f}% | {oos_m['MaxDD_%']:.2f}% | "
         f"{oos_m['Sharpe']:.2f} | {oos_m['Vol_%']:.2f}% |",
         f"| 同期買入持有 | {oos_bh['CAGR_%']:+.2f}% | {oos_bh['MaxDD_%']:.2f}% | "
         f"{oos_bh['Sharpe']:.2f} | {oos_bh['Vol_%']:.2f}% |",
         f"\n> OOS 期間 {folds[0][3]} ~ {folds[-1][4]}；OOS 交易數合計 {oos_trades} 筆。\n",

         "## 逐折：train 挑參數 → test 驗證",
         "| 折 | test 期間 | train挑中 | train夏普 | **test CAGR** | test回撤 | test夏普 | 同期買入持有CAGR |",
         "|---|---|---|---|---|---|---|---|"]
    for fr in fold_rows:
        L.append(f"| {fr['fold']} | {fr['test']} | {fr['cfg_label']} | "
                 f"{fr['m_train']['Sharpe']:.2f} | **{fr['m_test']['CAGR_%']:+.2f}%** | "
                 f"{fr['m_test']['MaxDD_%']:.2f}% | {fr['m_test']['Sharpe']:.2f} | "
                 f"{fr['bh_test']['CAGR_%']:+.2f}% |")

    L += ["\n## 對照：看整段 in-sample 挑最好（過擬合上界）",
          _hdr()[0], _hdr()[1],
          _row(f"整段最佳＝{_cfg_label(is_best)}", is_m),
          "\n> 這是『事後諸葛』能挑到的最好組態，代表 in-sample 樂觀上界；"
          "OOS 頭條若明顯低於它 → 選參數過程有樂觀偏誤（正常，重點是 OOS 是否仍為正 edge）。\n",

          "## 判讀",
          f"- **edge 是否存活**：OOS CAGR {oos_m['CAGR_%']:+.2f}%／夏普 {oos_m['Sharpe']:.2f} "
          f"vs 同期買入持有 {oos_bh['CAGR_%']:+.2f}%／{oos_bh['Sharpe']:.2f} → "
          f"{'✅ OOS 仍勝買入持有，動能 edge 存活（打折但真實）' if (oos_m['Sharpe'] > oos_bh['Sharpe'] and oos_m['CAGR_%'] > 0) else '⚠️ OOS 未穩定勝出，in-sample edge 恐含過擬合，實盤須保守'}。",
          "- **逐折一致性**：若三折 test CAGR 方向一致（多為正）→ 穩健；若某折大負 → 動能在該市況失效（如急殺/轉空）。",
          "- **選參數穩定性**：各折挑中的組態若相近（如都偏『分散+無停損』）→ 旋鈕穩定；若跳來跳去 → 訊號對參數敏感、脆弱。",
          "\n## 限制",
          "- 面板 ~200 檔、倖存者偏誤（實際 OOS edge 會再小些）；未計滑價。",
          "- 3 折、expanding train；初折 train 期較短（~1年），動能參數估計雜訊較大。",
          "- 網格為離散、只掃主要旋鈕；未做巢狀交叉驗證。"]

    out = ROOT / "reports" / "t16_oos.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")
    print(f"   walk-forward OOS：CAGR {oos_m['CAGR_%']:+.2f}%｜回撤 {oos_m['MaxDD_%']:.2f}%｜夏普 {oos_m['Sharpe']:.2f}"
          f"（同期買入持有 CAGR {oos_bh['CAGR_%']:+.2f}%／夏普 {oos_bh['Sharpe']:.2f}）")


if __name__ == "__main__":
    main()
