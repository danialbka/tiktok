from pathlib import Path

from movie_shorts.rd import TorrentFile
from movie_shorts.subtitles import choose_rd_sidecar_subtitle, extract_stored_rar_entry, fetch_subtitles


def test_choose_rd_sidecar_subtitle_prefers_root_english_srt() -> None:
    files = [
        TorrentFile(id=35, path="/Subs/SDH.eng.srt", bytes=100, selected=False),
        TorrentFile(id=2, path="/Mercy.2026.1080p.WEBRip.x264.AAC5.1-[YTS.BZ].srt", bytes=100, selected=False),
        TorrentFile(id=5, path="/Subs/Forced.ger.srt", bytes=100, selected=False),
    ]

    choice = choose_rd_sidecar_subtitle(files, language="en")

    assert choice is not None
    assert choice.id == 2


def test_extract_stored_rar_entry_reads_target_entry_from_ranges(tmp_path: Path, monkeypatch) -> None:
    archive = _build_rar4_archive(
        [
            ("video.mp4", b"abcd"),
            ("subs/english.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"),
        ]
    )

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]):
            start, end = headers["Range"].split("=")[1].split("-")
            chunk = archive[int(start) : int(end) + 1]

            class Response:
                content = chunk

                @staticmethod
                def raise_for_status() -> None:
                    return None

            return Response()

    monkeypatch.setattr("movie_shorts.subtitles.httpx.Client", FakeClient)
    destination = tmp_path / "out.srt"

    extract_stored_rar_entry("https://example.invalid/archive.rar", "/subs/english.srt", destination)

    assert destination.read_text(encoding="utf-8") == "1\n00:00:00,000 --> 00:00:01,000\nHello\n"


def test_fetch_subtitles_refreshes_unknown_cached_file_with_preferred_embedded_language(tmp_path: Path, monkeypatch) -> None:
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"fake-video")
    subtitle_path = tmp_path / "subtitles.srt"
    subtitle_path.write_text("old non english subtitle", encoding="utf-8")

    def fake_extract(video_path_arg: Path, output_path_arg: Path, language: str = "en") -> Path | None:
        assert language == "en"
        output_path_arg.write_text("fresh english subtitle", encoding="utf-8")
        return output_path_arg

    monkeypatch.setattr("movie_shorts.subtitles.extract_embedded_subtitles", fake_extract)

    resolved_path, source = fetch_subtitles(
        video_path=video_path,
        subtitle_path=subtitle_path,
        language="en",
        opensubtitles_api_key=None,
        query_title="Blood Diamond",
        query_year=2006,
    )

    assert source == "embedded"
    assert resolved_path.read_text(encoding="utf-8") == "fresh english subtitle"
    assert subtitle_path.with_name("subtitles.srt.meta.json").exists()


def test_fetch_subtitles_reuses_cached_file_when_meta_language_matches(tmp_path: Path, monkeypatch) -> None:
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"fake-video")
    subtitle_path = tmp_path / "subtitles.srt"
    subtitle_path.write_text("cached english subtitle", encoding="utf-8")
    subtitle_path.with_name("subtitles.srt.meta.json").write_text('{"source":"embedded","language":"en"}', encoding="utf-8")

    def fail_extract(*args, **kwargs):
        raise AssertionError("embedded extraction should not run when cached metadata matches")

    monkeypatch.setattr("movie_shorts.subtitles.extract_embedded_subtitles", fail_extract)

    resolved_path, source = fetch_subtitles(
        video_path=video_path,
        subtitle_path=subtitle_path,
        language="en",
        opensubtitles_api_key=None,
        query_title="Blood Diamond",
        query_year=2006,
    )

    assert source == "cached"
    assert resolved_path.read_text(encoding="utf-8") == "cached english subtitle"


def _build_rar4_archive(entries: list[tuple[str, bytes]]) -> bytes:
    archive = bytearray(b"Rar!\x1a\x07\x00")
    archive += _archive_header()
    for name, payload in entries:
        archive += _file_header(name.encode("utf-8"), len(payload))
        archive += payload
    archive += b"\xc4\x3d\x7b\x00\x40\x07\x00"
    return bytes(archive)


def _archive_header() -> bytes:
    import struct

    return struct.pack("<HBHHHI", 0x90CF, 0x73, 0x0000, 13, 0, 0)


def _file_header(name: bytes, payload_size: int) -> bytes:
    import struct
    import zlib

    head_flags = 0x8000 | 0x1000
    head_size = 32 + len(name)
    file_crc = zlib.crc32(b"") & 0xFFFFFFFF
    return struct.pack(
        "<HBHHIIBIIBBHI",
        0xFADB,
        0x74,
        head_flags,
        head_size,
        payload_size,
        payload_size,
        3,
        file_crc,
        0,
        20,
        0x30,
        len(name),
        0x20,
    ) + name
