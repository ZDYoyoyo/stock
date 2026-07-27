"""組合層級回測引擎 — 產出真實權益曲線與 CAGR / 最大回撤 / 夏普 (P2)。

與舊的事件研究式回測(backtest.py：每訊號期望值)不同，這裡模擬一個**有資金約束、
最多同時持 N 檔**的組合，逐日 mark-to-market 記錄權益曲線，才能算出組合層級的
CAGR、最大回撤、夏普比率——事件研究算不出這些（沒有損益路徑）。

分工（引擎純函式、不連網，可單元測試）：
  compute_t11_entries(panel, params) → {date: [(stock_id, score)]}   進場訊號（用 signals.t11_pass）
  run_portfolio(panel, entries, ...) → BacktestResult                組合模擬 + 指標
資料載入（FinMind，連網）在 scripts/run_portfolio_backtest.py。
panel 格式：{stock_id: DataFrame[date, open, close, volume, inst_net, margin_balance]}，日期升序。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .signals import FEE, FEE_DISCOUNT, TAX, T11Params, t11_pass

TRADING_DAYS = 252


@dataclass
class Trade:
    stock_id: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    ret: float          # 含成本報酬率
    pnl: float          # 含成本損益金額


@dataclass
class BacktestResult:
    equity: pd.Series                       # 每日權益（index=日期）
    trades: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        return (f"CAGR {m['CAGR_%']:+.1f}%｜最大回撤 {m['MaxDD_%']:.1f}%｜夏普 {m['Sharpe']:.2f}"
                f"｜年化波動 {m['Vol_%']:.1f}%｜勝率 {m['win_rate_%']:.0f}%"
                f"（{m['n_trades']} 筆）｜曝險 {m['exposure_%']:.0f}%")


def _buy_cost() -> float:
    return FEE * FEE_DISCOUNT


def _sell_cost() -> float:
    return FEE * FEE_DISCOUNT + TAX


def _ma20_at(closes: list[float], i: int):
    """到索引 i（含）為止的 20 日均價；不足 20 日回 None。"""
    if i + 1 < 20:
        return None
    return sum(closes[i - 19:i + 1]) / 20


def compute_t11_entries(panel: dict, params: T11Params = None) -> dict:
    """對每檔逐日跑 t11_pass（point-in-time），回傳 {date: [(stock_id, score)]}。

    score 用『連買天數 + 吃貨比重』粗排（與 live T11 評分同精神），供進場時挑強者。
    """
    params = params or T11Params()
    lb = params.lookback
    out: dict[str, list] = {}
    for sid, df in panel.items():
        df = df.sort_values("date").reset_index(drop=True)
        closes = df["close"].tolist()
        vols = df["volume"].tolist()
        nets = df["inst_net"].tolist()
        margins = df["margin_balance"].tolist() if "margin_balance" in df.columns \
            else [float("nan")] * len(df)
        dates = df["date"].tolist()
        for i in range(len(df)):
            if i + 1 < lb - params.allowed_missing:
                continue
            lo = max(0, i + 1 - lb)
            win_close = closes[lo:i + 1]
            win_vol = vols[lo:i + 1]
            win_net = nets[lo:i + 1]
            win_mg = margins[lo:i + 1]
            ma20 = _ma20_at(closes, i)
            if t11_pass(win_close, win_vol, win_net, win_mg, ma20, params):
                consec = sum(1 for v in reversed(win_net) if v > 0)  # 粗略連買
                buy_ratio = sum(win_net) / sum(win_vol) if sum(win_vol) > 0 else 0
                score = consec * 3 + buy_ratio * 100 * 2
                out.setdefault(dates[i], []).append((sid, score))
    return out


def run_portfolio(panel: dict, entries_by_date: dict, *,
                  init_capital: float = 1_000_000, max_positions: int = 5,
                  hold_days: int = 20) -> BacktestResult:
    """逐日模擬組合：訊號日收盤後掛單、隔日開盤進場、持有 hold_days 個交易日後收盤出場。

    - 最多同時持 max_positions 檔，等權（每檔目標＝當前權益 / max_positions）。
    - 逐日以收盤 mark-to-market 記錄權益。含手續費+證交稅。
    - entries_by_date: {訊號日: [(stock_id, score)]}；同日多檔依 score 高者優先。
    """
    # 全體交易日（各檔日期聯集，升序）+ 每檔 date→(open,close) 快速查表
    all_dates = sorted({d for df in panel.values() for d in df["date"]})
    px = {}
    for sid, df in panel.items():
        df = df.sort_values("date")
        px[sid] = {r.date: (r.open, r.close) for r in df.itertuples()}

    cash = init_capital
    positions: dict[str, dict] = {}   # sid -> {shares, entry_price, entry_date, exit_idx}
    pending: list = []                # 待進場 [(sid,)]，訊號日收盤產生、隔日開盤執行
    trades: list[Trade] = []
    equity_curve = []
    days_with_pos = 0

    for di, d in enumerate(all_dates):
        # 1) 開盤：執行昨日掛單（進場）
        if pending:
            slots = max_positions - len(positions)
            if slots > 0:
                target = (cash + _holdings_value(positions, px, d, use="open")) / max_positions
                for sid in pending:
                    if slots <= 0 or sid in positions:
                        continue
                    quote = px.get(sid, {}).get(d)
                    if not quote or quote[0] <= 0:
                        continue
                    entry = quote[0]
                    budget = min(target, cash)
                    shares = int(budget / (entry * (1 + _buy_cost())))
                    if shares <= 0:
                        continue
                    cost = shares * entry * (1 + _buy_cost())
                    if cost > cash:
                        continue
                    cash -= cost
                    positions[sid] = {"shares": shares, "entry_price": entry,
                                      "entry_date": d, "exit_idx": di + hold_days}
                    slots -= 1
            pending = []

        # 2) 收盤：到期出場
        for sid in list(positions.keys()):
            pos = positions[sid]
            if di >= pos["exit_idx"]:
                quote = px.get(sid, {}).get(d)
                if not quote or quote[1] <= 0:
                    continue
                exit_p = quote[1]
                proceeds = pos["shares"] * exit_p * (1 - _sell_cost())
                cost_basis = pos["shares"] * pos["entry_price"] * (1 + _buy_cost())
                cash += proceeds
                ret = proceeds / cost_basis - 1
                trades.append(Trade(sid, pos["entry_date"], d, pos["entry_price"],
                                    exit_p, pos["shares"], ret, proceeds - cost_basis))
                del positions[sid]

        # 3) 收盤 mark-to-market → 記錄權益
        equity = cash + _holdings_value(positions, px, d, use="close")
        equity_curve.append((d, equity))
        if positions:
            days_with_pos += 1

        # 4) 收盤後掛今日訊號 → 隔日開盤進場（避免用當日資訊當日成交的未來函數）
        if di < len(all_dates) - 1 and len(positions) < max_positions:
            cands = sorted(entries_by_date.get(d, []), key=lambda x: x[1], reverse=True)
            pending = [sid for sid, _ in cands if sid not in positions]

    eq = pd.Series({d: v for d, v in equity_curve})
    metrics = _metrics(eq, trades, days_with_pos, len(all_dates))
    return BacktestResult(equity=eq, trades=trades, metrics=metrics)


def _holdings_value(positions, px, d, use="close") -> float:
    idx = 0 if use == "open" else 1
    total = 0.0
    for sid, pos in positions.items():
        quote = px.get(sid, {}).get(d)
        price = quote[idx] if quote and quote[idx] > 0 else pos["entry_price"]
        total += pos["shares"] * price
    return total


def _metrics(equity: pd.Series, trades, days_with_pos: int, n_days: int) -> dict:
    if equity.empty or len(equity) < 2:
        return {"CAGR_%": 0, "MaxDD_%": 0, "Sharpe": 0, "Vol_%": 0,
                "win_rate_%": 0, "n_trades": len(trades), "exposure_%": 0,
                "final_equity": float(equity.iloc[-1]) if len(equity) else 0}
    rets = equity.pct_change().dropna()
    years = len(equity) / TRADING_DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    vol = rets.std() * math.sqrt(TRADING_DAYS)
    sharpe = (rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0
    # 最大回撤：權益 / 歷史高點 − 1 的最小值
    dd = (equity / equity.cummax() - 1).min()
    wins = [t for t in trades if t.ret > 0]
    return {
        "CAGR_%": round(cagr * 100, 2),
        "MaxDD_%": round(dd * 100, 2),
        "Sharpe": round(sharpe, 2),
        "Vol_%": round(vol * 100, 2),
        "win_rate_%": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "n_trades": len(trades),
        "exposure_%": round(days_with_pos / n_days * 100, 1) if n_days else 0,
        "final_equity": round(float(equity.iloc[-1]), 0),
    }
