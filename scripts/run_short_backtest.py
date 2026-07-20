"""地雷放空回測 — 驗證『財務地雷股放空』到底有沒有 edge。

核心問題：月營收 YoY 崩壞的地雷股，紅旗出現後放空，扣掉放空成本後真的會賺嗎？
且分兩種情境測我的假設：
  - 地雷 + 技術已轉弱（跌破季線）→ 猜「有 edge」
  - 地雷 + 價格還很強（站上季線）→ 猜「軋空陷阱、危險」

變體（訊號日 = 新公布的月營收 YoY <= 門檻）：
  base      任意日放空（放空的自然漂移；因大盤長期偏多，放空基準通常為負）
  L         地雷（營收 YoY 崩）
  L+弱      地雷 且 跌破季線(60MA)
  L+強      地雷 且 站上季線(60MA)

出場：訊號隔日開盤放空、持有 H 日後收盤回補。
成本：手續費0.1425%×2 + 證交稅0.3% + 融券手續費(借券)0.08%（放空比做多多一筆借券）。
資料：FinMind 逐檔多年歷史（月營收 create_time 當可得日，避免未來函數）。
⚠️ 研究/驗證用途，非投資建議；放空虧損理論無上限、有券源/軋空/強制回補風險。

用法：
    python -m scripts.run_short_backtest --universe 80 --start 2022-01-01 --rev-bad -20
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.backtest import _load_stock, _revenue_yoy_lookup
from scripts.run_full_backtest import _pick_universe

HOLDS = [10, 20, 40, 60]
FEE = 0.001425
TAX = 0.003
BORROW = 0.0008   # 融券手續費/借券（近似，一次性）


def _short_return(entry_open: float, exit_close: float) -> float:
    """含成本的放空報酬率（價格跌才賺）。開空收現付稅+借券，回補買回付手續費。"""
    proceeds = entry_open * (1 - FEE - TAX - BORROW)
    cost = exit_close * (1 + FEE)
    return (proceeds - cost) / entry_open


def _ma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def _collect(sid, start, rev_bad):
    df = _load_stock(sid, start)
    if df.empty or len(df) < 90:
        return None
    yoy_of = _revenue_yoy_lookup(sid, start)
    op, cl, dt = df["open"].tolist(), df["close"].tolist(), df["date"].tolist()

    # 每個交易日「當下可得」的最新月營收 YoY（未來函數已由 create_time 排除）
    yoy_series = [yoy_of(d) for d in dt]

    res = {h: {"base": [], "L": [], "Lw": [], "Ls": []} for h in HOLDS}
    for h in HOLDS:
        # 基準：任意日放空（不重疊）
        le = -1
        for i in range(60, len(cl) - h - 1):
            if i <= le or op[i + 1] <= 0:
                continue
            res[h]["base"].append(_short_return(op[i + 1], cl[i + 1 + h]))
            le = i + 1 + h
        # 地雷訊號：新公布的營收 YoY <= 門檻（與前一日的可得 YoY 不同 = 剛換月）
        le = -1
        for i in range(60, len(cl) - h - 1):
            y, yprev = yoy_series[i], yoy_series[i - 1]
            if y is None or y > rev_bad:
                continue
            if yprev is not None and abs(y - yprev) < 1e-9:   # 同一筆，不重複觸發
                continue
            if i <= le or op[i + 1] <= 0:
                continue
            r = _short_return(op[i + 1], cl[i + 1 + h])
            res[h]["L"].append(r)
            ma60 = _ma(cl, i, 60)
            if ma60:
                (res[h]["Lw"] if cl[i] < ma60 else res[h]["Ls"]).append(r)
            le = i + 1 + h
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=80)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--rev-bad", type=float, default=-20.0, help="營收 YoY 低於此(%)算地雷")
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"universe {len(universe)} 檔，起始 {args.start}，地雷門檻 營收YoY<={args.rev_bad}%")
    agg = {h: {"base": [], "L": [], "Lw": [], "Ls": []} for h in HOLDS}
    done = 0
    for sid, _market in universe:
        try:
            r = _collect(sid, args.start, args.rev_bad)
            if r:
                for h in HOLDS:
                    for k in agg[h]:
                        agg[h][k].extend(r[h][k])
                done += 1
                print(f"  {sid} ok [{done}/{len(universe)}]")
        except Exception as e:
            print(f"  {sid} 失敗 {e}")
        time.sleep(0.35)

    def stat(lst):
        s = pd.Series(lst)
        return (f"{s.mean() * 100:+.2f}% 勝{(s > 0).mean() * 100:.0f}% n{len(s)}"
                if len(s) else "n0")

    today = date.today().isoformat()
    path = ROOT / "reports" / "short_backtest_validation.md"
    lines = [f"# 地雷放空回測 — 驗證（{today}）\n",
             f"- universe：DB 流動性前 {len(universe)} 檔；有效 {done} 檔；起始 {args.start}",
             f"- 訊號：新公布月營收 YoY ≤ {args.rev_bad}%；放空隔日開盤進、持有 H 日回補",
             f"- 成本：手續費0.1425%×2＋證交稅0.3%＋借券0.08%；放空報酬=價跌才正\n",
             "| 持有 | 任意日放空(基準) | L 地雷 | L+弱(跌破季線) | L+強(站上季線) |",
             "|---|---|---|---|---|"]
    for h in HOLDS:
        a = agg[h]
        lines.append(f"| {h}天 | {stat(a['base'])} | {stat(a['L'])} | "
                     f"{stat(a['Lw'])} | {stat(a['Ls'])} |")
    lines += [
        "\n## 判讀",
        "- **L 地雷** 期望值若 >0 且明顯高於「任意日放空基準」→ 放空地雷有 edge。",
        "- **L+弱 vs L+強**：若 L+弱明顯優於 L+強 → 印證『破線走弱才空、價格還強別空(易軋空)』。",
        "- 放空基準本就偏負（大盤長期漲），故看的是『比基準好多少』與是否轉正。",
        "\n## 限制",
        "- 未計融券保證金成本/利息、券源不足與強制回補、滑價；2022~ 非樣本外。",
        "- 屬方向性驗證，非可直接下單績效；放空風險遠高於做多（虧損理論無上限）。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n=== 地雷放空回測（成本後）===")
    print(f"{'持有':>4} | {'任意日(基準)':>18} | {'L地雷':>18} | {'L+弱':>18} | {'L+強':>18}")
    for h in HOLDS:
        a = agg[h]
        print(f"{h:>4} | {stat(a['base']):>18} | {stat(a['L']):>18} | "
              f"{stat(a['Lw']):>18} | {stat(a['Ls']):>18}")
    print(f"\n✅ 報告 → {path}")


if __name__ == "__main__":
    main()
