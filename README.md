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
  Add every account shown in **Settings → Xtream** (VOD scanning ON, max
  connections 1 each).
- **TMDB + clean titles** = the provider's `tmdb` id is passed through and messy
  titles are cleaned (`EN - Inception (2010) …` → `Inception (2010)`), so
  Dispatcharr/Jellyfin match metadata and posters.
- **Bulk import** = already have a curated `.m3u`? **Settings → Import** matches
  it back to the catalogue by provider stream-id (so a playlist from *any* sub
  works) and reports imported / already / not-found / failed.
- **One-click downstream sync** = after curating, **Settings → Downstream** (or
  the header **Downstream sync** button) refreshes each curated Dispatcharr XC
  account, then runs the Jellyfin Xtream-library plugin sync and refreshes only
  the libraries you pick — in sequence, with per-step status.
- **Scheduled auto-sync** = **Settings → Schedule** runs the provider sync on a
  cron schedule (daily / twice-weekly / weekly / monthly, at a time in a timezone
  you pick), optionally chaining the downstream sync after each run. A slot missed
  while the app was offline is skipped (next slot is used); the Overview panel
  shows the next run and the last auto-sync result.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
DB_PATH=./curator.db .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8753
# open http://localhost:8753
#  1) ⚙ Settings → Source  → add one or more provider subscriptions (Server URL/Username/Password)
#  2) Sync                 → pulls the catalogue (~60s)
#  3) browse Live/Movies/Series, click Import (or Settings → Import for a curated .m3u)
#  4) Settings → Xtream    → add EACH shown account in Dispatcharr (VOD scanning ON, max conns 1)
#  5) Settings → Downstream (optional) → configure Dispatcharr + Jellyfin, then "Downstream sync"
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
**Settings → Xtream** as an **Xtream Codes** account (Server URL
`http://<truenas-ip>:8753`, **VOD Scanning ON**, **max connections 1**).

## Publishing the image (GHCR)

Two chained GitHub Actions build a **multi-arch** image (amd64 + arm64 —
Intel/AMD, Apple Silicon, 64-bit Raspberry Pi) and push it to
`ghcr.io/<owner>/curatarr`. Docker pulls the right architecture automatically.
(32-bit ARM isn't built — pydantic's Rust core has no armv7 wheels; use 64-bit
Pi OS.) `docker-release.yml` builds the version/edge tags; when a release build
succeeds, `docker-latest.yml` promotes that exact digest to `:latest` — so
`:latest` tracks the newest **release**, not main HEAD.

Tag scheme:

| Tag | Source | Use |
|-----|--------|-----|
| `:X.Y.Z`, `:X.Y` | push a **`vX.Y.Z`** git tag | pin a frozen release |
| `:latest` | promoted automatically after a successful `vX.Y.Z` build | newest release |
| `:edge` | push to **beta** branch | pre-release testing |

```bash
git remote add origin git@github.com:<you>/curatarr.git
git tag v0.4.1 && git push --tags    # → :0.4.1, :0.4, then :latest (promoted)
git push origin beta                 # → :edge (pre-release)
# (a plain push to main no longer publishes an image)
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
| `GET`  | `/api/status` | counts + saved source + last sync + next scheduled sync |
| `GET`/`POST` | `/api/source` | view / save the provider subscriptions (`{subs:[{url,username,password}]}`) |
| `POST` | `/api/sync` | pull provider catalogue + rebuild index |
| `GET`/`POST` | `/api/schedule` | view / save the auto-sync schedule (frequency, time, tz, downstream) |
| `POST` | `/api/schedule/preview` | next run for an unsaved schedule (live UI preview) |
| `GET`  | `/api/groups?kind=live\|movie\|series` | categories w/ counts |
| `GET`  | `/api/items?kind=live\|movie&group=&q=&page=` | paginated items |
| `GET`  | `/api/series?group=&q=&page=` | paginated series (one row each) |
| `POST` | `/api/series/counts` | `{series_keys:[…]}` → cached season/episode totals |
| `GET`  | `/api/series/detail?series_key=` | seasons + episodes (lazy from provider) |
| `POST` | `/api/import` | `{ids:[…]}` or `{series_key, season?, episode_ids?}` |
| `POST` | `/api/import-m3u` | bulk-import a curated `.m3u` (multipart file) |
| `POST` | `/api/unimport` | `{ids:[…]}` (ledger row ids) |
| `GET`  | `/api/imported` | the curated ledger |
| `GET`  | `/api/xc-info` | Xtream URL + credentials to paste into Dispatcharr |
| `GET`/`POST` | `/api/downstream/config` | view / save Dispatcharr + Jellyfin downstream config |
| `POST` | `/api/downstream/dispatcharr/accounts` | discover Dispatcharr M3U accounts |
| `POST` | `/api/downstream/jellyfin/discover` | discover Jellyfin libraries + plugin task |
| `POST` | `/api/downstream/run` | run the Dispatcharr → Jellyfin sync sequence |

Public (consumed by Dispatcharr): `GET /player_api.php` (Xtream actions),
`GET /xmltv.php` (empty EPG), and stream redirects
`GET /movie|series|live/{user}/{password}/{id}.{ext}`.

## Roadmap

- "New since last sync" diff view (#6).
- Export curated picks as an M3U playlist (#10).
- Multi-provider merge (#8).

## License

[MIT](LICENSE) © Ghalib Saleem
