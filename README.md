# Movie Shorts

Generate TikTok-style or Shorts-style vertical videos from movies already in your Real-Debrid account.

The pipeline downloads a source movie from Real-Debrid, finds subtitles, builds a short story arc from timed dialogue, enriches planning with screenplay or transcript context when available, and renders a 9:16 captioned export.

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
- Plans a compact chronological story arc up to 3 minutes, or a custom target duration up to 180 seconds.
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

### 4. Render the short

```bash
uv run movie-shorts render 18
```

### 5. Run a small batch

```bash
uv run movie-shorts batch-run --limit 3 --target-duration 120
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

Each manifest records:

- subtitle source
- selected beats
- rendered clips
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
