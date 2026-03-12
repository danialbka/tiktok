from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re

from .models import JobManifest, RenderClip, ScriptContextSource, StoryBeat, StoryVariant, SubtitleCue


WORD_RE = re.compile(r"[A-Za-z0-9']+")
SCENE_SPLIT_RE = re.compile(r"(?=(?:INT|EXT|INT/EXT|EXT/INT)\.\s)")
INTRIGUE_WORDS = {
    "kill",
    "dead",
    "death",
    "run",
    "blood",
    "secret",
    "truth",
    "why",
    "never",
    "help",
    "police",
    "love",
    "lied",
    "found",
    "gone",
    "remember",
}
STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "have",
    "your",
    "they",
    "them",
    "their",
    "just",
    "into",
    "about",
    "when",
    "what",
    "where",
    "which",
    "would",
    "there",
    "were",
    "been",
    "because",
    "while",
    "could",
    "should",
    "after",
    "before",
    "then",
    "than",
    "like",
    "want",
    "need",
    "know",
    "will",
}


@dataclass(slots=True)
class SubtitleWindow:
    start_ms: int
    end_ms: int
    text: str
    cue_count: int
    score: float
    transcript_score: float
    script_score: float = 0.0
    script_match_excerpt: str | None = None
    screenplay_scene: str | None = None
    screenplay_scene_index: int | None = None
    screenplay_source: str | None = None


@dataclass(slots=True)
class ScreenplayScene:
    heading: str
    text: str
    tokens: set[str]
    order: int
    provider: str
    source_title: str


@dataclass(slots=True)
class VariantCandidate:
    windows: list[SubtitleWindow]
    score: float
    selection_reason: str


def _window_summary(text: str, limit: int = 18) -> str:
    words = WORD_RE.findall(text)
    preview = " ".join(words[:limit]).strip()
    return preview or text[:80].strip()


def _score_text(text: str, cue_count: int) -> float:
    lowered = text.lower()
    word_count = len(WORD_RE.findall(lowered))
    intrigue_hits = sum(1 for word in INTRIGUE_WORDS if word in lowered)
    punctuation_hits = sum(lowered.count(mark) for mark in ("?", "!", "..."))
    density = min(word_count / max(cue_count, 1), 20)
    return intrigue_hits * 2.0 + punctuation_hits * 1.5 + density


def build_windows(
    cues: list[SubtitleCue],
    max_window_ms: int = 45_000,
    window_size_cues: int = 4,
    stride_cues: int = 2,
) -> list[SubtitleWindow]:
    windows: list[SubtitleWindow] = []
    if not cues:
        return windows

    for start_index in range(0, len(cues), stride_cues):
        chunk = cues[start_index : start_index + window_size_cues]
        if len(chunk) < 2:
            continue

        first = chunk[0]
        last = chunk[-1]
        if last.end_ms - first.start_ms > max_window_ms:
            trimmed: list[SubtitleCue] = []
            for cue in chunk:
                if not trimmed or cue.end_ms - trimmed[0].start_ms <= max_window_ms:
                    trimmed.append(cue)
                else:
                    break
            chunk = trimmed
            if len(chunk) < 2:
                continue

        text = " ".join(item.text for item in chunk)
        windows.append(
            SubtitleWindow(
                start_ms=chunk[0].start_ms,
                end_ms=chunk[-1].end_ms,
                text=text,
                cue_count=len(chunk),
                score=_score_text(text, len(chunk)),
                transcript_score=_score_text(text, len(chunk)),
            )
        )
    return windows


def _window_from_cues(chunk: list[SubtitleCue]) -> SubtitleWindow:
    text = " ".join(item.text for item in chunk)
    score = _score_text(text, len(chunk))
    return SubtitleWindow(
        start_ms=chunk[0].start_ms,
        end_ms=chunk[-1].end_ms,
        text=text,
        cue_count=len(chunk),
        score=score,
        transcript_score=score,
    )


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in (word.lower() for word in WORD_RE.findall(text))
        if len(token) > 2 and token not in STOPWORDS
    }


def _chunk_script_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    heading_chunks = [chunk.strip() for chunk in SCENE_SPLIT_RE.split(stripped) if chunk.strip()]
    if len(heading_chunks) > 1:
        return [_window_summary(chunk, limit=60) for chunk in heading_chunks[:120]]

    words = stripped.split()
    chunks: list[str] = []
    step = 80
    size = 120
    for index in range(0, len(words), step):
        chunk = " ".join(words[index : index + size]).strip()
        if len(chunk) >= 60:
            chunks.append(chunk)
    return chunks[:120]


