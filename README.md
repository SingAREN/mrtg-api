# mrtg-api

A small Flask application that publishes [MRTG](https://oss.oetiker.ch/mrtg/) network utilisation data and eduroam usage statistics as a JSON API and as embeddable HTML pages.

It reads the HTML pages and PNG graphs that MRTG already writes to disk, scrapes the throughput figures out of them, and serves them back as:

- **JSON**, for other systems to consume, and
- **rendered HTML pages**, with the graphs embedded inline as base64 images, so they can be embedded in a website without exposing the MRTG directory itself.

It can also render eduroam usage charts for each institution from CSV statistics published elsewhere.

## Contents

- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Configuration](#configuration)
- [Deployment (Docker)](#deployment-docker)
- [Running locally](#running-locally)
- [API reference](#api-reference)
- [Notes and limitations](#notes-and-limitations)

## How it works

```
                 ┌──────────────────────────────┐
  mrtg.cfg  ───► │                              │ ──► /mrtg/                  (JSON list of targets)
  <target>.html ►│          mrtg-api            │ ──► /mrtg/<target>          (JSON utilisation data)
  <target>-*.png►│   (Flask + gunicorn, :8081)  │ ──► /mrtg/<target>/render   (HTML page)
                 │                              │
  eduroam CSVs ─►│                              │ ──► /eduroam/<ihl>          (HTML page, charts drawn in browser)
                 └──────────────────────────────┘
```

**MRTG.** The app reads `mrtg.cfg` to find every `Target[...]` entry. For each target, it parses `<target>.html` with BeautifulSoup and extracts:

- the page title and the last-updated time
- the max, average and current **in** and **out** throughput for each period (`day`, `week`, `month`, `year`)
- the matching graph, `<target>-<period>.png`, encoded as a base64 `data:` URI

**eduroam.** The eduroam pages are rendered as HTML "shells". The browser then downloads the CSV statistics directly from `EDUROAM_STATS_BASE_URL` and draws the charts with [d3](https://d3js.org/) and [dimple](http://dimplejs.org/).

Responses are cached in memory for 300 seconds by default (Flask-Caching `SimpleCache`).

## Repository layout

```
mrtg-api/
├── Dockerfile
├── docker-compose.yml
└── mrtg-api/
    ├── mrtg_api.py            # Flask app: routes, configuration, MRTG scraping
    ├── wsgi.py                # gunicorn entry point (wsgi:app)
    ├── requirements.txt
    ├── templates/
    │   ├── network_utilisation_shell.html.j2   # MRTG target page
    │   ├── eduroam_usage_shell.html.j2         # per-institution eduroam page
    │   └── eduroam_server_load.html.j2         # national server load page (SINGAREN / TOTAL)
    └── static/                # CSS, fonts, images, d3 + dimple JS
```

## Requirements

- Docker and Docker Compose (recommended), **or** Python 3.12 with the packages in `requirements.txt`
- A host that runs MRTG and has its config and output directories available locally
- *(Optional)* An HTTP location that serves the eduroam statistics CSVs

## Configuration

All configuration lives in the `config` dictionary at the top of [`mrtg-api/mrtg_api.py`](mrtg-api/mrtg-api/mrtg_api.py).

| Key | Default | Description |
|---|---|---|
| `MRTG_BASE_DIR` | `/opt/mrtg/data/` | Directory that holds MRTG's `<target>.html` and `<target>-{day,week,month,year}.png` files. **Keep the trailing slash.** |
| `MRTG_CONFIG` | `/opt/mrtg/config/mrtg.cfg` | MRTG config file, scanned for `Target[...]` entries. |
| `EDUROAM_STATS_BASE_URL` | `''` | Base URL that serves the eduroam statistics CSVs (see [eduroam CSV files](#eduroam-csv-files)). |
| `GROUPS` | sample data | Maps a group name (e.g. an institution) to the MRTG targets and eduroam IHLs it is allowed to see. Used by the `/group/` endpoints. |
| `CACHE_DEFAULT_TIMEOUT` | `300` | Cache lifetime in seconds. |
| `PREFERRED_URL_SCHEME` | `https` | Used when building URLs behind a reverse proxy. |

Example `GROUPS`:

```python
'GROUPS': {
    'nus':  {'mrtg': ['nus-primary', 'nus-backup'], 'eduroam': ['nus']},
    'nscc': {'mrtg': ['nscc'],                      'eduroam': None},
}
```

The group `singaren` is reserved. It always returns **every** target.

### eduroam CSV files

The eduroam pages expect these files under `EDUROAM_STATS_BASE_URL`:

| File | Used by | Required columns |
|---|---|---|
| `stats/Daily<Mon><YYYY>.csv` (e.g. `DailyFeb2019.csv`) | `/eduroam/` listing | `IHL` |
| `Daily<Mon><YYYY>.csv` | institution page, daily chart | `IHL`, `Category`, date column |
| `Monthly<YYYY>.csv` | institution page, monthly chart | `IHL`, `Category` |
| `Yearly.csv` | institution page, yearly chart | `IHL`, `Category` |
| `ServerLoad<YYYY>.csv` | `SINGAREN` / `TOTAL` page | `Date`, `Month`, `Category` |

These CSVs are fetched by the **viewer's browser**, so they must be publicly reachable. If they are on a different origin, that server must also send CORS headers.

## Deployment (Docker)

The supplied `docker-compose.yml` assumes the following:

- The inner `mrtg-api/` directory (the one that contains the `Dockerfile`) is deployed at `/opt/mrtg-api` on the host.
- MRTG's files are under `/opt/mrtg/` on the host.
- A reverse proxy (e.g. nginx) on the same host handles public traffic. The container only listens on `127.0.0.1:8081`.

**1. Get the code**

```bash
git clone https://github.com/SingAREN/mrtg-api.git
```

```bash
sudo cp -r mrtg-api/mrtg-api /opt/mrtg-api
```

**2. Edit the configuration** in `/opt/mrtg-api/mrtg-api/mrtg_api.py` (paths, `GROUPS`, `EDUROAM_STATS_BASE_URL`).

**3. Build the image**

```bash
cd /opt/mrtg-api && docker build -t mrtg-api .
```

**4. Start the container**

```bash
cd /opt/mrtg-api && docker compose up -d
```

Compose mounts `mrtg_api.py` and `/opt/mrtg/` read-only into the container. To change the configuration later, edit the file and run `docker compose restart`. No rebuild is needed. Changes to templates, static files or `requirements.txt` do need a rebuild.

The container runs gunicorn with 3 workers × 3 threads, as an unprivileged `mrtg-api` user (UID 225). Make sure that user can read the MRTG files.

### Reverse proxy

The app uses `ProxyFix` and trusts one level of `X-Forwarded-For` and `X-Forwarded-Proto`. A minimal nginx location:

```nginx
location / {
    proxy_pass         http://127.0.0.1:8081;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

> **Important:** `/mrtg/<target>/render` and the `/group/` endpoints call the API again over HTTP, at the public URL the request came in on. The container must be able to resolve and reach its own public hostname. If it cannot, those endpoints will hang or return 404.

## Running locally

```bash
cd mrtg-api/mrtg-api
```

```bash
pip install -r requirements.txt
```

```bash
gunicorn --bind 127.0.0.1:8081 --workers 2 --threads 2 wsgi:app
```

Before you start it, point `MRTG_BASE_DIR` and `MRTG_CONFIG` at local copies of MRTG output. Run more than one worker or thread, because the render endpoints make HTTP requests back to the app.

## API reference

All errors return JSON in the form `{"error": "<message>"}` with status `400`, `403` or `404`.

### MRTG

#### `GET /mrtg/`

Lists every target in `mrtg.cfg`, with a link to its rendered page.

```json
{
  "NSCC":    "https://example.org/mrtg/nscc/render",
  "SG-ASIA": "https://example.org/mrtg/sg-asia/render"
}
```

#### `GET /mrtg/<target>`

Returns utilisation data for one target. Throughput values are returned without the percentage suffix that MRTG adds.

```json
{
  "title": "Traffic Analysis for NSCC",
  "last_update": "Tuesday, 7 October 2026 at 16:45",
  "day": {
    "in":  {"max": "9.2 Gb/s", "average": "3.1 Gb/s", "current": "2.8 Gb/s"},
    "out": {"max": "7.4 Gb/s", "average": "2.2 Gb/s", "current": "1.9 Gb/s"},
    "img": "data:image/png;base64,iVBORw0KGgo..."
  },
  "week":  { "...": "..." },
  "month": { "...": "..." },
  "year":  { "...": "..." }
}
```

| Status | Meaning |
|---|---|
| `404` | `<target>.html` does not exist in `MRTG_BASE_DIR` |
| `400` | The MRTG HTML page could not be parsed |

#### `GET /mrtg/<target>/render`

Returns the same data as an HTML page with the graphs and the in/out max, average and current tables. Suitable for embedding in an `<iframe>`.

#### `GET /mrtg/group/<group>`

Lists `[target, render_url]` pairs for the MRTG targets assigned to `<group>` in `GROUPS`. `singaren` returns all targets.

```json
[["interface1", "https://example.org/mrtg/interface1/render"]]
```

### eduroam

`/eduroam/*` endpoints send CORS headers, so other sites can call them.

#### `GET /eduroam/`

Lists the institutions (IHLs) found in yesterday's month's daily CSV, with a link to each page.

#### `GET /eduroam/<ihl>`

Renders the eduroam usage page for one institution, with daily, monthly and yearly charts of local users, visitors and rejections. `SINGAREN` and `TOTAL` render the national server-load page instead.

Query parameters (optional, default is yesterday):

| Parameter | Format | Example |
|---|---|---|
| `day` | `DD` | `04` |
| `month` | `MM` | `02` |
| `year` | `YYYY` | `2019` |

```
/eduroam/insead?day=04&month=02&year=2019
```

#### `GET /eduroam/group/<group>`

Lists `[ihl, url]` pairs for the IHLs assigned to `<group>` in `GROUPS`.

## Notes and limitations

- **MRTG page layout.** The scraper expects MRTG's default HTML output: four graphs (day, week, month, year), each followed by a table of in/out max, average and current values. Custom page templates or `Suppress[]` options will break the parsing.
- **Target names are lower-cased.** MRTG output files must be lower-case. This is MRTG's default.
- **Caching is per worker.** `SimpleCache` lives in each gunicorn worker's memory. Different workers can briefly return different snapshots, and a restart clears the cache.
- **The configuration is hard-coded** in `mrtg_api.py`. Keep a deployment-specific copy and do not commit real `GROUPS` or URLs.
- **Group endpoints provide no access control.** The `/group/` endpoints only filter results. Every target is still reachable directly at `/mrtg/<target>`. Restrict access at the reverse proxy if that matters.
