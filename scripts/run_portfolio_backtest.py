"""P2 — 組合層級回測（真實權益曲線 → CAGR/最大回撤/夏普）。

用 FinMind 逐檔抓多年歷史組成面板，跑 src/portfolio_backtest 的組合模擬。
訊號＝真正的 T11（signals.t11_pass，point-in-time），不再是簡化的法人連買。

用法：
    python -m scripts.run_portfolio_backtest --universe 15 --start 2023-01-01 \
        --max-pos 5 --hold 20 [--with-margin]
輸出：reports/portfolio_backtest.md
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.config import T11 as T11CFG
from src.db import connect
from src.finmind_client import fetch
from src.portfolio_backtest import compute_t11_entries, run_portfolio
from src.signals import T11Params


def _pick_universe(n: int):
    """DB 流動性前 n 檔（收盤>10），回傳 [(sid, market)]。
    ⚠️ 用當前 DB 存活股，含倖存者偏誤（P5 會處理下市股）。"""
    with connect() as conn:
        px = pd.read_sql("SELECT stock_id, close, volume FROM price", conn)
        info = pd.read_sql("SELECT stock_id, type FROM stock_info", conn)
    last_close = px.groupby("stock_id")["close"].last()
    avgvol = px.groupby("stock_id")["volume"].mean()
    mkt = dict(zip(info["stock_id"], info["type"]))
    cand = [(sid, mkt.get(sid, "twse")) for sid in avgvol.index if last_close.get(sid, 0) >= 10]
    cand.sort(key=lambda x: avgvol.get(x[0], 0), reverse=True)
    return cand[:n]


def _load_panel_stock(sid: str, market: str, start: str, with_margin: bool):
    """組單檔面板：date, open, close, volume(張), inst_net(張), margin_balance(張)。"""
    px = fetch("TaiwanStockPrice", start_date=start, data_id=sid)
    time.sleep(0.3)
    if not px:
        return None
    dfp = pd.DataFrame(px)[["date", "open", "close", "Trading_Volume"]]
    dfp = dfp.rename(columns={"Trading_Volume": "volume"})
    dfp["volume"] = dfp["volume"] / 1000  # 股→張

    inst = fetch("TaiwanStockInstitutionalInvestorsBuySell", start_date=start, data_id=sid)
    time.sleep(0.3)
    # 市場自適應：上市看投信、上櫃看外資（與 T11 一致）
    want_foreign = (market == "tpex")
    net_by_date = {}
    for d in inst:
        nm = d.get("name", "")
        net = (d.get("buy", 0) - d.get("sell", 0)) / 1000
        is_f = nm in ("Foreign_Investor", "Foreign_Dealer_Self")
        is_t = nm == "Investment_Trust"
        if (want_foreign and is_f) or (not want_foreign and is_t):
            net_by_date[d["date"]] = net_by_date.get(d["date"], 0) + net
    dfp["inst_net"] = dfp["date"].map(net_by_date).fillna(0)

    if with_margin:
        mg = fetch("TaiwanStockMarginPurchaseShortSale", start_date=start, data_id=sid)
        time.sleep(0.3)
        mb = {d["date"]: d.get("MarginPurchaseTodayBalance") for d in mg}
        dfp["margin_balance"] = dfp["date"].map(mb)
    else:
        dfp["margin_balance"] = float("nan")

    return dfp.sort_values("date").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=15)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--max-pos", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--with-margin", action="store_true",
                    help="加抓融資餘額做 T11 籌碼乾淨濾網（多一輪網路、較慢）")
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"universe {len(universe)} 檔，起始 {args.start}，"
          f"最多持 {args.max_pos} 檔、持有 {args.hold} 日"
          f"{'、含融資濾網' if args.with_margin else '、略過融資濾網'}")

    panel = {}
    for sid, market in universe:
        try:
            df = _load_panel_stock(sid, market, args.start, args.with_margin)
            if df is not None and len(df) >= 40:
                panel[sid] = df
                print(f"  ✓ {sid} ({'外' if market == 'tpex' else '投'}) {len(df)} 日")
        except Exception as e:
            print(f"  ✗ {sid} 失敗 {e}")

    if not panel:
        print("無有效資料，結束")
        return

    params = T11Params.from_config(T11CFG)
    entries = compute_t11_entries(panel, params)
    n_sig = sum(len(v) for v in entries.values())
    print(f"\nT11 訊號日 {len(entries)} 天、共 {n_sig} 檔次")

    res = run_portfolio(panel, entries, init_capital=args.capital,
                        max_positions=args.max_pos, hold_days=args.hold)
    m = res.metrics
    print("\n===== 組合回測結果 =====")
    print(res.summary())

    # 買入持有基準（等權全 universe，粗略）：拿每檔期末/期初平均
    bh = []
    for df in panel.values():
        c = df["close"].tolist()
        if c and c[0] > 0:
            bh.append(c[-1] / c[0] - 1)
    bh_ret = sum(bh) / len(bh) * 100 if bh else 0

    today = date.today().isoformat()
    period = f"{min(res.equity.index)}~{max(res.equity.index)}"
    lines = [
        f"# 組合回測（真實權益曲線）— {today}\n",
        f"- universe：DB 流動性前 {len(panel)} 檔（⚠️含倖存者偏誤）；期間 {period}",
        f"- 策略：真正的 T11 吸貨（signals.t11_pass, point-in-time）"
        f"{'＋融資濾網' if args.with_margin else '（略過融資濾網）'}",
        f"- 組合：初始 {args.capital:,.0f}、最多同時持 {args.max_pos} 檔（等權）、持有 {args.hold} 日；含手續費+證交稅\n",
        "## 關鍵績效指標",
        "| 指標 | 值 |",
        "|---|---|",
        f"| CAGR（年化報酬） | **{m['CAGR_%']:+.2f}%** |",
        f"| 最大回撤 MaxDD | **{m['MaxDD_%']:.2f}%** |",
        f"| 夏普比率 Sharpe | **{m['Sharpe']:.2f}** |",
        f"| 年化波動 | {m['Vol_%']:.2f}% |",
        f"| 勝率 | {m['win_rate_%']:.0f}%（{m['n_trades']} 筆） |",
        f"| 平均曝險 | {m['exposure_%']:.0f}% |",
        f"| 期末權益 | {m['final_equity']:,.0f} |",
        f"| 參考：universe 等權買入持有 | {bh_ret:+.1f}% |\n",
        "## 限制",
        "- universe 用當前存活股挑選 → 倖存者偏誤（P5 修）。",
        "- 固定持有 H 日、無停損（P3 加）；未計滑價。",
        "- 期間非樣本外（P4 加 train/test）。",
    ]
    out = ROOT / "reports" / "portfolio_backtest.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 報告 → {out}")


if __name__ == "__main__":
    main()