def _extract_scene_heading(text: str, order: int) -> str:
    stripped = text.strip()
    heading_match = re.match(r"((?:INT|EXT|INT/EXT|EXT/INT)\.[^A-Z]{0,80})", stripped)
    if heading_match:
        heading = _clean_scene_heading(heading_match.group(1))
        if heading:
            return heading
    preview = _window_summary(stripped, limit=10)
    return preview or f"Scene {order}"


def _clean_scene_heading(text: str) -> str:
    words = WORD_RE.findall(text.upper())
    if not words:
        return ""
    return " ".join(words[:10]).strip()


def _parse_script_scenes(text: str, provider: str, source_title: str) -> list[ScreenplayScene]:
    stripped = text.strip()
    if not stripped:
        return []

    chunks = [chunk.strip() for chunk in SCENE_SPLIT_RE.split(stripped) if chunk.strip()]
    if len(chunks) <= 1:
        chunks = _chunk_script_text(stripped)

    scenes: list[ScreenplayScene] = []
    for chunk in chunks[:150]:
        tokens = _tokenize(chunk)
        if not tokens:
            continue
        order = len(scenes) + 1
        scenes.append(
            ScreenplayScene(
                heading=_extract_scene_heading(chunk, order),
                text=chunk,
                tokens=tokens,
                order=order,
                provider=provider,
                source_title=source_title,
            )
        )
    return scenes


def _load_script_scenes(script_context: list[ScriptContextSource]) -> list[ScreenplayScene]:
    scene_sets: list[list[ScreenplayScene]] = []
    for source in script_context:
        if source.script_text_path:
            path = Path(source.script_text_path)
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
                scenes = _parse_script_scenes(text, source.provider, source.title)
                if scenes:
                    scene_sets.append(scenes)
        elif source.summary:
            scenes = _parse_script_scenes(source.summary, source.provider, source.title)
            if scenes:
                scene_sets.append(scenes)
    if not scene_sets:
        return []
    return max(scene_sets, key=len)[:150]


def _apply_script_context(windows: list[SubtitleWindow], script_context: list[ScriptContextSource]) -> None:
    scenes = _load_script_scenes(script_context)
    if not scenes:
        return

    for window in windows:
        window_tokens = _tokenize(window.text)
        if not window_tokens:
            continue

        best_score = 0.0
        best_scene: ScreenplayScene | None = None
        for scene in scenes:
            overlap = window_tokens & scene.tokens
            if not overlap:
                continue
            density = len(overlap) / max(len(window_tokens), 1)
            coverage = len(overlap) / max(min(len(window_tokens), len(scene.tokens)), 1)
            score = density * 8.0 + coverage * 10.0 + len(overlap) * 1.5
            if score > best_score:
                best_score = score
                best_scene = scene

        window.script_score = round(best_score, 2)
        window.script_match_excerpt = _window_summary(best_scene.text, limit=22) if best_scene else None
        window.screenplay_scene = best_scene.heading if best_scene else None
        window.screenplay_scene_index = best_scene.order if best_scene else None
        window.screenplay_source = best_scene.provider if best_scene else None
        window.score = round(window.transcript_score + best_score, 2)


def build_timed_windows(
    cues: list[SubtitleCue],
    window_ms: int = 18_000,
    stride_ms: int | None = None,
) -> list[SubtitleWindow]:
    if not cues:
        return []

    stride = stride_ms or window_ms
    runtime_ms = cues[-1].end_ms
    windows: list[SubtitleWindow] = []
    start_ms = max(0, cues[0].start_ms)
    while start_ms < runtime_ms:
        end_ms = min(runtime_ms, start_ms + window_ms)
        chunk = [cue for cue in cues if cue.end_ms > start_ms and cue.start_ms < end_ms]
        if chunk:
            text = " ".join(item.text for item in chunk)
            score = _score_text(text, len(chunk))
            windows.append(
                SubtitleWindow(
                    start_ms=chunk[0].start_ms,
                    end_ms=chunk[-1].end_ms,
                    text=text,
                    cue_count=len(chunk),
                    score=score,
                    transcript_score=score,
                )
            )
        start_ms += stride
    return windows


