# Karaoke Night — System Overview

A self-hosted, two-screen karaoke party app: a TV/big-screen player and a phone-based remote control, sharing a live queue over a Flask + SQLite backend. Deployed on Railway.

## Concept

- **TV screen** (`/`) — fullscreen YouTube player with an auto-hiding on-screen control bar (play/pause, seek, ±10s, playback speed). Auto-advances when a video ends, showing an "AI Vocal Score" screen with a 10s countdown first.
- **Remote** (`/search`) — phone-friendly control page guests use to search YouTube for karaoke tracks, paste links, build/organize **Playlists**, and manage the shared queue.
- Both pages poll a shared queue in the database, so any number of phones can add songs and everyone sees the same state on the TV. Playlists are also shared/DB-backed, so any phone can create, edit, or fire off a playlist and everyone sees the same set.

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
  index.html           # TV screen (player controls, speed modal, score screen)
  search.html          # Remote control (tabs: Search / Playlists / Queue)
  song.html            # UNUSED — references a Song.lyrics/audio_file schema that no longer exists
static/
  style.css            # Shared base styles — index.html & search.html mostly use inline <style> instead
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
  id, video_id, title, channel

Playlist
  id, name, created_at

PlaylistSong
  id, playlist_id (FK -> Playlist.id), video_id, title, channel
