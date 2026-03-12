from __future__ import annotations

from dataclasses import asdict
import json
import sys
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .instagram import InstagramPublisher
from .pipeline import Pipeline
from .rd import RealDebridClient


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
2. movie-shorts available-movies
   List all Real-Debrid movies, including ready downloads and torrents still processing.
3. movie-shorts sync --limit 10
   Import downloadable movie files into the local SQLite queue.
4. movie-shorts jobs --limit 10
   Inspect discovered, planned, completed, or failed jobs.
5. movie-shorts plan <job_id> [--target-duration <seconds>] [--variant-count 5]
   Download the movie if needed, extract or fetch subtitles, enrich script context, and write manifest.json.
   The planner can generate multiple distinct cut variants for the same movie.
   If target duration is omitted, the planner infers length from the story context.
6. movie-shorts render <job_id> --render-mode crop|fit|fit-43
   Render the planned cut variants from an existing manifest.
   crop = centered 9:16 crop.
   fit = keep the horizontal frame inside 9:16 with a blurred background.
   fit-43 = use a larger 4:3 movie window inside 9:16 with a blurred background.
7. movie-shorts run-movie
   Interactive preset runner: choose any Real-Debrid movie, wait on non-ready torrents if needed, then watch local download and processing progress in one command.
8. movie-shorts batch-run --limit 3 [--target-duration <seconds>] [--variant-count 5]
   Process multiple discovered jobs automatically.
9. movie-shorts retry <job_id>
   Reset a failed job back to discovered state.
10. movie-shorts instagram auth-help
   Show the Meta setup and scopes needed for Reels publishing.
11. movie-shorts instagram publish-job <job_id> --variant 1 --caption "..."
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


def _print_jobs_table(rows: list) -> None:
    table = Table("ID", "Status", "Filename", "Output")
    for row in rows:
        table.add_row(str(row["id"]), row["status"], row["filename"], row["output_path"] or "-")
    console.print(table)


def _format_bytes(size: int | None) -> str:
    if not size:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    return f"{value:.1f}{units[unit_index]}"


def _progress_bar(percent: int | None, width: int = 12) -> str:
    safe_percent = max(0, min(100, int(percent or 0)))
    filled = round((safe_percent / 100) * width)
    return f"{'█' * filled}{'░' * (width - filled)} {safe_percent:>3d}%"


def _print_available_movies_table(rows: list) -> None:
    table = Table("#", "Source", "Queue", "RD Status", "Title", "File", "Size")
    for index, row in enumerate(rows, start=1):
        title = row.parsed_title or Path(row.filename).stem
        if row.parsed_year:
            title = f"{title} ({row.parsed_year})"
        table.add_row(
            str(index),
            row.source_type,
            str(row.job_id) if row.job_id is not None else "-",
            row.job_status or row.rd_status or ("ready" if row.ready else "pending"),
            title,
            row.filename,
            _format_bytes(row.filesize),
        )
    console.print(table)


def _print_processing_torrents_table(rows: list) -> None:
    if not rows:
        return
    table = Table("Torrent", "Status", "Progress", "Title", "Size")
    for row in rows:
        metadata = _infer_title_from_filename(row.filename)
        table.add_row(
            row.id,
            row.status,
            _progress_bar(row.progress),
            metadata,
            _format_bytes(row.bytes_total),
        )
    console.print(table)


def _infer_title_from_filename(filename: str) -> str:
    inferred = RealDebridClient.infer_metadata(filename)
    title = str(inferred.get("parsed_title") or Path(filename).stem)
    year = inferred.get("parsed_year")
    if year:
        return f"{title} ({year})"
    return title


def _prompt_available_movie(pipeline: Pipeline, status: str | None, limit: int):
    rows = pipeline.list_browseable_movies(limit=limit)
    if status:
        filtered = [row for row in rows if row.job_status == status or row.rd_status == status]
        if filtered:
            rows = filtered
        else:
            console.print(f"No movies found with status '{status}'. Showing the full Real-Debrid list instead.")

    if rows:
        console.print("[bold]Real-Debrid Movies[/bold]")
        _print_available_movies_table(rows)

    if not rows:
        raise RuntimeError("No available movies were found in Real-Debrid.")

    valid_indexes = {index: row for index, row in enumerate(rows, start=1)}
    selected = int(typer.prompt("Movie number to run", default="1"))
    if selected not in valid_indexes:
        raise typer.BadParameter(f"Movie number {selected} is not in the current selection list.")
    return valid_indexes[selected]


def _prompt_render_mode() -> RenderMode:
    console.print("Render modes: crop, fit, fit-43")
    selected = typer.prompt("Render mode", default=RenderMode.fit_43.value).strip().lower()
    try:
        return RenderMode(selected)
    except ValueError as exc:
        raise typer.BadParameter(f"Unsupported render mode: {selected}") from exc


def _prompt_optional_target_duration() -> int | None:
    raw_value = typer.prompt("Target duration in seconds (blank for auto)", default="", show_default=False).strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise typer.BadParameter(f"Target duration must be an integer number of seconds, got: {raw_value}") from exc
    if value < 15:
        raise typer.BadParameter("Target duration must be at least 15 seconds.")
    return value


