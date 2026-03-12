from pathlib import Path

from movie_shorts.models import ScriptContextSource, SubtitleCue
from movie_shorts.planner import build_scene_blocks, choose_story_beats


def test_choose_story_beats_returns_clips_under_duration_cap() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=10_000, text="We need to leave right now."),
        SubtitleCue(index=2, start_ms=11_000, end_ms=20_000, text="If they find the secret, we are dead."),
        SubtitleCue(index=3, start_ms=50_000, end_ms=65_000, text="Tell me why you lied to me."),
        SubtitleCue(index=4, start_ms=66_000, end_ms=82_000, text="The police already know everything."),
        SubtitleCue(index=5, start_ms=120_000, end_ms=145_000, text="Run. He found the blood in the car."),
        SubtitleCue(index=6, start_ms=146_000, end_ms=170_000, text="I did it because I love you."),
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=90)

    assert manifest.clips
    assert manifest.clips[-1].output_end_ms <= 90_000
    assert {beat.beat_type for beat in manifest.beats}


def test_choose_story_beats_uses_script_context_to_reweight_windows(tmp_path: Path) -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=8_000, text="At the lake house the family waits by the red door."),
        SubtitleCue(index=2, start_ms=8_500, end_ms=16_000, text="The mother whispers that the clock stops at midnight."),
        SubtitleCue(index=3, start_ms=40_000, end_ms=48_000, text="Run now, blood everywhere, the police found the knife."),
        SubtitleCue(index=4, start_ms=48_500, end_ms=56_000, text="They know the secret and they will kill us."),
    ]
    script_path = tmp_path / "imsdb_script.txt"
    script_path.write_text(
        "INT. LAKE HOUSE - NIGHT The family gathers at the red door. "
        "The mother studies the old clock and warns that midnight changes everything.",
        encoding="utf-8",
    )
    script_context = [
        ScriptContextSource(
            provider="imsdb",
            title="Example Movie",
            url="https://imsdb.com/scripts/example.html",
            script_text_path=str(script_path),
            source_kind="script_text",
        )
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=90, script_context=script_context)

    assert manifest.beats
    assert "scriptplay overlap" not in manifest.beats[0].source_reason.lower()
    assert any("script-context overlap" in beat.source_reason for beat in manifest.beats)
    assert any("script context" in note.lower() or "screenplay or transcript overlap" in note.lower() for note in manifest.planner_notes)
    assert any(beat.screenplay_scene for beat in manifest.beats)


def test_longform_planner_maps_beats_to_screenplay_scenes(tmp_path: Path) -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=6_000, text="We made it to the lake house before dark."),
        SubtitleCue(index=2, start_ms=6_400, end_ms=13_000, text="The red door is open and the family is waiting."),
        SubtitleCue(index=3, start_ms=21_000, end_ms=27_000, text="At dinner the mother studies the old clock."),
        SubtitleCue(index=4, start_ms=27_300, end_ms=34_000, text="She says midnight changes everything here."),
        SubtitleCue(index=5, start_ms=42_000, end_ms=48_000, text="The tunnel is underneath the shed behind the house."),
        SubtitleCue(index=6, start_ms=48_300, end_ms=55_000, text="If we run now the children might survive."),
    ]
    script_path = tmp_path / "scriptslug_script.txt"
    script_path.write_text(
        "INT. LAKE HOUSE - NIGHT The family reaches the red door before dark and waits in silence. "
        "INT. DINING ROOM - NIGHT The mother studies the old clock and warns that midnight changes everything. "
        "EXT. SHED - NIGHT A hidden tunnel waits underneath the shed behind the house.",
        encoding="utf-8",
    )
    script_context = [
        ScriptContextSource(
            provider="scriptslug",
            title="Example Movie",
            url="https://www.scriptslug.com/script/example-2026",
            script_text_path=str(script_path),
            source_kind="script_text",
        )
    ]

    manifest = choose_story_beats(
        cues,
        max_duration_seconds=180,
        script_context=script_context,
        target_duration_seconds=90,
    )

    assert any("maps subtitle scene blocks onto screenplay scenes" in note.lower() for note in manifest.planner_notes)
    assert all(clip.screenplay_scene for clip in manifest.clips)
    assert manifest.clips[0].screenplay_scene_index == 1
    assert manifest.clips[1].screenplay_scene_index in {1, 2}
    assert manifest.clips[-1].screenplay_scene_index >= manifest.clips[0].screenplay_scene_index
    assert "matched screenplay scene" in manifest.beats[0].source_reason.lower()


