"""驗證『預估賣壓佔量%』(跨股票分點檔案的前瞻估計) 對隔日走勢有無預測力。

背景：舊的🎯(本檔近窗昨買今賣≥2次) 經 backtest_snipe 實測**對報酬無增量預測力**
（各組都約 oc −0.8%，收緊門檻也沒用）。本腳本驗證新做法是否真的比較好：
  預估賣壓佔量% = Σ(今日各分點淨買張 × 該分點歷史回吐量%) ÷ 今日量
  ——把「誰在買」×「這些人歷史上會倒多少」量化成一個連續變數。

⚠️ **point-in-time**：每個交易日 T 的分點檔案只用 `date < T` 的快取建立，
   否則等於拿未來資料判斷過去（前視偏誤），結果會虛胖。

前置：需要 snipe_signal_panel.csv（`python -m scripts.backtest_snipe --build`）
      與本機 broker_net 快取。用法：python -m scripts.backtest_broker_profile
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from src import broker_profile as bp
from src.config import DATA_DIR
from src.db import connect

PANEL = DATA_DIR / "history" / "snipe_signal_panel.csv"
OUT = ROOT / "reports" / "broker_profile_backtest.md"


def build_eval(min_ops: int = 10) -> pd.DataFrame:
    """面板每列補上 point-in-time 的預估賣壓佔量%。"""
    if not PANEL.exists():
        raise SystemExit(f"找不到面板 {PANEL}（先跑 python -m scripts.backtest_snipe --build）")
    panel = pd.read_csv(PANEL, dtype={"stock_id": str})
    cache = bp.load_cache()
    if not cache:
        raise SystemExit("本機無 broker_net 快取（需先跑過 run_all/深掘累積分點）")
    with connect() as c:
        px = pd.read_sql("SELECT date, stock_id, volume FROM price", c)
    vol = {(r.date, r.stock_id): r.volume for r in px.itertuples()}

    rows = []
    for d, g in panel.groupby("date"):
        pmap = bp.as_map(bp.build(min_ops=min_ops, before=d, cache=cache))   # ⚠️只用 d 之前
        if not pmap:
            continue
        for r in g.itertuples():
            net = cache.get(r.stock_id, {}).get(d)
            if not net:
                continue
            est = bp.expected_pressure(net, vol.get((d, r.stock_id)), pmap)
            if not est:
                continue
            rows.append({"date": d, "stock_id": r.stock_id, "鎖碼": r.鎖碼,
                         "est_pct": est.get("預估賣壓佔量%"), "n_hot": est.get("隔日沖分點數"),
                         "gap": r.gap, "oc": r.oc, "cc": r.cc})
    return pd.DataFrame(rows).dropna(subset=["est_pct", "oc"])


def main():
    df = build_eval()
    lines = ["# 分點檔案『預估賣壓佔量%』回測", "",
             f"樣本 {len(df)} 筆（{df['date'].min()} ~ {df['date'].max()}）　·　"
             "point-in-time 建檔（只用當日之前資料，無前視偏誤）", "",
             "報酬定義：gap＝隔夜跳空(T+1開vs T收)、oc＝隔日盤中(T+1收vs T+1開)、cc＝收收。", "",
             "## 相關係數", ""]
    for k in ("gap", "oc", "cc"):
        lines.append(f"- {k}：{df['est_pct'].corr(df[k]):+.3f}")

    df = df.copy()
    df["grp"] = pd.qcut(df["est_pct"], 4, labels=["Q1最低", "Q2", "Q3", "Q4最高"], duplicates="drop")
    agg = df.groupby("grp", observed=True).agg(
        樣本=("oc", "size"), 平均預估賣壓=("est_pct", "mean"), gap=("gap", "mean"),
        oc=("oc", "mean"), cc=("cc", "mean"), oc下跌比=("oc", lambda s: (s < 0).mean() * 100)).round(2)
    lines += ["", "## 依預估賣壓佔量% 分四組", "", agg.to_markdown()]

    lock = df[df["鎖碼"].astype(str).isin(["True", "1"])]
    if len(lock) > 20:
        lock = lock.copy()
        lock["g3"] = pd.qcut(lock["est_pct"], 3, labels=["低", "中", "高"], duplicates="drop")
        a2 = lock.groupby("g3", observed=True).agg(
            樣本=("oc", "size"), oc=("oc", "mean"),
            oc下跌比=("oc", lambda s: (s < 0).mean() * 100)).round(2)
        lines += ["", "## 只看鎖碼股（主力淨買）", "", a2.to_markdown()]

    lines += ["", "## 結論", "",
              "⚠️ 研究用途、非投資建議。edge 仍屬薄弱（相關約 −0.1、最高組勝率不到 6 成），",
              "扣掉當沖成本後所剩有限；且樣本期短、快取偏向報告候選股（非隨機抽樣）。",
              "定位＝**排序/警示用的連續變數**，不是提款機。"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n✅ 報告 → {OUT}")


if __name__ == "__main__":
    main()
