# EPA ONE — Social Graphics Generator

A Streamlit app that reads the **Master Report** Google Sheet for each age category (U16, U18, U20) and generates the three match-week graphics — **Standings**, **Fixtures** and **Results** — sized for the social platform you pick (Instagram by default).

## What it reads

| Sheet tab | Used for |
|---|---|
| `FAKTA_PERTANDINGAN` | Fixtures and results (GW, group, home, score, away, date, kick-off, venue) |
| `KLASEMEN_MANUAL` | Official standings per group, already sorted with the sheet's H2H tie-breakers |

No changes to your spreadsheet are needed. A row with a score like `2-1` counts as played; a blank score is an upcoming or postponed match. Club spellings are matched loosely ("PERSITA" = "Persita", "Isenmulang" = "Isen Mulang", "Garudayaksa FC U16" = "Garudayaksa FC") through `clubs.csv`.

## Project layout

```
app.py              Streamlit UI
data.py             Google Sheets / xlsx loading and parsing
render.py           Pillow drawing of the three templates + platform sizes + colour theme
fonts.py            Google Fonts downloader (cached in assets/fonts/cache/)
clubs.csv           Club display names, logo file names, aliases
assets/logos/       Club logos (transparent PNG, names from clubs.csv)
assets/brand/       league.png, epaone.png, badge_u16.png, badge_u18.png, badge_u20.png
assets/fonts/       Inter bundled as the offline fallback
.streamlit/secrets.toml.example
```

## Setup

### 1. Logos
All current club logos (22, incl. Garuda United for U18) and the brand marks are already included, normalised to identical canvas sizes (clubs 512×512, age badges 600×800). To add or replace one:
* Club logos → `assets/logos/` using the file names in `clubs.csv` (e.g. `persija_jakarta.png`).
* Brand marks → `assets/brand/` (see the README inside the folder).
* The **Data check** tab lists any missing logo or unmatched club name.

### 2. Give the app access to the sheets
Pick one:

**A. Service account (recommended, sheets stay private)**
1. Google Cloud Console → create a project → enable **Google Sheets API** and **Google Drive API**.
2. IAM → Service Accounts → create one → Keys → Add key → JSON.
3. Share each Master Report spreadsheet with the service account's `client_email` (Viewer).
4. Copy the JSON fields into `[gcp_service_account]` in secrets (see the example file).

**B. Public link** — set each sheet to "Anyone with the link can view" and choose *Google Sheet (public link)* in the sidebar. No credentials needed.

**C. Upload .xlsx** — download the workbook and upload it in the sidebar (good for testing).

Put the three spreadsheet URLs under `[sheets]` in secrets so each age category loads automatically.

### 3. Run locally
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run app.py
```

### 4. Deploy (Streamlit Community Cloud)
1. Push this folder to a GitHub repo (`secrets.toml` is git-ignored).
2. share.streamlit.io → New app → pick the repo, main file `app.py`.
3. App → Settings → Secrets → paste the contents of your `secrets.toml`.

## Using it
1. Sidebar: choose **age category**, **data source**, and **platform / size**.
1. **Gameweek slider** (top of the page): Results show that GW, Standings are as of that GW (official sheet table for the latest GW, rebuilt from results with head-to-head tie-breaks for earlier GWs), Fixtures default to the following gameweek(s). Every tab can still be adjusted individually.
2. **Standings** — choose group(s), upload a photo per group, set the gameweek label. Several groups = one image each (ZIP download).
3. **Fixtures** — choose gameweeks (double-headers like GW 7 & 8 show each pairing once), optional background photo and a date/kick-off/venue line.
4. **Results** — choose a gameweek, upload a photo for each group block; photos alternate left/right.
5. Download as **.jpg** (multiple slides come as a ZIP of .jpg files).

Until a photo is uploaded, a **PHOTO** placeholder holds its spot so you can check the layout. Turn off *Photo placeholder* in the sidebar to let the table/results use that space instead.

### Platform sizes
| Option | Size | Layout |
|---|---|---|
| Instagram feed portrait 4:5 (default) | 1080×1350 | stacked |
| Instagram square 1:1 | 1080×1080 | stacked, compact |
| Instagram Story / Reels 9:16 | 1080×1920 | stacked, taller photos |
| Facebook feed portrait 4:5 | 1080×1350 | stacked |
| Facebook landscape 1.91:1 | 1200×630 | side-by-side |
| X / Twitter 16:9 | 1600×900 | side-by-side |
| TikTok / WhatsApp Status 9:16 | 1080×1920 | stacked |
| YouTube Community square | 1080×1080 | stacked |

Add or change sizes in `FORMATS` at the top of `render.py`.

### Fonts
Sidebar → **Fonts**: pick a heading font and (optionally) a separate font for club names and numbers.
- Curated list of Google Fonts (Lato is the default and closest to the original artwork).
- **Other Google Font…** — type any family name exactly as on fonts.google.com.
- **Upload .ttf/.otf…** — your own font files (include Black / ExtraBold / Bold in the file names).
Fonts download on first use and are cached on the server. If a weight doesn't exist, the nearest one is used; if the download fails, the bundled Inter is used.

### Colours
Sidebar → **Colours** adjusts the main gradients (background, panel, table rows, table header, text). Defaults were sampled from the official artwork. Full gradient definitions are in the `Theme` class in `render.py`.

## Adding a new club
Add a row to `clubs.csv` — `name`, `display_name` (what's printed), `logo_file`, and `aliases` (other spellings, separated by `;`) — and drop the logo into `assets/logos/`.
