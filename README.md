![Movie Shorts banner](./banner.png)

# Movie Shorts

Generate TikTok-style or Shorts-style vertical videos from movies already in your Real-Debrid account.

The pipeline downloads a source movie from Real-Debrid, finds subtitles, builds a story arc from timed dialogue, enriches planning with screenplay or transcript context when available, and renders a 9:16 captioned export.

## For Agents

If you are using Codex or another agent to operate this repo:

- read this `README.md` first before making workflow decisions
- prefer the built-in CLI and existing presets before adding new code
- use `GPT-5.4` in Codex for the best results on planning, debugging, and media workflow tasks

## What It Does

- Syncs downloadable video files from your Real-Debrid account into a local SQLite job queue.
- Pulls subtitles from embedded tracks first.
- Falls back to Real-Debrid sidecar subtitles when a torrent package includes `.srt` files.
- Falls back to OpenSubtitles when local subtitle sources are missing.
- Enriches planning with script or transcript context in this order:
  - `Script Slug`
  - `IMSDb`
  - `SimplyScripts`
  - general web search
- Plans a story arc whose runtime is inferred from the material unless you explicitly set a target duration.
- Renders a vertical video with burned subtitles using yellow `Arial WGL Bold Italic`.

## Requirements

- Python `>=3.10`
- `uv`
- `ffmpeg`
- `ffprobe`
- A Real-Debrid API key
- Optional:
  - OpenSubtitles API key
  - OpenAI API key for future planner upgrades

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Create a local secrets file:

```bash
cp .env.example .env.local
```

3. Fill in at least:

```env
REAL_DEBRID_API_KEY=your-real-debrid-api-key
```

4. Verify access:

```bash
uv run movie-shorts whoami
```

## Security

Secrets and generated outputs are intentionally ignored from git:

- `.env`
- `.env.local`
- `.cache/`
- `data/`
- `artifacts/`

Use `.env.example` as the checked-in template. Do not commit live API keys.

## CLI Workflow

### 1. Browse all Real-Debrid movies, including in-progress torrents

```bash
uv run movie-shorts available-movies
```

That command shows one browseable list with:

- ready movie files already available in Real-Debrid downloads
- movies that are still processing inside Real-Debrid
- any matching local queue ID/status if the movie was already synced before

### 2. Sync downloads into the queue

```bash
uv run movie-shorts sync --limit 10
```

### 3. Inspect queued jobs

```bash
uv run movie-shorts jobs --limit 10
```

### 4. Plan one movie

```bash
uv run movie-shorts plan 18
```

Plan a longer cut:

```bash
uv run movie-shorts plan 18 --target-duration 120
```

Plan 5 different cut options for the same movie:

```bash
uv run movie-shorts plan 18 --target-duration 120 --variant-count 5
```

### 5. Render the short(s)

```bash
uv run movie-shorts render 18
```

Render with the full horizontal frame preserved inside a 9:16 canvas:

```bash
uv run movie-shorts render 18 --render-mode fit
```

Render with a larger 4:3 movie window inside 9:16, similar to some Instagram movie-clip accounts:

```bash
uv run movie-shorts render 18 --render-mode fit-43
```

### 6. Use the interactive movie runner

If you do not want to remember multiple commands, use the built-in runner:

```bash
uv run movie-shorts run-movie
```

That command can:

- show all Real-Debrid movies, including ones still processing
- let you pick the movie from a numbered menu
- let you choose `crop`, `fit`, or `fit-43`
- let you leave duration on auto or set a target
- wait on a not-ready Real-Debrid torrent and queue it automatically once it finishes
- show a live bar while the source video downloads locally
- show live processing progress while plan + render runs
- queue the movie automatically if it was not already in the local database
- run plan + render in one step

You can also mix interactive selection with explicit flags:

```bash
uv run movie-shorts run-movie 18 --render-mode fit-43 --variant-count 5
```

### 7. Run a small batch

```bash
uv run movie-shorts batch-run --limit 3 --target-duration 120 --variant-count 5
```

### 8. Retry a failed job

```bash
uv run movie-shorts retry 18
```

### Agent-oriented CLI help

```bash
uv run movie-shorts -help
```

## Instagram Browser Upload

The repo includes a reusable Codex skill for Instagram web uploads through the real Instagram browser UI:

- repo skill: `skills/instagram-browser-upload/`
- paired skill to use with it: `$playwright-interactive`

Use the browser route when you want Instagram posting from this repo.

Typical browser-based flow:

1. Start a Playwright session.
2. Load a local saved storage-state file if you have one.
3. Open Instagram and confirm the active account.
4. Use `Create -> Post`.
5. Attach the rendered MP4.
6. Click through the reel crop/edit screens.
7. Fill the caption.
8. Click `Share` and wait for `Reel shared`.

Important:

- keep browser session-state files local and out of git
- the reusable instructions are committed, but live authenticated state is not
- a saved local state file can make later uploads much faster when still valid

Example local saved state path:

```text
.cache/playwright/instagram-browser-state.json
```

## Output Layout

- Queue database: `data/movie_shorts.db`
- Download cache: `.cache/downloads/`
- Per-job artifacts: `artifacts/<job_id>/`
- Plan manifest: `artifacts/<job_id>/manifest.json`
- Final video: `artifacts/<job_id>/short.mp4`
- Variant videos: `artifacts/<job_id>/variants/short_01.mp4` through `short_05.mp4`

Each manifest records:

- subtitle source
- selected beats
- rendered clips
- cut variants
- planner notes
- script or transcript context used for planning

## Subtitle and Script Fallback Order

### Subtitle order

1. Embedded subtitles in the movie file
2. Real-Debrid sidecar subtitle files from the torrent package
3. OpenSubtitles API

### Script context order

1. Script Slug
2. IMSDb
3. SimplyScripts
4. General web search

If no script source is found, planning still works from subtitle continuity alone.

## Typical End-to-End Example

```bash
uv sync
uv run movie-shorts whoami
uv run movie-shorts sync --limit 10
uv run movie-shorts jobs --limit 10
uv run movie-shorts plan 18 --target-duration 120
uv run movie-shorts render 18
```

## Current Limitations

- Framing is still a center crop, not face-aware reframing.
- Story planning is much better with screenplay or transcript context, but timestamps still come from subtitles.
- Some titles may have no public script coverage and will fall back to subtitle-only planning.
- Rendering large 4K sources can take a while because clip extraction and subtitle burn-in re-encode video.
- Browser-based Instagram upload depends on a valid local logged-in browser state or a manual login step.
- Render modes:
  - `crop`: center-crop to fill 9:16
  - `fit`: keep the horizontal frame visible inside 9:16 with a blurred background
  - `fit-43`: use a larger 4:3 movie window inside 9:16 with a blurred background
- Duration behavior:
  - by default the planner infers how short or long the cut should be from scene continuity and context
  - `--target-duration` overrides that when you want a specific runtime
  - `MOVIE_SHORTS_MAX_DURATION_SECONDS` is optional and acts only as a cap when set
