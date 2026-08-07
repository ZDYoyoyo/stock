"""極簡 inline SVG 迷你圖（自足、無外部庫、可直接嵌 HTML 報告）。

只用純 SVG(rect/line/polyline/circle)＋<title> tooltip → CSP-safe、離線可開、明暗主題皆清楚。
台股配色：紅(_UP)=正/漲、綠(_DOWN)=負/跌。供個股深掘『圖譜』區塊用。
"""
from __future__ import annotations

_UP = "#d63031"      # 紅＝正/漲
_DOWN = "#158a4e"    # 綠＝負/跌
_LINE = "#2d7ef7"    # 中性線圖色（明暗皆可見）
_AXIS = "#9aa0a6"    # 軸/基準線
_H = 90              # 圖高
_PADY = 10


def _clean(values) -> list[tuple[int, float]]:
    """回 [(index, float)]，濾掉 None/NaN。"""
    out = []
    for i, v in enumerate(values):
        try:
            if v is None:
                continue
            f = float(v)
            if f != f:                      # NaN
                continue
            out.append((i, f))
        except (TypeError, ValueError):
            continue
    return out


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _svg(inner: str, w: int) -> str:
    return (f'<svg viewBox="0 0 {w} {_H}" width="100%" height="{_H}" '
            f'preserveAspectRatio="none" style="display:block">{inner}</svg>')


def bars(values, dates=None, signed=False, unit="", fmt="{:,.0f}") -> str:
    """長條圖。signed=True → 零基準置中、紅正綠負；否則底部基準、單色(紅)。"""
    pts = _clean(values)
    if not pts:
        return "<p class='note'>（無資料）</p>"
    n = len(values)
    w = max(n * 16 + 16, 80)
    step = (w - 16) / n
    vs = [v for _, v in pts]
    if signed:
        m = max((abs(x) for x in vs), default=1) or 1
        zero_y = _PADY + (_H - 2 * _PADY) / 2
        scale = (_H - 2 * _PADY) / 2 / m
    else:
        top = max(vs) or 1
        zero_y = _H - _PADY
        scale = (_H - 2 * _PADY) / top
    bw = max(step - 3, 2)
    parts = [f'<line x1="8" y1="{zero_y:.1f}" x2="{w-8}" y2="{zero_y:.1f}" '
             f'stroke="{_AXIS}" stroke-width="0.6" vector-effect="non-scaling-stroke"/>']
    for i, v in pts:
        x = 8 + i * step + (step - bw) / 2
        hbar = abs(v) * scale
        y = zero_y - hbar if (v >= 0 or not signed) else zero_y
        color = _UP if v >= 0 else _DOWN
        d = dates[i] if dates and i < len(dates) else i
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(hbar,0.6):.1f}" '
                     f'fill="{color}"><title>{_esc(d)}: {fmt.format(v)}{unit}</title></rect>')
    return _svg("".join(parts), w)


def line(values, dates=None, unit="", fmt="{:,.2f}", color=_LINE) -> str:
    """折線圖（縮放到 [min,max]）；stroke 不隨拉伸變形。資料<2 點回提示。"""
    pts = _clean(values)
    if len(pts) < 2:
        return "<p class='note'>（資料不足）</p>"
    n = len(values)
    w = max(n * 16 + 16, 80)
    step = (w - 16) / max(n - 1, 1)
    vs = [v for _, v in pts]
    lo, hi = min(vs), max(vs)
    rng = (hi - lo) or 1

    def X(i):
        return 8 + i * step

    def Y(v):
        return _PADY + (hi - v) / rng * (_H - 2 * _PADY)

    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in pts)
    dots = "".join(
        f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="1.8" fill="{color}">'
        f'<title>{_esc(dates[i] if dates and i < len(dates) else i)}: {fmt.format(v)}{unit}</title></circle>'
        for i, v in pts)
    pl = (f'<polyline fill="none" stroke="{color}" stroke-width="1.6" '
          f'vector-effect="non-scaling-stroke" points="{poly}"/>')
    return _svg(pl + dots, w)
