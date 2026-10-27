"""
EPA ONE — Social Graphics Generator (Streamlit)

Run locally:   streamlit run app.py
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image

import data as D
import fonts as F
import render as R

BASE = Path(__file__).parent
BRAND_DIR = BASE / "assets" / "brand"
AGES = ["U16", "U18", "U20"]

st.set_page_config(page_title="EPA ONE Graphics", page_icon="⚽", layout="wide")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def secret(path: list[str], default=None):
    try:
        cur = st.secrets
        for p in path:
            cur = cur[p]
        return cur
    except Exception:
        return default


def brand_file(*names):
    for n in names:
        for ext in ("png", "webp", "jpg", "jpeg"):
            p = BRAND_DIR / f"{n}.{ext}"
            if p.exists():
                return p
    return None


@st.cache_data(ttl=300, show_spinner="Reading Google Sheet…")
def fetch_gspread(sheet: str, _sa: dict, cache_key: str):
    return D.load_grids_gspread(sheet, _sa)


@st.cache_data(ttl=300, show_spinner="Reading Google Sheet…")
def fetch_public(sheet: str):
    return D.load_grids_public(sheet)


@st.cache_data(show_spinner="Reading workbook…")
def fetch_xlsx(raw: bytes):
    return D.load_grids_xlsx(io.BytesIO(raw))


def to_jpg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=95, subsampling=0)
    return b.getvalue()


def downloads(images: list[tuple[str, Image.Image]], key: str):
    enc, ext, mime = to_jpg, "jpg", "image/jpeg"
    if len(images) == 1:
        name, img = images[0]
        st.download_button(f"⬇️ Download {ext.upper()}", enc(img), f"{name}.{ext}", mime,
                           key=f"dl_{key}", use_container_width=True, type="primary")
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, img in images:
                z.writestr(f"{name}.{ext}", enc(img))
        st.download_button(f"⬇️ Download all ({len(images)}) as ZIP", buf.getvalue(), f"{key}.zip",
                           "application/zip", key=f"dl_{key}", use_container_width=True, type="primary")


def show_grid(images: list[tuple[str, Image.Image]], key: str):
    cols = st.columns(min(3, len(images)) or 1)
    for i, (name, img) in enumerate(images):
        with cols[i % len(cols)]:
            st.image(img, caption=name, use_container_width=True)
    downloads(images, key)


def photo_input(label: str, key: str, help_text: str | None = None):
    """Uploader + vertical-focus slider. Returns (image_or_None, focus)."""
    up = st.file_uploader(label, type=["png", "jpg", "jpeg", "webp"], key=f"up_{key}", help=help_text)
    focus = st.slider("Photo crop — vertical focus (0 = top, 1 = bottom)", 0.0, 1.0, 0.4, 0.05,
                      key=f"fo_{key}") if up else 0.4
    return (Image.open(up) if up else None), focus


# --------------------------------------------------------------------------- #
# Sidebar — data source, platform, branding
# --------------------------------------------------------------------------- #
st.sidebar.title("⚽ EPA ONE Graphics")

age = st.sidebar.radio("Age category", AGES, horizontal=True)
age_label = age.replace("U", "U-")

sa_info = secret(["gcp_service_account"])
modes = ["Google Sheet (service account)", "Google Sheet (public link)", "Upload .xlsx"]
default_mode = 0 if sa_info else 1
mode = st.sidebar.selectbox("Data source", modes, index=default_mode)

grids = None
try:
    if mode == "Upload .xlsx":
        up = st.sidebar.file_uploader("Master Report workbook (.xlsx)", type=["xlsx"])
        if up:
            grids = fetch_xlsx(up.getvalue())
    else:
        sheet = st.sidebar.text_input(f"{age} spreadsheet URL / ID", value=secret(["sheets", age], ""),
                                      help="Set defaults in .streamlit/secrets.toml under [sheets].")
        if st.sidebar.button("🔄 Refresh data", use_container_width=True):
            st.cache_data.clear()
        if sheet:
            if mode.startswith("Google Sheet (service"):
                if not sa_info:
                    st.sidebar.error("No [gcp_service_account] in secrets — use the public-link mode or add credentials.")
                else:
                    grids = fetch_gspread(sheet, dict(sa_info), cache_key=f"{age}:{sheet}")
            else:
                grids = fetch_public(sheet)
except Exception as e:  # noqa: BLE001
    st.sidebar.error(f"Could not read the sheet: {e}")

st.sidebar.divider()
fmt_name = st.sidebar.selectbox("📱 Platform / size", list(R.FORMATS), index=0)
FMT = R.FORMATS[fmt_name]
placeholder = st.sidebar.toggle("Photo placeholder until a photo is uploaded", value=True,
                                help="Off = the layout uses the space instead (bigger table / centred results).")
lang = st.sidebar.selectbox("Date language", ["English", "Indonesia"])
gw_word = st.sidebar.text_input("Gameweek word", "GAMEWEEK")
group_word = st.sidebar.text_input("Group word", "GRUP")

BUNDLED = "Inter (bundled, offline)"
OTHER = "Other Google Font…"
UPLOAD = "Upload .ttf/.otf…"


@st.cache_resource(show_spinner="Downloading font from Google Fonts…")
def google_family(name: str):
    return F.get_family(name)


def font_picker(role: str, default: str):
    opts = F.SUGGESTED + [BUNDLED, OTHER, UPLOAD]
    choice = st.selectbox(f"{role} font", opts, index=opts.index(default), key=f"font_{role}")
    if choice == BUNDLED:
        return None, choice
    if choice == UPLOAD:
        ups = st.file_uploader(f"{role} font files (one per weight; name them …Black / …ExtraBold / …Bold)",
                               type=["ttf", "otf"], accept_multiple_files=True, key=f"fontup_{role}")
        files = {}
        for u in ups or []:
            dest = F.CACHE_DIR / "uploads" / u.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(u.getvalue())
            n = u.name.lower()
            key = ("black" if "black" in n or "heavy" in n else "xbold" if "extrabold" in n
                   else "semi" if "semibold" in n else "bold")
            files[key] = dest
        return files or None, "upload"
    name = st.text_input("Google Font name (exactly as on fonts.google.com)", key=f"fontname_{role}") \
        if choice == OTHER else choice
    if not name:
        return None, choice
    files = google_family(name)
    if not files:
        st.warning(f"Couldn't download '{name}' — using Inter. Check the spelling on fonts.google.com.")
    return files or None, name


with st.sidebar.expander("🔤 Fonts", expanded=False):
    st.caption("Fonts are downloaded from Google Fonts on first use and cached. "
               "Lato is the closest match to the original artwork.")
    h_files, h_name = font_picker("Heading", "Lato")
    same = st.checkbox("Use the same font for club names & numbers", value=True)
    b_files, b_name = (h_files, h_name) if same else font_picker("Body", "Lato")
R.set_fonts(heading=h_files, body=b_files)

with st.sidebar.expander("🌈 Colours"):
    _t = R.Theme()
    c1, c2 = st.columns(2)
    bg_main = c1.color_picker("Background", "#073a89")
    bg_top = c2.color_picker("Background top band", "#1a6ae3")
    p_top = c1.color_picker("Panel top / middle", "#002b6a")
    p_bot = c2.color_picker("Panel bottom", "#1868dc")
    r_top = c1.color_picker("Table row top", "#0c2f7b")
    r_bot = c2.color_picker("Table row bottom", "#5f8dec")
    h_top = c1.color_picker("Table header top", "#312cb8")
    h_bot = c2.color_picker("Table header bottom", "#08061d")
    navy = c1.color_picker("Text on white pills", _t.navy)
    sub_c = c2.color_picker("Title 2nd/3rd line", _t.subtitle)

with st.sidebar.expander("🎨 Brand logos"):
    st.caption(f"Default files are read from `assets/brand/`. Uploads here override them for this session.")
    up_league = st.file_uploader("League logo (top-left)", type=["png", "webp"], key="b_league")
    up_comp = st.file_uploader("EPA ONE logo (top-right)", type=["png", "webp"], key="b_comp")
    up_badge = st.file_uploader(f"EPA ONE {age_label} badge (centre)", type=["png", "webp"], key=f"b_badge_{age}")

with st.sidebar.expander("🛡️ Club logos (session upload)"):
    st.caption("Permanent logos live in `assets/logos/` (see clubs.csv). "
               "Uploading here works for this session only — name files after the club, "
               "e.g. `Persija Jakarta.png`.")
    club_logo_ups = st.file_uploader("Club logos", type=["png", "webp"], accept_multiple_files=True, key="club_logos")

brand = R.Brand(
    league_logo=Image.open(up_league) if up_league else brand_file("league", "ileague"),
    comp_logo=Image.open(up_comp) if up_comp else brand_file("epaone"),
    age_badge=Image.open(up_badge) if up_badge else brand_file(f"badge_{age.lower()}", f"epaone_{age.lower()}"),
    age_label=age_label,
)
theme = R.Theme(
    bg_stops=[(0, bg_top), (0.07, bg_main), (1, bg_main)],
    panel_stops=[(0, "#0d4ea6"), (0.14, p_top), (0.55, p_top), (1, p_bot)],
    header_bar_stops=[(0, h_top), (1, h_bot)],
    row_stops=[(0, r_top), (1, r_bot)],
    navy=navy, subtitle=sub_c,
)

clubs = D.ClubBook()
session_logos = {D.norm(Path(f.name).stem): Image.open(f) for f in (club_logo_ups or [])}


def logo(name: str):
    return session_logos.get(D.norm(name)) or session_logos.get(D.norm(clubs.get(name).name)) or clubs.logo_path(name)


def disp(name: str, upper=True, suffix=""):
    s = clubs.get(name).display
    s = s.upper() if upper else s
    return f"{s} {suffix}".strip() if suffix else s


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
st.title(f"EPA ONE {age_label} — Social Graphics")
st.caption(f"Output: **{fmt_name}**")

if not grids:
    st.info("👈 Choose a data source in the sidebar to begin. "
            "Each age category (U16, U18, U20) reads its own Master Report spreadsheet.")
    st.stop()

ds = D.build_dataset(grids)
for w in ds.warnings:
    st.warning(w)
if ds.matches.empty:
    st.stop()

last_gw = ds.last_played_gw() or 1
next_gw = ds.next_unplayed_gw() or last_gw

sel_gw = st.select_slider(
    "🗓️ Gameweek", options=ds.gameweeks, value=last_gw,
    help="Results show this gameweek, standings are as of this gameweek, "
         "fixtures default to the next gameweek(s). Each tab can still be adjusted.")
played_upto = ds.matches[(ds.matches.gw <= sel_gw)]
n_played, n_total = int(played_upto[played_upto.gw == sel_gw].played.sum()), int((played_upto.gw == sel_gw).sum())
st.caption(f"GW {sel_gw}: {n_played}/{n_total} matches have a score · latest gameweek with results: GW {last_gw}")


def following_gws(gw: int) -> list[int]:
    """Next gameweek after gw, plus the one after it when it's a double-header (same pairings)."""
    nxt = [g for g in ds.gameweeks if g > gw][:2]
    if not nxt:
        return []
    def pairs(g):
        m = ds.matches[ds.matches.gw == g]
        return {frozenset((D.norm(a), D.norm(b))) for a, b in zip(m.home, m.away)}
    if len(nxt) == 2 and pairs(nxt[0]) == pairs(nxt[1]):
        return nxt
    return nxt[:1]


