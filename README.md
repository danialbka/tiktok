# Movie Shorts

Generate TikTok-style or Shorts-style vertical videos from movies already in your Real-Debrid account.

The pipeline downloads a source movie from Real-Debrid, finds subtitles, builds a story arc from timed dialogue, enriches planning with screenplay or transcript context when available, and renders a 9:16 captioned export.

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

### 1. Sync downloads into the queue

```bash
uv run movie-shorts sync --limit 10
```

### 2. Inspect queued jobs

```bash
uv run movie-shorts jobs --limit 10
```

### 3. Plan one movie

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

### 4. Render the short(s)

```bash
uv run movie-shorts render 18
```

Render with the full horizontal frame preserved inside a 9:16 canvas:

```bash
uv run movie-shorts render 18 --render-mode fit
```

### 5. Run a small batch

```bash
uv run movie-shorts batch-run --limit 3 --target-duration 120 --variant-count 5
```

### 6. Retry a failed job

```bash
uv run movie-shorts retry 18
```

### Agent-oriented CLI help

```bash
uv run movie-shorts -help
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
- Render modes:
  - `crop`: center-crop to fill 9:16
  - `fit`: keep the horizontal frame visible inside 9:16 with a blurred background
- Duration behavior:
  - by default the planner infers how short or long the cut should be from scene continuity and context
  - `--target-duration` overrides that when you want a specific runtime
  - `MOVIE_SHORTS_MAX_DURATION_SECONDS` is optional and acts only as a cap when set
