from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".m4v",
    ".wmv",
    ".webm",
}


@dataclass(slots=True)
class DownloadItem:
    id: str
    filename: str
    filesize: int
    download_url: str
    link: str
    mime_type: str | None = None
    generated_at: str | None = None

    @property
    def is_video(self) -> bool:
        suffix = Path(self.filename).suffix.lower()
        return suffix in VIDEO_EXTENSIONS or (self.mime_type or "").startswith("video/")

    @property
    def stem(self) -> str:
        return Path(self.filename).stem or self.filename


@dataclass(slots=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class StoryBeat:
    beat_type: str
    source_start_ms: int
    source_end_ms: int
    display_order: int
    score: float
    summary: str
    source_reason: str


@dataclass(slots=True)
class RenderClip:
    beat_type: str
    source_start_ms: int
    source_end_ms: int
    output_start_ms: int
    output_end_ms: int
    summary: str


@dataclass(slots=True)
class ScriptContextSource:
    provider: str
    title: str
    url: str
    summary: str | None = None
    writer: str | None = None
    year: int | None = None
    asset_url: str | None = None
    script_text_path: str | None = None
    source_kind: str = "metadata"


@dataclass(slots=True)
class JobManifest:
    job_id: int
    filename: str
    source_video_path: str
    subtitle_source: str
    subtitle_path: str
    total_runtime_seconds: float
    beats: list[StoryBeat] = field(default_factory=list)
    clips: list[RenderClip] = field(default_factory=list)
    script_context: list[ScriptContextSource] = field(default_factory=list)
    planner_notes: list[str] = field(default_factory=list)
    render_output_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["beats"] = [asdict(item) for item in self.beats]
        data["clips"] = [asdict(item) for item in self.clips]
        data["script_context"] = [asdict(item) for item in self.script_context]
        return data
