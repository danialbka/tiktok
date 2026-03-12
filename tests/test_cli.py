from types import SimpleNamespace

from movie_shorts import cli


def test_dash_help_prints_agent_usage(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["movie-shorts", "-help"])
    cli.main()

    captured = capsys.readouterr()
    assert "Movie Shorts agent usage" in captured.out
    assert "movie-shorts sync --limit 10" in captured.out
    assert "movie-shorts available-movies" in captured.out
    assert "movie-shorts run-movie" in captured.out
    assert "movie-shorts render <job_id> --render-mode crop|fit|fit-43" in captured.out
    assert "movie-shorts instagram publish-job <job_id> --variant 1 --caption" in captured.out


def test_run_movie_prompts_for_missing_inputs(monkeypatch, capsys) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def list_browseable_movies(self, limit=20):
            assert limit == 20
            return [
                SimpleNamespace(
                    source_type="download",
                    rd_download_id="rd-18",
                    filename="The.Housemaid.2025.mkv",
                    filesize=1000,
                    download_url="https://download/18",
                    link_url="https://real-debrid.com/d/example18",
                    mime_type="video/x-matroska",
                    generated_at="2026-03-12T00:00:00Z",
                    parsed_title="The Housemaid",
                    parsed_year=2025,
                    job_id=None,
                    job_status=None,
                    output_path=None,
                    ready=True,
                    rd_status="downloaded",
                    rd_progress=100,
                    rd_torrent_id=None,
                ),
                SimpleNamespace(
                    source_type="download",
                    rd_download_id="rd-6",
                    filename="Mercy.2026.mp4",
                    filesize=1000,
                    download_url="https://download/6",
                    link_url="https://real-debrid.com/d/example6",
                    mime_type="video/mp4",
                    generated_at="2026-03-12T00:00:00Z",
                    parsed_title="Mercy",
                    parsed_year=2026,
                    job_id=6,
                    job_status="completed",
                    output_path="artifacts/6/short.mp4",
                    ready=True,
                    rd_status="downloaded",
                    rd_progress=100,
                    rd_torrent_id=None,
                ),
            ]

        def queue_available_movie(self, movie):
            self.calls.append(("queue", movie.rd_download_id))
            return 18

        def close(self):
            self.calls.append(("close",))

    fake_pipeline = FakePipeline()
    prompts = iter(["1", "fit-43", ""])

    monkeypatch.setattr(cli, "_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr(cli, "_queue_selected_movie_with_progress", lambda pipeline, movie: 18)
    monkeypatch.setattr(cli, "_run_movie_with_progress", lambda pipeline, job_id, target_duration, render_mode, variant_count: (f"artifacts/{job_id}/manifest.json", f"artifacts/{job_id}/short_{render_mode.value}.mp4"))

    cli.run_movie(
        job_id=None,
        sync_first=False,
        status=None,
        limit=20,
        target_duration=None,
        render_mode=None,
        variant_count=5,
    )

    captured = capsys.readouterr()
    assert "Planned job 18" in captured.out
    assert "Rendered job 18" in captured.out
    assert fake_pipeline.calls == [("close",)]


def test_run_movie_supports_noninteractive_options(monkeypatch, capsys) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def sync_downloads(self, limit=20):
            self.calls.append(("sync", limit))
            return [67]

        def close(self):
            self.calls.append(("close",))

    fake_pipeline = FakePipeline()
    monkeypatch.setattr(cli, "_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(
        cli,
        "_run_movie_with_progress",
        lambda pipeline, job_id, target_duration, render_mode, variant_count: (
            f"artifacts/{job_id}/manifest.json",
            f"artifacts/{job_id}/short_{render_mode.value}.mp4",
        ),
    )

    cli.run_movie(
        job_id=67,
        sync_first=True,
        limit=30,
        target_duration=120,
        render_mode=cli.RenderMode.fit_43,
        variant_count=4,
    )

    captured = capsys.readouterr()
    assert "Synced 1 video download(s) before selection." in captured.out
    assert ("sync", 30) in fake_pipeline.calls
    assert "Planned job 67" in captured.out
    assert "Rendered job 67" in captured.out


def test_available_movies_prints_ready_and_processing_sections(monkeypatch, capsys) -> None:
    class FakePipeline:
        def list_browseable_movies(self, limit=25):
            assert limit == 25
            return [
                SimpleNamespace(
                    source_type="download",
                    rd_download_id="rd-18",
                    filename="The.Housemaid.2025.mkv",
                    filesize=1024,
                    download_url="https://download/18",
                    link_url="https://real-debrid.com/d/example18",
                    mime_type="video/x-matroska",
                    generated_at="2026-03-12T00:00:00Z",
                    parsed_title="The Housemaid",
                    parsed_year=2025,
                    job_id=18,
                    job_status="completed",
                    output_path="artifacts/18/short.mp4",
                    ready=True,
                    rd_status="downloaded",
                    rd_progress=100,
                    rd_torrent_id=None,
                ),
                SimpleNamespace(
                    source_type="torrent",
                    rd_download_id="torrent:TORRENT1",
                    filename="All.Is.Lost.2013.1080p.BluRay.x264.YIFY.mp4",
                    filesize=1770000000,
                    download_url="",
                    link_url="",
                    mime_type=None,
                    generated_at=None,
                    parsed_title="All Is Lost",
                    parsed_year=2013,
                    job_id=None,
                    job_status=None,
                    output_path=None,
                    ready=False,
                    rd_status="downloading",
                    rd_progress=62,
                    rd_torrent_id="TORRENT1",
                ),
            ]

        def close(self):
            return None

    monkeypatch.setattr(cli, "_pipeline", lambda: FakePipeline())

    cli.available_movies(limit=25)

    captured = capsys.readouterr()
    assert "Real-Debrid Movies" in captured.out
    assert "The Housemaid" in captured.out
    assert "All Is Lost" in captured.out


def test_queue_selected_movie_waits_for_torrent_when_not_ready(monkeypatch) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def wait_and_queue_torrent_movie(self, torrent_id, progress_callback=None, timeout_seconds=3600.0, poll_interval_seconds=5.0):
            self.calls.append(("wait", torrent_id, timeout_seconds, poll_interval_seconds))
            assert progress_callback is not None
            progress_callback("downloading", 45, 100)
            progress_callback("downloaded", 100, 100)
            return 77

    fake_pipeline = FakePipeline()
    movie = SimpleNamespace(ready=False, rd_torrent_id="TORRENT77", filename="Pandorum.2009.mkv")

    assert cli._queue_selected_movie_with_progress(fake_pipeline, movie) == 77
    assert ("wait", "TORRENT77", 3600.0, 5.0) in fake_pipeline.calls
