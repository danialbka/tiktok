from __future__ import annotations

import json
import sys
from enum import Enum

import typer
from rich.console import Console
from rich.table import Table

from .config import Settings
from .pipeline import Pipeline


app = typer.Typer(help="Generate TikTok-style shorts from Real-Debrid movies.")
console = Console()


class RenderMode(str, Enum):
    crop = "crop"
    fit = "fit"


AGENT_HELP_TEXT = """\
Movie Shorts agent usage

Purpose:
Turn Real-Debrid movie files into short-form 9:16 captioned videos.

Expected setup:
- REAL_DEBRID_API_KEY must exist in .env.local
- OPENSUBTITLES_API_KEY is optional but recommended for subtitle fallback
- ffmpeg and ffprobe must be installed

Recommended workflow:
1. movie-shorts whoami
   Verify Real-Debrid credentials.
2. movie-shorts sync --limit 10
   Import downloadable movie files into the local SQLite queue.
3. movie-shorts jobs --limit 10
   Inspect discovered, planned, completed, or failed jobs.
4. movie-shorts plan <job_id> [--target-duration <seconds>] [--variant-count 5]
   Download the movie if needed, extract or fetch subtitles, enrich script context, and write manifest.json.
   The planner can generate multiple distinct cut variants for the same movie.
   If target duration is omitted, the planner infers length from the story context.
5. movie-shorts render <job_id> --render-mode crop|fit
   Render the planned cut variants from an existing manifest.
   crop = centered 9:16 crop.
   fit = keep the horizontal frame inside 9:16 with a blurred background.
6. movie-shorts batch-run --limit 3 [--target-duration <seconds>] [--variant-count 5]
   Process multiple discovered jobs automatically.
7. movie-shorts retry <job_id>
   Reset a failed job back to discovered state.

Important artifacts:
- SQLite queue: data/movie_shorts.db
- Download cache: .cache/downloads/
- Job artifacts: artifacts/<job_id>/
- Planning manifest: artifacts/<job_id>/manifest.json
- Rendered short: artifacts/<job_id>/short.mp4
- Rendered variants: artifacts/<job_id>/variants/short_01.mp4, short_02.mp4, ...

Agent guidance:
- Use sync before planning new titles.
- Prefer plan first when debugging subtitle/script matching.
- Check manifest.json for beats, clip timing, subtitle source, and script_context.
- If a job fails because embedded subtitles are missing, add OPENSUBTITLES_API_KEY and retry.
- batch-run continues past per-job failures and records last_error in the queue.
"""


def _pipeline() -> Pipeline:
    return Pipeline(Settings.load())


@app.command()
def whoami() -> None:
    pipeline = _pipeline()
    try:
        user = pipeline.whoami()
    finally:
        pipeline.close()
    console.print_json(json.dumps(user))


@app.command()
def sync(limit: int = typer.Option(25, min=1, max=200, help="Maximum Real-Debrid downloads to inspect.")) -> None:
    pipeline = _pipeline()
    try:
        job_ids = pipeline.sync_downloads(limit=limit)
    finally:
        pipeline.close()
    console.print(f"Synced {len(job_ids)} video download(s) into the local queue.")


@app.command("jobs")
def list_jobs(status: str | None = typer.Option(None, help="Optional job status filter."), limit: int = 20) -> None:
    settings = Settings.load()
    pipeline = Pipeline(settings)
    try:
        rows = pipeline.store.list_jobs(status=status, limit=limit)
    finally:
        pipeline.close()
    table = Table("ID", "Status", "Filename", "Output")
    for row in rows:
        table.add_row(str(row["id"]), row["status"], row["filename"], row["output_path"] or "-")
    console.print(table)


@app.command()
def plan(
    job_id: int,
    target_duration: int | None = typer.Option(None, min=15, help="Optional target runtime in seconds."),
    variant_count: int = typer.Option(5, min=1, max=12, help="Number of distinct cut variants to plan."),
) -> None:
    pipeline = _pipeline()
    try:
        manifest_path = pipeline.plan_job(job_id, target_duration_seconds=target_duration, variant_count=variant_count)
    finally:
        pipeline.close()
    console.print(f"Planned job {job_id}: {manifest_path}")


@app.command()
def render(
    job_id: int,
    render_mode: RenderMode = typer.Option(RenderMode.crop, help="Render layout mode: crop or fit."),
) -> None:
    pipeline = _pipeline()
    try:
        output_path = pipeline.render_job(job_id, render_mode=render_mode.value)
    finally:
        pipeline.close()
    console.print(f"Rendered job {job_id}: {output_path}")


@app.command("batch-run")
def batch_run(
    limit: int = typer.Option(3, min=1, max=50, help="Number of discovered jobs to process."),
    target_duration: int | None = typer.Option(None, min=15, help="Optional target runtime in seconds."),
    render_mode: RenderMode = typer.Option(RenderMode.crop, help="Render layout mode: crop or fit."),
    variant_count: int = typer.Option(5, min=1, max=12, help="Number of distinct cut variants to plan."),
) -> None:
    pipeline = _pipeline()
    try:
        completed = pipeline.batch_run(
            limit=limit,
            target_duration_seconds=target_duration,
            render_mode=render_mode.value,
            variant_count=variant_count,
        )
    finally:
        pipeline.close()
    console.print(f"Completed {len(completed)} job(s): {', '.join(str(item) for item in completed) if completed else 'none'}")


@app.command()
def retry(job_id: int) -> None:
    pipeline = _pipeline()
    try:
        pipeline.retry_job(job_id)
    finally:
        pipeline.close()
    console.print(f"Job {job_id} reset to discovered.")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "-help":
        console.print(AGENT_HELP_TEXT)
        return
    app()
