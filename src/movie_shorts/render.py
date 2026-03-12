from __future__ import annotations

from pathlib import Path
import json
import subprocess
import shutil

from .models import JobManifest, SubtitleCue


CAPTION_STYLE = "FontName=Arial WGL Bold Italic,FontSize=11,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=70,Alignment=2"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, text=True, capture_output=True)


def probe_audio_streams(video_path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index:stream_tags=language,title",
            "-of",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def _audio_map_args(video_path: Path, preferred_language: str = "en") -> list[str]:
    streams = probe_audio_streams(video_path)
    if not streams:
        return ["-map", "0:a:0?"]

    preferred_tokens = {preferred_language.lower()}
    if preferred_language.lower() == "en":
        preferred_tokens.update({"eng", "english"})

    def score(stream: dict) -> tuple[int, int]:
        tags = stream.get("tags") or {}
        language = str(tags.get("language") or "").lower()
        title = str(tags.get("title") or "").lower()
        value = 0
        if language in preferred_tokens or any(token in language for token in preferred_tokens):
            value += 200
        if any(token in title for token in preferred_tokens):
            value += 80
        if any(token in title for token in {"commentary", "description", "descriptive", "director"}):
            value -= 120
        if preferred_language.lower() == "en" and any(token in language for token in {"ita", "italian", "fra", "fre", "spa", "es"}):
            value -= 20
        return (value, -int(stream.get("index", 0)))

    selected = max(streams, key=score)
    return ["-map", f"0:{selected['index']}?"]


def write_concat_file(paths: list[Path], destination: Path) -> Path:
    lines = [f"file '{path.resolve().as_posix()}'" for path in paths]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def write_remapped_srt(manifest: JobManifest, cues: list[SubtitleCue], destination: Path) -> Path:
    lines: list[str] = []
    index = 1
    for clip in manifest.clips:
        clip_offset = clip.output_start_ms - clip.source_start_ms
        for cue in cues:
            if cue.end_ms <= clip.source_start_ms or cue.start_ms >= clip.source_end_ms:
                continue
            start_ms = max(cue.start_ms, clip.source_start_ms) + clip_offset
            end_ms = min(cue.end_ms, clip.source_end_ms) + clip_offset
            lines.extend(
                [
                    str(index),
                    f"{_format_timestamp(start_ms)} --> {_format_timestamp(end_ms)}",
                    cue.text,
                    "",
                ]
            )
            index += 1
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def render_short(
    manifest: JobManifest,
    source_video: Path,
    cues: list[SubtitleCue],
    work_dir: Path,
    output_path: Path,
    render_mode: str | None = None,
    preferred_audio_language: str = "en",
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    selected_mode = render_mode or manifest.render_mode or "crop"
    filter_flag, filter_value = _video_filter_args(selected_mode)
    preset = _preset_for_mode(selected_mode)
    audio_map_args = _audio_map_args(source_video, preferred_audio_language)
    parts: list[Path] = []
    for clip_index, clip in enumerate(manifest.clips, start=1):
        part_path = work_dir / f"clip_{clip_index:02d}.mp4"
        duration_seconds = max(0.1, (clip.source_end_ms - clip.source_start_ms) / 1000)
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{clip.source_start_ms / 1000:.3f}",
            "-i",
            str(source_video),
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            "0:v:0",
            *audio_map_args,
            filter_flag,
            filter_value,
            "-af",
            "loudnorm",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(part_path),
        ]
        _run(command)
        parts.append(part_path)

    concat_path = write_concat_file(parts, work_dir / "concat.txt")
    stitched_raw_path = work_dir / "stitched_raw.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(stitched_raw_path),
        ]
    )

    subtitle_path = write_remapped_srt(manifest, cues, work_dir / "burned.srt")
    stitched_path = work_dir / "stitched.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(stitched_raw_path),
            "-vf",
            f"subtitles={subtitle_path.as_posix()}:force_style='{CAPTION_STYLE}'",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            "22",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(stitched_path),
        ]
    )
    shutil.copy2(stitched_path, output_path)
    shutil.copy2(subtitle_path, output_path.with_suffix(".srt"))
    return output_path


def _video_filter_args(render_mode: str) -> tuple[str, str]:
    if render_mode == "crop":
        return ("-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
    if render_mode == "fit":
        return (
            "-filter_complex",
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=10:1[bgf];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgf];"
            "[bgf][fgf]overlay=(W-w)/2:(H-h)/2",
        )
    if render_mode == "fit-43":
        return (
            "-filter_complex",
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=10:1[bgf];"
            "[fg]scale=1080:810:force_original_aspect_ratio=increase,"
            "crop=1080:810[fgf];"
            "[bgf][fgf]overlay=(W-w)/2:(H-h)/2",
        )
    raise ValueError(f"Unsupported render mode: {render_mode}")


def _preset_for_mode(render_mode: str) -> str:
    if render_mode in {"fit", "fit-43"}:
        return "veryfast"
    return "medium"


def _format_timestamp(value_ms: int) -> str:
    total_ms = max(0, value_ms)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