def build_scene_blocks(
    cues: list[SubtitleCue],
    gap_threshold_ms: int = 3_000,
    min_block_ms: int = 12_000,
    max_block_ms: int = 28_000,
) -> list[SubtitleWindow]:
    if not cues:
        return []

    blocks: list[list[SubtitleCue]] = []
    current: list[SubtitleCue] = []

    for index, cue in enumerate(cues):
        current.append(cue)
        duration_ms = current[-1].end_ms - current[0].start_ms
        next_gap_ms = (
            cues[index + 1].start_ms - cue.end_ms
            if index + 1 < len(cues)
            else gap_threshold_ms + 1
        )
        should_close = False
        if duration_ms >= min_block_ms and next_gap_ms >= gap_threshold_ms:
            should_close = True
        elif duration_ms >= 4_000 and next_gap_ms >= gap_threshold_ms * 3:
            should_close = True
        elif duration_ms >= max_block_ms:
            should_close = True

        if should_close:
            blocks.append(current)
            current = []

    if current:
        if blocks and current[-1].end_ms - current[0].start_ms < min_block_ms // 2:
            blocks[-1].extend(current)
        else:
            blocks.append(current)

    merged: list[list[SubtitleCue]] = []
    for block in blocks:
        if not merged:
            merged.append(block)
            continue
        duration_ms = block[-1].end_ms - block[0].start_ms
        prior_duration_ms = merged[-1][-1].end_ms - merged[-1][0].start_ms
        gap_ms = block[0].start_ms - merged[-1][-1].end_ms
        if duration_ms < min_block_ms // 2 and prior_duration_ms + duration_ms <= max_block_ms * 2 and gap_ms <= gap_threshold_ms * 2:
            merged[-1].extend(block)
        else:
            merged.append(block)

    return [_window_from_cues(block) for block in merged if len(block) >= 1]


def _continuity_bonus(left: SubtitleWindow, right: SubtitleWindow) -> float:
    left_tokens = _tokenize(left.text)
    right_tokens = _tokenize(right.text)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    if not overlap:
        return 0.0
    return (len(overlap) / max(min(len(left_tokens), len(right_tokens)), 1)) * 10.0


def _screenplay_sequence_bonus(left: SubtitleWindow, right: SubtitleWindow) -> float:
    if left.screenplay_scene_index is None or right.screenplay_scene_index is None:
        return 0.0
    delta = right.screenplay_scene_index - left.screenplay_scene_index
    if delta == 0:
        return 2.0
    if delta == 1:
        return 5.0
    if delta == 2:
        return 2.5
    if delta < 0:
        return -6.0
    return -min(4.5, float(delta - 2) * 1.2)


def _screenplay_alignment_ratio(windows: list[SubtitleWindow]) -> float:
    if not windows:
        return 0.0
    aligned = sum(1 for window in windows if window.screenplay_scene_index is not None)
    return aligned / len(windows)


def _infer_contextual_target_ms(
    scene_blocks: list[SubtitleWindow],
    total_runtime_ms: int,
    duration_cap_ms: int,
) -> int:
    if not scene_blocks:
        return max(15_000, min(duration_cap_ms, total_runtime_ms))

    average_block_ms = sum(block.end_ms - block.start_ms for block in scene_blocks) / len(scene_blocks)
    screenplay_alignment = _screenplay_alignment_ratio(scene_blocks)
    average_score = sum(block.score for block in scene_blocks) / len(scene_blocks)

    desired_blocks = 2.0 + min(len(scene_blocks), 8) * 0.55
    desired_blocks += screenplay_alignment * 1.5
    desired_blocks += min(1.5, average_score / 18.0)
    desired_blocks = min(float(len(scene_blocks)), max(2.0, desired_blocks))

    inferred = int(average_block_ms * desired_blocks)
    inferred = max(15_000, inferred)
    return min(duration_cap_ms, inferred, total_runtime_ms)


