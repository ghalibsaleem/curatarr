# M3U Curator

A curation front-end for huge IPTV/VOD M3Us. Parse a massive provider dump
(hundreds of thousands of entries), browse it Jellyseerr-style by **Live /
Movies / Series → Season → Episode**, search, and **click Import** to copy the
hand-picked entries — `#EXTINF` + URL, byte-for-byte — into a lean curated
`.m3u` that Dispatcharr consumes.

Verified against a real 105 MB / 381,740-entry provider M3U: full parse + index
in ~3.8 s; classification, series grouping, and idempotent import all confirmed.

## How it works

```
saved source URL  ──Sync (download)──▶  SQLite (items cache + import ledger)  ──serve──▶  web UI
   (editable in UI)                                                                          │ click Import
                                                                                            ▼
                                                        curated .m3u  ◀──append (verbatim, deduped)
                                                             │
                                                             ▼
                                                  Dispatcharr /data/m3us  (auto-import)
```

The source M3U URL is saved in the app (SQLite `meta`) and editable at runtime
via **Source…**. Hitting **Sync** re-downloads fresh data from that URL
(streamed to a cache file, constant memory) and rebuilds the index. A local file
path works too. Credentials in the URL are masked in any error/log output.

- **Classification** is driven by the URL path segment (`/series/`, `/movie/`,
  else live), with an `SxxExx` title fallback for flat providers. Group-title
  keywords are deliberately *not* trusted for type — live channels are commonly
  filed under groups named "TV SHOWS"/"SERIES".
- **Series grouping** strips the episode token (`S01 E02`, `1x02`,
  `Season 1 Episode 2`) to a clean title and keys on `(group, title)` so numbered
  or identically-named shows from different categories never merge.
- **Import is idempotent**: each entry is tracked by a content hash of its
  `#EXTINF`+URL, so re-importing is a no-op and the curated file never dupes.
  The ledger survives re-scans.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DEST_M3U=./curated.m3u DB_PATH=./curator.db \
  .venv/bin/uvicorn backend.app:app --port 8753
# open http://localhost:8753 → "Source…" to paste your M3U URL → "Sync" → browse & import
# (optional) seed the URL up front with SOURCE_M3U=http://provider/get.php?...
```

> Note: needs prebuilt wheels for FastAPI/pydantic. On bleeding-edge Python
> (3.14) install latest (`pip install -U fastapi pydantic 'uvicorn[standard]'`);
> the Docker image pins Python 3.12 where the requirements install cleanly.

## Run with Docker

```bash
docker compose up --build -d   # serves on :8753
```

Mount the curated output at Dispatcharr's M3U folder so picks auto-import — e.g.
in `docker-compose.yml` set the second volume to:

```yaml
- /mnt/AppsStorage-1/Dispatcharr/Data/m3us:/curated
```

Then in Dispatcharr add `curated.m3u` as an M3U Account (or enable
"Auto-Import Mapped Files").

## Config (environment)

| Var | Default | Purpose |
|-----|---------|---------|
| `SOURCE_M3U` | _(empty)_ | Optional initial source URL/path, seeded on first run only; thereafter edited in the UI |
| `SOURCE_CACHE` | `source_cache.m3u` | Where a downloaded M3U is cached |
| `DEST_M3U` | `curated.m3u` | Curated output Dispatcharr reads |
| `DB_PATH` | `curator.db` | SQLite cache + import ledger |

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/status` | counts + saved source + last sync |
| `GET`  | `/api/source` | current saved source URL + sync info |
| `POST` | `/api/source` | save/update source (`{"url": "..."}`) |
| `POST` | `/api/sync` | download from saved URL + rebuild index |
| `GET`  | `/api/groups?kind=live\|movie\|series` | categories w/ counts |
| `GET`  | `/api/items?kind=live\|movie&group=&q=&page=` | paginated items |
| `GET`  | `/api/series?group=&q=&page=` | paginated series |
| `GET`  | `/api/series/detail?series_key=` | seasons + episodes |
| `POST` | `/api/import` | `{"ids":[..]}` or `{"series_key":"..","season":N?}` |
| `GET`  | `/api/imported` | the import ledger |

## Not in v1

Xtream Codes API input, `.strm`/NFO emission for media servers, multi-provider
merge/dedup, EPG. The schema and classifier already leave room for these.