```

- `Song` is a static, seeded songbook (~380 rows) spanning genres: Pop, R&B, OPM, Ballad, Dance, Duet, 90s/2000s, Disney, etc. Seeded once at startup via `seed_songlist()` (`db.create_all()` + seed run inside `with app.app_context():` at module load, so it works under gunicorn too, not just `__main__`). **The Song Book UI tab that browsed this table has been removed** (replaced by Playlists — see below), so `Song`/`/songlist` are now effectively unused by the frontend, though the route and data still exist.
- `QueueItem` is the live, shared play queue — the single source of truth both screens poll.
- `Playlist` / `PlaylistSong` are new: user-created, shared playlists. Each `PlaylistSong` is a denormalized copy of a video's id/title/channel (same shape as `QueueItem`), not a foreign key into `Song`, since playlist entries come from YouTube search/link results, not the static songbook.

## Routes (`app.py`)

**Pages**
- `GET /` — TV screen, passes current queue into `index.html`
- `GET /search` — remote control, passes songbook genres + queue into `search.html` (genres are currently unused by the frontend now that Song Book is gone, but still passed in)

**JSON API — Queue**
- `GET /api/queue` — current queue (polled by both pages every 2–3s)
- `GET /songlist?q=&genre=` — filter/search the songbook (route still exists; no longer called from the UI)
- `POST /add_to_queue` — add a specific video (id, title, channel) — used after a YouTube search result
- `POST /add_link_to_queue` — paste a raw YouTube URL/ID; extracts the video ID via regex, fetches title/channel via oEmbed, then re-validates via YouTube Data API `videos` endpoint and **rejects non-embeddable videos**
- `POST /remove_from_queue` — remove by index
- `POST /skip_song` — drop the first (currently playing) item; called by the TV screen (after the score screen's countdown) and by the remote's Skip button
- `POST /clear_queue` — wipe the whole queue

**JSON API — Playlists** *(new)*
- `GET /playlists` — all playlists with their songs, `{id, name, songs: [{id, video_id, title, channel}]}`
- `POST /create_playlist` — `name` → creates an empty playlist, returns full playlist list
- `POST /delete_playlist` — `playlist_id` → deletes playlist + its songs
- `POST /add_to_playlist` — `playlist_id, video_id, title, channel` → appends a song to a playlist
- `POST /remove_from_playlist` — `song_id` → removes one song from whichever playlist it's in
- `POST /queue_playlist` — `playlist_id` → appends **every** song in the playlist to the live queue in one call
- `POST /queue_playlist_song` — `song_id` → appends a single playlist song to the live queue

**Blueprint** (`youtube_api.py`)
- `GET /youtube_search?q=` — wraps YouTube Data API v3 `search.list` (`type=video`, `videoEmbeddable=true`, `maxResults=20`), returns `[{id, title, channel, thumbnail, link}]`. Skips non-video results (channels/playlists). Returns `502`/`500` with an error message on API failure.

## Frontend behavior

### `index.html` (TV screen)

- Requires a tap/Enter/Space "Start" gesture before playback begins (smart-TV autoplay restriction workaround) — nothing renders or polls until then.
- Loads the YouTube IFrame API, creates a `YT.Player` with controls/keyboard/related-videos/fullscreen all disabled, plus an invisible `.video-blocker` overlay so the embedded player can't be clicked/interacted with directly — tapping it toggles the custom control bar instead.
- **Player control bar** (`.player-controls` / `.pc-panel`): a floating glass-morphism bar pinned to the bottom of the video with:
  - Seek row: current time, a draggable seek track (mouse + touch, live time-label while dragging, smooth glide between polling updates via CSS `transition`, snaps to exact position only on release), duration.
  - Transport row: −10s, play/pause, +10s, and a playback-speed pill (`pc-speed-toggle`, shows e.g. `1.25x`).
  - Auto-hides after 4s of inactivity (`showControls()`/`hideControls()`, shared `controlsHideTimer`); reappears on mousemove/touch or clicking the speed pill; tapping the video overlay force-toggles visibility.
- **Playback speed modal** (`.speed-overlay`): a small floating card (not a full-screen takeover) anchored near the bottom-right of the player, opened/closed by **clicking the same speed pill again** (toggle — no separate close/back button). Contents: live `X.XXx` readout, −/slider/+ (0.25–2.0, step 0.05), and preset pills (0.5–2.0). Fully responsive (base / ≤900px / ≤640px / ≤380px / short-landscape breakpoints), internally scrollable (`max-height: calc(100dvh - …)`, `overflow-y:auto`) so it never gets clipped on short screens. **Its visibility is synced to the same `controlsHideTimer` as the main control bar** — opening it, adjusting speed, or hovering it all reset the shared 4s timer; the timeout and the video-tap-to-close both hide the control bar and the speed modal together.
- `onPlayerStateChange` detects video end (`state === 0`) and triggers the **AI Vocal Score sequence** (`beginScoreSequence()`): a full-screen animated score screen (random 85–100 score, occasionally 96–100 "rare perfect", ring/counter animation, star rating, confetti for high scores, hype audio sting) with a 10s countdown ring, then calls `POST /skip_song` and fades back to the next video.
- Polls `GET /api/queue` every 2s; only re-renders the video if the *first* queue item's `video_id` actually changed (and skips entirely while the score screen is showing), otherwise just updates the queue strip/ticker.
- UI chrome: top bar (logo + landscape-lock toggle + hint), a queue strip overlay (now playing + rotating "up next" ticker line), bottom ticker bar scrolling the full queue, and an empty state prompting people to open `/search`.
- Mobile-only landscape lock: toggle button + rotate-device prompt overlay, persisted via `localStorage`, attempts `screen.orientation.lock('landscape')` + fullscreen on Start.

### `search.html` (remote)

- Three tabs: **Search**, **Playlists**, **Queue**. *(The old "Song Book" genre-browse tab has been fully replaced by Playlists.)*
- **Search tab**: calls `/youtube_search`, renders results with thumbnail/title/channel and two actions per result — **"+ Add"** (straight to the live queue) and **📂** (opens the Add-to-Playlist picker). Also has an "Add by link" section (`/add_link_to_queue`) with inline success/error messaging.
- **Playlists tab** *(new, replaces Song Book)*:
  - Grid view: each playlist card shows name + song count and a pink ▶ button that instantly queues the *entire* playlist without opening it (`queuePlaylistById`).
  - Tapping a card opens a detail view: back button, playlist name, delete (🗑), a full-width "▶ Add All to Queue" button, and a per-song list where each row has a **+** (queue just that song) and **✕** (remove from playlist).
  - "+ New" opens an inline bottom-sheet modal (`#new-playlist-modal`) to name a new playlist — **no native `prompt()`**, so it doesn't block the page.
  - **Add-to-playlist picker** (`#playlist-picker`): opened from a search result's 📂 button; lists existing playlists (tap to add the song) plus an inline "create new playlist + add" row at the bottom.
