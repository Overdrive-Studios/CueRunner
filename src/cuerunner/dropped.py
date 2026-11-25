"""Decide what a dropped file should become: an audio, video or MIDI file cue, or nothing."""

from __future__ import annotations

from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".aiff", ".aif", ".mp3", ".flac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
MIDI_EXTENSIONS = {".mid", ".midi"}


def kind_for_path(path: str | Path) -> str | None:
    """'audio', 'video', 'midifile' for a supported file, or None for anything else."""
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in MIDI_EXTENSIONS:
        return "midifile"
    return None


def split_drop(paths: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Split dropped paths into ([(path, kind)], skipped names). Folders and unknown types are skipped."""
    accepted: list[tuple[str, str]] = []
    skipped: list[str] = []
    for path in paths:
        kind = kind_for_path(path)
        if kind is None:
            skipped.append(Path(path).name or path)
        else:
            accepted.append((path, kind))
    return accepted, skipped
