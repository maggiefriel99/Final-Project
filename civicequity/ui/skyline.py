"""NYC skyline illustrations as inline SVG: Brooklyn Bridge, One World Trade, Empire State,
Chrysler, and 432 Park over a field of generated buildings. Drawn in code so it scales cleanly
and needs no image files or licenses.
"""

import random

W, H = 1600, 300  # viewBox; ground line is y = H


def _rect(x, w, h):
    return f"M{x},{H} V{H - h} H{x + w} V{H}"


def _filler(rng, x0, x1, hmin, hmax, wmin=18, wmax=46, boxes=None):
    """Ordinary towers between the landmarks, some with setbacks or rooftop water tanks.
    Each tower's outline is added to boxes (x, width, height) so windows can be placed inside it."""
    parts, x = [], x0
    while x < x1:
        w = rng.randint(wmin, wmax)
        h = rng.randint(hmin, hmax)
        style = rng.random()
        if style < 0.25 and w > 26:  # setback tower
            inset = w // 4
            parts.append(f"M{x},{H} V{H - h} H{x + inset} V{H - h - 18} H{x + w - inset} V{H - h} H{x + w} V{H}")
        elif style < 0.4:  # water tank on the roof
            parts.append(_rect(x, w, h))
            tx = x + w // 2 - 4
            parts.append(f"M{tx},{H - h} V{H - h - 9} H{tx + 8} V{H - h} Z")
        else:
            parts.append(_rect(x, w, h))
        if boxes is not None:
            boxes.append((x, w, h))
        x += w + rng.randint(-4, 2)
    return parts


def _brooklyn_bridge(x0, x1, deck):
    """Two gothic towers, the deck, and the suspension cables."""
    t1, t2, tw, th = x0 + 90, x1 - 150, 34, 150
    shapes = []
    for tx in (t1, t2):
        # tower with two pointed arches cut out (evenodd fill)
        arch = lambda ax: (f"M{ax},{H - deck + 10} V{H - th + 62} Q{ax + 5},{H - th + 44} {ax + 10},{H - th + 62} "
                           f"V{H - deck + 10} Z")
        shapes.append(f"M{tx},{H} V{H - th} H{tx + tw} V{H} Z " + arch(tx + 5) + " " + arch(tx + tw - 15))
    shapes.append(f"M{x0},{H - deck} H{x1} V{H - deck + 6} H{x0} Z")  # deck
    cables = []
    mid = (t1 + t2 + tw) / 2
    cables.append(f"M{x0},{H - deck} Q{(x0 + t1) / 2},{H - deck - 30} {t1 + tw / 2},{H - th + 4}")
    cables.append(f"M{t1 + tw / 2},{H - th + 4} Q{mid},{H - deck + 30} {t2 + tw / 2},{H - th + 4}")
    cables.append(f"M{t2 + tw / 2},{H - th + 4} Q{(t2 + x1) / 2 + 20},{H - deck - 30} {x1},{H - deck}")
    for i in range(1, 16):  # vertical suspenders on the main span
        sx = t1 + tw + i * (t2 - t1 - tw) / 16
        frac = abs((sx - mid) / ((t2 - t1) / 2))
        sy = H - deck - (th - deck - 4) * frac ** 2 + 2
        cables.append(f"M{sx:.1f},{H - deck} V{sy:.1f}")
    return shapes, cables


def _one_wtc(x):
    w, h = 74, 236
    return [f"M{x},{H} V{H - 40} L{x + 8},{H - h} H{x + w - 8} L{x + w},{H - 40} V{H} Z",
            f"M{x + w / 2 - 2},{H - h} V{H - h - 52} H{x + w / 2 + 2} V{H - h} Z"]


def _empire_state(x):
    steps = [(96, 118), (70, 168), (50, 196), (32, 212), (16, 226), (6, 248)]
    parts = []
    for w, h in steps:
        cx = x + 48 - w / 2
        parts.append(_rect(cx, w, h))
    parts.append(f"M{x + 47.2},{H - 248} V{H - 282} H{x + 48.8} V{H - 248} Z")
    return parts