- **Queue tab**: full queue list with thumbnails, "Now Playing" tag on item 0, per-item remove, plus global Skip/Clear buttons.
- **All playlist/queue mutations are optimistic (AJAX-first, not wait-then-render)**: creating a playlist, adding/removing a song, queueing a playlist or single song, and deleting a playlist all update the local `queue`/`playlists` state and re-render **immediately**, then fire the corresponding `POST` in the background; on failure the local state is silently rolled back to the last known-good snapshot. This makes every playlist/queue action in the UI feel instant instead of waiting on a round-trip.
- Polls `GET /api/queue` every 3s to stay in sync with whatever other phones (or the TV) have changed. **Playlists are not currently polled** — they refresh only when the current phone performs a playlist action (create/add/remove/delete) via `loadPlaylists()`/response payloads. If two phones edit playlists at the same time, one may not see the other's change until their own next action. *(Flagged in Known loose ends below.)*

## Known loose ends / things to confirm before extending

1. **Playlists are not live-synced across phones.** Unlike the queue (polled every 2–3s), the Playlists tab only refreshes on local action. If this needs to feel as "live" as the queue, add a `setInterval(loadPlaylists, 3000)`-style poll, ideally only while the Playlists tab is active to avoid unnecessary traffic.
2. **`Song` / `/songlist` are now vestigial.** The Song Book UI that consumed them was removed in favor of Playlists. Decide whether to: (a) keep the 380-row seeded songbook + route around for a future "browse by genre → add to playlist" feature, or (b) remove `Song`, `seed_songlist()`, and `/songlist` entirely to simplify the schema.
3. **`trusted_channels.py` is dead code** — a large list of channel names (mostly real karaoke channels, but the tail has obviously placeholder/junk entries like `Karaoke4AllEverything`). Nothing imports or filters against it. If the intent was to only surface trusted-channel results in `/youtube_search`, that filter was never wired in — and placeholder entries should be pruned/replaced with real channel IDs (not display names, which can collide/change).
4. **`song.html` is orphaned** — references `song.title`, `song.artist`, `song.audio_file`, `song.lyrics`, none of which exist in the current models or routes. Likely leftover from an earlier self-hosted audio/lyrics version. Decide: delete it, or is a per-song detail page planned?
5. **`SECRET_KEY` handling should be double-checked** — should come from `os.environ.get('SECRET_KEY')` (with a dev fallback), not be hardcoded, if it isn't already.
6. **`style.css` is only partly used** — `index.html`/`search.html` have their own inline `<style>` blocks; `style.css` mainly served `song.html` (itself unused) plus a few class names that may no longer appear anywhere. Worth checking for dead CSS.
7. **Folder structure assumption** — confirm `templates/`/`static/` layout matches what's actually deployed on Railway.
8. **No auth on any route** — anyone who can reach `/search` (e.g. on the local network) can control the queue and edit/delete any playlist. Presumably intentional for a party app, but worth confirming scope (LAN-only vs public Railway URL) — this matters more now that playlists are shared/mutable by anyone.
9. **AI Vocal Score is fully randomized** — `randomScore()` has no relationship to actual singing/audio input; it's a party-game flourish (85–100 range, 15% chance of 96–100), not real scoring. Worth stating explicitly to any future dev/AI so no one assumes there's mic analysis happening.
10. **Playback speed range is 0.25×–2.0×** (`MIN_SPEED`/`MAX_SPEED` constants in `index.html`), stepped in 0.05 increments via slider/±/presets — all funnel through `setSpeedExact()`, which is the single place to change these bounds if needed.
11. **Optimistic UI failure handling is "silent rollback."** If a playlist/queue POST fails, the UI reverts without any visible error toast — the user just sees their action "undo" itself a moment later. Consider adding a lightweight error toast if this proves confusing in practice.

## Env vars (`_env`)
- `SECRET_KEY` — Flask session secret
- `YOUTUBE_API_KEY` — YouTube Data API v3 key, used by both `youtube_api.py` and `app.py`'s `add_link_to_queue`