from movie_shorts import cli


def test_dash_help_prints_agent_usage(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["movie-shorts", "-help"])
    cli.main()

    captured = capsys.readouterr()
    assert "Movie Shorts agent usage" in captured.out
    assert "movie-shorts sync --limit 10" in captured.out
    assert "movie-shorts render <job_id> --render-mode crop|fit" in captured.out
