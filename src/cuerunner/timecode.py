"""Time formatting for the cue list and transport clock."""

from __future__ import annotations


def format_clock(ms: int) -> str:
    """Running clock: mm:ss.t (tenths of a second)."""
    ms = max(0, ms)
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis // 100}"


def format_duration(ms: int) -> str:
    """Cue duration: m:ss, or a dash when unknown."""
    if ms <= 0:
        return "-"
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


def format_hms(ms: int) -> str:
    """Total running time: h:mm:ss."""
    total = max(0, round(ms / 1000))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
