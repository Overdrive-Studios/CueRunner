"""Time formatting for the cue list, transport clock and countdown."""

from __future__ import annotations


def format_clock(ms: float) -> str:
    """Running clock: mm:ss.t (tenths of a second).

    Takes a float as well as an int: a voice's position is fractional once its rate is not
    1.0, and a float reaching the format string used to raise and take the whole app down.
    """
    ms = max(0, int(ms))
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis // 100}"


def format_duration(ms: float) -> str:
    """Cue duration: m:ss, or a dash when unknown."""
    ms = int(ms)
    if ms <= 0:
        return "-"
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


def format_hms(ms: float) -> str:
    """Total running time: h:mm:ss."""
    total = max(0, round(int(ms) / 1000))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def format_mmss(seconds: float) -> str:
    """Countdown display: mm:ss."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"