def choose_story_beats(
    cues: list[SubtitleCue],
    max_duration_seconds: int | None,
    script_context: list[ScriptContextSource] | None = None,
    target_duration_seconds: int | None = None,
    variant_count: int = 5,
) -> JobManifest:
    total_runtime_ms = max(cue.end_ms for cue in cues)
    windows = build_windows(cues)
    if len(windows) < 2:
        raise RuntimeError("Not enough subtitle content to build story beats.")
    _apply_script_context(windows, script_context or [])

    total_runtime_seconds = total_runtime_ms / 1000
    ordered = sorted(windows, key=lambda item: item.start_ms)
    scene_blocks = build_scene_blocks(cues)
    _apply_script_context(scene_blocks, script_context or [])
    duration_cap_ms = min(total_runtime_ms, max_duration_seconds * 1000) if max_duration_seconds else total_runtime_ms
    contextual_target_ms = _infer_contextual_target_ms(
        scene_blocks=scene_blocks or ordered,
        total_runtime_ms=total_runtime_ms,
        duration_cap_ms=duration_cap_ms,
    )
    target_duration_ms = min(
        duration_cap_ms,
        max(15_000, int((target_duration_seconds * 1000) if target_duration_seconds else contextual_target_ms)),
    )
    desired_variants = max(1, variant_count)
    longform_mode = bool((target_duration_seconds is not None and target_duration_seconds >= 90) or (target_duration_seconds is None and target_duration_ms >= 90_000))

    if longform_mode:
        has_screenplay_mapping = any(window.screenplay_scene_index is not None for window in scene_blocks)
        variant_candidates = _rank_contiguous_story_arcs(scene_blocks, target_duration_ms)
        selected_candidates = _select_diverse_candidates(variant_candidates, desired_variants)
        if not selected_candidates:
            selected_candidates = [VariantCandidate(windows=_select_contiguous_story_arc(scene_blocks, target_duration_ms), score=0.0, selection_reason="Fallback contiguous arc.")]
        planner_notes = [f"Planner targeted approximately {round(target_duration_ms / 1000)} seconds using a contiguous chronological story arc."]
        if has_screenplay_mapping:
            planner_notes.append(
                "Planner maps subtitle scene blocks onto screenplay scenes, then favors arcs that move through neighboring screenplay scenes with strong dialogue continuity."
            )
        else:
            planner_notes.append(
                "Planner groups subtitles into scene-like blocks and favors arcs with strong chronological dialogue continuity when screenplay scenes are unavailable."
            )
    else:
        variant_candidates = _rank_hook_variants(ordered, target_duration_ms)
        selected_candidates = _select_diverse_candidates(variant_candidates, desired_variants)
        if not selected_candidates:
            base_windows = _build_default_hook_variant(ordered)
            selected_candidates = [VariantCandidate(windows=base_windows, score=0.0, selection_reason="Fallback hook-forward cut.")]
        planner_notes = [
            "Planner uses subtitle windows scored for intrigue and dialogue density.",
            "Display order may differ from chronology to improve hook strength.",
        ]

    variants = _build_story_variants(
        selected_candidates,
        target_duration_ms=target_duration_ms,
        total_runtime_ms=total_runtime_ms,
        max_duration_seconds=max_duration_seconds,
        chronological=longform_mode,
    )
    if not variants:
        raise RuntimeError("Story planner could not fit any clips within the requested duration.")

    primary_variant = variants[0]
    manifest = JobManifest(
        job_id=0,
        filename="",
        source_video_path="",
        subtitle_source="",
        subtitle_path="",
        total_runtime_seconds=round(total_runtime_seconds, 2),
        beats=primary_variant.beats,
        clips=primary_variant.clips,
        variants=variants,
        planner_notes=planner_notes,
    )
    if any(window.script_score > 0 for window in windows):
        manifest.planner_notes.append("Planner re-scored subtitle windows using screenplay or transcript overlap from fetched script context.")
    if len(variants) > 1:
        manifest.planner_notes.append(f"Planner generated {len(variants)} distinct cut variants for review.")
    if not target_duration_seconds:
        manifest.planner_notes.append(f"Planner inferred a contextual duration target of {round(target_duration_ms / 1000)} seconds.")
    return manifest


def _build_source_reason(beat_type: str, window: SubtitleWindow) -> str:
    if window.script_score > 0 and window.script_match_excerpt:
        scene_label = f" Matched screenplay scene: {window.screenplay_scene}." if window.screenplay_scene else ""
        return (
            f"Selected for {beat_type} using transcript energy {window.transcript_score:.2f} "
            f"plus script-context overlap {window.script_score:.2f}. "
            f"Closest screenplay context: {window.script_match_excerpt}.{scene_label}"
        )
    return f"Selected for {beat_type} using transcript energy score {window.transcript_score:.2f}."


