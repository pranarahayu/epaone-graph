"""
Data loading & parsing for the EPA ONE graphics generator.

Sources supported (all return the same structure):
  * Google Sheets via a service account (gspread)        -> private sheets
  * Google Sheets shared "anyone with the link" (CSV)     -> no credentials
  * An uploaded .xlsx export of the master report         -> offline testing

Sheets used from the master report:
  * FAKTA_PERTANDINGAN  -> fixtures & results
  * KLASEMEN_MANUAL     -> official standings (already tie-broken by the sheet)
"""
from __future__ import annotations

import csv
import io
import re
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

BASE_DIR = Path(__file__).parent
CLUBS_CSV = BASE_DIR / "clubs.csv"
LOGO_DIR = BASE_DIR / "assets" / "logos"

MATCH_SHEET = "FAKTA_PERTANDINGAN"
STANDINGS_SHEET = "KLASEMEN_MANUAL"

# --------------------------------------------------------------------------- #
# Club names
# --------------------------------------------------------------------------- #
_AGE_SUFFIX = re.compile(r"\s*u\s*-?\s*(16|18|20)\s*$", re.I)


def norm(name: str) -> str:
    """Normalise a club name so 'PERSITA', 'Persita U16', 'Isen Mulang' all match."""
    if name is None:
        return ""
    s = _AGE_SUFFIX.sub("", str(name).strip())
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Club:
    name: str
    display: str
    logo: Optional[Path]