def test_choose_story_beats_can_target_longer_duration() -> None:
    cues = []
    start = 0
    for index in range(36):
        cues.append(
            SubtitleCue(
                index=index + 1,
                start_ms=start,
                end_ms=start + 4_000,
                text=f"Scene {index} secret truth police run confession line {index}",
            )
        )
        start += 20_000

    manifest = choose_story_beats(cues, max_duration_seconds=180, target_duration_seconds=120)

    assert len(manifest.clips) >= 5
    assert manifest.clips[-1].output_end_ms >= 90_000
    assert any("targeted approximately 120 seconds" in note.lower() for note in manifest.planner_notes)


def test_longform_target_prefers_contiguous_arc_over_scattered_peaks() -> None:
    cues = []
    start = 0
    texts = []
    texts.extend(["alpha secret mission contact escape"] * 10)
    texts.extend(["lab confession antidote patient doctor"] * 10)
    texts.extend(["alpha secret mission contact escape"] * 10)
    for index, text in enumerate(texts):
        cues.append(SubtitleCue(index=index + 1, start_ms=start, end_ms=start + 6_000, text=text))
        start += 12_000

    manifest = choose_story_beats(cues, max_duration_seconds=180, target_duration_seconds=120)

    starts = [clip.source_start_ms for clip in manifest.clips]
    assert starts == sorted(starts)
    span = manifest.clips[-1].source_end_ms - manifest.clips[0].source_start_ms
    assert span < 170_000


def test_build_scene_blocks_respects_gaps_and_merges_short_tail() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=4_000, text="We need to talk right now."),
        SubtitleCue(index=2, start_ms=4_400, end_ms=9_000, text="This secret changes everything."),
        SubtitleCue(index=3, start_ms=9_500, end_ms=15_000, text="You cannot tell the police."),
        SubtitleCue(index=4, start_ms=22_500, end_ms=27_000, text="The trial starts tonight."),
        SubtitleCue(index=5, start_ms=27_400, end_ms=31_500, text="She is still my little girl."),
    ]

    blocks = build_scene_blocks(cues, gap_threshold_ms=3_000, min_block_ms=10_000, max_block_ms=25_000)

    assert len(blocks) == 2
    assert blocks[0].start_ms == 0
    assert blocks[0].end_ms == 15_000
    assert blocks[1].start_ms == 22_500
    assert blocks[1].end_ms == 31_500


def test_longform_clips_follow_scene_boundaries() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=5_000, text="Dinner is ready and nobody should leave."),
        SubtitleCue(index=2, start_ms=5_400, end_ms=11_000, text="The family knows the truth now."),
        SubtitleCue(index=3, start_ms=11_400, end_ms=18_000, text="If the doctor comes back we run."),
        SubtitleCue(index=4, start_ms=24_500, end_ms=29_000, text="The police found the knife in the lake."),
        SubtitleCue(index=5, start_ms=29_300, end_ms=36_000, text="Tell me why you lied to your daughter."),
        SubtitleCue(index=6, start_ms=36_500, end_ms=44_000, text="Because I thought she would die tonight."),
        SubtitleCue(index=7, start_ms=51_000, end_ms=57_000, text="Open the gate and let me see her."),
        SubtitleCue(index=8, start_ms=57_300, end_ms=64_000, text="No, this ends with the trial right now."),
    ]

    manifest = choose_story_beats(cues, max_duration_seconds=180, target_duration_seconds=90)

    assert len(manifest.clips) >= 2
    assert manifest.clips[0].source_start_ms <= 500
    assert manifest.clips[0].source_end_ms >= 18_000
    assert manifest.clips[-1].source_end_ms >= 64_000
