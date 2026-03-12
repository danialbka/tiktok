from __future__ import annotations

from pathlib import Path
import subprocess

from .models import JobManifest, SubtitleCue


CAPTION_STYLE = "FontName=Arial,FontSize=11,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=70,Alignment=2"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, text=True, capture_output=True)


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
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for clip_index, clip in enumerate(manifest.clips, start=1):
        part_path = work_dir / f"clip_{clip_index:02d}.mp4"
        duration_seconds = max(0.1, (clip.source_end_ms - clip.source_start_ms) / 1000)
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{clip.source_start_ms / 1000:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(source_video),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-af",
            "loudnorm",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
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
    stitched_path = work_dir / "stitched.mp4"
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
            str(stitched_path),
        ]
    )

    subtitle_path = write_remapped_srt(manifest, cues, work_dir / "burned.srt")
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(stitched_path),
            "-vf",
            f"subtitles={subtitle_path.as_posix()}:force_style='{CAPTION_STYLE}'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "22",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return output_path


def _format_timestamp(value_ms: int) -> str:
    total_ms = max(0, value_ms)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
