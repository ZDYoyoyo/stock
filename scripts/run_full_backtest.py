"""#2 完整回測 — 擴大universe、多年歷史，嚴謹比對『訊號 vs 任意日基準』。

- universe：從 DB 挑最具流動性的 N 檔（含上市/上櫃），代表可交易標的。
- 每檔用 FinMind 逐檔抓多年歷史（≈回補歷史），市場自適應投信/外資。
- 比較 baseline(任意日) / A(法人連買) / D(連買+抗跌+營收) 在多持有天數的期望值。
- 輸出 reports/backtest_validation.md（可入庫的驗證報告）。

背景執行：
    python -m scripts.run_full_backtest --universe 60 --start 2022-01-01
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.backtest import _load_stock, _revenue_yoy_lookup, _round_trip_return
from src.db import connect

HOLDS = [5, 10, 20, 40]
K, MOM = 4, 10


def _pick_universe(n: int):
    """從 DB 挑流動性最高的 n 檔（收盤>10 濾雞蛋水餃股），回傳 [(sid, market)]。"""
    with connect() as conn:
        px = pd.read_sql("SELECT stock_id, close, volume FROM price", conn)
        info = pd.read_sql("SELECT stock_id, type FROM stock_info", conn)
    last_close = px.groupby("stock_id")["close"].last()
    avgvol = px.groupby("stock_id")["volume"].mean()
    mkt = dict(zip(info["stock_id"], info["type"]))
    cand = [(sid, mkt.get(sid, "twse")) for sid in avgvol.index
            if last_close.get(sid, 0) >= 10]
    cand.sort(key=lambda x: avgvol.get(x[0], 0), reverse=True)
    return cand[:n]


def _collect(sid, investor, start):
    df = _load_stock(sid, start)
    if df.empty or len(df) < 80:
        return None
    yoy = _revenue_yoy_lookup(sid, start)
    col = "trust_net" if investor == "trust" else "foreign_net"
    net, op, cl, dt = (df[col].tolist(), df["open"].tolist(),
                       df["close"].tolist(), df["date"].tolist())
    consec = [0] * len(net)
    for i in range(len(net)):
        consec[i] = consec[i - 1] + 1 if (net[i] > 0 and i > 0 and net[i - 1] > 0) \
            else (1 if net[i] > 0 else 0)
    res = {h: {"A": [], "D": [], "base": []} for h in HOLDS}
    for h in HOLDS:
        le = -1
        for i in range(MOM, len(net) - h - 1):
            if i <= le or op[i + 1] <= 0:
                continue
            res[h]["base"].append(_round_trip_return(op[i + 1], cl[i + 1 + h]))
            le = i + 1 + h
        le = -1
        for i in range(MOM, len(net) - h - 1):
            if consec[i] < K or i <= le or op[i + 1] <= 0 or cl[i - MOM] <= 0:
                continue
            r = _round_trip_return(op[i + 1], cl[i + 1 + h])
            res[h]["A"].append(r)
            yv = yoy(dt[i])
            if cl[i] > cl[i - MOM] and yv is not None and yv >= 0:
                res[h]["D"].append(r)
            le = i + 1 + h
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=60)
    ap.add_argument("--start", default="2022-01-01")
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"universe {len(universe)} 檔，起始 {args.start}")
    agg = {h: {"A": [], "D": [], "base": []} for h in HOLDS}
    done = 0
    for sid, market in universe:
        inv = "foreign" if market == "tpex" else "trust"
        try:
            r = _collect(sid, inv, args.start)
            if r:
                for h in HOLDS:
                    for key in ("A", "D", "base"):
                        agg[h][key].extend(r[h][key])
                done += 1
                print(f"  {sid}({'外' if inv=='foreign' else '投'}) ok  [{done}/{len(universe)}]")
        except Exception as e:
            print(f"  {sid} 失敗 {e}")
        time.sleep(0.35)

    today = date.today().isoformat()
    path = ROOT / "reports" / "backtest_validation.md"
    lines = [f"# 完整回測驗證 — 訊號 vs 任意日基準（{today}）\n",
             f"- universe：DB 流動性前 {len(universe)} 檔（含上市/上櫃），起始 {args.start}",
             f"- 有效樣本：{done} 檔；訊號=法人連買≥{K}日；成本含手續費+證交稅；同檔不重疊持有\n",
             "| 持有 | 基準(任意日) | A法人連買 | D組合(連買+抗跌+營收) | A超額 |",
             "|---|---|---|---|---|"]
    for h in HOLDS:
        b, a, d = (pd.Series(agg[h]["base"]), pd.Series(agg[h]["A"]), pd.Series(agg[h]["D"]))
        bm = b.mean() * 100 if len(b) else 0
        am = a.mean() * 100 if len(a) else 0
        dm = d.mean() * 100 if len(d) else 0
        lines.append(
            f"| {h}天 | {bm:+.2f}% (勝{(b>0).mean()*100:.0f}%, n{len(b)}) | "
            f"{am:+.2f}% (勝{(a>0).mean()*100:.0f}%, n{len(a)}) | "
            f"{dm:+.2f}% (n{len(d)}) | {am-bm:+.2f}% |")
    lines += [
        "\n## 判讀",
        "- 若『A超額』時正時負且接近 0 → 訊號報酬主要來自大盤 beta，非選股 alpha。",
        "- 若『D組合』在較長持有天數穩定高於基準 → 加條件過濾在中長波段才顯價值。",
        "\n## 限制",
        "- 允許跨檔並存、未計滑價；2022~ 多為特定行情，非樣本外驗證。",
        "- 屬方向性驗證，非可直接下單的精算績效。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 驗證報告 → {path}")


if __name__ == "__main__":
    main()
