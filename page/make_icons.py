"""Draw the app icons: the "settled line" mark on the ink-blue page: red noise that calms into a white line.

Run once: python page/make_icons.py  ->  page/static/icons/*.png
"""
from pathlib import Path

from PIL import Image, ImageDraw

INK = (28, 43, 90)        # the page's ink blue (#1C2B5A)
NOISE = (255, 133, 118)   # the noise, in the night page's red (#FF8576)
LINE = (241, 243, 248)    # the settled line, paper white
OUT = Path(__file__).parent / "static" / "icons"

# The same path as the SVG mark (viewBox 96x36): start point, then cubic segments; "S" mirrors the last control point.
START = (2, 18)
SEGMENTS = [("C", (8, 4), (14, 4), (18, 18)), ("S", (28, 32), (32, 18)), ("S", (40, 8), (44, 18)),
            ("S", (50, 26), (54, 18)), ("S", (62, 18), (66, 18)), ("L", (94, 18))]


def path_points(steps=60):
    pts, cur, last_ctrl = [START], START, None
    for seg in SEGMENTS:
        if seg[0] == "L":
            end = seg[1]
            pts += [(cur[0] + (end[0] - cur[0]) * t / steps, cur[1] + (end[1] - cur[1]) * t / steps) for t in range(1, steps + 1)]
            cur, last_ctrl = end, None
            continue
        if seg[0] == "C":
            c1, c2, end = seg[1], seg[2], seg[3]
        else:  # S: first control point is the reflection of the previous second control point
            c2, end = seg[1], seg[2]
            c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
        for i in range(1, steps + 1):
            t = i / steps
            mt = 1 - t
            x = mt**3 * cur[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * end[0]
            y = mt**3 * cur[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * end[1]
            pts.append((x, y))
        cur, last_ctrl = end, c2
    return pts


def icon(size, mark_width_ratio, radius_ratio=0.0):
    ss = 4  # draw large, then shrink, for smooth edges
    big = size * ss
    img = Image.new("RGB", (big, big), INK)
    d = ImageDraw.Draw(img)
    scale = big * mark_width_ratio / 92  # path spans x 2..94
    ox = (big - 92 * scale) / 2 - 2 * scale
    oy = big / 2 - 18 * scale
    r = 2.2 * scale  # stroke radius (stroke width 4.4 in path units)
    for x, y in path_points():
        cx, cy = ox + x * scale, oy + y * scale
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=NOISE if x < 64 else LINE)  # noise until the line settles
    # Mirror for Hebrew readers: the noise is on the right and settles into the line on the left.
    img = img.transpose(Image.FLIP_LEFT_RIGHT).resize((size, size), Image.LANCZOS)
    if radius_ratio:
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * ss - 1, size * ss - 1), radius=int(size * ss * radius_ratio), fill=255)
        rgba = img.convert("RGBA")
        rgba.putalpha(mask.resize((size, size), Image.LANCZOS))
        return rgba
    return img


def badge(size):
    """Android's status-bar icon for notifications: the mark alone, white on transparent (Android tints it)."""
    ss = 4
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    scale = big * 0.86 / 92
    ox, oy = (big - 92 * scale) / 2 - 2 * scale, big / 2 - 18 * scale
    r = 3.2 * scale   # a heavier stroke than the app icon, so it reads at 24px
    for x, y in path_points():
        cx, cy = ox + x * scale, oy + y * scale
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 255))
    return img.transpose(Image.FLIP_LEFT_RIGHT).resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192, 0.72, 0.22).save(OUT / "icon-192.png")
    icon(512, 0.72, 0.22).save(OUT / "icon-512.png")
    icon(512, 0.56).save(OUT / "icon-maskable-512.png")   # full bleed; mark inside the 80% safe zone
    icon(180, 0.66).save(OUT / "apple-touch-icon.png")     # iOS rounds the corners itself
    badge(96).save(OUT / "badge-96.png")                    # notification badge (Android status bar)
    print("icons written to", OUT)
