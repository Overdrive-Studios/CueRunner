"""Fade maths, kept separate from the UI so it can be tested without a display."""

from __future__ import annotations

FADE_TICK_MS = 50


def fade_volumes(start: float, end: float, seconds: float, tick_ms: int = FADE_TICK_MS) -> list[float]:
    """Volume steps from start to end, one per tick, finishing exactly on end."""
    if seconds <= 0:
        return [end]
    steps = max(1, round(seconds * 1000 / tick_ms))
    return [start + (end - start) * i / steps for i in range(1, steps + 1)]