def _run_movie_with_progress(
    pipeline: Pipeline,
    job_id: int,
    target_duration: int | None,
    render_mode: RenderMode,
    variant_count: int,
) -> tuple[Path, Path]:
    row = pipeline.store.get_job(job_id)
    fallback_download_total = int(row["filesize"] or 0) or None
    download_task_id: int | None = None
    process_task_id: int | None = None

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=32),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    with progress:
        process_task_id = progress.add_task("Preparing movie", total=1)

        def stage_callback(description: str, completed: int, total: int) -> None:
            progress.update(process_task_id, description=description, completed=completed, total=max(total, 1))

        def download_callback(downloaded_bytes: int, total_bytes: int | None) -> None:
            nonlocal download_task_id
            effective_total = total_bytes or fallback_download_total or max(downloaded_bytes, 1)
            if download_task_id is None:
                download_task_id = progress.add_task("Downloading source video", total=effective_total)
            progress.update(download_task_id, completed=downloaded_bytes, total=effective_total)

        manifest_path = pipeline.plan_job(
            job_id,
            target_duration_seconds=target_duration,
            variant_count=variant_count,
            stage_callback=stage_callback,
            download_progress_callback=download_callback,
        )
        output_path = pipeline.render_job(
            job_id,
            render_mode=render_mode.value,
            stage_callback=stage_callback,
        )

        if download_task_id is not None:
            total = progress.tasks[download_task_id].total or progress.tasks[download_task_id].completed
            progress.update(download_task_id, completed=total)
        total = progress.tasks[process_task_id].total or progress.tasks[process_task_id].completed
        progress.update(process_task_id, description="Completed", completed=total)

    return manifest_path, output_path


def _queue_selected_movie_with_progress(pipeline: Pipeline, selected_movie) -> int:
    if selected_movie.ready:
        return selected_movie.job_id or pipeline.queue_available_movie(selected_movie)

    if not selected_movie.rd_torrent_id:
        raise RuntimeError(f"Movie '{selected_movie.filename}' is not ready and has no Real-Debrid torrent id.")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=32),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    with progress:
        task_id = progress.add_task("Waiting for Real-Debrid torrent", total=100)

        def remote_callback(status: str, completed: int, total: int) -> None:
            progress.update(task_id, description=f"Waiting for Real-Debrid torrent ({status})", completed=completed, total=max(total, 1))

        job_id = pipeline.wait_and_queue_torrent_movie(
            selected_movie.rd_torrent_id,
            progress_callback=remote_callback,
        )
        progress.update(task_id, description="Real-Debrid source ready", completed=100, total=100)
    return job_id


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


@app.command("available-movies")
def available_movies(limit: int | None = typer.Option(None, min=1, help="Optional maximum Real-Debrid items to inspect. Default: all.")) -> None:
    pipeline = _pipeline()
    try:
        rows = pipeline.list_browseable_movies(limit=limit)
    finally:
        pipeline.close()

    if rows:
        console.print("[bold]Real-Debrid Movies[/bold]")
        _print_available_movies_table(rows)
    else:
        console.print("No movies found in Real-Debrid.")


@app.command("jobs")
def list_jobs(status: str | None = typer.Option(None, help="Optional job status filter."), limit: int = 20) -> None:
    settings = Settings.load()
    pipeline = Pipeline(settings)
    try:
        rows = pipeline.store.list_jobs(status=status, limit=limit)
    finally:
        pipeline.close()
    _print_jobs_table(rows)


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


@app.command("run-movie")
def run_movie(
    job_id: int | None = typer.Argument(None, help="Optional job ID. If omitted, choose from a prompt."),
    sync_first: bool = typer.Option(False, help="Sync all ready Real-Debrid movies into the queue before selection."),
    status: str | None = typer.Option(None, help="Optional queue status filter when choosing a movie interactively."),
    limit: int | None = typer.Option(None, min=1, help="Optional maximum number of Real-Debrid movies to show. Default: all."),
    target_duration: int | None = typer.Option(None, min=15, help="Optional target runtime in seconds."),
    render_mode: RenderMode | None = typer.Option(None, help="Render layout mode. If omitted, choose interactively."),
    variant_count: int = typer.Option(5, min=1, max=12, help="Number of distinct cut variants to plan."),
) -> None:
    pipeline = _pipeline()
    try:
        if sync_first:
            synced = pipeline.sync_downloads(limit=limit)
            console.print(f"Synced {len(synced)} video download(s) before selection.")

        if job_id is not None:
            selected_job_id = job_id
        else:
            selected_movie = _prompt_available_movie(pipeline, status=status, limit=limit)
            selected_job_id = _queue_selected_movie_with_progress(pipeline, selected_movie)
        selected_render_mode = render_mode or _prompt_render_mode()
        selected_target_duration = target_duration if target_duration is not None else _prompt_optional_target_duration()
        manifest_path, output_path = _run_movie_with_progress(
            pipeline,
            selected_job_id,
            selected_target_duration,
            selected_render_mode,
            variant_count,
        )
    finally:
        pipeline.close()

    console.print(f"Planned job {selected_job_id}: {manifest_path}")
    console.print(f"Rendered job {selected_job_id}: {output_path}")


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
