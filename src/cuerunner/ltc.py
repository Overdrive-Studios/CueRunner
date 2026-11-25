"""SMPTE linear timecode (LTC): the biphase-mark codec behind an LTC input.

An LTC frame is 80 bits sent least-significant bit first. Each bit is two half-cells. A 0 has
a transition only at the start of its bit, and a 1 also has one in the middle. The last 16
bits are the sync word. This module encodes and decodes audio samples only; capturing live
audio is a separate job for the window.

The encoder and decoder are checked against each other in tests. Real LTC sources can
differ in level and noise, so the decoder expects a clean signal. Put a filter in front of
it when reading live audio.
"""

from __future__ import annotations

import numpy as np

SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]  # bits 64 to 79, in transmission order
FRAME_BITS = 80
RATES = (24, 25, 30)


def parse_timecode_ms(text: str, fps: int) -> int:
    """'01:02:03:04' -> milliseconds at fps, the same as mtc.parse_timecode."""
    from cuerunner.mtc import parse_timecode

    return parse_timecode(text, fps)


def frame_bits(ms: int, fps: int) -> list[int]:
    """The 80 bits for one frame at this time. Bit 27 (or 59 at 25 fps) is the phase bit."""
    if fps not in RATES:
        raise ValueError(f"Unsupported frame rate: {fps}")
    total = round(ms * fps / 1000)
    frames = total % fps
    seconds_total = total // fps
    minutes_total, seconds = divmod(seconds_total, 60)
    hours, minutes = divmod(minutes_total, 60)
    hours %= 24

    bits = [0] * FRAME_BITS

    def put(start: int, width: int, value: int) -> None:
        for i in range(width):
            bits[start + i] = (value >> i) & 1

    put(0, 4, frames % 10)
    put(8, 2, frames // 10)
    put(16, 4, seconds % 10)
    put(24, 3, seconds // 10)
    put(32, 4, minutes % 10)
    put(40, 3, minutes // 10)
    put(48, 4, hours % 10)
    put(56, 2, hours // 10)
    for i, b in enumerate(SYNC):
        bits[64 + i] = b

    phase_index = 59 if fps == 25 else 27
    bits[phase_index] = 0
    if sum(bits) % 2:  # even number of ones keeps the level consistent from frame to frame
        bits[phase_index] = 1
    return bits


def encode(start_ms: int, fps: int, frame_count: int, rate: int, amplitude: float = 0.8) -> np.ndarray:
    """Audio samples for consecutive frames starting at start_ms."""
    if fps not in RATES:
        raise ValueError(f"Unsupported frame rate: {fps}")
    first_frame = round(start_ms * fps / 1000)
    level = -1.0
    # A lead-in of two half-cells at a steady level, so the first bit's starting transition is visible to the decoder.
    lead = round(rate / (fps * 160)) * 2
    samples: list[float] = [level] * lead
    for n in range(frame_count):
        ms = round((first_frame + n) * 1000 / fps)
        cells: list[float] = []
        for bit in frame_bits(ms, fps):
            level = -level  # every bit starts with a transition
            cells.append(level)
            if bit:
                level = -level  # a 1 has a second transition in the middle
            cells.append(level)
        samples.extend(_render_cells(cells, rate, fps))
    return np.array(samples, dtype=np.float32) * amplitude


def _render_cells(cells: list[float], rate: int, fps: int) -> list[float]:
    """Each half-cell is rate / (fps * 160) samples long, rounded so the total is exact."""
    per_frame = rate / fps
    cell_count = len(cells)
    out: list[float] = []
    for k, value in enumerate(cells):
        begin = round(k * per_frame / cell_count)
        end = round((k + 1) * per_frame / cell_count)
        out.extend([value] * (end - begin))
    return out


def decode(samples: np.ndarray, rate: int, fps: int) -> list[int]:
    """Milliseconds for each complete LTC frame found in the samples, at the given frame rate."""
    if fps not in RATES:
        raise ValueError(f"Unsupported frame rate: {fps}")
    positive = np.asarray(samples) >= 0
    transitions = np.flatnonzero(positive[1:] != positive[:-1]) + 1
    if len(transitions) < 2:
        return []
    half_cell = rate / (fps * 160)
    intervals = np.diff(transitions) / half_cell
    bits: list[int] = []
    half_pending = False
    for n in np.rint(intervals).astype(int):
        if n >= 2:
            if half_pending:
                half_pending = False  # a broken half-cell: drop it and resynchronise
            bits.append(0)
        elif n == 1:
            if half_pending:
                bits.append(1)
                half_pending = False
            else:
                half_pending = True
        # n == 0 is noise shorter than a half-cell: ignored
    times = []
    last_start = None
    for j in range(len(bits) - FRAME_BITS + 1):
        if bits[j + 64 : j + 80] != SYNC:
            continue
        if last_start is not None and j - last_start < FRAME_BITS:
            continue  # a sync pattern inside the frame just read, not the start of a new one
        last_start = j
        times.append(_time_of(bits[j : j + FRAME_BITS], fps))
    return times


def _time_of(frame: list[int], fps: int) -> int:
    def field(start: int, width: int) -> int:
        return sum(frame[start + i] << i for i in range(width))

    frames = field(0, 4) + 10 * field(8, 2)
    seconds = field(16, 4) + 10 * field(24, 3)
    minutes = field(32, 4) + 10 * field(40, 3)
    hours = field(48, 4) + 10 * field(56, 2)
    total = ((hours * 60 + minutes) * 60 + seconds) * fps + frames
    return round(total * 1000 / fps)