def _select_longform_windows(ordered: list[SubtitleWindow], target_duration_ms: int) -> list[SubtitleWindow]:
    desired_clip_count = max(5, min(8, round(target_duration_ms / 18_000)))
    segment_size = max(1, len(ordered) // desired_clip_count)
    selected: list[SubtitleWindow] = []
    for index in range(desired_clip_count):
        start = index * segment_size
        end = len(ordered) if index == desired_clip_count - 1 else min(len(ordered), (index + 1) * segment_size)
        segment = ordered[start:end]
        if not segment:
            continue
        best = max(segment, key=lambda item: item.score)
        if selected and best.start_ms <= selected[-1].start_ms:
            fallback = [item for item in segment if item.start_ms > selected[-1].start_ms]
            if fallback:
                best = max(fallback, key=lambda item: item.score)
        if all(existing.start_ms != best.start_ms for existing in selected):
            selected.append(best)
    return selected or ordered[: min(4, len(ordered))]


def _select_contiguous_story_arc(ordered: list[SubtitleWindow], target_duration_ms: int) -> list[SubtitleWindow]:
    candidates = _rank_contiguous_story_arcs(ordered, target_duration_ms)
    return candidates[0].windows if candidates else []


def _rank_contiguous_story_arcs(ordered: list[SubtitleWindow], target_duration_ms: int) -> list[VariantCandidate]:
    if not ordered:
        return []

    candidates: list[VariantCandidate] = []
    max_possible_span_ms = ordered[-1].end_ms - ordered[0].start_ms
    minimum_span_ms = min(int(target_duration_ms * 0.65), max_possible_span_ms)
    for start_index in range(len(ordered)):
        span_end = start_index
        while span_end < len(ordered) and ordered[span_end].end_ms - ordered[start_index].start_ms < target_duration_ms * 1.1:
            span_end += 1
        for end_index in range(start_index + 1, span_end + 1):
            window_slice = ordered[start_index:end_index]
            span_ms = window_slice[-1].end_ms - window_slice[0].start_ms
            if span_ms < minimum_span_ms:
                continue
            average_score = sum(window.score for window in window_slice) / len(window_slice)
            continuity = sum(_continuity_bonus(window_slice[i], window_slice[i + 1]) for i in range(len(window_slice) - 1))
            continuity /= max(len(window_slice) - 1, 1)
            screenplay_continuity = sum(
                _screenplay_sequence_bonus(window_slice[i], window_slice[i + 1])
                for i in range(len(window_slice) - 1)
            )
            screenplay_continuity /= max(len(window_slice) - 1, 1)
            first_third = window_slice[: max(1, len(window_slice) // 3)]
            last_third = window_slice[-max(1, len(window_slice) // 3) :]
            setup_score = sum(window.script_score + window.transcript_score * 0.35 for window in first_third) / len(first_third)
            payoff_score = sum(window.score for window in last_third) / len(last_third)
            progression = max(0.0, payoff_score - setup_score) * 0.45
            screenplay_alignment = _screenplay_alignment_ratio(window_slice) * 3.0
            large_gap_penalty = 0.0
            for left, right in zip(window_slice, window_slice[1:]):
                gap_ms = max(0, right.start_ms - left.end_ms)
                if gap_ms > 9_000:
                    large_gap_penalty += min(3.5, gap_ms / 6_000)
            duration_penalty = abs(span_ms - target_duration_ms) / max(target_duration_ms, 1) * 4.0
            score = (
                average_score
                + continuity
                + screenplay_continuity
                + screenplay_alignment
                + progression
                - duration_penalty
                - large_gap_penalty
            )
            start_scene = window_slice[0].screenplay_scene or _window_summary(window_slice[0].text, limit=8)
            end_scene = window_slice[-1].screenplay_scene or _window_summary(window_slice[-1].text, limit=8)
            candidates.append(
                VariantCandidate(
                    windows=_expand_arc_bounds(ordered, start_index, end_index, target_duration_ms),
                    score=score,
                    selection_reason=f"Contiguous arc from {start_scene} to {end_scene}.",
                )
            )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return _dedupe_candidate_windows(candidates)


def _expand_arc_bounds(
    ordered: list[SubtitleWindow],
    start_index: int,
    end_index: int,
    target_duration_ms: int,
) -> list[SubtitleWindow]:
    while start_index > 0 or end_index < len(ordered):
        current = ordered[start_index:end_index]
        span_ms = current[-1].end_ms - current[0].start_ms
        if span_ms >= target_duration_ms * 0.92:
            break

        left_gain = float("-inf")
        right_gain = float("-inf")
        if start_index > 0:
            left = ordered[start_index - 1]
            left_gain = left.score + _continuity_bonus(left, ordered[start_index]) + _screenplay_sequence_bonus(left, ordered[start_index])
        if end_index < len(ordered):
            right = ordered[end_index]
            right_gain = (
                right.score
                + _continuity_bonus(ordered[end_index - 1], right)
                + _screenplay_sequence_bonus(ordered[end_index - 1], right)
            )

        if right_gain >= left_gain and end_index < len(ordered):
            end_index += 1
        elif start_index > 0:
            start_index -= 1
        else:
            break

    return ordered[start_index:end_index]


def _build_default_hook_variant(ordered: list[SubtitleWindow]) -> list[SubtitleWindow]:
    hook = max(ordered, key=lambda item: item.score)
    context_candidates = [item for item in ordered if item.start_ms < hook.start_ms]
    context = context_candidates[max(0, math.floor(len(context_candidates) * 0.55))] if context_candidates else ordered[0]
    escalation_candidates = [item for item in ordered if item.start_ms > context.start_ms and item.start_ms != hook.start_ms]
    escalation = max(escalation_candidates or ordered, key=lambda item: item.score)
    payoff_candidates = [item for item in ordered if item.start_ms > escalation.start_ms]
    payoff = max(payoff_candidates or ordered[-2:], key=lambda item: item.score)

    unique_windows: list[SubtitleWindow] = []
    for item in (hook, context, escalation, payoff):
        if all(existing.start_ms != item.start_ms for existing in unique_windows):
            unique_windows.append(item)
    return unique_windows


def _rank_hook_variants(ordered: list[SubtitleWindow], target_duration_ms: int) -> list[VariantCandidate]:
    top_hooks = sorted(ordered, key=lambda item: item.score, reverse=True)[: min(8, len(ordered))]
    candidates: list[VariantCandidate] = []
    for hook in top_hooks:
        prior = [item for item in ordered if item.start_ms < hook.start_ms]
        later = [item for item in ordered if item.start_ms > hook.start_ms]
        context_pool = prior[-3:] if prior else ordered[:1]
        escalation_pool = sorted(later, key=lambda item: item.score, reverse=True)[:3] if later else ordered[-2:]
        payoff_pool = sorted(
            [item for item in ordered if item.start_ms > (escalation_pool[0].start_ms if escalation_pool else hook.start_ms)],
            key=lambda item: item.score,
            reverse=True,
        )[:3]
        if not payoff_pool:
            payoff_pool = ordered[-2:]

        for context in context_pool:
            for escalation in escalation_pool:
                for payoff in payoff_pool:
                    unique_windows: list[SubtitleWindow] = []
                    for item in (hook, context, escalation, payoff):
                        if all(existing.start_ms != item.start_ms for existing in unique_windows):
                            unique_windows.append(item)
                    if len(unique_windows) < 3:
                        continue
                    score = sum(window.score for window in unique_windows)
                    earliest = min(window.start_ms for window in unique_windows)
                    latest = max(window.end_ms for window in unique_windows)
                    duration_penalty = abs((latest - earliest) - target_duration_ms) / max(target_duration_ms, 1) * 2.0
                    selection_reason = f"Hook-forward cut anchored by {_window_summary(hook.text, limit=10)}."
                    candidates.append(
                        VariantCandidate(
                            windows=unique_windows,
                            score=score - duration_penalty,
                            selection_reason=selection_reason,
                        )
                    )
    candidates.sort(key=lambda item: item.score, reverse=True)
    return _dedupe_candidate_windows(candidates)


def _dedupe_candidate_windows(candidates: list[VariantCandidate]) -> list[VariantCandidate]:
    deduped: list[VariantCandidate] = []
    seen_keys: set[tuple[tuple[int, int], ...]] = set()
    for candidate in candidates:
        key = tuple((window.start_ms, window.end_ms) for window in candidate.windows)
        if key in seen_keys:
            continue
        deduped.append(candidate)
        seen_keys.add(key)
    return deduped


def _select_diverse_candidates(candidates: list[VariantCandidate], variant_count: int) -> list[VariantCandidate]:
    if variant_count <= 1:
        return candidates[:1]

    selected: list[VariantCandidate] = []
    for candidate in candidates:
        similarity = max((_candidate_similarity(candidate, prior) for prior in selected), default=0.0)
        if similarity >= 0.65:
            continue
        selected.append(candidate)
        if len(selected) >= variant_count:
            return selected

    for candidate in candidates:
        if len(selected) >= variant_count:
            break
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _candidate_similarity(left: VariantCandidate, right: VariantCandidate) -> float:
    left_ids = {(window.start_ms, window.end_ms) for window in left.windows}
    right_ids = {(window.start_ms, window.end_ms) for window in right.windows}
    if not left_ids or not right_ids:
        return 0.0
    intersection = len(left_ids & right_ids)
    union = len(left_ids | right_ids)
    return intersection / max(union, 1)


def _build_story_variants(
    candidates: list[VariantCandidate],
    target_duration_ms: int,
    total_runtime_ms: int,
    max_duration_seconds: int | None,
    chronological: bool,
) -> list[StoryVariant]:
    variants: list[StoryVariant] = []
    for index, candidate in enumerate(candidates, start=1):
        beats, clips = _build_clips(candidate.windows, target_duration_ms, total_runtime_ms, chronological=chronological)
        duration_cap_ms = max_duration_seconds * 1000 if max_duration_seconds else None
        while duration_cap_ms is not None and clips and clips[-1].output_end_ms > duration_cap_ms:
            clips.pop()
            beats.pop()
        if not clips:
            continue
        variants.append(
            StoryVariant(
                variant_id=index,
                label=f"Variant {index}",
                selection_reason=candidate.selection_reason,
                beats=beats,
                clips=clips,
            )
        )
    return variants


def _build_clips(
    windows: list[SubtitleWindow],
    target_duration_ms: int,
    total_runtime_ms: int,
    chronological: bool,
) -> tuple[list[StoryBeat], list[RenderClip]]:
    beat_labels = (
        ["hook", "setup", "turn", "escalation", "reversal", "climax", "payoff", "aftermath"]
        if chronological
        else ["hook", "context", "escalation", "payoff"]
    )
    desired_clip_ms = max(8_000, min(22_000, target_duration_ms // max(len(windows), 1)))
    clips: list[RenderClip] = []
    output_cursor_ms = 0
    prior_end = 0
    for index, window in enumerate(windows):
        if chronological:
            clip_start = max(0, window.start_ms - 350)
            clip_end = min(total_runtime_ms, window.end_ms + 500)
            duration_ms = clip_end - clip_start
            if duration_ms < 8_000:
                center_ms = (window.start_ms + window.end_ms) // 2
                clip_start = max(0, center_ms - desired_clip_ms // 2)
                clip_end = min(total_runtime_ms, clip_start + desired_clip_ms)
        else:
            center_ms = (window.start_ms + window.end_ms) // 2
            clip_start = max(0, center_ms - desired_clip_ms // 2)
            clip_end = min(total_runtime_ms, clip_start + desired_clip_ms)
        if prior_end and clip_start < prior_end + 750:
            shift = (prior_end + 750) - clip_start
            clip_start = min(total_runtime_ms, clip_start + shift)
            clip_end = min(total_runtime_ms, clip_end + shift)
        if index < len(windows) - 1:
            next_start = windows[index + 1].start_ms
            if clip_end > next_start - 750:
                clip_end = max(clip_start + 3_000, next_start - 750)
        if clip_end <= clip_start:
            continue
        beat_type = beat_labels[min(index, len(beat_labels) - 1)]
        clips.append(
            RenderClip(
                beat_type=beat_type,
                source_start_ms=int(clip_start),
                source_end_ms=int(clip_end),
                output_start_ms=output_cursor_ms,
                output_end_ms=output_cursor_ms + int(clip_end - clip_start),
                summary=_window_summary(window.text),
                screenplay_scene=window.screenplay_scene,
                screenplay_scene_index=window.screenplay_scene_index,
            )
        )
        output_cursor_ms += int(clip_end - clip_start)
        prior_end = int(clip_end)

    beats = [
        StoryBeat(
            beat_type=clip.beat_type,
            source_start_ms=clip.source_start_ms,
            source_end_ms=clip.source_end_ms,
            display_order=index + 1,
            score=round(windows[index].score, 2),
            summary=clip.summary,
            source_reason=_build_source_reason(clip.beat_type, windows[index]),
            screenplay_scene=windows[index].screenplay_scene,
            screenplay_scene_index=windows[index].screenplay_scene_index,
            screenplay_source=windows[index].screenplay_source,
        )
        for index, clip in enumerate(clips)
    ]
    return beats, clips
