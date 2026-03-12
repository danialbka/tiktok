from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
import json
import shutil
import time

from .config import Settings
from .models import AvailableMovie, JobManifest
from .planner import choose_story_beats
from .rd import RealDebridClient, TorrentInfo
from .render import render_short
from .scripts import ScriptContextFetcher
from .store import JobStore
from .subtitles import choose_rd_sidecar_subtitle, extract_stored_rar_entry, fetch_subtitles, load_cues


class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = JobStore(settings.db_path)
        self.rd = RealDebridClient(settings.real_debrid_api_key)
        self.script_fetcher = ScriptContextFetcher()

    def close(self) -> None:
        self.rd.close()
        self.script_fetcher.close()

    def whoami(self) -> dict:
        return self.rd.get_user()

    def list_available_movies(self, limit: int | None = None) -> list[AvailableMovie]:
        items = self.rd.list_downloads(limit=limit)
        available: list[AvailableMovie] = []
        for item in items:
            queued = self.store.get_job_by_rd_id(item.id)
            queued_metadata = json.loads(queued["metadata_json"] or "{}") if queued else {}
            inferred = self.rd.infer_metadata(item.filename)
            available.append(
                AvailableMovie(
                    source_type="download",
                    rd_download_id=item.id,
                    filename=item.filename,
                    filesize=item.filesize,
                    download_url=item.download_url,
                    link_url=item.link,
                    mime_type=item.mime_type,
                    generated_at=item.generated_at,
                    parsed_title=str(queued_metadata.get("parsed_title") or inferred.get("parsed_title") or item.stem),
                    parsed_year=queued_metadata.get("parsed_year") or inferred.get("parsed_year"),
                    job_id=int(queued["id"]) if queued else None,
                    job_status=str(queued["status"]) if queued else None,
                    output_path=str(queued["output_path"]) if queued and queued["output_path"] else None,
                    ready=True,
                    rd_status="downloaded",
                    rd_progress=100,
                )
            )
        return available

    def list_processing_torrents(self, limit: int | None = None) -> list[TorrentInfo]:
        torrents = self.rd.list_torrents(limit=limit)
        return [item for item in torrents if item.status != "downloaded" or item.progress < 100]

    def list_browseable_movies(self, limit: int | None = None) -> list[AvailableMovie]:
        downloads = self.list_available_movies(limit=limit)
        existing_links = {item.link_url for item in downloads}
        browseable = list(downloads)
        for torrent in self.rd.list_torrents(limit=limit):
            if torrent.progress >= 100 and torrent.status == "downloaded":
                continue
            if any(link in existing_links for link in torrent.links):
                continue
            inferred = self.rd.infer_metadata(torrent.filename)
            browseable.append(
                AvailableMovie(
                    source_type="torrent",
                    rd_download_id=f"torrent:{torrent.id}",
                    filename=torrent.filename,
                    filesize=torrent.bytes_total,
                    download_url="",
                    link_url=torrent.links[0] if torrent.links else "",
                    parsed_title=str(inferred.get("parsed_title") or Path(torrent.filename).stem),
                    parsed_year=inferred.get("parsed_year"),
                    ready=False,
                    rd_status=torrent.status,
                    rd_progress=torrent.progress,
                    rd_torrent_id=torrent.id,
                )
            )
        return browseable

    def queue_available_movie(self, movie: AvailableMovie) -> int:
        metadata = self.rd.infer_metadata(movie.filename)
        return self.store.upsert_download(
            {
                "rd_download_id": movie.rd_download_id,
                "filename": movie.filename,
                "download_url": movie.download_url,
                "link_url": movie.link_url,
                "filesize": movie.filesize,
                "mime_type": movie.mime_type,
                "metadata": metadata | {"generated_at": movie.generated_at},
            }
        )

    def queue_torrent_movie(self, torrent: TorrentInfo) -> int:
        video_link = self.rd.pick_video_link(torrent)
        if not video_link:
            raise RuntimeError(f"Torrent {torrent.id} does not expose a downloadable video link yet.")
        package = self.rd.unrestrict_link(video_link)
        metadata = self.rd.infer_metadata(package.filename or torrent.filename)
        return self.store.upsert_download(
            {
                "rd_download_id": package.id,
                "filename": package.filename,
                "download_url": package.download_url,
                "link_url": package.link,
                "filesize": package.filesize,
                "mime_type": package.mime_type,
                "metadata": metadata | {"generated_at": package.generated_at, "source_torrent_id": torrent.id},
            }
        )

    def wait_and_queue_torrent_movie(
        self,
        torrent_id: str,
        progress_callback: Callable[[str, int, int], None] | None = None,
        timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 5.0,
    ) -> int:
        deadline = time.time() + timeout_seconds
        last_info: TorrentInfo | None = None
        while time.time() < deadline:
            info = self.rd.get_torrent_info(torrent_id)
            last_info = info
            if progress_callback is not None:
                progress_callback(info.status, int(info.progress or 0), 100)
            if info.status == "downloaded" and info.progress >= 100:
                return self.queue_torrent_movie(info)
            if info.status in {"error", "virus", "dead"}:
                raise RuntimeError(f"Torrent {torrent_id} failed with status {info.status}.")
            time.sleep(poll_interval_seconds)
        raise RuntimeError(
            f"Timed out waiting for torrent {torrent_id} to finish. Last status: {last_info.status if last_info else 'unknown'}"
        )

    def sync_downloads(self, limit: int | None = None) -> list[int]:
        items = self.rd.list_downloads(limit=limit)
        synced: list[int] = []
        for item in items:
            job_id = self.queue_available_movie(
                AvailableMovie(
                    source_type="download",
                    rd_download_id=item.id,
                    filename=item.filename,
                    filesize=item.filesize,
                    download_url=item.download_url,
                    link_url=item.link,
                    mime_type=item.mime_type,
                    generated_at=item.generated_at,
                )
            )
            synced.append(job_id)
        return synced

    def batch_run(
        self,
        limit: int = 5,
        target_duration_seconds: int | None = None,
        render_mode: str = "crop",
        variant_count: int = 5,
    ) -> list[int]:
        jobs = self.store.list_jobs(status="discovered", limit=limit)
        completed: list[int] = []
        for row in jobs:
            job_id = int(row["id"])
            try:
                self.plan_job(job_id, target_duration_seconds=target_duration_seconds, variant_count=variant_count)
                self.render_job(job_id, render_mode=render_mode)
            except Exception as exc:
                self._mark_failed(job_id, exc)
                continue
            completed.append(job_id)
        return completed

    def plan_job(
        self,
        job_id: int,
        target_duration_seconds: int | None = None,
        variant_count: int = 5,
        stage_callback: Callable[[str, int, int], None] | None = None,
        download_progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        row = self.store.get_job(job_id)
        job_dir = self.settings.artifact_dir / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        self._emit_stage(stage_callback, "Preparing source video", 0, 6)
        if row["local_video_path"]:
            local_video_path = Path(row["local_video_path"])
        else:
            local_video_path = self._ensure_video(job_id, download_progress_callback=download_progress_callback)
        self._emit_stage(stage_callback, "Fetching script context", 1, 6)
        metadata = json.loads(row["metadata_json"] or "{}")
        script_context = []
        script_context_error: str | None = None
        rd_sidecar_subtitle_path: Path | None = None
        if self.settings.enable_script_context:
            try:
                script_context = self.script_fetcher.fetch(
                    title=str(metadata.get("parsed_title") or Path(row["filename"]).stem),
                    year=metadata.get("parsed_year"),
                    artifact_dir=job_dir,
                )
            except Exception as exc:
                script_context_error = str(exc)
        try:
            rd_sidecar_subtitle_path = self._fetch_rd_sidecar_subtitle(row, job_dir)
        except Exception as exc:
            script_context_error = script_context_error or f"RD sidecar subtitle recovery skipped: {exc}"
        self._emit_stage(stage_callback, "Resolving subtitles", 2, 6)
        subtitle_path, subtitle_source = fetch_subtitles(
            local_video_path,
            job_dir / "subtitles.srt",
            language=self.settings.default_language,
            opensubtitles_api_key=self.settings.opensubtitles_api_key,
            query_title=str(metadata.get("parsed_title") or Path(row["filename"]).stem),
            query_year=metadata.get("parsed_year"),
            rd_sidecar_subtitle_path=rd_sidecar_subtitle_path,
        )
        self._emit_stage(stage_callback, "Loading subtitle cues", 3, 6)
        cues = load_cues(subtitle_path)
        self._emit_stage(stage_callback, "Choosing story beats", 4, 6)
        manifest = choose_story_beats(
            cues,
            self.settings.max_duration_seconds,
            script_context=script_context,
            target_duration_seconds=target_duration_seconds,
            variant_count=variant_count,
        )
        manifest.job_id = job_id
        manifest.filename = str(row["filename"])
        manifest.source_video_path = str(local_video_path)
        manifest.subtitle_source = subtitle_source
        manifest.subtitle_path = str(subtitle_path)
        manifest.script_context = script_context
        if script_context:
            manifest.planner_notes.append(
                "Script context was fetched and used to guide beat selection."
            )
        elif script_context_error:
            manifest.planner_notes.append(f"Script context fetch skipped after error: {script_context_error}")
        if target_duration_seconds:
            manifest.planner_notes.append(f"Requested target duration: {target_duration_seconds} seconds.")
        manifest.planner_notes.append(f"Requested variant count: {variant_count}.")

        self._emit_stage(stage_callback, "Writing manifest", 5, 6)
        manifest_path = job_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        self.store.update_job(
            job_id,
            status="planned",
            local_video_path=str(local_video_path),
            local_subtitle_path=str(subtitle_path),
            manifest_path=str(manifest_path),
            last_error=None,
        )
        self.store.add_event(job_id, "planned", {"manifest_path": str(manifest_path)})
        self._emit_stage(stage_callback, "Planning complete", 6, 6)
        return manifest_path

    def render_job(
        self,
        job_id: int,
        render_mode: str = "crop",
        stage_callback: Callable[[str, int, int], None] | None = None,
    ) -> Path:
        row = self.store.get_job(job_id)
        if row["status"] not in {"planned", "rendering", "completed"}:
            raise RuntimeError(f"Job {job_id} is not planned yet.")
        manifest_path = Path(row["manifest_path"])
        self._emit_stage(stage_callback, "Loading render manifest", 0, 1)
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = self._manifest_from_dict(manifest_data)
        source_video = Path(row["local_video_path"])
        cues = load_cues(Path(row["local_subtitle_path"]))

        job_dir = self.settings.artifact_dir / str(job_id)
        output_path, variants_dir, work_dir = self._render_layout_paths(job_dir, render_mode)
        variants_dir.mkdir(parents=True, exist_ok=True)
        self.store.update_job(job_id, status="rendering", last_error=None)
        manifest.render_mode = render_mode
        variant_manifests = manifest.variants or []
        if not variant_manifests and manifest.clips:
            from .models import StoryVariant

            variant_manifests = [
                StoryVariant(
                    variant_id=1,
                    label="Variant 1",
                    selection_reason="Primary planned cut.",
                    beats=manifest.beats,
                    clips=manifest.clips,
                )
            ]
            manifest.variants = variant_manifests

        render_total = len(variant_manifests) + 2
        self._emit_stage(stage_callback, "Preparing render outputs", 1, render_total)
        rendered_outputs: list[Path] = []
        for variant in variant_manifests:
            self._emit_stage(
                stage_callback,
                f"Rendering variant {variant.variant_id}/{len(variant_manifests)}",
                variant.variant_id,
                render_total,
            )
            variant_output = variants_dir / f"short_{variant.variant_id:02d}.mp4"
            variant_work_dir = work_dir / f"variant_{variant.variant_id:02d}"
            variant_manifest = JobManifest(
                job_id=manifest.job_id,
                filename=manifest.filename,
                source_video_path=manifest.source_video_path,
                subtitle_source=manifest.subtitle_source,
                subtitle_path=manifest.subtitle_path,
                total_runtime_seconds=manifest.total_runtime_seconds,
                render_mode=render_mode,
                beats=variant.beats,
                clips=variant.clips,
                script_context=manifest.script_context,
                planner_notes=manifest.planner_notes,
            )
            rendered_variant = render_short(
                variant_manifest,
                source_video,
                cues,
                variant_work_dir,
                variant_output,
                render_mode=render_mode,
                preferred_audio_language=self.settings.default_language,
            )
            variant.render_output_path = str(rendered_variant)
            rendered_outputs.append(rendered_variant)

        if not rendered_outputs:
            raise RuntimeError(f"Job {job_id} has no renderable variants.")

        self._emit_stage(stage_callback, "Finalizing rendered outputs", len(variant_manifests) + 1, render_total)
        shutil.copy2(rendered_outputs[0], output_path)
        primary_sidecar = rendered_outputs[0].with_suffix(".srt")
        if primary_sidecar.exists():
            shutil.copy2(primary_sidecar, output_path.with_suffix(".srt"))
        rendered = output_path
        manifest.beats = manifest.variants[0].beats
        manifest.clips = manifest.variants[0].clips
        manifest.render_output_path = str(rendered)
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        self.store.update_job(job_id, status="completed", output_path=str(rendered))
        self.store.add_event(
            job_id,
            "rendered",
            {
                "output_path": str(rendered),
                "variant_outputs": [str(path) for path in rendered_outputs],
                "render_mode": render_mode,
            },
        )
        self._emit_stage(stage_callback, "Rendering complete", render_total, render_total)
        return rendered

    @staticmethod
    def _render_layout_paths(job_dir: Path, render_mode: str) -> tuple[Path, Path, Path]:
        if render_mode == "crop":
            return (job_dir / "short.mp4", job_dir / "variants", job_dir / "work")

        suffix = render_mode.lower()
        return (
            job_dir / f"short_{suffix}.mp4",
            job_dir / f"variants_{suffix}",
            job_dir / f"work_{suffix}",
        )

    def retry_job(self, job_id: int) -> None:
        self.store.update_job(
            job_id,
            status="discovered",
            last_error=None,
            manifest_path=None,
            output_path=None,
            local_subtitle_path=None,
        )
        self.store.add_event(job_id, "retried", {})

    def _ensure_video(
        self,
        job_id: int,
        download_progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        row = self.store.get_job(job_id)
        target = self.settings.download_dir / f"{job_id}_{Path(row['filename']).name}"
        if target.exists() and target.stat().st_size > 0:
            return target
        self.store.update_job(job_id, status="downloading")
        downloaded = self.rd.download_file(
            row["download_url"],
            target,
            progress_callback=download_progress_callback,
        )
        self.store.update_job(job_id, local_video_path=str(downloaded), status="downloaded")
        self.store.add_event(job_id, "downloaded", {"local_video_path": str(downloaded)})
        return downloaded

    @staticmethod
    def _emit_stage(
        callback: Callable[[str, int, int], None] | None,
        description: str,
        completed: int,
        total: int,
    ) -> None:
        if callback is not None:
            callback(description, completed, total)

    def _mark_failed(self, job_id: int, exc: Exception) -> None:
        self.store.update_job(job_id, status="failed", last_error=str(exc))
        self.store.add_event(job_id, "failed", {"error": str(exc)})

    def _fetch_rd_sidecar_subtitle(self, row, job_dir: Path) -> Path | None:
        torrent = self.rd.find_torrent_by_link(row["link_url"])
        if not torrent:
            return None

        candidate = choose_rd_sidecar_subtitle(torrent.files, language=self.settings.default_language)
        if not candidate:
            return None

        primary_video = torrent.primary_video_file
        if not primary_video:
            return None

        clone_id = self.rd.add_magnet(self.rd.build_magnet_link(torrent))
        try:
            self.rd.select_files(clone_id, [primary_video.id, candidate.id])
            clone_info = self.rd.wait_for_torrent(clone_id, timeout_seconds=45.0, poll_interval_seconds=1.0)
            if not clone_info.links:
                raise RuntimeError(f"Temporary torrent clone {clone_id} did not produce any downloadable links.")
            package = self.rd.unrestrict_link(clone_info.links[0])
            recovered_path = job_dir / f"rd_{Path(candidate.path).name}"
            return extract_stored_rar_entry(package.download_url, candidate.path, recovered_path)
        finally:
            try:
                self.rd.delete_torrent(clone_id)
            except Exception:
                pass

    @staticmethod
    def _manifest_from_dict(data: dict) -> JobManifest:
        from .models import RenderClip, ScriptContextSource, StoryBeat, StoryVariant

        return JobManifest(
            job_id=int(data["job_id"]),
            filename=data["filename"],
            source_video_path=data["source_video_path"],
            subtitle_source=data["subtitle_source"],
            subtitle_path=data["subtitle_path"],
            total_runtime_seconds=float(data["total_runtime_seconds"]),
            render_mode=str(data.get("render_mode") or "crop"),
            beats=[StoryBeat(**beat) for beat in data["beats"]],
            clips=[RenderClip(**clip) for clip in data["clips"]],
            variants=[
                StoryVariant(
                    variant_id=int(variant["variant_id"]),
                    label=variant["label"],
                    selection_reason=variant["selection_reason"],
                    beats=[StoryBeat(**beat) for beat in variant.get("beats", [])],
                    clips=[RenderClip(**clip) for clip in variant.get("clips", [])],
                    render_output_path=variant.get("render_output_path"),
                )
                for variant in data.get("variants", [])
            ],
            script_context=[ScriptContextSource(**source) for source in data.get("script_context", [])],
            planner_notes=list(data.get("planner_notes") or []),
            render_output_path=data.get("render_output_path"),
            created_at=data.get("created_at", ""),
        )
