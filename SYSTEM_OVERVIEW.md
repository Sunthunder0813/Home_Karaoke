# Karaoke Night — System Overview

A self-hosted, two-screen karaoke party app: a TV/big-screen player and a phone-based remote control, sharing a live queue over a Flask + SQLite backend. Deployed on Railway.

## Concept

- **TV screen** (`/`) — fullscreen, hands-off YouTube player. No visible controls. Auto-advances when a video ends.
- **Remote** (`/search`) — phone-friendly control page guests use to browse a songbook, search YouTube for karaoke tracks, paste links, and manage the shared queue.
- Both pages poll a shared queue in the database, so any number of phones can add songs and everyone sees the same state on the TV.

## Stack

| Layer | Tech |
|---|---|
| Backend | Flask, Flask-SQLAlchemy |
| DB | SQLite (`karaoke.db`) |
| Frontend | Server-rendered Jinja2 templates + vanilla JS (no framework/build step) |
| Video playback | YouTube IFrame Player API (TV), YouTube Data API v3 (search), oEmbed (link metadata) |
| Deploy | Railway, Dockerfile build, 2 replicas, `asia-southeast1-eqsg3a` region |

## Repo layout

```
app.py                 # Flask app: models, routes, seed data
youtube_api.py         # Blueprint: /youtube_search (YouTube Data API v3 wrapper)
trusted_channels.py    # List of "trusted" karaoke channel names — NOT currently wired up anywhere
templates/
  index.html           # TV screen
  search.html           # Remote control (tabs: Song Book / YouTube Search / Add Link / Queue)
  song.html             # UNUSED — references a Song.lyrics/audio_file schema that no longer exists
static/
  style.css             # Shared base styles — index.html & search.html mostly use inline <style> instead
karaoke.db              # SQLite DB file
railway.json            # Railway deploy config
_env                    # Env vars (SECRET_KEY, YOUTUBE_API_KEY) — not committed with real values
```

> Note: uploaded files were flat (no `templates/`/`static/` subfolders), but `app.py` uses `render_template()` and `style.css` is referenced as `/static/style.css` — so in the real project these almost certainly live in `templates/` and `static/` as Flask expects. Worth confirming folder structure before making changes.

## Data model (`app.py`)

```python
Song
  id, title, artist, genre (default 'Pop')

QueueItem
  id, position, video_id, title, channel
```

- `Song` is a static, seeded songbook (~380 rows currently in the DB) spanning genres: Pop, R&B, OPM, Ballad, Dance, Duet, 90s/2000s, Disney, etc. Seeded once at startup via `seed_songlist()` (`db.create_all()` + seed run inside `with app.app_context():` at module load, so it works under gunicorn too, not just `__main__`).
- `QueueItem` is the live, shared play queue — the single source of truth both screens poll. `position` is an explicit integer column; `_reindex()` renumbers 0..n after removals so ordering stays clean.

## Routes (`app.py`)

**Pages**
- `GET /` — TV screen, passes current queue into `index.html`
- `GET /search` — remote control, passes songbook genres + queue into `search.html`

**JSON API**
- `GET /api/queue` — current queue (polled by both pages every 2–3s)
- `GET /songlist?q=&genre=` — filter/search the songbook
- `POST /add_to_queue` — add a specific video (id, title, channel) — used after a YouTube search result
- `POST /add_link_to_queue` — paste a raw YouTube URL/ID; extracts the video ID via regex, fetches title/channel via oEmbed, then re-validates via YouTube Data API `videos` endpoint and **rejects non-embeddable videos**
- `POST /remove_from_queue` — remove by index, then reindex
- `POST /skip_song` — drop the first (currently playing) item; called by the TV screen when a video ends, and by the remote's Skip button
- `POST /clear_queue` — wipe the whole queue

**Blueprint** (`youtube_api.py`)
- `GET /youtube_search?q=` — wraps YouTube Data API v3 `search.list` (`type=video`, `videoEmbeddable=true`, `maxResults=20`), returns `[{id, title, channel, thumbnail, link}]`. Skips non-video results (channels/playlists). Returns `502`/`500` with an error message on API failure.

