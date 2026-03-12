from __future__ import annotations

from pathlib import Path
import json

from .config import Settings
from .models import JobManifest
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

    def sync_downloads(self, limit: int = 100) -> list[int]:
        items = self.rd.list_downloads(limit=limit)
        synced: list[int] = []
        for item in items:
            metadata = self.rd.infer_metadata(item.filename)
            job_id = self.store.upsert_download(
                {
                    "rd_download_id": item.id,
                    "filename": item.filename,
                    "download_url": item.download_url,
                    "link_url": item.link,
                    "filesize": item.filesize,
                    "mime_type": item.mime_type,
                    "metadata": metadata | {"generated_at": item.generated_at},
                }
            )
            synced.append(job_id)
        return synced

    def batch_run(self, limit: int = 5, target_duration_seconds: int | None = None) -> list[int]:
        jobs = self.store.list_jobs(status="discovered", limit=limit)
        completed: list[int] = []
        for row in jobs:
            job_id = int(row["id"])
            try:
                self.plan_job(job_id, target_duration_seconds=target_duration_seconds)
                self.render_job(job_id)
            except Exception as exc:
                self._mark_failed(job_id, exc)
                continue
            completed.append(job_id)
        return completed

    def plan_job(self, job_id: int, target_duration_seconds: int | None = None) -> Path:
        row = self.store.get_job(job_id)
        job_dir = self.settings.artifact_dir / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        local_video_path = Path(row["local_video_path"]) if row["local_video_path"] else self._ensure_video(job_id)
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
        subtitle_path, subtitle_source = fetch_subtitles(
            local_video_path,
            job_dir / "subtitles.srt",
            language=self.settings.default_language,
            opensubtitles_api_key=self.settings.opensubtitles_api_key,
            query_title=str(metadata.get("parsed_title") or Path(row["filename"]).stem),
            query_year=metadata.get("parsed_year"),
            rd_sidecar_subtitle_path=rd_sidecar_subtitle_path,
        )
        cues = load_cues(subtitle_path)
        manifest = choose_story_beats(
            cues,
            self.settings.max_duration_seconds,
            script_context=script_context,
            target_duration_seconds=target_duration_seconds,
        )
        manifest.job_id = job_id
        manifest.filename = str(row["filename"])
        manifest.source_video_path = str(local_video_path)
        manifest.subtitle_source = subtitle_source
        manifest.subtitle_path = str(subtitle_path)
        manifest.script_context = script_context
        if script_context:
            manifest.planner_notes.append(
                "Script context saved from Script Slug/IMSDb and used to guide beat selection."
            )
        elif script_context_error:
            manifest.planner_notes.append(f"Script context fetch skipped after error: {script_context_error}")
        if target_duration_seconds:
            manifest.planner_notes.append(f"Requested target duration: {target_duration_seconds} seconds.")

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
        return manifest_path

    def render_job(self, job_id: int) -> Path:
        row = self.store.get_job(job_id)
        if row["status"] not in {"planned", "rendering", "completed"}:
            raise RuntimeError(f"Job {job_id} is not planned yet.")
        manifest_path = Path(row["manifest_path"])
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = self._manifest_from_dict(manifest_data)
        source_video = Path(row["local_video_path"])
        cues = load_cues(Path(row["local_subtitle_path"]))

        output_path = self.settings.artifact_dir / str(job_id) / "short.mp4"
        self.store.update_job(job_id, status="rendering", last_error=None)
        rendered = render_short(manifest, source_video, cues, self.settings.artifact_dir / str(job_id) / "work", output_path)
        manifest.render_output_path = str(rendered)
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        self.store.update_job(job_id, status="completed", output_path=str(rendered))
        self.store.add_event(job_id, "rendered", {"output_path": str(rendered)})
        return rendered

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

    def _ensure_video(self, job_id: int) -> Path:
        row = self.store.get_job(job_id)
        target = self.settings.download_dir / f"{job_id}_{Path(row['filename']).name}"
        if target.exists() and target.stat().st_size > 0:
            return target
        self.store.update_job(job_id, status="downloading")
        downloaded = self.rd.download_file(row["download_url"], target)
        self.store.update_job(job_id, local_video_path=str(downloaded), status="downloaded")
        self.store.add_event(job_id, "downloaded", {"local_video_path": str(downloaded)})
        return downloaded

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
        from .models import RenderClip, ScriptContextSource, StoryBeat

        return JobManifest(
            job_id=int(data["job_id"]),
            filename=data["filename"],
            source_video_path=data["source_video_path"],
            subtitle_source=data["subtitle_source"],
            subtitle_path=data["subtitle_path"],
            total_runtime_seconds=float(data["total_runtime_seconds"]),
            beats=[StoryBeat(**beat) for beat in data["beats"]],
            clips=[RenderClip(**clip) for clip in data["clips"]],
            script_context=[ScriptContextSource(**source) for source in data.get("script_context", [])],
            planner_notes=list(data.get("planner_notes") or []),
            render_output_path=data.get("render_output_path"),
            created_at=data.get("created_at", ""),
        )
