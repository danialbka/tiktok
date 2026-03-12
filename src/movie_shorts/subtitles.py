from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import shutil
import subprocess
import struct

import httpx
import pysubs2

from .rd import TorrentFile
from .models import SubtitleCue


TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}
RAR_MARKER = b"Rar!\x1a\x07\x00"
TAG_RE = re.compile(r"\{\\.*?\}")


@dataclass(slots=True)
class RarStoredEntry:
    name: str
    pack_size: int
    data_offset: int
    method: int


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def probe_subtitle_streams(video_path: Path) -> list[dict]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        str(video_path),
    ]
    result = _run(command)
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def extract_embedded_subtitles(video_path: Path, output_path: Path, language: str = "en") -> Path | None:
    streams = probe_subtitle_streams(video_path)
    if not streams:
        return None

    preferred = None
    for stream in streams:
        codec = stream.get("codec_name")
        tags = stream.get("tags") or {}
        if codec not in TEXT_SUBTITLE_CODECS:
            continue
        if tags.get("language") == language:
            preferred = stream
            break
        if preferred is None:
            preferred = stream
    if not preferred:
        return None

    stream_index = preferred["index"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        f"0:{stream_index}",
        str(output_path),
    ]
    _run(command)
    return output_path if output_path.exists() else None


def search_opensubtitles(
    api_key: str,
    query: str,
    year: int | None = None,
    language: str = "en",
) -> dict | None:
    params = {"query": query, "languages": language, "type": "movie"}
    if year:
        params["year"] = str(year)
    headers = {"Api-Key": api_key, "User-Agent": "movie-shorts v0.1"}
    response = httpx.get("https://api.opensubtitles.com/api/v1/subtitles", params=params, headers=headers, timeout=30.0)
    response.raise_for_status()
    data = response.json().get("data") or []
    if not data:
        return None
    return data[0]


def download_opensubtitles(api_key: str, file_id: int, destination: Path) -> Path:
    headers = {"Api-Key": api_key, "User-Agent": "movie-shorts v0.1"}
    response = httpx.post(
        "https://api.opensubtitles.com/api/v1/download",
        json={"file_id": file_id},
        headers=headers,
        timeout=30.0,
    )
    response.raise_for_status()
    link = response.json()["link"]
    with httpx.stream("GET", link, follow_redirects=True, timeout=60.0) as download:
        download.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in download.iter_bytes():
                handle.write(chunk)
    return destination


def fetch_subtitles(
    video_path: Path,
    subtitle_path: Path,
    language: str,
    opensubtitles_api_key: str | None,
    query_title: str,
    query_year: int | None,
    rd_sidecar_subtitle_path: Path | None = None,
) -> tuple[Path, str]:
    embedded = extract_embedded_subtitles(video_path, subtitle_path, language=language)
    if embedded:
        return embedded, "embedded"

    if rd_sidecar_subtitle_path and rd_sidecar_subtitle_path.exists():
        shutil.copy2(rd_sidecar_subtitle_path, subtitle_path)
        return subtitle_path, "rd-sidecar"

    if not opensubtitles_api_key:
        raise RuntimeError("No embedded subtitles found and OPENSUBTITLES_API_KEY is not configured.")

    search_result = search_opensubtitles(opensubtitles_api_key, query_title, query_year, language=language)
    if not search_result:
        raise RuntimeError("OpenSubtitles did not return a matching subtitle.")

    files = search_result["attributes"].get("files") or []
    if not files:
        raise RuntimeError("OpenSubtitles result did not include downloadable files.")
    file_id = files[0]["file_id"]
    downloaded = download_opensubtitles(opensubtitles_api_key, int(file_id), subtitle_path)
    if downloaded.suffix.lower() == ".zip":
        raise RuntimeError("ZIP subtitle archives are not supported in v1.")
    return downloaded, "opensubtitles"


