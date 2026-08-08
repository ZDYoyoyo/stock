"""隔日沖鎖碼訊號回測：漲停/大漲+主力/隔日沖大戶鎖碼，隔日該做空(倒貨)還做多(軋空)？別自欺。

對每個歷史交易日 T：找漲停/大漲(≥gain_th)股 → 取成交額前 N 檔 → 分點算主力淨額(T)、
比對近窗隔日沖常客(🎯) → 接三種隔日報酬：
  gap = 隔夜跳空 (T+1開 vs T收)          → 有沒有開高(隔日沖/軋空推升)
  oc  = 隔日盤中 (T+1收 vs T+1開)         → 有沒有『開高走低』(當沖做空的關鍵)
  cc  = 收收    (T+1收 vs T收)            → 整體隔日方向
分組看：全體漲停 / 主力淨買(鎖碼) / 主力淨賣 / 🎯隔日沖常客鎖碼，各組三報酬均值與勝負比。

⚠️ 分點量大→逐檔逐日單查(走 broker_net 本機快取)，需 Sponsor。面板存 CSV 可重用。
用法：
    python -m scripts.backtest_snipe --build --days 45 --top 8
    python -m scripts.backtest_snipe                 # 用現有面板只分析
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.db import connect
from src.config import DATA_DIR

PANEL = DATA_DIR / "history" / "snipe_signal_panel.csv"
_TOP = 15


def _regular_counts(sid: str, dates: list[str]) -> dict:
    """{分點: 窗內昨買今賣次數}（走 broker_net 快取）。供收緊🎯門檻測試。"""
    from src.broker_signal import _branch_net
    nets = {}
    for d in dates:
        n = _branch_net(sid, d)
        if n:
            nets[d] = n
    ds = [d for d in dates if d in nets]
    hits: dict[str, int] = {}
    for i in range(1, len(ds)):
        ny, nt = nets[ds[i - 1]], nets[ds[i]]
        buyers = sorted(((k, v) for k, v in ny.items() if v > 0),
                        key=lambda z: z[1], reverse=True)[:_TOP]
        for k, _ in buyers:
            if nt.get(k, 0) < 0:
                hits[k] = hits.get(k, 0) + 1
    return hits


def build(days: int, top_n: int, gain_th: float, lookback: int, sleep: float = 0.0):
    from src import broker_client as bc
    from src.broker_signal import _branch_net
    if not bc.available():
        raise SystemExit("分點不可用（需 FinMind Sponsor）。")
    with connect() as c:
        px = pd.read_sql("SELECT date, stock_id, open, close, volume FROM price", c)
    dates = sorted(px["date"].unique())
    # 需要 T+1 算隔日報酬、T 前 lookback 算常客 → 有效 T 落在 [lookback, len-2]
    lo = lookback
    hi = len(dates) - 1
    win_ts = dates[lo:hi][-days:]
    print(f"回測交易日 {len(win_ts)}（{win_ts[0]}~{win_ts[-1]}）…")

    rows, done = [], 0
    for T in win_ts:
        i = dates.index(T)
        prevd, nextd = dates[i - 1], dates[i + 1]
        cur = px[px["date"] == T].set_index("stock_id")
        pv = px[px["date"] == prevd].set_index("stock_id")["close"]
        nxt = px[px["date"] == nextd].set_index("stock_id")
        # 漲停/大漲 + 取成交額前 top_n
        cand = []
        for sid, r in cur.iterrows():
            c0, v = r["close"], r["volume"]
            if sid not in pv.index or pv[sid] <= 0 or not v or v <= 0:
                continue
            chg = (c0 - pv[sid]) / pv[sid] * 100
            if chg < gain_th:
                continue
            cand.append((sid, c0, chg, c0 * v / 1e5))
        cand.sort(key=lambda z: z[3], reverse=True)
        win_dates = dates[i - lookback:i + 1]        # T 前 lookback ~ T（含 T，對齊 screener）
        for sid, c0, chg, amt in cand[:top_n]:
            if sid not in nxt.index:
                continue
            no, nc = nxt.loc[sid, "open"], nxt.loc[sid, "close"]
            if not no or no <= 0 or not nc or nc <= 0:
                continue
            net_t = _branch_net(sid, T)
            if not net_t:
                continue
            vals = sorted(net_t.values(), reverse=True)
            mn = round(sum(x for x in vals[:_TOP] if x > 0) + sum(x for x in vals[-_TOP:] if x < 0))
            reg_hits, reg_top5 = 0, 0
            if mn > 0:
                buyers_sorted = [k for k, val in sorted(net_t.items(), key=lambda z: z[1], reverse=True)
                                 if val > 0]
                top15, top5 = set(buyers_sorted[:_TOP]), set(buyers_sorted[:5])
                counts = _regular_counts(sid, win_dates)
                matched = {b: counts[b] for b in top15 if b in counts}
                if matched:
                    reg_hits = max(matched.values())               # 命中常客的最高昨買今賣次數
                    reg_top5 = int(any(b in top5 for b in matched))  # 命中常客中有無今日前5大買
            rows.append({
                "date": T, "stock_id": sid, "漲跌%": round(chg, 2), "主力淨額": mn,
                "鎖碼": int(mn > 0), "🎯": int(reg_hits >= 2),
                "reg_hits": reg_hits, "reg_top5": reg_top5,
                "gap": round((no - c0) / c0 * 100, 2),
                "oc": round((nc - no) / no * 100, 2),
                "cc": round((nc - c0) / c0 * 100, 2),
            })
            if sleep:
                time.sleep(sleep)
        done += 1
        print(f"  [{done}/{len(win_ts)}] {T}  漲停{len(cand)} 檔  累計樣本 {len(rows)}")

    panel = pd.DataFrame(rows)
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL, index=False, encoding="utf-8")
    print(f"✅ 面板 {len(panel)} 樣本 → {PANEL}")
    return panel


def _grp(p, mask, name):
    g = p[mask]
    if len(g) < 8:
        return None
    return {
        "組別": name, "樣本": len(g),
        "隔夜gap%": round(g["gap"].mean(), 2),
        "開高走低oc%": round(g["oc"].mean(), 2),
        "oc<0比%": round((g["oc"] < 0).mean() * 100, 0),
        "收收cc%": round(g["cc"].mean(), 2),
        "cc<0比%": round((g["cc"] < 0).mean() * 100, 0),
    }


def analyze(panel: pd.DataFrame):
    n = len(panel)
    print(f"\n=== 隔日沖鎖碼訊號回測（{n} 個漲停-日樣本）===")
    if n < 30:
        print("樣本太少，結論不可信。")
        return
    p = panel.copy()
    groups = [
        _grp(p, p["date"].notna(), "全體漲停/大漲"),
        _grp(p, p["鎖碼"] == 1, "＋主力淨買(鎖碼)"),
        _grp(p, p["鎖碼"] == 0, "＋主力淨賣"),
        _grp(p, p["🎯"] == 1, "🎯隔日沖常客鎖碼"),
    ]
    tbl = pd.DataFrame([g for g in groups if g])
    print(tbl.to_string(index=False))

    # 收緊🎯門檻：看能不能篩出『開高走低更兇』的子集
    if "reg_hits" in p.columns:
        print("\n--- 收緊🎯門檻（命中常客次數／是否今日前5大買）---")
        tight = [
            _grp(p, p["reg_hits"] >= 2, "🎯 hits≥2(現行)"),
            _grp(p, p["reg_hits"] >= 3, "🎯 hits≥3"),
            _grp(p, p["reg_hits"] >= 4, "🎯 hits≥4"),
            _grp(p, p["reg_top5"] == 1, "🎯 常客為今日前5大買"),
            _grp(p, (p["reg_hits"] >= 3) & (p["reg_top5"] == 1), "🎯 hits≥3 且 前5大買"),
        ]
        tt = pd.DataFrame([g for g in tight if g])
        if not tt.empty:
            print(tt.to_string(index=False))
    print("\n[相關] 主力淨額 vs 隔日：",
          f"gap {p['主力淨額'].corr(p['gap']):+.3f}｜oc {p['主力淨額'].corr(p['oc']):+.3f}｜"
          f"cc {p['主力淨額'].corr(p['cc']):+.3f}")
    print("\n【判讀】看🎯組：gap>0 且 oc<<0＝開高走低(隔日沖倒貨→做空有理)；"
          "cc>0＝軋空續強(別空)；三者接近 0＝無方向(只是舞台)。"
          "\n⚠️ 限制：小樣本、期間短、僅漲停子集、未計成本/滑價、倖存者偏誤；方向性參考非策略保證。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--gain", type=float, default=9.0)
    ap.add_argument("--lookback", type=int, default=8)
    args = ap.parse_args()

    try:
        from src import datastore
        if datastore.has_history():
            datastore.load()
    except Exception:
        pass

    if args.build or not PANEL.exists():
        panel = build(args.days, args.top, args.gain, args.lookback)
    else:
        panel = pd.read_csv(PANEL, dtype={"stock_id": str})
        print(f"用現有面板 {PANEL}（{len(panel)} 樣本；--build 可重抓）")
    analyze(panel)


if __name__ == "__main__":
    main()