## Frontend behavior

### `index.html` (TV screen)
- Requires a tap/Enter/Space "Start" gesture before playback begins (smart-TV autoplay restriction workaround) — nothing renders or polls until then.
- Loads the YouTube IFrame API, creates a `YT.Player` with controls/keyboard/related-videos/fullscreen all disabled, plus an invisible `.video-blocker` overlay so the embedded player can't be clicked/interacted with directly.
- `onPlayerStateChange` detects video end (`state === 0`) and calls `POST /skip_song` to auto-advance.
- Polls `GET /api/queue` every 2s; only re-renders the video if the *first* queue item's `video_id` actually changed, otherwise just updates the queue strip/ticker (avoids restarting the current video).
- UI chrome: top bar (logo + hint), a queue strip overlay (now playing + rotating "up next" ticker line), bottom ticker bar scrolling the full queue, and an empty state prompting people to open `/search`.

### `search.html` (remote)
- Four tabs: **Song Book**, **YouTube Search**, **Add Link**, **Queue**.
- Song Book: loads `/songlist`, client-side filters by genre pill + text search; each row's "Search" button jumps to the YouTube Search tab pre-filled with `"{title} {artist} karaoke"` and auto-runs it.
- YouTube Search: calls `/youtube_search`, renders results with thumbnail/title/channel and an "+ Add" button → `POST /add_to_queue`.
- Add Link: pastes a URL → `POST /add_link_to_queue`, shows inline success/error messaging.
- Queue tab: full queue list with thumbnails, "Now Playing" tag on item 0, per-item remove, plus global Skip/Clear buttons.
- Polls `GET /api/queue` every 3s to stay in sync with whatever other phones (or the TV) have changed.

## Known loose ends / things to confirm before extending

1. **`trusted_channels.py` is dead code** — a large list of channel names (mostly real karaoke channels, but the tail is filled with obviously placeholder/junk entries like `Karaoke4AllEverything`, `Karaoke4AllUniverses`, etc.). Nothing in `app.py` or `youtube_api.py` imports or filters against it. If the intent was to only surface trusted-channel results in `/youtube_search`, that filter was never wired in — and the placeholder entries should be pruned/replaced with a real channel list (ideally channel IDs, not display names, since names can collide/change).
2. **`song.html` is orphaned** — references `song.title`, `song.artist`, `song.audio_file`, `song.lyrics` and a `/static/{{ song.audio_file }}` audio source, none of which exist in the current `Song` model or any route. Likely leftover from an earlier "self-hosted lyrics/audio" version before the app switched to YouTube-embed based playback. Decide: delete it, or is there a plan to bring back a per-song detail/lyrics page?
3. **`SECRET_KEY` is a hardcoded placeholder** (`'replace-this-with-a-secret-key'`) in `app.py` rather than pulled from env — should move to `os.environ.get('SECRET_KEY')` alongside `YOUTUBE_API_KEY`.
4. **`style.css` is only partly used** — `index.html`/`search.html` have their own inline `<style>` blocks; `style.css` appears to mainly serve `song.html` (which is itself unused) plus some shared class names (`.bigscreen-btn`, `.yt-search-bar`, `#queue-hint`) that don't obviously appear in the current inline-styled pages either. Worth checking for dead CSS.
5. **Folder structure assumption** — see note above; confirm `templates/`/`static/` layout matches what's actually deployed.
6. **DB currently has 381 songs, 0 queue items** — seed data is in place and queue is empty (fresh/reset state).
7. **No auth on any route** — anyone who can reach `/search` (e.g. on the local network) can control the queue. Presumably intentional for a party app, but worth confirming scope (LAN-only vs public Railway URL).

## Env vars (`_env`)
- `SECRET_KEY` — Flask session secret (currently unused since `app.py` hardcodes its own)
- `YOUTUBE_API_KEY` — YouTube Data API v3 key, used by both `youtube_api.py` and `app.py`'s `add_link_to_queue`
