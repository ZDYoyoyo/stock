"""風控試算 CLI — 給一檔股票算停損/停利/建議張數。

用法（專案根目錄）：
    python -m scripts.risk_calc --stock 2206 --account 1000000 --risk 1.5
    python -m scripts.risk_calc --stock 6669 --account 500000 --risk 1 --entry 5100 --atr-mult 2.5
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk import plan_trade


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--account", type=float, required=True, help="總資金")
    ap.add_argument("--risk", type=float, default=1.0, help="單筆風險%（建議0.5~2）")
    ap.add_argument("--entry", type=float, default=None, help="進場價（預設用最新收盤）")
    ap.add_argument("--atr-mult", type=float, default=2.0, help="停損=進場-此倍數×ATR")
    ap.add_argument("--target-r", type=float, default=2.0, help="停利=幾倍風險")
    args = ap.parse_args()

    p = plan_trade(args.stock, args.account, risk_pct=args.risk, entry=args.entry,
                   atr_mult=args.atr_mult, target_r=args.target_r)

    print(f"\n=== {args.stock} 交易風控計畫 ===")
    if "error" in p or "note" in p:
        print(p.get("error") or p.get("note"))
        for k in ("entry", "stop", "atr"):
            if k in p:
                print(f"  {k}: {p[k]}")
        return
    print(f"進場價        : {p['entry']}")
    print(f"ATR(波動)     : {p['atr']}")
    print(f"停損價        : {p['stop']}  ({p['stop_%']}%)")
    print(f"停利價        : {p['target']}  (+{p['target_%']}%)  風險報酬比 {p['risk_reward']}")
    cap = "（受單檔部位上限縮量）" if p.get("capped_by_position_limit") else ""
    print(f"建議張數      : {p['suggest_lots']} 張 {cap}")
    print(f"部位金額      : {p['position_value']:,}  (佔資金 {p['position_%']}%)")
    print(f"觸停損虧損    : {p['risk_amount']:,}  (佔資金 {p['risk_%']}%)")
    print("\n紀律：進場同時掛停損；停利可改移動停損讓獲利奔跑。")


if __name__ == "__main__":
    main()
