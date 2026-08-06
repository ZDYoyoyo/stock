"""隔日沖賣壓% 訊號回測：高賣壓是否預測『隔日』下跌？（別自欺，先驗證再信）

假設：某日『隔日沖賣壓%』(昨日前15大買超分點今日轉淨賣量÷量)高 → 昨天的大買家在倒貨 → 隔日(T+1)偏弱。
做法：對高流動高波動 universe(分點 arena)，逐檔逐日抓分點算賣壓%(T)，join 次日報酬 ret_next=(close[T+1]-close[T])/close[T]。
分析：把賣壓%分五等分，看各組『次日平均報酬』與『下跌比率』有無單調關係；另算相關係數。

⚠️ 分點量大→逐檔逐日單查(每檔每日 1 call)，universe×window 較燒 call(需 Sponsor 6000/hr)。
面板存 data/history/daytrade_signal_panel.csv 可重用(--rebuild 才重抓)。

用法：
    python -m scripts.backtest_daytrade --build --universe 30 --days 40   # 抓分點建面板
    python -m scripts.backtest_daytrade                                    # 用現有面板只分析
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.db import connect
from src.config import DATA_DIR

PANEL = DATA_DIR / "history" / "daytrade_signal_panel.csv"
_TOP = 15


def _universe(k: int, win: list[str]) -> list[str]:
    """高流動+高波動池(隔日沖 arena)：取近窗 avg成交額×波動 前 k 檔。"""
    with connect() as c:
        px = pd.read_sql("SELECT date, stock_id, close, volume FROM price", c)
    p = px[px["date"].isin(win)]
    rows = []
    for sid, d in p.groupby("stock_id"):
        d = d.sort_values("date")
        if len(d) < len(win) * 0.7:
            continue
        ret = d["close"].pct_change()
        amt = (d["close"] * d["volume"]).mean()
        rows.append((sid, d["close"].iloc[-1], d["volume"].mean(), ret.std() * 100, amt))
    u = pd.DataFrame(rows, columns=["sid", "close", "avgvol", "retstd", "amt"]).dropna()
    u = u[(u["avgvol"] > 3000) & (u["close"] > 10) & (u["close"] < 600)]
    u["score"] = u["retstd"] * np.log(u["amt"])
    return u.sort_values("score", ascending=False).head(k)["sid"].tolist()


def build(k: int, days: int, sleep: float = 0.3):
    from src import broker_client as bc
    from src.broker_signal import _branch_net
    if not bc.available():
        raise SystemExit("分點不可用（需 FinMind Sponsor）。")
    with connect() as c:
        px = pd.read_sql("SELECT date, stock_id, close, volume FROM price", c)
    dates = sorted(px["date"].unique())
    win = dates[-(days + 1):]                      # 多留 1 天算 ret_next
    universe = _universe(k, win)
    print(f"universe {len(universe)} 檔 × 窗 {len(win)} 日（分點 call ≈ {len(universe)*len(win)}）…")

    pxw = px[px["date"].isin(win)]
    rows, done = [], 0
    for sid in universe:
        d = pxw[pxw["stock_id"] == sid].sort_values("date").reset_index(drop=True)
        vol = dict(zip(d["date"], d["volume"]))
        close = dict(zip(d["date"], d["close"]))
        sd = list(d["date"])
        nets = {}
        for dt in sd:
            n = _branch_net(sid, dt)
            if n:
                nets[dt] = n
            time.sleep(sleep)
        for i in range(1, len(sd) - 1):            # 需 T-1(算賣壓) 與 T+1(算次日報酬)
            t, tm1, tp1 = sd[i], sd[i - 1], sd[i + 1]
            net_t, net_y = nets.get(t), nets.get(tm1)
            v = vol.get(t)
            if not net_t or not net_y or not v or v <= 0:
                continue
            buyers = sorted(((x, q) for x, q in net_y.items() if q > 0),
                            key=lambda z: z[1], reverse=True)[:_TOP]
            overlap = sum(min(q, -net_t.get(x, 0)) for x, q in buyers if net_t.get(x, 0) < 0)
            pressure = overlap / v * 100
            vals = sorted(net_t.values(), reverse=True)
            main_net = sum(w for w in vals[:_TOP] if w > 0) + sum(w for w in vals[-_TOP:] if w < 0)
            ret_same = (close[t] - close[tm1]) / close[tm1] * 100
            ret_next = (close[tp1] - close[t]) / close[t] * 100
            rows.append({"stock_id": sid, "date": t, "隔日沖賣壓%": round(pressure, 2),
                         "主力淨額": round(main_net), "ret_same": round(ret_same, 2),
                         "ret_next": round(ret_next, 2)})
        done += 1
        print(f"  [{done}/{len(universe)}] {sid}  累計 {len(rows)} 樣本")

    panel = pd.DataFrame(rows)
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL, index=False, encoding="utf-8")
    print(f"✅ 面板 {len(panel)} 樣本 → {PANEL}")
    return panel


def analyze(panel: pd.DataFrame):
    n = len(panel)
    print(f"\n=== 隔日沖賣壓% 訊號回測（{n} 個股票-日樣本）===")
    if n < 30:
        print("樣本太少，結論不可信。")
        return
    p = panel.copy()
    # 五等分（去重邊界避免同值擠一組）
    try:
        p["組"] = pd.qcut(p["隔日沖賣壓%"], 5, labels=["Q1最低", "Q2", "Q3", "Q4", "Q5最高"], duplicates="drop")
    except ValueError:
        p["組"] = pd.qcut(p["隔日沖賣壓%"].rank(method="first"), 5,
                         labels=["Q1最低", "Q2", "Q3", "Q4", "Q5最高"])
    g = p.groupby("組", observed=True).agg(
        樣本=("ret_next", "size"),
        賣壓中位=("隔日沖賣壓%", "median"),
        次日均報酬=("ret_next", "mean"),
        次日下跌比=("ret_next", lambda s: (s < 0).mean() * 100),
        當日均報酬=("ret_same", "mean"),
    ).round(2)
    print(g.to_string())
    corr = p["隔日沖賣壓%"].corr(p["ret_next"])
    corr_s = p["隔日沖賣壓%"].corr(p["ret_same"])
    print(f"\n相關係數 賣壓% vs 次日報酬: {corr:+.3f}　| vs 當日報酬: {corr_s:+.3f}")

    # 極端尾巴：只有很高的賣壓才可能有訊號
    print("\n[極端尾巴] 高賣壓門檻 → 次日表現")
    for th in (10, 15, 20, 30):
        hi = p[p["隔日沖賣壓%"] >= th]
        if len(hi) >= 20:
            print(f"  賣壓≥{th}% (n={len(hi):>4}): 次日均 {hi['ret_next'].mean():+.2f}%"
                  f"（下跌 {(hi['ret_next'] < 0).mean()*100:.0f}%）")

    # 組合：高賣壓 且 當日已下跌（續弱確認）
    q80 = p["隔日沖賣壓%"].quantile(0.8)
    combo = p[(p["隔日沖賣壓%"] >= q80) & (p["ret_same"] < 0)]
    print(f"\n[組合] 高賣壓(前20%) 且 當日已跌 (n={len(combo)}): "
          f"次日均 {combo['ret_next'].mean():+.2f}%（下跌 {(combo['ret_next'] < 0).mean()*100:.0f}%）")

    # 對照：主力淨額方向的預測力
    cm = p["主力淨額"].corr(p["ret_next"])
    print(f"\n[對照] 主力淨額 vs 次日報酬 相關 {cm:+.3f}｜"
          f"主力淨買日 次日均 {p[p['主力淨額'] > 0]['ret_next'].mean():+.2f}%、"
          f"淨賣日 {p[p['主力淨額'] < 0]['ret_next'].mean():+.2f}%")

    print("\n【結論】隔日沖賣壓% 單獨看≈無 edge（相關≈0、分組不單調）；"
          "只有『極端(≥20%)』或『搭配當日走弱』才預告隔日偏空。主力淨額方向性略強但仍弱。"
          "\n⚠️ 限制：小 universe(高流動高波動)、期間短、倖存者偏誤、未計成本/滑價；僅方向性參考、非策略保證。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="抓分點重建面板（否則用現有面板只分析）")
    ap.add_argument("--universe", type=int, default=30)
    ap.add_argument("--days", type=int, default=40)
    args = ap.parse_args()

    try:
        from src import datastore
        if datastore.has_history():
            datastore.load()
    except Exception:
        pass

    if args.build or not PANEL.exists():
        panel = build(args.universe, args.days)
    else:
        panel = pd.read_csv(PANEL, dtype={"stock_id": str})
        print(f"用現有面板 {PANEL}（{len(panel)} 樣本；--build 可重抓）")
    analyze(panel)


if __name__ == "__main__":
    main()