tab_st, tab_fx, tab_rs, tab_chk = st.tabs(["📊 Standings", "📅 Fixtures", "🏁 Results", "🔎 Data check"])

# ---------------------------------- STANDINGS ------------------------------ #
with tab_st:
    c1, c2 = st.columns([1, 1.4])
    with c1:
        groups = st.multiselect("Groups (one slide each)", ds.groups, default=ds.groups[:1], key="st_groups")
        src_opts = ["Official table (KLASEMEN_MANUAL)", "Computed as of a gameweek"]
        official_ok = ds.standings_source == "sheet" and sel_gw >= last_gw
        src = st.radio("Table source", src_opts, index=0 if official_ok else 1, key=f"st_src_{sel_gw}",
                       help="The official table includes the H2H tie-breakers from the sheet. "
                            "'Computed' rebuilds the table from results up to the chosen GW "
                            "(Pts > head-to-head > GD > GF). Picked automatically for past gameweeks.")
        gw_st = st.number_input("Gameweek shown", 1, max(ds.gameweeks), sel_gw, key=f"st_gw_{sel_gw}")
        suffix = st.toggle(f"Add '{age}' after club names", value=True, key="st_suffix")
        hl = st.number_input("Highlight top N rows", 0, 4, 1, key="st_hl")
        title_st = st.text_input("Title", "STANDINGS", key="st_title")
    with c2:
        st.markdown("**Photos** (one per group slide)")
        st_photos = {}
        for g in groups:
            with st.expander(f"Photo — {group_word} {g}", expanded=len(groups) == 1):
                st_photos[g] = photo_input(f"Photo for {group_word} {g}", f"st_{age}_{g}")

    if groups:
        tables = ds.standings if src == src_opts[0] else D.compute_standings(ds.matches, gw_st)
        imgs = []
        for i, g in enumerate(groups):
            t = tables.get(g)
            if t is None or t.empty:
                st.warning(f"No standings for group {g}.")
                continue
            rows = [R.StandingRow(int(r.pos), disp(r.club, True, age if suffix else ""), logo(r.club),
                                  int(r.p), int(r.gd), int(r.pts)) for r in t.itertuples()]
            ph, fo = st_photos.get(g, (None, 0.4))
            img = R.render_standings(rows, FMT, brand, theme, title=title_st, sub1=f"{group_word} {g}",
                                     sub2=f"{gw_word} {gw_st}", photo=ph, photo_focus=fo,
                                     highlight_top=int(hl),
                                     placeholder=placeholder)
            imgs.append((f"standings_{age}_grup{g}_gw{gw_st}", img))
        if imgs:
            st.divider()
            show_grid(imgs, f"standings_{age}_gw{gw_st}")

