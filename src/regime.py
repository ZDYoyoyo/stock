"""多空環境紅綠燈（regime filter）— 判斷「現在該不該做波段多單」。

用市場廣度（站上20MA比例 + 漲跌家數）判定環境，給出**建議部位水位**。
核心用途：爛環境（震盪/空頭）時叫你減量/觀望，避免硬凹。
"""
from .screeners.market_breadth import run as breadth_run

# (下限%站上20MA, 標籤, 建議部位水位, 波段多單建議)
_LEVELS = [
    (60, "🟢 偏多", "7~10 成", "可積極選股做多"),
    (45, "🟡 中性偏多", "4~6 成", "選股從嚴、控制部位"),
    (30, "🟠 震盪偏弱", "2~3 成", "只做最強勢股、嚴設停損"),
    (0,  "🔴 偏空", "0~2 成", "以觀望/空手為主，勿逆勢凹多單"),
]


def assess() -> dict:
    b = breadth_run()
    pct = b.get("pct_above_ma20")
    up, down = b.get("up", 0), b.get("down", 0)

    if pct is None:
        return {"regime": "資料不足（需≥20交易日）", "breadth": b}

    label, level, advice = "🔴 偏空", "0~2 成", "以觀望為主"
    for lo, lb, lv, ad in _LEVELS:
        if pct >= lo:
            label, level, advice = lb, lv, ad
            break

    return {
        "date": b.get("date"),
        "pct_above_ma20": pct,
        "adv_decl": f"{up}/{down}",
        "regime": label,
        "suggested_exposure": level,
        "swing_advice": advice,
        "breadth": b,
    }


def summary_line(a: dict) -> str:
    if "pct_above_ma20" not in a:
        return f"多空環境：{a.get('regime')}"
    line = (f"多空環境 {a['date']}：{a['regime']}（站上20MA {a['pct_above_ma20']}%、"
            f"漲跌 {a['adv_decl']}）→ 建議部位 {a['suggested_exposure']}；{a['swing_advice']}")
    return line + _breadth_flip_note(a)


def _breadth_flip_note(a: dict) -> str:
    """燈號看『站上20MA比例』，是結構指標、反應慢；當天漲跌家數一面倒地跟燈號唱反調時補一句。

    2026-11-24 實際狀況：漲跌 1904/61 全面大漲，燈號仍是 🔴 偏空「0~2 成」——
    燈號本身沒錯(多數股票還在20MA下)，但不提一句會讓人以為系統沒看到反彈。只加註、不改燈號。
    """
    b = a.get("breadth") or {}
    up, down = b.get("up") or 0, b.get("down") or 0
    label = a.get("regime", "")
    if up + down == 0:
        return ""
    if "偏多" not in label and up >= 3 * max(down, 1):
        return (f"｜⚡今日普漲（漲跌 {up}/{down}），但燈號看站上20MA比例、反應較慢；"
                f"要連續幾天普漲、站上20MA比例回升才算轉折")
    if "偏多" in label and down >= 3 * max(up, 1):
        return (f"｜⚡今日普跌（漲跌 {up}/{down}），燈號尚未反映；"
                f"若連續幾天普跌、站上20MA比例下滑就該減碼")
    return ""


if __name__ == "__main__":
    print(summary_line(assess()))
