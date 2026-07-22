"""持股追蹤器 — 記錄實單、算即時損益、停損/停利警示。

純本機：持股存在 data/portfolio.csv（gitignore，你的實單不會被上傳）。
即時價優先用 TWSE MIS（盤中），抓不到則用 DB 最新收盤。
"""
import pandas as pd

from .config import DATA_DIR
from .db import connect
from .realtime import quote

CSV = DATA_DIR / "portfolio.csv"
_COLS = ["stock_id", "lots", "cost", "buy_date", "stop", "target", "note"]


def load() -> pd.DataFrame:
    if not CSV.exists():
        return pd.DataFrame(columns=_COLS)
    return pd.read_csv(CSV, dtype={"stock_id": str})


def save(df: pd.DataFrame):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV, index=False, encoding="utf-8-sig")


def add(stock_id, lots, cost, stop=None, target=None, buy_date="", note=""):
    df = load()
    df = df[df["stock_id"] != str(stock_id)]  # 同代號覆蓋（重複買請自行加總成本）
    row = {"stock_id": str(stock_id), "lots": lots, "cost": cost,
           "buy_date": buy_date, "stop": stop, "target": target, "note": note}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save(df)
    return df


def update(stock_id, lots=None, cost=None, stop=None, target=None, note=None):
    """只改指定欄位，其餘保留（不像 add 會整列覆蓋）。改停損停利/加減張數用這個。

    回傳 (df, changed)；changed=False 表示查無此持股。
    """
    df = load()
    sid = str(stock_id)
    mask = df["stock_id"] == sid
    if not mask.any():
        return df, False
    fields = {"lots": lots, "cost": cost, "stop": stop, "target": target, "note": note}
    for col, val in fields.items():
        if val is not None:
            df.loc[mask, col] = val
    save(df)
    return df, True


def remove(stock_id):
    df = load()
    df = df[df["stock_id"] != str(stock_id)]
    save(df)
    return df


def _names(sids):
    with connect() as conn:
        info = pd.read_sql("SELECT stock_id, stock_name, type FROM stock_info", conn)
    return (dict(zip(info["stock_id"], info["stock_name"])),
            dict(zip(info["stock_id"], info["type"])))


def _current_prices(sids):
    """即時價：先 MIS，缺的用 DB 收盤補。回傳 {sid: price}。"""
    prices = {}
    try:
        for q in quote(list(sids)):
            if q.get("price"):
                prices[q["stock_id"]] = q["price"]
    except Exception:
        pass
    missing = [s for s in sids if s not in prices]
    if missing:
        with connect() as conn:
            ph = ",".join("?" * len(missing))
            db = pd.read_sql(
                f"SELECT stock_id, close FROM price WHERE stock_id IN ({ph}) "
                "AND date=(SELECT MAX(date) FROM price)", conn, params=missing)
        prices.update(dict(zip(db["stock_id"], db["close"])))
    return prices


def status(lot_shares: int = 1000) -> tuple[pd.DataFrame, dict]:
    df = load()
    if df.empty:
        return df, {}
    names, _ = _names(df["stock_id"].tolist())
    prices = _current_prices(df["stock_id"].tolist())

    rows = []
    for r in df.itertuples():
        price = prices.get(r.stock_id)
        cost = float(r.cost)
        lots = float(r.lots)
        shares = lots * lot_shares
        cost_val = cost * shares
        mkt_val = price * shares if price else None
        pl = (mkt_val - cost_val) if mkt_val is not None else None
        pl_pct = ((price - cost) / cost * 100) if price and cost else None

        alert = ""
        stop = float(r.stop) if pd.notna(r.stop) and str(r.stop) != "" else None
        target = float(r.target) if pd.notna(r.target) and str(r.target) != "" else None
        if price and stop and price <= stop:
            alert = "⚠️已觸停損"
        elif price and target and price >= target:
            alert = "🎯已達停利"
        elif price and stop:
            alert = f"距停損 {(price - stop) / price * 100:+.1f}%"

        rows.append({
            "代號": r.stock_id, "名稱": names.get(r.stock_id, ""),
            "張數": lots, "成本": round(cost, 2),
            "現價": round(price, 2) if price else None,
            "損益%": round(pl_pct, 2) if pl_pct is not None else None,
            "損益金額": int(pl) if pl is not None else None,
            "停損": stop, "狀態": alert,
        })
    view = pd.DataFrame(rows)

    total_cost = (df["cost"].astype(float) * df["lots"].astype(float) * lot_shares).sum()
    total_pl = view["損益金額"].dropna().sum()
    summary = {
        "總成本": int(total_cost),
        "總損益": int(total_pl),
        "總報酬%": round(total_pl / total_cost * 100, 2) if total_cost else 0,
        "持股數": len(view),
    }
    return view, summary
