"""
Google Fonts downloader.

Tries two sources and keeps the first file that Pillow can actually open:
  1. the Google Fonts CSS API (static TTF per weight)
  2. the official google/fonts GitHub repository (static TTF, or a variable font
     that render.py instantiates at the right weight)

Files are cached in assets/fonts/cache/ so each font is downloaded once per server.
A returned font "spec" is a path string, optionally with "|wght=NNN" for variable fonts.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from PIL import ImageFont

CACHE_DIR = Path(__file__).parent / "assets" / "fonts" / "cache"

# weight keys used by render.py -> CSS weight -> static file style name
WEIGHTS = {"black": 900, "xbold": 800, "bold": 700, "semi": 600}
STYLE = {900: "Black", 800: "ExtraBold", 700: "Bold", 600: "SemiBold", 500: "Medium", 400: "Regular"}

# Curated list of bold sans-serifs for sports graphics. Lato is closest to the original artwork.
SUGGESTED = [
    "Lato", "Montserrat", "Poppins", "Plus Jakarta Sans", "Inter", "Roboto", "Rubik",
    "Barlow", "Barlow Condensed", "Archivo", "Kanit", "Exo 2", "Outfit", "Figtree", "Sora",
    "Red Hat Display", "Nunito Sans", "Work Sans", "Oswald", "Saira", "Titillium Web", "Chakra Petch",
]

# A plain, non-browser User-Agent: Google Fonts answers it with TTF files.
# (Old IE user agents get .eot, modern browsers get .woff2 — Pillow can't reliably open either.)
_UA = "epaone-graphics/1.0 (python urllib)"


def _get(url: str, timeout: int = 30, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def spec_path(spec: str) -> Path:
    return Path(str(spec).split("|", 1)[0])


def spec_weight(spec: str) -> Optional[int]:
    m = re.search(r"\|wght=(\d+)", str(spec))
    return int(m.group(1)) if m else None


def open_spec(spec: str, size: int) -> ImageFont.FreeTypeFont:
    """Open a font spec; for variable fonts set the weight axis. Raises if unusable."""
    f = ImageFont.truetype(str(spec_path(spec)), size)
    w = spec_weight(spec)
    if w is not None:
        axes = f.get_variation_axes()
        vals = []
        for ax in axes:
            name = ax.get("name", b"")
            name = name.decode(errors="ignore") if isinstance(name, bytes) else str(name)
            if name.lower().startswith("weight") or name.lower() == "wght":
                vals.append(max(ax["minimum"], min(ax["maximum"], w)))
            else:
                vals.append(ax["default"])
        f.set_variation_by_axes(vals)
    return f


def _usable(spec: str) -> bool:
    try:
        f = open_spec(spec, 40)
        bb = f.getbbox("AaGg19")
        return bb is not None and bb[2] > bb[0]
    except Exception:
        return False


def _slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


# --------------------------------------------------------------------------- #
# Source 1: Google Fonts CSS API
# --------------------------------------------------------------------------- #
def _from_css_api(family: str, weight: int, log: list) -> Optional[str]:
    out = CACHE_DIR / f"{_slug(family)}-{weight}.ttf"
    if out.exists() and _usable(str(out)):
        return str(out)
    fam = urllib.parse.quote_plus(family.strip())
    try:
        css = _get(f"https://fonts.googleapis.com/css2?family={fam}:wght@{weight}", accept="text/css").decode()
    except urllib.error.HTTPError as e:
        log.append(f"Google Fonts API: {family} {weight} → HTTP {e.code}" +
                   (" (font name or weight not available)" if e.code == 400 else ""))
        return None
    except Exception as e:
        log.append(f"Google Fonts API unreachable: {e}")
        return None
    blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*{([^}]*)}", css)
    body = next((b for name, b in blocks if name == "latin"), None) or css
    m = re.search(r"url\((https://[^)]+)\)", body)
    if not m:
        log.append(f"Google Fonts API: no file URL for {family} {weight}")
        return None
    try:
        data = _get(m.group(1))
    except Exception as e:
        log.append(f"Font file download failed: {e}")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    if _usable(str(out)):
        return str(out)
    log.append(f"Google Fonts API returned an unusable file for {family} {weight} "
               f"(starts with {data[:4]!r}); trying GitHub")
    out.unlink(missing_ok=True)
    return None


# --------------------------------------------------------------------------- #
# Source 2: github.com/google/fonts
# --------------------------------------------------------------------------- #
_GH_API = "https://api.github.com/repos/google/fonts/contents/{lic}/{dir}"
_GH_RAW = "https://raw.githubusercontent.com/google/fonts/main/{lic}/{dir}/{file}"


def _github_listing(family: str, log: list) -> tuple[Optional[str], Optional[str], list[str]]:
    d = re.sub(r"[^a-z0-9]", "", family.lower())
    cache = CACHE_DIR / f"_gh_{d}.json"
    if cache.exists():
        j = json.loads(cache.read_text())
        return j["lic"], d, j["files"]
    for lic in ("ofl", "apache", "ufl"):
        try:
            items = json.loads(_get(_GH_API.format(lic=lic, dir=d), accept="application/vnd.github+json"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            log.append(f"GitHub listing failed: HTTP {e.code}")
            return None, None, []
        except Exception as e:
            log.append(f"GitHub unreachable: {e}")
            return None, None, []
        files = [it["name"] for it in items if isinstance(it, dict) and it.get("name", "").endswith(".ttf")]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"lic": lic, "files": files}))
        return lic, d, files
    log.append(f"'{family}' not found in the Google Fonts repository — check the spelling on fonts.google.com")
    return None, None, []


def _from_github(family: str, weights: dict[str, int], log: list) -> dict[str, str]:
    lic, d, files = _github_listing(family, log)
    if not files:
        return {}
    upright = [f for f in files if "italic" not in f.lower()]
    base = re.sub(r"[^A-Za-z0-9]", "", family)
    found: dict[str, str] = {}

    def fetch(fname: str) -> Optional[Path]:
        out = CACHE_DIR / "gh" / fname
        if out.exists() and out.stat().st_size > 1000:
            return out
        try:
            data = _get(_GH_RAW.format(lic=lic, dir=d, file=urllib.parse.quote(fname)), timeout=60)
        except Exception as e:
            log.append(f"GitHub download failed for {fname}: {e}")
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    # static files first (e.g. Lato-Black.ttf)
    for key, w in weights.items():
        name = f"{base}-{STYLE[w]}.ttf"
        match = next((f for f in upright if f.lower() == name.lower()), None)
        if match:
            p = fetch(match)
            if p and _usable(str(p)):
                found[key] = str(p)
    if len(found) == len(weights):
        return found
    # variable font (e.g. Montserrat[wght].ttf) covers every remaining weight
    var = next((f for f in upright if "[" in f and "wght" in f), None)
    if var:
        p = fetch(var)
        if p:
            for key, w in weights.items():
                if key not in found:
                    spec = f"{p}|wght={w}"
                    if _usable(spec):
                        found[key] = spec
    return found


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def get_family(family: str) -> tuple[dict[str, str], list[str]]:
    """Return ({weight_key: font_spec}, log_messages). Missing weights are left out
    (render.py uses the nearest available weight)."""
    log: list[str] = []
    family = (family or "").strip()
    if not family:
        return {}, log
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for key, w in WEIGHTS.items():
        spec = _from_css_api(family, w, log)
        if spec:
            files[key] = spec
    missing = {k: w for k, w in WEIGHTS.items() if k not in files}
    if missing and len(missing) == len(WEIGHTS):  # API gave nothing usable -> GitHub
        files.update(_from_github(family, missing, log))
    if not files:  # some families only ship regular/bold
        for w in (700, 400):
            spec = _from_css_api(family, w, log)
            if spec:
                files["bold"] = spec
                break
    return files, log
