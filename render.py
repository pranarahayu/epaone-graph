"""
Pillow renderer for EPA ONE social graphics: STANDINGS, FIXTURES, RESULTS.

Every template adapts to the chosen social-media format (portrait, square,
story, landscape). Drawing is done at 2x and down-sampled for smooth edges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

BASE_DIR = Path(__file__).parent
FONT_DIR = BASE_DIR / "assets" / "fonts"
SS = 2  # supersampling factor

ImgLike = Union[str, Path, Image.Image, None]

# --------------------------------------------------------------------------- #
# Output formats
# --------------------------------------------------------------------------- #
FORMATS: dict[str, tuple[int, int]] = {
    "Instagram — Feed portrait 4:5 (1080×1350)": (1080, 1350),
    "Instagram — Square 1:1 (1080×1080)": (1080, 1080),
    "Instagram — Story / Reels 9:16 (1080×1920)": (1080, 1920),
    "Facebook — Feed portrait 4:5 (1080×1350)": (1080, 1350),
    "Facebook — Landscape 1.91:1 (1200×630)": (1200, 630),
    "X / Twitter — Landscape 16:9 (1600×900)": (1600, 900),
    "TikTok — 9:16 (1080×1920)": (1080, 1920),
    "WhatsApp Status — 9:16 (1080×1920)": (1080, 1920),
    "YouTube Community — Square (1080×1080)": (1080, 1080),
}
DEFAULT_FORMAT = next(iter(FORMATS))

# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
Stops = list  # [(position 0..1, "#rrggbb"), ...]


@dataclass
class Theme:
    """Colours sampled from the official EPA ONE artwork. Every *_stops value is a
    gradient: a list of (position 0..1, hex colour). '_h' = horizontal gradient."""
    bg_stops: Stops = field(default_factory=lambda: [(0, "#1a6ae3"), (0.07, "#0b3f91"), (1, "#073a89")])
    panel_stops: Stops = field(default_factory=lambda: [(0, "#0d4ea6"), (0.14, "#002b6a"),
                                                        (0.55, "#002b6a"), (1, "#1868dc")])
    header_bar_stops: Stops = field(default_factory=lambda: [(0, "#312cb8"), (1, "#08061d")])
    row_stops: Stops = field(default_factory=lambda: [(0, "#0c2f7b"), (1, "#5f8dec")])
    leader_stops_h: Stops = field(default_factory=lambda: [(0, "#a7a7a7"), (0.5, "#e2e2e2"), (1, "#ffffff")])
    fixture_pill_stops: Stops = field(default_factory=lambda: [(0, "#fbfcfc"), (1, "#c8cbd1")])
    result_pill_stops_h: Stops = field(default_factory=lambda: [(0, "#a9a8a7"), (0.5, "#dcdcdc"), (1, "#ffffff")])
    navy: str = "#0f2a86"
    title: str = "#ffffff"
    subtitle: str = "#ffffff"
    group_label: str = "#dbe4ff"
    photo_overlay: str = "#002b6a"


def _hex(c: str, a: int = 255) -> tuple:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


# --------------------------------------------------------------------------- #
# Fonts — two roles: "heading" (title block) and "body" (names, numbers, labels).
# Weight keys: black (900), xbold (800), bold (700), semi (600). Prefix "h_" = heading.
# --------------------------------------------------------------------------- #
_BUNDLED = {
    "black": FONT_DIR / "Inter-Black.otf", "xbold": FONT_DIR / "Inter-ExtraBold.otf",
    "bold": FONT_DIR / "Inter-Bold.otf", "semi": FONT_DIR / "Inter-SemiBold.otf",
}
_FONTS: dict[str, dict[str, Path]] = {"heading": dict(_BUNDLED), "body": dict(_BUNDLED)}


def set_fonts(heading: Optional[dict] = None, body: Optional[dict] = None):
    """heading/body: {"black": path, "xbold": path, "bold": path, "semi": path}.
    Missing weights fall back to the nearest one given, then to bundled Inter."""
    for role, files in (("heading", heading), ("body", body)):
        merged = dict(_BUNDLED)
        if files:
            order = ["black", "xbold", "bold", "semi"]
            given = {k: Path(v) for k, v in files.items() if v and Path(v).exists()}
            for w in order:
                if w in given:
                    merged[w] = given[w]
                elif given:
                    i = order.index(w)
                    near = sorted(given, key=lambda g: abs(order.index(g) - i))[0]
                    merged[w] = given[near]
        _FONTS[role] = merged


@lru_cache(maxsize=1024)
def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default(size)


def font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    role = "heading" if weight.startswith("h_") else "body"
    w = weight[2:] if weight.startswith("h_") else weight
    return _load_font(str(_FONTS[role][w]), max(6, int(round(size))))


def text_w(d: ImageDraw.ImageDraw, s: str, f) -> float:
    return d.textlength(s, font=f)


def fit_lines(d, text: str, weight: str, max_w: float, size: float, min_size: float,
              max_lines: int = 2):
    """Shrink and/or wrap text so it fits max_w. Returns (font, [lines])."""
    words = text.split()
    s = size
    while s >= min_size:
        f = font(weight, s)
        if text_w(d, text, f) <= max_w:
            return f, [text]
        if max_lines >= 2 and len(words) > 1:
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                w = max(text_w(d, a, f), text_w(d, b, f))
                if w <= max_w and (best is None or w < best[0]):
                    best = (w, [a, b])
            if best:
                return f, best[1]
        s -= 1
    f = font(weight, min_size)
    return f, [text]


def draw_lines(d, lines, f, x, cy, fill, anchor_x="l", line_gap=1.05):
    """Draw 1-2 lines vertically centred on cy. anchor_x: l, m or r."""
    asc, desc = f.getmetrics()
    lh = (asc + desc) * line_gap
    total = lh * len(lines)
    y = cy - total / 2
    for ln in lines:
        d.text((x, y + lh / 2), ln, font=f, fill=fill, anchor=anchor_x + "m")
        y += lh


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def load_img(src: ImgLike) -> Optional[Image.Image]:
    if src is None:
        return None
    if isinstance(src, Image.Image):
        return src.convert("RGBA")
    try:
        p = Path(src)
        if p.exists():
            return Image.open(p).convert("RGBA")
    except Exception:
        pass
    try:  # file-like (e.g. Streamlit UploadedFile)
        return Image.open(src).convert("RGBA")
    except Exception:
        return None


def trim(img: Image.Image) -> Image.Image:
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def contain(img: Image.Image, w: float, h: float) -> Image.Image:
    img = trim(img)
    r = min(w / img.width, h / img.height)
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)


def cover(img: Image.Image, w: int, h: int, focus_y: float = 0.4, focus_x: float = 0.5) -> Image.Image:
    return ImageOps.fit(img.convert("RGBA"), (int(w), int(h)), Image.LANCZOS,
                        centering=(focus_x, focus_y))


def grad(w: int, h: int, stops, horizontal: bool = False) -> Image.Image:
    """Multi-stop linear gradient. stops: [(pos, hex), ...] or a (top, bottom) pair of hex strings."""
    if isinstance(stops, tuple) and len(stops) == 2 and isinstance(stops[0], str):
        stops = [(0, stops[0]), (1, stops[1])]
    stops = sorted((float(p), _hex(c)) for p, c in stops)
    strip = Image.new("RGBA", (256, 1) if horizontal else (1, 256))
    for i in range(256):
        t = i / 255
        lo = max([s for s in stops if s[0] <= t] or [stops[0]], key=lambda s: s[0])
        hi = min([s for s in stops if s[0] >= t] or [stops[-1]], key=lambda s: s[0])
        k = 0 if hi[0] == lo[0] else (t - lo[0]) / (hi[0] - lo[0])
        c = tuple(int(lo[1][j] + (hi[1][j] - lo[1][j]) * k) for j in range(4))
        strip.putpixel((i, 0) if horizontal else (0, i), c)
    return strip.resize((max(1, int(w)), max(1, int(h))), Image.BILINEAR)


def vgrad(w: int, h: int, top: str, bottom: str) -> Image.Image:
    return grad(w, h, [(0, top), (1, bottom)])


def rmask(w: int, h: int, r: float) -> Image.Image:
    m = Image.new("L", (int(w), int(h)), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, int(w) - 1, int(h) - 1), radius=int(r), fill=255)
    return m


def paste_grad_rrect(canvas: Image.Image, box, r, stops, horizontal=False, border=None, bw=0):
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    canvas.paste(grad(w, h, stops, horizontal), (x0, y0), rmask(w, h, r))
    if border and bw:
        ImageDraw.Draw(canvas).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=int(r),
                                                 outline=_hex(border), width=int(bw))


def paste_photo(canvas, img, box, r, border=None, bw=0, focus_y=0.4):
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    if img is None:  # placeholder until a photo is uploaded
        paste_grad_rrect(canvas, box, r, [(0, "#1d55b8"), (1, "#0e3c8f")])
        d = ImageDraw.Draw(canvas)
        s = min(w, h)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - s * 0.05
        cw, ch = s * 0.22, s * 0.15  # simple camera icon
        col = (255, 255, 255, 110)
        d.rounded_rectangle((cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2), radius=s * 0.025,
                            outline=col, width=max(2, int(s * 0.012)))
        d.rounded_rectangle((cx - cw * 0.18, cy - ch / 2 - s * 0.03, cx + cw * 0.18, cy - ch / 2),
                            radius=s * 0.01, fill=col)
        rr = ch * 0.3
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=col, width=max(2, int(s * 0.012)))
        d.text((cx, cy + ch / 2 + s * 0.07), "PHOTO", font=font("black", s * 0.07), fill=col, anchor="mm")
    else:
        canvas.paste(cover(img, w, h, focus_y), (x0, y0), rmask(w, h, r))
    if border and bw:
        ImageDraw.Draw(canvas).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=int(r),
                                                 outline=_hex(border), width=int(bw))


def initials_badge(name: str, size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=_hex("#e9edf7"), outline=_hex("#0f2a86"), width=max(2, size // 20))
    words = [w for w in name.replace("FC", "").split() if w]
    ini = "".join(w[0] for w in words[:3]).upper() or "?"
    d.text((size / 2, size / 2), ini, font=font("black", size * (0.38 if len(ini) < 3 else 0.3)),
           fill=_hex("#0f2a86"), anchor="mm")
    return im


def paste_logo(canvas, src: ImgLike, name: str, cx, cy, box_w, box_h=None):
    box_h = box_h or box_w
    img = load_img(src)
    if img is None:
        img = initials_badge(name, int(min(box_w, box_h)))
    else:
        img = contain(img, box_w, box_h)
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


# --------------------------------------------------------------------------- #
# Frame (background, panel, header, carousel dots)
# --------------------------------------------------------------------------- #
@dataclass
class Brand:
    league_logo: ImgLike = None      # top-left (e.g. I.League)
    comp_logo: ImgLike = None        # top-right (EPA ONE)
    age_badge: ImgLike = None        # centre (EPA ONE U-16 / U-18 / U-20)
    age_label: str = "U-16"


@dataclass
class Frame:
    W: int
    H: int
    k: float          # design scale (1.0 == 1080×1350 portrait), already × SS
    landscape: bool
    panel: tuple
    content: tuple    # x0, y0, x1, y1 available for the template body
    bg: Image.Image   # background + panel
    fg: Image.Image   # foreground layer (gets a soft shadow on compose)


def _stripes(w, h, k, alpha=14, spacing=26, width=9):
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    sp, sw = int(spacing * k), max(1, int(width * k))
    for x in range(-h, w + h, sp):
        d.line([(x, h), (x + h, 0)], fill=(255, 255, 255, alpha), width=sw)
    return lay


def make_frame(fmt: tuple[int, int], theme: Theme, brand: Brand, title: str,
               sub1: str, sub2: str, dots: tuple[int, int] = (0, 0)) -> Frame:
    W0, H0 = fmt
    W, H = W0 * SS, H0 * SS
    landscape = W0 > H0
    k = (H0 / 1180 if landscape else min(W0 / 1080, H0 / 1350)) * SS

    bg = grad(W, H, theme.bg_stops)
    # diagonal stripe texture on lower-left and upper-right corners
    st = _stripes(W, H, k, alpha=16)
    fade = Image.linear_gradient("L").resize((W, H))
    fade = Image.eval(fade, lambda v: max(0, v - 120) * 2)
    bg.alpha_composite(Image.composite(st, Image.new("RGBA", (W, H), (0, 0, 0, 0)), fade))

    mx = 42 * k
    top = 62 * k if not landscape else 40 * k
    bottom = (H - 52 * k) if dots[1] > 1 else (H - 40 * k)
    panel = (mx, top, W - mx, bottom)
    pw, ph = int(panel[2] - panel[0]), int(panel[3] - panel[1])
    pimg = grad(pw, ph, theme.panel_stops)
    pst = _stripes(pw, ph, k, alpha=10, spacing=34, width=12)
    pf = Image.linear_gradient("L").resize((pw, ph)).rotate(0)
    pf = Image.eval(pf, lambda v: max(0, v - 150) * 2)
    pimg.alpha_composite(Image.composite(pst, Image.new("RGBA", (pw, ph), (0, 0, 0, 0)), pf))
    bg.paste(pimg, (int(panel[0]), int(panel[1])), rmask(pw, ph, 40 * k))

    fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)

    # corner logos
    league_h = 98 * k
    paste_logo(fg, brand.league_logo, "I.League", 32 * k + league_h * 0.5, 30 * k + league_h * 0.55,
               league_h, league_h) if load_img(brand.league_logo) else None
    comp_h = 112 * k
    if load_img(brand.comp_logo):
        img = contain(load_img(brand.comp_logo), comp_h * 1.2, comp_h)
        fg.alpha_composite(img, (int(W - 34 * k - img.width), int(28 * k)))

    # centre header: badge + title block
    head_top = panel[1] + (14 * k if not landscape else 10 * k)
    badge_h = 170 * k
    title_f = font("h_black", 70 * k)
    sub1_f = font("h_xbold", 38 * k)
    sub2_f = font("h_bold", 27 * k)
    tw = max(text_w(d, title, title_f), text_w(d, sub1, sub1_f), text_w(d, sub2, sub2_f))
    badge = load_img(brand.age_badge)
    if badge is not None:
        badge = contain(badge, badge_h * 1.05, badge_h)
        bw = badge.width
    else:
        bw = 150 * k
    gap = 26 * k
    total = bw + gap + tw
    x0 = W / 2 - total / 2
    if badge is not None:
        fg.alpha_composite(badge, (int(x0), int(head_top)))
    else:  # text fallback badge
        cx = x0 + bw / 2
        d.text((cx, head_top + badge_h * 0.42), "EPAONE", font=font("h_black", 38 * k), fill="white", anchor="mm")
        d.text((cx, head_top + badge_h * 0.72), brand.age_label, font=font("h_black", 40 * k), fill="white", anchor="mm")
    tx = x0 + bw + gap
    ty = head_top + 8 * k
    d.text((tx, ty), title, font=title_f, fill=_hex(theme.title), anchor="lt")
    ty += 74 * k
    if sub1:
        d.text((tx, ty), sub1, font=sub1_f, fill=_hex(theme.subtitle), anchor="lt")
        ty += 40 * k
    if sub2:
        d.text((tx, ty), sub2, font=sub2_f, fill=_hex(theme.subtitle), anchor="lt")
    head_bottom = head_top + badge_h

    # carousel dots
    cur, n = dots
    if n > 1:
        r = 6 * k
        gap_d = 22 * k
        sx = W / 2 - (n - 1) * gap_d / 2
        cy = (panel[3] + H) / 2
        for i in range(n):
            col = (255, 255, 255, 255) if i == cur else (255, 255, 255, 110)
            d.ellipse((sx + i * gap_d - r, cy - r, sx + i * gap_d + r, cy + r), fill=col)

    pad_x = 70 * k if not landscape else 60 * k
    content = (panel[0] + pad_x, head_bottom + 36 * k, panel[2] - pad_x, panel[3] - 42 * k)
    return Frame(W, H, k, landscape, panel, content, bg, fg)


def compose(fr: Frame, body: Image.Image, shadow=True) -> Image.Image:
    """bg  ->  soft shadow of body  ->  body  ->  header fg. Returns final RGB at 1x."""
    out = fr.bg.copy()
    if shadow:
        a = body.getchannel("A").filter(ImageFilter.GaussianBlur(7 * fr.k))
        sh = Image.new("RGBA", body.size, (2, 10, 50, 0))
        sh.putalpha(a.point(lambda v: int(v * 0.55)))
        out.alpha_composite(sh, (0, int(5 * fr.k)))
    out.alpha_composite(body)
    out.alpha_composite(fr.fg)
    return out.resize((fr.W // SS, fr.H // SS), Image.LANCZOS).convert("RGB")


# --------------------------------------------------------------------------- #
# STANDINGS
# --------------------------------------------------------------------------- #
@dataclass
class StandingRow:
    pos: int
    name: str
    logo: ImgLike
    p: int
    gd: int
    pts: int


def render_standings(rows: list[StandingRow], fmt, brand: Brand, theme: Theme = Theme(),
                     title="STANDINGS", sub1="GRUP A", sub2="GAMEWEEK 8", photo: ImgLike = None,
                     photo_focus=0.35, highlight_top=1, dots=(0, 0), placeholder=True, col_labels=("POS", "CLUB", "P", "+/-", "PTS")):
    fr = make_frame(fmt, theme, brand, title, sub1, sub2, dots)
    k = fr.k
    body = Image.new("RGBA", (fr.W, fr.H), (0, 0, 0, 0))
    x0, y0, x1, y1 = fr.content
    img = load_img(photo)
    show_photo = img is not None or placeholder
    n = max(1, len(rows))

    hdr_h = 52 * k
    gap = 6 * k
    if fr.landscape:
        # photo left, table right
        split = x0 + (x1 - x0) * (0.42 if show_photo else 0)
        if show_photo:
            paste_photo(body, img, (x0, y0, split - 24 * k, y1), 18 * k, focus_y=photo_focus)
        tx0 = split
        rh = min(70 * k, (y1 - y0 - hdr_h - gap * (n + 1)) / n)
        ty = y0 + ((y1 - y0) - (hdr_h + gap + n * (rh + gap))) / 2
    else:
        tx0 = x0
        if show_photo:
            rh = min(54 * k, (y1 - y0) * 0.5 / (n + 1))
            table_h = hdr_h + gap + n * (rh + gap)
            ph_bottom = y1 - table_h - 2 * k
            paste_photo(body, img, (x0, y0, x1, ph_bottom), 18 * k, focus_y=photo_focus)
            ty = ph_bottom + 2 * k
        else:
            rh = min(82 * k, (y1 - y0 - hdr_h - gap * (n + 1)) / n)
            table_h = hdr_h + gap + n * (rh + gap)
            ty = y0 + ((y1 - y0) - table_h) / 2
    tx1 = x1
    TW = tx1 - tx0
    d = ImageDraw.Draw(body)

    cx_pos = tx0 + TW * 0.075
    cx_logo = tx0 + TW * 0.175
    x_club = tx0 + TW * 0.235
    cx_p, cx_gd, cx_pts = tx0 + TW * 0.755, tx0 + TW * 0.845, tx0 + TW * 0.935
    club_max_w = cx_p - x_club - TW * 0.05

    # header bar
    paste_grad_rrect(body, (tx0, ty, tx1, ty + hdr_h), 12 * k, theme.header_bar_stops,
                     False, "#ffffff", 3 * k)
    hf = font("xbold", 25 * k)
    cy = ty + hdr_h / 2
    lp, lc, lpp, lgd, lpts = col_labels
    d.text((cx_pos, cy), lp, font=hf, fill="white", anchor="mm")
    d.text((x_club, cy), lc, font=hf, fill="white", anchor="lm")
    d.text((cx_p, cy), lpp, font=hf, fill="white", anchor="mm")
    d.text((cx_gd, cy), lgd, font=hf, fill="white", anchor="mm")
    d.text((cx_pts, cy), lpts, font=hf, fill="white", anchor="mm")

    y = ty + hdr_h + gap
    for i, r in enumerate(rows):
        lead = i < highlight_top
        stops, horiz = (theme.leader_stops_h, True) if lead else (theme.row_stops, False)
        txt = _hex(theme.navy) if lead else (255, 255, 255, 255)
        paste_grad_rrect(body, (tx0, y, tx1, y + rh), 12 * k, stops, horiz, "#ffffff", 2.5 * k)
        cy = y + rh / 2
        num_size = min(30 * k, rh * 0.52)
        d.text((cx_pos, cy), str(r.pos), font=font("black", num_size), fill=txt, anchor="mm")
        paste_logo(body, r.logo, r.name, cx_logo, cy, rh * 0.82)
        f, lines = fit_lines(d, r.name, "black", club_max_w, min(24 * k, rh * 0.42), 14 * k,
                             max_lines=2 if rh > 60 * k else 1)
        draw_lines(d, lines, f, x_club, cy, txt, "l")
        d.text((cx_p, cy), str(r.p), font=font("black", num_size), fill=txt, anchor="mm")
        d.text((cx_gd, cy), f"{r.gd}", font=font("black", num_size), fill=txt, anchor="mm")
        d.text((cx_pts, cy), str(r.pts), font=font("black", num_size * 1.06), fill=txt, anchor="mm")
        y += rh + gap
    return compose(fr, body)


# --------------------------------------------------------------------------- #
# FIXTURES
# --------------------------------------------------------------------------- #
@dataclass
class FixtureRow:
    group_label: str
    home: str
    home_logo: ImgLike
    away: str
    away_logo: ImgLike
    note: str = ""        # optional line under the pill, e.g. "SAT 24 OCT · 15:00 · Lapangan JSI"


def _fixture_pill(body, d, theme, r: FixtureRow, x0, x1, y, ph, lh, k, vs_text):
    cx = (x0 + x1) / 2
    if r.group_label:
        d.text((cx, y + lh * 0.5), r.group_label, font=font("black", min(20 * k, lh * 0.8)),
               fill=_hex(theme.group_label), anchor="mm")
    py = y + lh
    paste_grad_rrect(body, (x0, py, x1, py + ph), ph / 2, theme.fixture_pill_stops)
    cy = py + ph / 2
    logo = ph * 1.12
    paste_logo(body, r.home_logo, r.home, x0 + ph * 0.55, cy, logo)
    paste_logo(body, r.away_logo, r.away, x1 - ph * 0.55, cy, logo)
    vs_f = font("xbold", min(24 * k, ph * 0.38))
    d.text((cx, cy), vs_text, font=vs_f, fill=_hex(theme.navy), anchor="mm")
    gapc = 34 * k + text_w(d, vs_text, vs_f) / 2
    name_w = (x1 - x0) / 2 - gapc - ph * 1.25
    size = min(25 * k, ph * 0.36)
    f, lines = fit_lines(d, r.home.upper(), "black", name_w, size, 12 * k)
    draw_lines(d, lines, f, cx - gapc, cy, _hex(theme.navy), "r", 0.98)
    f, lines = fit_lines(d, r.away.upper(), "black", name_w, size, 12 * k)
    draw_lines(d, lines, f, cx + gapc, cy, _hex(theme.navy), "l", 0.98)
    if r.note:
        d.text((cx, py + ph + 4 * k), r.note, font=font("semi", min(17 * k, ph * 0.28)),
               fill=(255, 255, 255, 230), anchor="mt")


def render_fixtures(rows: list[FixtureRow], fmt, brand: Brand, theme: Theme = Theme(),
                    title="FIXTURES", sub1="GAMEWEEK 7 & 8", sub2="24-25 October 2026",
                    photo: ImgLike = None, photo_focus=0.45, photo_opacity=0.32, vs_text="vs", dots=(0, 0)):
    fr = make_frame(fmt, theme, brand, title, sub1, sub2, dots)
    k = fr.k
    # background photo washed in blue, clipped to the panel
    img = load_img(photo)
    if img is not None:
        px0, py0, px1, py1 = [int(v) for v in fr.panel]
        pw, ph = px1 - px0, py1 - py0
        ph_img = cover(img, pw, ph, photo_focus)
        tint = Image.new("RGBA", (pw, ph), _hex(theme.photo_overlay))
        mixed = Image.blend(tint, ph_img, photo_opacity)
        fr.bg.paste(mixed, (px0, py0), rmask(pw, ph, 40 * k))

    body = Image.new("RGBA", (fr.W, fr.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    x0, y0, x1, y1 = fr.content
    n = max(1, len(rows))
    has_note = any(r.note for r in rows)

    cols = 2 if (fr.landscape and n > 3) or (n > 12) else 1
    per_col = -(-n // cols)
    col_gap = 50 * k
    col_w = ((x1 - x0) - col_gap * (cols - 1)) / cols

    base_lh, base_ph, base_gap = 28 * k, 64 * k, (30 if has_note else 16) * k
    need = per_col * (base_lh + base_ph + base_gap)
    f = min(1.25, (y1 - y0) / need)
    lh, ph, gp = base_lh * f, base_ph * min(f, 1.15), base_gap * f
    block = lh + ph + gp
    start_y = y0 + max(0, ((y1 - y0) - per_col * block) / 2)
    inset = 0 if cols > 1 else (x1 - x0) * 0.0
    for i, r in enumerate(rows):
        c, j = divmod(i, per_col)
        cx0 = x0 + c * (col_w + col_gap) + inset
        _fixture_pill(body, d, theme, r, cx0, cx0 + col_w - 2 * inset, start_y + j * block, ph, lh, k, vs_text)
    return compose(fr, body)


# --------------------------------------------------------------------------- #
# RESULTS
# --------------------------------------------------------------------------- #
@dataclass
class ResultRow:
    home: str
    home_logo: ImgLike
    away: str
    away_logo: ImgLike
    score: str          # "2-1" (or "vs" / "PPD" for unplayed)


@dataclass
class ResultBlock:
    label: str                      # "GRUP A"
    rows: list[ResultRow] = field(default_factory=list)
    photo: ImgLike = None
    photo_focus: float = 0.4


def _score_pill(body, d, theme, r: ResultRow, x0, x1, y, ph, k):
    paste_grad_rrect(body, (x0, y, x1, y + ph), ph / 2, theme.result_pill_stops_h, True)
    cy = y + ph / 2
    paste_logo(body, r.home_logo, r.home, x0 + ph * 0.62, cy, ph * 0.9)
    paste_logo(body, r.away_logo, r.away, x1 - ph * 0.62, cy, ph * 0.9)
    s = r.score
    size = min(40 * k, ph * 0.6) if any(ch.isdigit() for ch in s) else min(28 * k, ph * 0.42)
    d.text(((x0 + x1) / 2, cy), s, font=font("black", size), fill=_hex(theme.navy), anchor="mm")


def render_results(blocks: list[ResultBlock], fmt, brand: Brand, theme: Theme = Theme(),
                   title="RESULTS", sub1="GAMEWEEK 5", sub2="17 October 2026", dots=(0, 0),
                   placeholder=True):
    fr = make_frame(fmt, theme, brand, title, sub1, sub2, dots)
    k = fr.k
    body = Image.new("RGBA", (fr.W, fr.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    x0, y0, x1, y1 = fr.content
    blocks = [b for b in blocks if b.rows] or blocks
    nb = max(1, len(blocks))

    if fr.landscape:
        # one column per group: photo on top, pills below
        col_gap = 34 * k
        cw = ((x1 - x0) - col_gap * (nb - 1)) / nb
        max_rows = max((len(b.rows) for b in blocks), default=1)
        lh, ph, gp = 40 * k, 58 * k, 12 * k
        pills_h = lh + max_rows * (ph + gp)
        avail = y1 - y0
        if pills_h > avail * 0.75:
            f = avail * 0.75 / pills_h
            lh, ph, gp, pills_h = lh * f, ph * f, gp * f, pills_h * f
        for i, b in enumerate(blocks):
            cx0 = x0 + i * (cw + col_gap)
            has_photo = load_img(b.photo) is not None or placeholder
            photo_h = (avail - pills_h - 16 * k) if has_photo else 0
            if has_photo:
                paste_photo(body, load_img(b.photo), (cx0, y0, cx0 + cw, y0 + photo_h), 16 * k,
                            "#ffffff", 4 * k, b.photo_focus)
            yy = y0 + (photo_h + 16 * k if has_photo else (avail - pills_h) / 2)
            d.text((cx0 + cw / 2, yy + lh / 2), b.label, font=font("black", 28 * k * min(1, lh / (40 * k))),
                   fill="white", anchor="mm")
            yy += lh
            for r in b.rows:
                _score_pill(body, d, theme, r, cx0, cx0 + cw, yy, ph, k)
                yy += ph + gp
        return compose(fr, body)

    # portrait / square / story: stacked blocks, photo alternating sides
    lh, ph, gp = 48 * k, 60 * k, 14 * k
    bgap = 34 * k
    needs = [lh + max(1, len(b.rows)) * (ph + gp) - gp for b in blocks]
    avail = (y1 - y0) - bgap * (nb - 1)
    total = sum(needs)
    if total > avail:
        f = avail / total
        lh, ph, gp = lh * f, ph * f, gp * f
        needs = [n * f for n in needs]
        total = avail
    extra = (avail - total) / nb
    max_extra = 260 * k  # stop photos from getting absurdly tall
    extra = min(extra, max_extra)
    used = total + extra * nb + bgap * (nb - 1)
    y = y0 + ((y1 - y0) - used) / 2

    W_c = x1 - x0
    pill_w = W_c * 0.52
    photo_w = W_c * 0.43
    for i, b in enumerate(blocks):
        bh = needs[i] + extra
        img = load_img(b.photo)
        photo_left = (i % 2 == 0)
        if img is not None or placeholder:
            if photo_left:
                pbox = (x0, y, x0 + photo_w, y + bh)
                px0, px1 = x1 - pill_w, x1
            else:
                pbox = (x1 - photo_w, y, x1, y + bh)
                px0, px1 = x0, x0 + pill_w
            paste_photo(body, img, pbox, 14 * k, "#ffffff", 4 * k, b.photo_focus)
        else:
            px0, px1 = x0 + (W_c - pill_w * 1.15) / 2, x0 + (W_c + pill_w * 1.15) / 2
        yy = y + (bh - needs[i]) / 2
        d.text(((px0 + px1) / 2, yy + lh * 0.45), b.label, font=font("black", min(30 * k, lh * 0.62)),
               fill="white", anchor="mm")
        yy += lh
        for r in b.rows:
            _score_pill(body, d, theme, r, px0, px1, yy, ph, k)
            yy += ph + gp
        y += bh + bgap
    return compose(fr, body)
