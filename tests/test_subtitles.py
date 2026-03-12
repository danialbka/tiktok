from pathlib import Path

from movie_shorts.rd import TorrentFile
from movie_shorts.subtitles import choose_rd_sidecar_subtitle, extract_stored_rar_entry


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
