# Curatarr

A curation front-end for huge IPTV/VOD providers. It reads a provider's **Xtream
Codes** catalogue (live + movies + series), lets you browse it Jellyseerr-style
and **cherry-pick** individual channels, movies, seasons or episodes, then
re-serves *only your picks* as a **curated Xtream Codes panel** that Dispatcharr
ingests as an account.

This solves a gap Dispatcharr can't: it has **no per-series VOD cherry-pick**
(only account-level VOD on/off + categories), and only treats **Xtream**
accounts as VOD. By acting as a curated Xtream panel in front of the provider,
this app gives Dispatcharr native VOD metadata + VOD2MLIB support for exactly the
titles you chose.

Verified against a real provider: ~81k catalogue items (25k live, 44k movies,
11.5k series) indexed in ~60s; lazy per-series episode fetch; the full Dispatcharr
Xtream scan sequence (auth, categories, vod/series streams, season-keyed
`get_series_info`) and 302 stream redirects.

## How it works

```
PROVIDER Xtream API ──read catalogue──▶  Curatarr  ──serve only PICKS──▶  Dispatcharr (XC account, VOD on)
 (get.php creds)         browse + cherry-pick           player_api.php             │  native VOD + VOD2MLIB
                              │ import → ledger (SQLite)                            ▼
                              └───────── stream open ──── 302 redirect ────▶ provider (bytes flow direct)
```

- **Input** = the provider's `player_api.php`. Credentials/base URL are parsed
  from the saved source URL (a `get.php?...&username=...&password=...` link), so
  there's nothing extra to configure. Movies are fetched per-category (the
  provider returns nothing for `get_vod_streams` without a category); **episodes
  are fetched lazily** when you open or import a series (avoids ~11k calls).
- **Curation** = the SQLite **ledger** is the single source of truth for your
  picks (idempotent by a stream-URL hash).
- **Output** = a curated Xtream panel (`player_api.php`) exposing only the
  ledger. Verified against Dispatcharr v0.25.1 source: `get_series_info` returns
  the **season-keyed `episodes` object** Dispatcharr expects.
- **Streaming** = Dispatcharr rebuilds stream URLs against our server and follows
  redirects, so our `/movie|series|live/{u}/{p}/{id}.{ext}` endpoints **302 to the
  real provider URL** — the video never flows through this app.
- **Load-balancing across subscriptions** = add multiple subs of the same
  provider (e.g. 2 subs each capped at 1 connection = 2 screens). The catalogue
  is synced/curated **once**; the wrapper exposes **one Xtream account per sub**
  (`curator`, `curator2`, …, sharing one password). Each account's stream
  redirects use that sub's credentials, so Dispatcharr — which merges content by
  TMDB and balances across accounts — spreads concurrent streams over your subs.
  Add every account shown in "Dispatcharr setup" (VOD scanning ON, max
  connections 1 each).

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DB_PATH=./curator.db .venv/bin/uvicorn backend.main:app --port 8753
# open http://localhost:8753
#  1) "Source…"  → add one or more provider subscriptions (Server URL/Username/Password)
#  2) "Sync"     → pulls the catalogue (~60s)
#  3) browse Live/Movies/Series, click Import
#  4) "Dispatcharr setup" → add EACH shown Xtream account in Dispatcharr (VOD scanning ON)
```

> On bleeding-edge Python (3.14) install latest wheels:
> `pip install -U fastapi pydantic 'uvicorn[standard]'`. The Docker image pins
> Python 3.12 where `requirements.txt` installs cleanly.

## Deploy (Docker / TrueNAS Scale)

On the TrueNAS host (user is comfortable with compose):

```bash
git clone <repo> /mnt/<pool>/apps/curatarr      # onto a dataset
cd /mnt/<pool>/apps/curatarr
# edit docker-compose.yml: point the /data volume at a snapshotted dataset
docker compose up -d --build                     # serves on :8753
```

The DB (subscriptions + curated picks) persists on the mounted `/data` volume, so
keep it on a dataset you snapshot/back up. Dispatcharr reaches Curatarr via the
TrueNAS host IP + port (`http://<truenas-ip>:8753`) — no shared Docker network
needed.

Then, in Dispatcharr → M3U & EPG Manager, add **each** account shown under
**Dispatcharr setup** as an **Xtream Codes** account (Server URL
`http://<truenas-ip>:8753`, **VOD Scanning ON**, **max connections 1**).

## Publishing the image (GHCR)

A GitHub Action (`.github/workflows/docker-publish.yml`) builds a **multi-arch**
image (amd64 + arm64 + arm/v7 — Intel/AMD, Apple Silicon, 64-bit & 32-bit
Raspberry Pi) and pushes it to `ghcr.io/<owner>/curatarr` on every push to `main`
and on `v*` tags. Docker pulls the right architecture automatically. To use it:

```bash
git remote add origin git@github.com:<you>/curatarr.git
git push -u origin main          # triggers the build → ghcr.io/<you>/curatarr:latest
git tag v0.1.0 && git push --tags   # also publishes :v0.1.0
```

Then either make the GHCR package **public** (GitHub → Packages → curatarr →
visibility), or `docker login ghcr.io` on the TrueNAS box with a PAT
(`read:packages`). Finally point the compose at it:

```yaml
    image: ghcr.io/<you>/curatarr:latest
    pull_policy: always
    # (remove the build: / image: curatarr:latest / pull_policy: build lines)
```

## Config (environment)

| Var | Default | Purpose |
|-----|---------|---------|
| `SOURCE_M3U` | _(empty)_ | Optional initial source Xtream URL, seeded on first run; thereafter edited in the UI |
| `DB_PATH` | `curator.db` | SQLite scan cache + curated ledger |
| `XC_USER` / `XC_PASS` | `curator` / random | Credentials Dispatcharr uses to consume the wrapper (generated once if unset) |

## API

Internal (UI):

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/status` | counts + saved source + last sync |
| `GET`/`POST` | `/api/source` | view / save the provider subscriptions (`{subs:[{url,username,password}]}`) |
| `POST` | `/api/sync` | pull provider catalogue + rebuild index |
| `GET`  | `/api/groups?kind=live\|movie\|series` | categories w/ counts |
| `GET`  | `/api/items?kind=live\|movie&group=&q=&page=` | paginated items |
| `GET`  | `/api/series?group=&q=&page=` | paginated series (one row each) |
| `GET`  | `/api/series/detail?series_key=` | seasons + episodes (lazy from provider) |
| `POST` | `/api/import` | `{ids:[…]}` or `{series_key, season?, episode_ids?}` |
| `POST` | `/api/unimport` | `{ids:[…]}` (ledger row ids) |
| `GET`  | `/api/imported` | the curated ledger |
| `GET`  | `/api/xc-info` | Xtream URL + credentials to paste into Dispatcharr |

Public (consumed by Dispatcharr): `GET /player_api.php` (Xtream actions),
`GET /xmltv.php` (empty EPG), and stream redirects
`GET /movie|series|live/{user}/{password}/{id}.{ext}`.

## Not in v1

- TMDB/cover passthrough to Dispatcharr (it still enriches by name/year).
- "New since last sync" diff view; scheduled auto-sync.
- Multi-provider merge.
