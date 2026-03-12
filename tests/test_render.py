from pathlib import Path
import subprocess

from movie_shorts.planner import choose_story_beats
from movie_shorts.render import _audio_map_args, _video_filter_args, render_short
from movie_shorts.models import SubtitleCue


def _probe_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def test_render_short_creates_vertical_video(tmp_path: Path) -> None:
    video_path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:d=18",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=3_000, text="Why did you come back here?"),
        SubtitleCue(index=2, start_ms=3_500, end_ms=6_000, text="Because the secret is buried in this house."),
        SubtitleCue(index=3, start_ms=6_500, end_ms=9_000, text="If they find us, we are dead."),
        SubtitleCue(index=4, start_ms=9_500, end_ms=12_000, text="Then run before the police arrive."),
        SubtitleCue(index=5, start_ms=12_500, end_ms=15_000, text="I lied because I thought it would save you."),
        SubtitleCue(index=6, start_ms=15_500, end_ms=17_500, text="Now tell me the truth."),
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=30)
    manifest.job_id = 99
    manifest.filename = "sample.mp4"
    manifest.source_video_path = str(video_path)
    manifest.subtitle_source = "synthetic"
    manifest.subtitle_path = str(tmp_path / "synthetic.srt")

    output_path = tmp_path / "short.mp4"
    render_short(manifest, video_path, cues, tmp_path / "work", output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert output_path.with_suffix(".srt").exists()
    expected_duration = manifest.clips[-1].output_end_ms / 1000
    assert _probe_duration_seconds(output_path) <= expected_duration + 1.0


def test_render_short_supports_fit_mode(tmp_path: Path) -> None:
    video_path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:d=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=3_000, text="Why did you come back here?"),
        SubtitleCue(index=2, start_ms=3_500, end_ms=6_000, text="Because the secret is buried in this house."),
        SubtitleCue(index=3, start_ms=6_500, end_ms=9_000, text="If they find us, we are dead."),
        SubtitleCue(index=4, start_ms=9_200, end_ms=9_800, text="Now tell me the truth."),
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=20)
    manifest.job_id = 100
    manifest.filename = "sample.mp4"
    manifest.source_video_path = str(video_path)
    manifest.subtitle_source = "synthetic"
    manifest.subtitle_path = str(tmp_path / "synthetic.srt")
    manifest.render_mode = "fit"

    output_path = tmp_path / "short_fit.mp4"
    render_short(manifest, video_path, cues, tmp_path / "work_fit", output_path, render_mode="fit")

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert output_path.with_suffix(".srt").exists()
    expected_duration = manifest.clips[-1].output_end_ms / 1000
    assert _probe_duration_seconds(output_path) <= expected_duration + 1.0


def test_render_short_supports_fit_43_mode(tmp_path: Path) -> None:
    video_path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:d=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=3_000, text="Why did you come back here?"),
        SubtitleCue(index=2, start_ms=3_500, end_ms=6_000, text="Because the secret is buried in this house."),
        SubtitleCue(index=3, start_ms=6_500, end_ms=9_000, text="If they find us, we are dead."),
        SubtitleCue(index=4, start_ms=9_200, end_ms=9_800, text="Now tell me the truth."),
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=20)
    manifest.job_id = 101
    manifest.filename = "sample.mp4"
    manifest.source_video_path = str(video_path)
    manifest.subtitle_source = "synthetic"
    manifest.subtitle_path = str(tmp_path / "synthetic.srt")
    manifest.render_mode = "fit-43"

    output_path = tmp_path / "short_fit_43.mp4"
    render_short(manifest, video_path, cues, tmp_path / "work_fit_43", output_path, render_mode="fit-43")

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert output_path.with_suffix(".srt").exists()
    expected_duration = manifest.clips[-1].output_end_ms / 1000
    assert _probe_duration_seconds(output_path) <= expected_duration + 1.0


def test_video_filter_args_supports_crop_fit_and_fit_43() -> None:
    crop_flag, crop_filter = _video_filter_args("crop")
    fit_flag, fit_filter = _video_filter_args("fit")
    fit_43_flag, fit_43_filter = _video_filter_args("fit-43")

    assert crop_flag == "-vf"
    assert "crop=1080:1920" in crop_filter
    assert fit_flag == "-filter_complex"
    assert "overlay=(W-w)/2:(H-h)/2" in fit_filter
    assert fit_43_flag == "-filter_complex"
    assert "scale=1080:810:force_original_aspect_ratio=increase" in fit_43_filter
    assert "crop=1080:810" in fit_43_filter


def test_audio_map_args_prefers_english_track(tmp_path: Path) -> None:
    video_path = tmp_path / "multi_audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-metadata:s:a:0",
            "language=ita",
            "-metadata:s:a:1",
            "language=eng",
            "-metadata:s:a:1",
            "title=English Main",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert _audio_map_args(video_path, preferred_language="en") == ["-map", "0:2?"]