class ClubBook:
    """Maps any spelling of a club to its display name and logo file."""

    def __init__(self, csv_path: Path = CLUBS_CSV, logo_dir: Path = LOGO_DIR):
        self.logo_dir = logo_dir
        self._by_key: dict[str, Club] = {}
        if csv_path.exists():
            with open(csv_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = (row.get("name") or "").strip()
                    if not name:
                        continue
                    display = (row.get("display_name") or name).strip()
                    logo_file = (row.get("logo_file") or "").strip()
                    logo = (logo_dir / logo_file) if logo_file else None
                    club = Club(name, display, logo)
                    keys = [name] + [a for a in (row.get("aliases") or "").split(";") if a.strip()]
                    for k in keys:
                        self._by_key[norm(k)] = club

    def get(self, raw: str) -> Club:
        key = norm(raw)
        if key in self._by_key:
            return self._by_key[key]
        # Fallback: try a logo named after the normalised key.
        guess = None
        for ext in ("png", "webp", "jpg", "jpeg"):
            p = self.logo_dir / f"{key}.{ext}"
            if p.exists():
                guess = p
                break
        clean = _AGE_SUFFIX.sub("", str(raw).strip())
        return Club(clean, clean, guess)

    def logo_path(self, raw: str) -> Optional[Path]:
        p = self.get(raw).logo
        return p if (p and p.exists()) else None

    def unknown(self, names) -> list[str]:
        return sorted({n for n in names if n and norm(n) not in self._by_key})

    def missing_logos(self, names) -> list[str]:
        return sorted({n for n in names if n and self.logo_path(n) is None})


# --------------------------------------------------------------------------- #
# Value parsing helpers
# --------------------------------------------------------------------------- #
_ID_MONTHS = {
    "jan": 1, "januari": 1, "feb": 2, "februari": 2, "mar": 3, "maret": 3,
    "apr": 4, "april": 4, "mei": 5, "may": 5, "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7, "agu": 8, "agt": 8, "agus": 8, "agustus": 8,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
    "oct": 10, "oktober": 10, "october": 10, "nov": 11, "november": 11,
    "des": 12, "dec": 12, "desember": 12, "december": 12, "january": 1,
    "february": 2, "march": 3,
}


def parse_date(v) -> Optional[dt.date]:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)):  # Sheets serial number
        try:
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))).date()
        except Exception:
            return None
    s = str(v).strip()
    # strip weekday prefixes like "Sabtu, "
    s = re.sub(r"^[A-Za-z]+,\s*", "", s)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})[\s\-/]+([A-Za-z]+)[\s\-/,]+(\d{2,4})", s)
    if m and m.group(2).lower() in _ID_MONTHS:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return dt.date(y, _ID_MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def parse_score(v) -> Optional[tuple[int, int]]:
    if v is None:
        return None
    m = re.match(r"^\s*(\d+)\s*[-–:]\s*(\d+)\s*$", str(v))
    return (int(m.group(1)), int(m.group(2))) if m else None


def to_int(v, default=0) -> int:
    try:
        return int(float(str(v).replace(",", ".").strip()))
    except (ValueError, TypeError):
        return default


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# --------------------------------------------------------------------------- #
# Raw grid loaders  (each returns {sheet_name: list[list[str]]})
# --------------------------------------------------------------------------- #
def extract_sheet_id(url_or_id: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()


def load_grids_gspread(sheet: str, service_account_info: dict,
                       sheets=(MATCH_SHEET, STANDINGS_SHEET)) -> dict:
    import gspread  # imported lazily so offline mode doesn't need it

    gc = gspread.service_account_from_dict(dict(service_account_info))
    sh = gc.open_by_key(extract_sheet_id(sheet))
    out = {}
    titles = {ws.title for ws in sh.worksheets()}
    for name in sheets:
        if name in titles:
            out[name] = sh.worksheet(name).get_all_values()
    return out


def load_grids_public(sheet: str, sheets=(MATCH_SHEET, STANDINGS_SHEET)) -> dict:
    """For sheets shared as 'Anyone with the link can view'."""
    import urllib.parse
    import urllib.request

    sid = extract_sheet_id(sheet)
    out = {}
    for name in sheets:
        url = (f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&headers=0"
               f"&sheet={urllib.parse.quote(name)}")
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                text = r.read().decode("utf-8")
            out[name] = list(csv.reader(io.StringIO(text)))
        except Exception:
            pass
    return out


def load_grids_xlsx(file, sheets=(MATCH_SHEET, STANDINGS_SHEET)) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(file, data_only=True, read_only=True)
    out = {}
    for name in sheets:
        if name in wb.sheetnames:
            out[name] = [[_cell_str(c) for c in row] for row in wb[name].iter_rows(values_only=True)]
    return out


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def _find(header: list[str], *cands) -> Optional[int]:
    low = [h.strip().lower() for h in header]
    for c in cands:
        c = c.lower()
        for i, h in enumerate(low):
            if h == c:
                return i
    for c in cands:
        c = c.lower()
        for i, h in enumerate(low):
            if h.startswith(c):
                return i
    return None


def parse_matches(grid: list[list[str]]) -> pd.DataFrame:
    """FAKTA_PERTANDINGAN -> DataFrame(gw, group, home, away, score, hg, ag, played, date, time, venue, city)."""
    cols = ["gw", "group", "home", "away", "score", "hg", "ag", "played", "date", "time", "venue", "city"]
    if not grid:
        return pd.DataFrame(columns=cols)
    hdr_i = next((i for i, r in enumerate(grid[:15]) if any(str(c).strip().lower() == "tuan rumah" for c in r)), None)
    if hdr_i is None:
        return pd.DataFrame(columns=cols)
    header = [str(h) for h in grid[hdr_i]]
    rows = [r + [""] * (len(header) - len(r)) for r in grid[hdr_i + 1:]]

    i_gw = _find(header, "gw")
    i_home = _find(header, "tuan rumah")
    i_away = _find(header, "tamu")
    i_score = _find(header, "skor")
    i_date = _find(header, "tanggal")
    i_time = _find(header, "waktu")
    i_venue = _find(header, "lokasi")
    i_city = _find(header, "kota")

    # The group letter may sit in the 'Grup' or the 'NO' column - detect it.
    cand = [c for c in (_find(header, "grup"), _find(header, "no")) if c is not None]
    def letter_ratio(ci):
        vals = [str(r[ci]).strip() for r in rows if str(r[ci]).strip()]
        return sum(bool(re.fullmatch(r"[A-Za-z]", v)) for v in vals) / max(len(vals), 1)
    i_group = max(cand, key=letter_ratio) if cand else None

    recs = []
    for r in rows:
        home, away = str(r[i_home]).strip(), str(r[i_away]).strip()
        if not home or not away:
            continue
        sc = parse_score(r[i_score]) if i_score is not None else None
        recs.append(dict(
            gw=to_int(r[i_gw], 0) if i_gw is not None else 0,
            group=str(r[i_group]).strip().upper() if i_group is not None else "",
            home=home, away=away,
            score=f"{sc[0]}-{sc[1]}" if sc else "",
            hg=sc[0] if sc else None, ag=sc[1] if sc else None,
            played=sc is not None,
            date=parse_date(r[i_date]) if i_date is not None else None,
            time=str(r[i_time]).strip() if i_time is not None else "",
            venue=str(r[i_venue]).strip() if i_venue is not None else "",
            city=str(r[i_city]).strip() if i_city is not None else "",
        ))
    return pd.DataFrame(recs, columns=cols)


def parse_standings(grid: list[list[str]]) -> dict[str, pd.DataFrame]:
    """KLASEMEN_MANUAL -> {'A': DataFrame(pos, club, p, w, d, l, gf, ga, gd, pts), ...}"""
    out: dict[str, pd.DataFrame] = {}
    if not grid:
        return out
    group, header, rows = None, None, []

    def flush():
        if group and rows:
            out[group] = pd.DataFrame(rows)

    for r in grid:
        first = str(r[0]).strip() if r else ""
        m = re.fullmatch(r"GRUP\s+([A-Z])", first, re.I)
        if m:
            flush()
            group, header, rows = m.group(1).upper(), None, []
            continue
        if first.upper().startswith(("PERINGKAT", "PEMERINGKATAN")):
            flush()
            group, header, rows = None, None, []
            continue
        if group is None:
            continue
        if first.upper() == "POS":
            header = [str(h).strip().upper() for h in r]
            continue
        if header and first and len(r) > 1 and str(r[1]).strip():
            g = lambda *k: r[_find(header, *k)] if _find(header, *k) is not None else ""
            rows.append(dict(
                pos=to_int(first), club=str(r[1]).strip(),
                p=to_int(g("MAIN")), w=to_int(g("M")), d=to_int(g("S")), l=to_int(g("K")),
                gf=to_int(g("GM")), ga=to_int(g("GK")), gd=to_int(g("SG")), pts=to_int(g("POIN")),
            ))
    flush()
    return out


def _table(games, clubs: dict) -> dict:
    st = {k: dict(club=n, p=0, w=0, d=0, l=0, gf=0, ga=0) for k, n in clubs.items()}
    for h, a, hg, ag in games:
        if h not in st or a not in st:
            continue
        H, A = st[h], st[a]
        H["p"] += 1; A["p"] += 1
        H["gf"] += hg; H["ga"] += ag; A["gf"] += ag; A["ga"] += hg
        if hg > ag: H["w"] += 1; A["l"] += 1
        elif hg < ag: A["w"] += 1; H["l"] += 1
        else: H["d"] += 1; A["d"] += 1
    for v in st.values():
        v["gd"], v["pts"] = v["gf"] - v["ga"], v["w"] * 3 + v["d"]
    return st


def compute_standings(matches: pd.DataFrame, up_to_gw: Optional[int] = None) -> dict[str, pd.DataFrame]:
    """Table from results up to a gameweek: Pts > head-to-head (pts, GD, GF among tied clubs) > GD > GF.
    Used for past gameweeks or when KLASEMEN_MANUAL is unavailable."""
    m = matches if up_to_gw is None else matches[matches.gw <= up_to_gw]
    tables = {}
    for grp, gdf in matches.groupby("group"):  # all clubs of the group, even before they've played
        clubs = {}
        for c in list(gdf.home) + list(gdf.away):
            clubs.setdefault(norm(c), c)
        pg = m[(m.group == grp) & m.played]
        games = [(norm(h), norm(a), int(hg), int(ag)) for h, a, hg, ag in zip(pg.home, pg.away, pg.hg, pg.ag)]
        overall = _table(games, clubs)
        order = []
        by_pts: dict[int, list] = {}
        for k, v in overall.items():
            by_pts.setdefault(v["pts"], []).append(k)
        for pts in sorted(by_pts, reverse=True):
            tied = by_pts[pts]
            if len(tied) > 1:
                sub = {k: clubs[k] for k in tied}
                h2h = _table([g for g in games if g[0] in sub and g[1] in sub], sub)
                tied.sort(key=lambda k: (h2h[k]["pts"], h2h[k]["gd"], h2h[k]["gf"],
                                         overall[k]["gd"], overall[k]["gf"]), reverse=True)
            order += tied
        df = pd.DataFrame([overall[k] for k in order])
        df.insert(0, "pos", range(1, len(df) + 1))
        tables[grp] = df[["pos", "club", "p", "w", "d", "l", "gf", "ga", "gd", "pts"]]
    return tables


@dataclass
class Dataset:
    matches: pd.DataFrame
    standings: dict[str, pd.DataFrame]
    standings_source: str = "sheet"
    warnings: list[str] = field(default_factory=list)

    @property
    def groups(self) -> list[str]:
        g = set(self.matches.group.dropna()) | set(self.standings)
        return sorted(x for x in g if x)

    @property
    def gameweeks(self) -> list[int]:
        return sorted(int(x) for x in self.matches.gw.unique() if x)

    def last_played_gw(self) -> Optional[int]:
        p = self.matches[self.matches.played]
        return int(p.gw.max()) if len(p) else None

    def next_unplayed_gw(self) -> Optional[int]:
        played_gws = set(self.matches[self.matches.played].gw)
        for g in self.gameweeks:
            if g not in played_gws:
                return g
        return None

    def all_clubs(self) -> list[str]:
        names = set(self.matches.home) | set(self.matches.away)
        for df in self.standings.values():
            names |= set(df.club)
        return sorted(names)


def build_dataset(grids: dict) -> Dataset:
    warnings = []
    matches = parse_matches(grids.get(MATCH_SHEET, []))
    if matches.empty:
        warnings.append(f"No matches found in '{MATCH_SHEET}'.")
    standings = parse_standings(grids.get(STANDINGS_SHEET, []))
    source = "sheet"
    if not standings and not matches.empty:
        standings = compute_standings(matches)
        source = "computed"
        warnings.append(f"'{STANDINGS_SHEET}' not found — standings computed from results (Pts > GD > GF).")
    return Dataset(matches, standings, source, warnings)


# --------------------------------------------------------------------------- #
# Text helpers for headers
# --------------------------------------------------------------------------- #
MONTHS = {
    "English": ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"],
    "Indonesia": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
                  "Agustus", "September", "Oktober", "November", "Desember"],
}


def format_date_range(dates, lang: str = "English") -> str:
    ds = sorted({d for d in dates if d})
    if not ds:
        return ""
    mn = MONTHS[lang]
    a, b = ds[0], ds[-1]
    if a == b:
        return f"{a.day} {mn[a.month-1]} {a.year}"
    if (a.month, a.year) == (b.month, b.year):
        return f"{a.day}-{b.day} {mn[a.month-1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {mn[a.month-1]} - {b.day} {mn[b.month-1]} {b.year}"
    return f"{a.day} {mn[a.month-1]} {a.year} - {b.day} {mn[b.month-1]} {b.year}"


def core_dates(dates, window_days: int = 4) -> list:
    """Drop rescheduled outliers (e.g. a postponed match moved weeks later):
    keep dates within a few days of the gameweek's median date."""
    ds = sorted(d for d in dates if d)
    if len(ds) < 3:
        return ds
    med = ds[len(ds) // 2]
    return [d for d in ds if abs((d - med).days) <= window_days]


def format_gw_label(gws, word: str = "GAMEWEEK") -> str:
    gws = sorted(set(int(g) for g in gws))
    if not gws:
        return ""
    if len(gws) == 1:
        return f"{word} {gws[0]}"
    if gws == list(range(gws[0], gws[-1] + 1)) and len(gws) > 2:
        return f"{word} {gws[0]}-{gws[-1]}"
    return f"{word} " + " & ".join(map(str, gws)) if len(gws) == 2 else f"{word} " + ", ".join(map(str, gws))


def dedupe_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    """GW 7 & 8 are double-headers of the same pairing — show each pairing once."""
    seen, keep = set(), []
    for i, r in df.iterrows():
        k = (r.group, frozenset((norm(r.home), norm(r.away))))
        if k not in seen:
            seen.add(k)
            keep.append(i)
    return df.loc[keep]
