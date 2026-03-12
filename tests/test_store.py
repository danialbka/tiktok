from pathlib import Path

from movie_shorts.store import JobStore


def test_upsert_download_creates_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.upsert_download(
        {
            "rd_download_id": "abc123",
            "filename": "Example.Movie.2025.mkv",
            "download_url": "https://example.invalid/video.mkv",
            "link_url": "https://example.invalid/source",
            "filesize": 123,
            "mime_type": "video/x-matroska",
            "metadata": {"parsed_title": "Example Movie", "parsed_year": 2025},
        }
    )

    row = store.get_job(job_id)
    assert row["filename"] == "Example.Movie.2025.mkv"
    assert row["status"] == "discovered"
