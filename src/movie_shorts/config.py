from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    real_debrid_api_key: str
    opensubtitles_api_key: str | None
    openai_api_key: str | None
    instagram_access_token: str | None
    instagram_user_id: str | None
    instagram_graph_api_version: str
    db_path: Path
    download_dir: Path
    artifact_dir: Path
    max_duration_seconds: int | None
    default_language: str
    enable_script_context: bool

    @classmethod
    def load(cls, root: Path | None = None, require_real_debrid: bool = True) -> "Settings":
        root_dir = root or Path.cwd()
        load_dotenv(root_dir / ".env.local", override=False)
        load_dotenv(root_dir / ".env", override=False)

        api_key = os.getenv("REAL_DEBRID_API_KEY")
        if require_real_debrid and not api_key:
            raise RuntimeError("REAL_DEBRID_API_KEY is required.")

        settings = cls(
            real_debrid_api_key=api_key or "",
            opensubtitles_api_key=os.getenv("OPENSUBTITLES_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN"),
            instagram_user_id=os.getenv("INSTAGRAM_USER_ID"),
            instagram_graph_api_version=os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v24.0"),
            db_path=Path(os.getenv("MOVIE_SHORTS_DB_PATH", "data/movie_shorts.db")),
            download_dir=Path(os.getenv("MOVIE_SHORTS_DOWNLOAD_DIR", ".cache/downloads")),
            artifact_dir=Path(os.getenv("MOVIE_SHORTS_ARTIFACT_DIR", "artifacts")),
            max_duration_seconds=_optional_int(os.getenv("MOVIE_SHORTS_MAX_DURATION_SECONDS")),
            default_language=os.getenv("MOVIE_SHORTS_DEFAULT_LANGUAGE", "en"),
            enable_script_context=os.getenv("MOVIE_SHORTS_ENABLE_SCRIPT_CONTEXT", "true").lower() in {"1", "true", "yes", "on"},
        )
        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"", "0", "none", "null", "off", "false"}:
        return None
    return int(normalized)