# ---------------------------------- FIXTURES ------------------------------- #
with tab_fx:
    c1, c2 = st.columns([1, 1.4])
    default_fx = following_gws(sel_gw)
    with c1:
        gws = st.multiselect("Gameweeks", ds.gameweeks, default=default_fx, key=f"fx_gws_{sel_gw}",
                             help="Pick both GWs of a double-header (e.g. 7 & 8) — repeated pairings are shown once.")
        fx_groups = st.multiselect("Groups", ds.groups, default=ds.groups, key="fx_groups")
        dedupe = st.toggle("Show each pairing once", value=True, key="fx_dedupe")
        note_mode = st.selectbox("Line under each match", ["None", "Date & kick-off", "Date, kick-off & venue", "Venue"],
                                 key="fx_note")
        split = st.toggle("One slide per group", value=False, key="fx_split")
        title_fx = st.text_input("Title", "FIXTURES", key="fx_title")
    with c2:
        fx_photo, fx_focus = photo_input("Background photo", f"fx_{age}", "Shown faded behind the fixtures.")
        fx_opacity = st.slider("Photo visibility", 0.0, 0.8, 0.32, 0.02, key="fx_op")

    if gws:
        sel = ds.matches[ds.matches.gw.isin(gws) & ds.matches.group.isin(fx_groups)]
        sel = sel.sort_values(["group", "gw"], kind="stable")
        dates_all = sel.date
        if dedupe:
            sel = D.dedupe_fixtures(sel)
        sub1_fx = D.format_gw_label(gws, gw_word)
        sub2_fx = st.text_input("Date line", D.format_date_range(D.core_dates(dates_all), lang),
                                key=f"fx_date_{'-'.join(map(str, gws))}")

        def note(r):
            if note_mode == "None":
                return ""
            parts = []
            if "Date" in note_mode and r.date:
                parts.append(D.format_date_range([r.date], lang))
            if "kick-off" in note_mode and r.time:
                parts.append(f"{r.time} WIB")
            if "enue" in note_mode and r.venue:
                parts.append(r.venue)
            return "  ·  ".join(parts)

        def frow(r, show_group=True):
            return R.FixtureRow(f"{group_word} {r.group}" if show_group else "", disp(r.home), logo(r.home),
                                disp(r.away), logo(r.away), note(r))

        imgs = []
        if split:
            gs = [g for g in fx_groups if g in set(sel.group)]
            for i, g in enumerate(gs):
                rows = [frow(r, False) for r in sel[sel.group == g].itertuples()]
                img = R.render_fixtures(rows, FMT, brand, theme, title=title_fx, sub1=f"{sub1_fx} · {group_word} {g}",
                                        sub2=sub2_fx, photo=fx_photo, photo_focus=fx_focus,
                                        photo_opacity=fx_opacity)
                imgs.append((f"fixtures_{age}_gw{'-'.join(map(str, gws))}_grup{g}", img))
        else:
            rows = [frow(r) for r in sel.itertuples()]
            if len(rows) > 12 and FMT[0] <= FMT[1]:
                st.info(f"{len(rows)} matches on one slide — consider 'One slide per group'.")
            img = R.render_fixtures(rows, FMT, brand, theme, title=title_fx, sub1=sub1_fx, sub2=sub2_fx,
                                    photo=fx_photo, photo_focus=fx_focus, photo_opacity=fx_opacity)
            imgs.append((f"fixtures_{age}_gw{'-'.join(map(str, gws))}", img))
        st.divider()
        show_grid(imgs, f"fixtures_{age}")