def load_cues(subtitle_path: Path) -> list[SubtitleCue]:
    source = subtitle_path
    if subtitle_path.suffix.lower() not in {".srt", ".ass", ".ssa", ".vtt"}:
        converted = subtitle_path.with_suffix(".srt")
        shutil.copy2(subtitle_path, converted)
        source = converted

    subtitles = pysubs2.load(str(source))
    cues: list[SubtitleCue] = []
    for index, line in enumerate(subtitles, start=1):
        text = TAG_RE.sub("", line.text).replace("\\N", " ").strip()
        if not text:
            continue
        cues.append(SubtitleCue(index=index, start_ms=int(line.start), end_ms=int(line.end), text=text))
    if not cues:
        raise RuntimeError(f"No readable subtitle cues found in {subtitle_path}")
    return cues


def choose_rd_sidecar_subtitle(files: list[TorrentFile], language: str = "en") -> TorrentFile | None:
    candidates: list[tuple[int, TorrentFile]] = []
    for item in files:
        path = item.path.lower()
        suffix = Path(path).suffix.lower()
        if suffix not in SUBTITLE_EXTENSIONS:
            continue

        score = 0
        filename = Path(path).name.lower()
        if path.count("/") <= 1:
            score += 150
            if "forced" not in filename and "sdh" not in filename:
                score += 50
        if filename.endswith(".srt"):
            score += 20
        if language in {"en", "eng", "english"}:
            if any(token in filename for token in ["eng", "english", ".en.", ".en-", ".en_"]):
                score += 120
            if "sdh.eng" in filename:
                score += 60
            if "sdh" in filename:
                score -= 10
        if "forced" in filename:
            score -= 120
        if "subs/" in path:
            score -= 5
        if filename.count(".") > 3:
            score += 5

        candidates.append((score, item))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1] if candidates and candidates[0][0] > 0 else None


def extract_stored_rar_entry(download_url: str, entry_name: str, destination: Path) -> Path:
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        archive_header = _fetch_range(client, download_url, 0, 4095)
        if not archive_header.startswith(RAR_MARKER):
            raise RuntimeError("Expected a RAR archive when recovering RD sidecar subtitles.")

        offset = 7
        archive_block_size = _parse_block_size(archive_header, offset)
        offset += archive_block_size
        normalized_target = entry_name.lstrip("/")

        while True:
            header = _fetch_range(client, download_url, offset, offset + 4095)
            entry = _parse_rar4_entry(header, offset)
            if entry is None:
                raise RuntimeError(f"Could not find subtitle entry {normalized_target} in RD package archive.")
            if entry.name == normalized_target or Path(entry.name).name == Path(normalized_target).name:
                if entry.method != 0x30:
                    raise RuntimeError(f"RAR entry {entry.name} uses unsupported compression method {entry.method:#x}.")
                payload = _fetch_range(client, download_url, entry.data_offset, entry.data_offset + entry.pack_size - 1)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                return destination
            offset = entry.data_offset + entry.pack_size


def _fetch_range(client: httpx.Client, url: str, start: int, end: int) -> bytes:
    response = client.get(url, headers={"Range": f"bytes={start}-{end}"})
    response.raise_for_status()
    return response.content


def _parse_block_size(buffer: bytes, offset: int) -> int:
    _, _, _, head_size = struct.unpack("<HBHH", buffer[offset : offset + 7])
    return head_size


def _parse_rar4_entry(buffer: bytes, offset: int) -> RarStoredEntry | None:
    if len(buffer) < 32:
        return None

    _, head_type, flags, head_size = struct.unpack("<HBHH", buffer[:7])
    if head_type == 0x7B:
        return None
    if head_type != 0x74:
        raise RuntimeError(f"Unsupported RAR block type {head_type:#x}.")

    pack_size, _, _, _, _, _, method, name_size, _ = struct.unpack("<IIBIIBBHI", buffer[7:32])
    cursor = 32
    if flags & 0x100:
        high_pack_size, _ = struct.unpack("<II", buffer[cursor : cursor + 8])
        pack_size |= high_pack_size << 32
        cursor += 8
    name = buffer[cursor : cursor + name_size].decode("utf-8", errors="ignore")
    return RarStoredEntry(
        name=name,
        pack_size=pack_size,
        data_offset=offset + head_size,
        method=method,
    )