def _chrysler(x):
    w = 52
    parts = [_rect(x, w, 168)]
    for i, (iw, ih) in enumerate([(44, 182), (36, 194), (28, 205), (20, 215), (12, 224)]):
        cx = x + (w - iw) / 2
        r = iw / 2
        parts.append(f"M{cx},{H - ih + 14} A{r},{r} 0 0 1 {cx + iw},{H - ih + 14} V{H - 160} H{cx} Z")
    parts.append(f"M{x + w / 2 - 1.5},{H - 222} L{x + w / 2},{H - 268} L{x + w / 2 + 1.5},{H - 222} Z")
    return parts


def _windows(rng, boxes, per_box=(1, 6)):
    """Lit windows, only inside buildings."""
    out = []
    for x, w, h in boxes:
        for _ in range(rng.randint(*per_box)):
            wx = rng.uniform(x + 3, x + w - 6)
            wy = rng.uniform(H - h + 6, H - 8)
            out.append(f'<rect x="{wx:.0f}" y="{wy:.0f}" width="2.6" height="3.4" opacity="{rng.uniform(0.35, 0.95):.2f}"/>')
    return "".join(out)


def skyline_svg(variant: str = "hero", seed: int = 7) -> str:
    """variant: "hero" (night scene for dark banners) or "strip" (light line art for page headers)."""
    rng = random.Random(seed)
    back_boxes, front_boxes = [], []
    back = _filler(rng, 0, W, 50, 150, 22, 54, back_boxes)
    front = (_filler(rng, 470, 640, 60, 140, boxes=front_boxes) + _filler(rng, 720, 940, 70, 150, boxes=front_boxes)
             + _filler(rng, 1140, 1290, 60, 130, boxes=front_boxes) + _filler(rng, 1330, W, 40, 120, boxes=front_boxes))
    front_boxes += [(646, 62, 200), (970, 76, 118), (1072, 48, 160), (1291, 22, 230)]  # landmark bodies
    bridge, cables = _brooklyn_bridge(20, 470, 58)
    landmarks = (_one_wtc(640) + _empire_state(960) + _chrysler(1070)
                 + [_rect(1290, 24, 238)])  # 432 Park Avenue
    front_d = " ".join(front + landmarks)

    if variant == "strip":
        return (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg" '
                f'role="img" aria-label="New York City skyline">'
                f'<path d="{" ".join(back)}" fill="#EEEEEE"/>'
                f'<path d="{front_d}" fill="#DDDDDD"/>'
                f'<path d="{" ".join(bridge)}" fill="#DDDDDD" fill-rule="evenodd"/>'
                f'<path d="{" ".join(cables)}" stroke="#CCCCCC" stroke-width="1.4" fill="none"/></svg>')

    return (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="New York City skyline at night">'
            f'<defs><linearGradient id="ceGlow" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="#103FEF" stop-opacity="0"/>'
            f'<stop offset="0.75" stop-color="#FF7A1A" stop-opacity="0.18"/>'
            f'<stop offset="1" stop-color="#FF7A1A" stop-opacity="0.32"/></linearGradient></defs>'
            f'<rect x="0" y="120" width="{W}" height="{H - 120}" fill="url(#ceGlow)"/>'
            f'<path d="{" ".join(back)}" fill="#1B1F5C" opacity="0.85"/>'
            f'<g fill="#B5DBFF">{_windows(rng, back_boxes, (0, 2))}</g>'
            f'<path d="{front_d}" fill="#050510"/>'
            f'<path d="{" ".join(bridge)}" fill="#050510" fill-rule="evenodd"/>'
            f'<path d="{" ".join(cables)}" stroke="#050510" stroke-width="1.6" fill="none"/>'
            f'<g fill="#FFB320">{_windows(rng, front_boxes, (2, 7))}</g>'
            f'<rect x="0" y="{H - 2}" width="{W}" height="2" fill="#103FEF"/></svg>')
