# Movie Shorts

Python pipeline for turning movies in a Real-Debrid account into TikTok-style vertical shorts using subtitle-driven story planning.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies with `uv sync`.
3. Put secrets in `.env.local`.
4. Run `uv run movie-shorts whoami` to verify Real-Debrid access.
5. Run `uv run movie-shorts sync` to import downloaded movies into the local queue.
6. Run `uv run movie-shorts batch-run --limit 1` to plan and render the first queued movie.

## Notes

- Embedded subtitles are preferred. OpenSubtitles is only used when `OPENSUBTITLES_API_KEY` is configured and embedded subtitle extraction fails.
- Script context is enriched from Script Slug and IMSDb when a matching script page is found.
- Rendering uses centered 9:16 crop in v1.
- Each job writes artifacts, manifests, and logs to `artifacts/<job_id>/`.