# ---------------------------------- RESULTS -------------------------------- #
with tab_rs:
    c1, c2 = st.columns([1, 1.4])
    with c1:
        gw_rs = st.selectbox("Gameweek", ds.gameweeks, index=ds.gameweeks.index(sel_gw), key=f"rs_gw_{sel_gw}")
        rs_groups = st.multiselect("Groups", ds.groups, default=ds.groups, key="rs_groups")
        unplayed = st.selectbox("Matches without a score", ["Hide", "Show as 'VS'", "Show as 'PPD'"], key="rs_unp")
        title_rs = st.text_input("Title", "RESULTS", key="rs_title")
    gm = ds.matches[(ds.matches.gw == gw_rs) & ds.matches.group.isin(rs_groups)]
    with c2:
        st.markdown("**Photos** (one per group block — they alternate left/right)")
        rs_photos = {}
        for g in rs_groups:
            with st.expander(f"Photo — {group_word} {g}"):
                rs_photos[g] = photo_input(f"Photo for {group_word} {g}", f"rs_{age}_{gw_rs}_{g}")
    played_dates = gm[gm.played].date
    sub2_rs = st.text_input("Date line", D.format_date_range(D.core_dates(played_dates if len(played_dates) else gm.date), lang),
                            key=f"rs_date_{gw_rs}")

    blocks = []
    for g in rs_groups:
        gg = gm[gm.group == g]
        rows = []
        for r in gg.itertuples():
            if r.played:
                sc = r.score
            elif unplayed == "Hide":
                continue
            else:
                sc = "VS" if "VS" in unplayed else "PPD"
            rows.append(R.ResultRow(r.home, logo(r.home), r.away, logo(r.away), sc))
        if rows:
            ph, fo = rs_photos.get(g, (None, 0.4))
            blocks.append(R.ResultBlock(f"{group_word} {g}", rows, ph, fo))
    if blocks:
        img = R.render_results(blocks, FMT, brand, theme, title=title_rs, sub1=f"{gw_word} {gw_rs}", sub2=sub2_rs,
                                 placeholder=placeholder)
        st.divider()
        show_grid([(f"results_{age}_gw{gw_rs}", img)], f"results_{age}_gw{gw_rs}")
    else:
        st.info("No results for this gameweek yet.")

# ---------------------------------- DATA CHECK ----------------------------- #
with tab_chk:
    names = ds.all_clubs()
    unknown = clubs.unknown(names)
    missing = sorted({n for n in names if logo(n) is None})
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Club name matching")
        if unknown:
            st.warning("Not in clubs.csv (add as a row or an alias):\n\n- " + "\n- ".join(unknown))
        else:
            st.success("Every club name in the sheet matches clubs.csv.")
    with c2:
        st.subheader("Logos")
        if missing:
            exp = sorted({clubs.get(n).logo.name if clubs.get(n).logo else D.norm(n) + ".png" for n in missing})
            st.warning("Missing logo files in `assets/logos/`:\n\n- " + "\n- ".join(exp))
        else:
            st.success("All club logos found.")
        st.write("Brand:", {"league": bool(brand.league_logo), "epaone": bool(brand.comp_logo),
                            f"badge {age}": bool(brand.age_badge)})
    st.subheader("Standings")
    for g, t in ds.standings.items():
        st.markdown(f"**{group_word} {g}** — source: {ds.standings_source}")
        st.dataframe(t, hide_index=True, use_container_width=True)
    st.subheader("Matches")
    st.dataframe(ds.matches, hide_index=True, use_container_width=True)
