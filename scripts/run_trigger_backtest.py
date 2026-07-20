"""觸發器甜蜜點回測 — 地雷進「放空觀察清單」後，哪種技術觸發器才是最佳放空點？

回答使用者的洞見：「破季線才空可能太晚，地雷出現就該準備」。
作法（兩階段）：
  階段一：月營收 YoY 崩 = 地雷 → 進觀察清單（效期 W 交易日）。
  階段二：清單內等『技術觸發器』首次出現 → 隔日開盤放空、持有 H 日回補。
關鍵：用觀察清單，只會空「已開始跌」的，價格還強而從未觸發的自動被濾掉（避開軋空）。

比較的觸發器（找清單內第一次出現）：
  即空(不等)   地雷隔日就空（對照組＝太早，易被軋）
  破季線60MA   收盤跌破 60 日均線
  破月線20MA   收盤跌破 20 日均線（較早）
  破近10日低   收盤跌破近 10 日最低（更早抓轉折）
  動能翻負      收盤 < 20 日前收盤（動能由正轉負）

每個觸發器看：成本後報酬、勝率、觸發數、**觸發率%（多少地雷真的等到觸發）**、
**平均延遲天（比即空晚幾天進場）** → 早晚 vs 報酬的取捨一目了然。

資料：FinMind 逐檔多年（月營收 create_time 當可得日，無未來函數）。
⚠️ 研究驗證用途，非投資建議；放空風險遠高於做多。

用法：
    python -m scripts.run_trigger_backtest --universe 80 --start 2022-01-01 --rev-bad -20
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
from scripts.run_short_backtest import _short_return

HOLDS = [20, 40]
WATCH_WINDOW = 60          # 地雷進清單後，觸發器的有效追蹤天數
TRIGGERS = ["即空(不等)", "破季線60MA", "破月線20MA", "破近10日低", "動能翻負"]


def _ma(vals, i, n):
    return sum(vals[i - n + 1:i + 1]) / n if i + 1 >= n else None


def _fires(kind, i, cl):
    """第 i 天收盤是否觸發某觸發器。"""
    if kind == "即空(不等)":
        return True                       # 進清單隔日即空（實際在 flag 當下處理）
    if kind == "破季線60MA":
        ma = _ma(cl, i, 60)
        return ma is not None and cl[i] < ma
    if kind == "破月線20MA":
        ma = _ma(cl, i, 20)
        return ma is not None and cl[i] < ma
    if kind == "破近10日低":                # 收盤跌破近10日收盤最低（免 low 欄）
        return i >= 10 and cl[i] < min(cl[i - 10:i])
    if kind == "動能翻負":
        return i >= 20 and cl[i] < cl[i - 20]
    return False


def _collect(sid, start, rev_bad):
    df = _load_stock(sid, start)
    if df.empty or len(df) < 130:
        return None
    yoy_of = _revenue_yoy_lookup(sid, start)
    op, cl, dt = df["open"].tolist(), df["close"].tolist(), df["date"].tolist()
    yoy = [yoy_of(d) for d in dt]

    # 地雷日：新公布的營收 YoY <= 門檻
    flags = []
    for i in range(60, len(cl)):
        y, yp = yoy[i], yoy[i - 1]
        if y is None or y > rev_bad:
            continue
        if yp is not None and abs(y - yp) < 1e-9:
            continue
        flags.append(i)

    out = {t: {"rets": {h: [] for h in HOLDS}, "delay": [], "fired": 0}
           for t in TRIGGERS}
    out["_flags"] = 0
    last_exit = {t: -1 for t in TRIGGERS}
    maxh = max(HOLDS)

    for f in flags:
        if f + WATCH_WINDOW + maxh + 2 >= len(cl):
            continue
        out["_flags"] += 1
        for t in TRIGGERS:
            if t == "即空(不等)":
                j = f                      # 地雷當天就當觸發，隔日開盤空
            else:
                j = next((k for k in range(f, f + WATCH_WINDOW + 1) if _fires(t, k, cl)), None)
            if j is None or j <= last_exit[t] or op[j + 1] <= 0:
                continue
            out[t]["fired"] += 1
            out[t]["delay"].append(j - f)
            for h in HOLDS:
                out[t]["rets"][h].append(_short_return(op[j + 1], cl[j + 1 + h]))
            last_exit[t] = j + 1 + maxh
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=80)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--rev-bad", type=float, default=-20.0)
    args = ap.parse_args()

    universe = _pick_universe(args.universe)
    print(f"universe {len(universe)} 檔，起始 {args.start}，地雷門檻 營收YoY<={args.rev_bad}%，"
          f"觀察窗 {WATCH_WINDOW} 日")
    agg = {t: {"rets": {h: [] for h in HOLDS}, "delay": [], "fired": 0} for t in TRIGGERS}
    flags_total = 0
    done = 0
    for sid, _m in universe:
        try:
            r = _collect(sid, args.start, args.rev_bad)
            if r:
                flags_total += r["_flags"]
                for t in TRIGGERS:
                    agg[t]["fired"] += r[t]["fired"]
                    agg[t]["delay"].extend(r[t]["delay"])
                    for h in HOLDS:
                        agg[t]["rets"][h].extend(r[t]["rets"][h])
                done += 1
                print(f"  {sid} ok [{done}/{len(universe)}] 地雷{r['_flags']}")
        except Exception as e:
            print(f"  {sid} 失敗 {e}")
        time.sleep(0.35)

    def cell(t, h):
        s = pd.Series(agg[t]["rets"][h])
        return f"{s.mean()*100:+.2f}% 勝{(s>0).mean()*100:.0f}% n{len(s)}" if len(s) else "n0"

    today = date.today().isoformat()
    lines = [f"# 觸發器甜蜜點回測 — 地雷放空觀察清單（{today}）\n",
             f"- universe {len(universe)}／有效 {done} 檔；起始 {args.start}；"
             f"地雷=月營收YoY≤{args.rev_bad}%；觀察窗 {WATCH_WINDOW} 日；地雷事件 {flags_total} 次",
             "- 兩階段：地雷進清單 → 等觸發器首次出現才空（隔日開盤，持有H日，含放空成本）\n",
             "| 觸發器 | 觸發率% | 平均延遲天 | " + " | ".join(f"{h}天報酬" for h in HOLDS) + " |",
             "|---|---|---|" + "---|" * len(HOLDS)]
    for t in TRIGGERS:
        rate = agg[t]["fired"] / flags_total * 100 if flags_total else 0
        delay = (sum(agg[t]["delay"]) / len(agg[t]["delay"])) if agg[t]["delay"] else 0
        lines.append(f"| {t} | {rate:.0f}% | {delay:.0f} | "
                     + " | ".join(cell(t, h) for h in HOLDS) + " |")
    lines += [
        "\n## 判讀",
        "- **報酬越高(越接近正)＋觸發率不太低** = 甜蜜點；報酬要跟『即空(不等)』比才知等待值不值。",
        "- **平均延遲天**看『比即空晚幾天進場』；延遲小又報酬好 = 早且穩。",
        "- 觸發率<100%代表有些地雷『從未跌破』被自動略過——那些多半就是會軋空的強勢股，避開是對的。",
        "\n## 限制：未計融券利息/券源/強制回補/滑價；2022~非樣本外；方向性驗證非精算績效。",
    ]
    path = ROOT / "reports" / "trigger_backtest_validation.md"
    path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n=== 觸發器甜蜜點（地雷事件 {flags_total} 次，成本後放空報酬）===")
    print(f"{'觸發器':>12} | {'觸發率':>5} | {'延遲':>4} | " + " | ".join(f"{h}天" for h in HOLDS))
    for t in TRIGGERS:
        rate = agg[t]["fired"] / flags_total * 100 if flags_total else 0
        delay = (sum(agg[t]["delay"]) / len(agg[t]["delay"])) if agg[t]["delay"] else 0
        print(f"{t:>12} | {rate:>4.0f}% | {delay:>4.0f} | " + " | ".join(cell(t, h) for h in HOLDS))
    print(f"\n✅ 報告 → {path}")


if __name__ == "__main__":
    main()
