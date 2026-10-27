"""
Google Fonts downloader.

Fetches static TTF files from the public Google Fonts CSS API and caches them in
assets/fonts/cache/ so each font/weight is downloaded only once per server.
Any family on fonts.google.com can be used by its exact name.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).parent / "assets" / "fonts" / "cache"

# weight keys used by render.py -> CSS weight
WEIGHTS = {"black": 900, "xbold": 800, "bold": 700, "semi": 600}

# Curated list: bold, sporty sans-serifs that work for sports graphics.
# The first one is the closest match to the original EPA ONE artwork.
SUGGESTED = [
    "Lato",
    "Montserrat",
    "Poppins",
    "Plus Jakarta Sans",
    "Inter",
    "Roboto",
    "Rubik",
    "Barlow",
    "Barlow Condensed",
    "Archivo",
    "Kanit",
    "Exo 2",
    "Outfit",
    "Figtree",
    "Sora",
    "Red Hat Display",
    "Nunito Sans",
    "Work Sans",
    "Oswald",
    "Saira",
    "Titillium Web",
    "Chakra Petch",
]

# Google serves TTF (not WOFF2) to non-browser user agents, which is what Pillow needs.
_UA = "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"


def _slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def _css(family: str, weight: int) -> Optional[str]:
    fam = urllib.parse.quote_plus(family.strip())
    url = f"https://fonts.googleapis.com/css2?family={fam}:wght@{weight}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None  # 400 = family/weight doesn't exist


def _download(family: str, weight: int) -> Optional[Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{_slug(family)}-{weight}.ttf"
    if out.exists() and out.stat().st_size > 1000:
        return out
    css = _css(family, weight)
    if not css:
        return None
    # Prefer the latin subset block when the CSS lists several unicode ranges.
    blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*{([^}]*)}", css)
    body = next((b for name, b in blocks if name == "latin"), None) or css
    m = re.search(r"url\((https://[^)]+)\)", body)
    if not m:
        return None
    try:
        req = urllib.request.Request(m.group(1), headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        out.write_bytes(data)
        return out
    except Exception:
        return None


def get_family(family: str) -> dict[str, Path]:
    """Return {weight_key: ttf_path} for the weights this family offers.
    Missing weights are simply left out (render.py falls back to the nearest)."""
    if not family:
        return {}
    files = {}
    for key, w in WEIGHTS.items():
        p = _download(family, w)
        if p:
            files[key] = p
    if not files:  # some families only ship regular/bold
        for w in (700, 400):
            p = _download(family, w)
            if p:
                files["bold"] = p
                break
    return files
