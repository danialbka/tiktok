from __future__ import annotations

from dataclasses import asdict
import json
import sys
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Settings
from .instagram import InstagramPublisher
from .pipeline import Pipeline


app = typer.Typer(help="Generate TikTok-style shorts from Real-Debrid movies.")
instagram_app = typer.Typer(help="Publish rendered outputs to Instagram Reels.")
console = Console()


class RenderMode(str, Enum):
    crop = "crop"
    fit = "fit"
    fit_43 = "fit-43"


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
5. movie-shorts render <job_id> --render-mode crop|fit|fit-43
   Render the planned cut variants from an existing manifest.
   crop = centered 9:16 crop.
   fit = keep the horizontal frame inside 9:16 with a blurred background.
   fit-43 = use a larger 4:3 movie window inside 9:16 with a blurred background.
6. movie-shorts batch-run --limit 3 [--target-duration <seconds>] [--variant-count 5]
   Process multiple discovered jobs automatically.
7. movie-shorts retry <job_id>
   Reset a failed job back to discovered state.
8. movie-shorts instagram auth-help
   Show the Meta setup and scopes needed for Reels publishing.
9. movie-shorts instagram publish-job <job_id> --variant 1 --caption "..."
   Publish a rendered local artifact to Instagram Reels.

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
- Instagram publishing needs INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID in .env.local.
"""


def _pipeline() -> Pipeline:
    return Pipeline(Settings.load())


def _settings_without_rd() -> Settings:
    return Settings.load(require_real_debrid=False)


def _instagram_publisher() -> InstagramPublisher:
    settings = _settings_without_rd()
    if not settings.instagram_access_token or not settings.instagram_user_id:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID are required for Instagram commands.")
    return InstagramPublisher(
        access_token=settings.instagram_access_token,
        instagram_user_id=settings.instagram_user_id,
        graph_api_version=settings.instagram_graph_api_version,
    )


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
    render_mode: RenderMode = typer.Option(RenderMode.crop, help="Render layout mode: crop, fit, or fit-43."),
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
    render_mode: RenderMode = typer.Option(RenderMode.crop, help="Render layout mode: crop, fit, or fit-43."),
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


@instagram_app.command("auth-help")
def instagram_auth_help() -> None:
    console.print(
        """\
Instagram publishing setup

Expected account setup:
- Instagram professional account (Business or Creator)
- The Instagram account must be linked to a Facebook Page
- A Meta app with Instagram API / Graph API access

Common permissions for content publishing:
- instagram_business_basic
- instagram_business_content_publish
- pages_show_list
- pages_read_engagement

Local env vars:
- INSTAGRAM_ACCESS_TOKEN
- INSTAGRAM_USER_ID
- INSTAGRAM_GRAPH_API_VERSION (optional, default: v24.0)

Current CLI flow:
1. Render a local MP4 from this project
2. Run movie-shorts instagram whoami
3. Run movie-shorts instagram publish-job <job_id> --variant 1 --caption "..."

Notes:
- The CLI uses Meta's resumable upload flow for local files.
- If your app/token setup differs, use the token Meta provides for the linked professional account.
"""
    )


@instagram_app.command("whoami")
def instagram_whoami() -> None:
    publisher = _instagram_publisher()
    try:
        account = publisher.get_account()
    finally:
        publisher.close()
    console.print_json(json.dumps(account))


@instagram_app.command("publish")
def instagram_publish(
    video_path: Path,
    caption: str = typer.Option("", help="Caption to publish with the Reel."),
    share_to_feed: bool = typer.Option(True, "--share-to-feed/--reels-only", help="Share the Reel to the main Instagram feed."),
    thumb_offset_ms: int | None = typer.Option(None, min=0, help="Optional cover frame offset in milliseconds."),
    timeout_seconds: int = typer.Option(900, min=30, help="Maximum seconds to wait for Meta processing."),
    poll_interval_seconds: int = typer.Option(10, min=1, help="Polling interval while the Reel processes."),
) -> None:
    if not video_path.exists():
        raise typer.BadParameter(f"Video path does not exist: {video_path}")
    publisher = _instagram_publisher()
    try:
        result = publisher.publish_reel_from_path(
            video_path=video_path,
            caption=caption,
            share_to_feed=share_to_feed,
            thumb_offset_ms=thumb_offset_ms,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    finally:
        publisher.close()
    console.print_json(json.dumps(asdict(result)))


@instagram_app.command("publish-url")
def instagram_publish_url(
    video_url: str,
    caption: str = typer.Option("", help="Caption to publish with the Reel."),
    share_to_feed: bool = typer.Option(True, "--share-to-feed/--reels-only", help="Share the Reel to the main Instagram feed."),
    thumb_offset_ms: int | None = typer.Option(None, min=0, help="Optional cover frame offset in milliseconds."),
    timeout_seconds: int = typer.Option(900, min=30, help="Maximum seconds to wait for Meta processing."),
    poll_interval_seconds: int = typer.Option(10, min=1, help="Polling interval while the Reel processes."),
) -> None:
    publisher = _instagram_publisher()
    try:
        result = publisher.publish_reel_from_url(
            video_url=video_url,
            caption=caption,
            share_to_feed=share_to_feed,
            thumb_offset_ms=thumb_offset_ms,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    finally:
        publisher.close()
    console.print_json(json.dumps(asdict(result)))


@instagram_app.command("publish-job")
def instagram_publish_job(
    job_id: int,
    caption: str = typer.Option("", help="Caption to publish with the Reel."),
    variant: int = typer.Option(1, min=1, help="Which rendered variant to publish."),
    render_mode: RenderMode = typer.Option(RenderMode.crop, help="Which render set to use: crop, fit, or fit-43."),
    share_to_feed: bool = typer.Option(True, "--share-to-feed/--reels-only", help="Share the Reel to the main Instagram feed."),
    thumb_offset_ms: int | None = typer.Option(None, min=0, help="Optional cover frame offset in milliseconds."),
    timeout_seconds: int = typer.Option(900, min=30, help="Maximum seconds to wait for Meta processing."),
    poll_interval_seconds: int = typer.Option(10, min=1, help="Polling interval while the Reel processes."),
) -> None:
    settings = _settings_without_rd()
    video_path = InstagramPublisher.resolve_rendered_video_path(
        artifact_dir=settings.artifact_dir,
        job_id=job_id,
        variant=variant,
        render_mode=render_mode.value,
    )
    if not video_path.exists():
        raise typer.BadParameter(f"Rendered output not found: {video_path}")
    publisher = _instagram_publisher()
    try:
        result = publisher.publish_reel_from_path(
            video_path=video_path,
            caption=caption,
            share_to_feed=share_to_feed,
            thumb_offset_ms=thumb_offset_ms,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    finally:
        publisher.close()
    console.print_json(json.dumps(asdict(result)))


app.add_typer(instagram_app, name="instagram")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "-help":
        console.print(AGENT_HELP_TEXT)
        return
    app()
