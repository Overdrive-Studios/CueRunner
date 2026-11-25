"""MIDI Timecode (MTC) generator for Timecode cues.

A Timecode cue sends MTC from a start time at the chosen frame rate to one MIDI output.
Full-frame messages set a position; quarter-frame messages run the clock, eight per
two frames. Nothing here touches Qt, so the encoding and the sequencing can be tested
directly. The window drives MtcGenerator from a timer.
"""

from __future__ import annotations

from typing import Callable

FRAME_RATES = (24, 25, 30)
RATE_CODES = {24: 0, 25: 1, 30: 3}  # 2 = 29.97 drop-frame, not offered yet


def parse_timecode(text: str, fps: int) -> int:
    """'01:02:03:04' -> milliseconds at the given frame rate. Frames must be below fps."""
    parts = text.strip().split(":")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        raise ValueError("Start time must look like 00:00:00:00 (hours:minutes:seconds:frames).")
    hours, minutes, seconds, frames = (int(p) for p in parts)
    if minutes > 59 or seconds > 59 or frames >= fps:
        raise ValueError(f"Start time is out of range for {fps} fps.")
    total_frames = ((hours * 60 + minutes) * 60 + seconds) * fps + frames
    return round(total_frames * 1000 / fps)


def _split(ms: int, fps: int) -> tuple[int, int, int, int]:
    """Milliseconds -> (hours, minutes, seconds, frames), wrapping at 24 hours."""
    frame_count = int(ms * fps // 1000) % (24 * 3600 * fps)
    seconds_total, frames = divmod(frame_count, fps)
    minutes_total, seconds = divmod(seconds_total, 60)
    hours, minutes = divmod(minutes_total, 60)
    return hours, minutes, seconds, frames


def full_frame(ms: int, fps: int) -> bytes:
    """A full-frame SysEx that sets the receiver's position."""
    if fps not in RATE_CODES:
        raise ValueError(f"Unsupported frame rate: {fps}")
    hours, minutes, seconds, frames = _split(ms, fps)
    hour_byte = (RATE_CODES[fps] << 5) | hours
    return bytes([0xF0, 0x7F, 0x7F, 0x01, 0x01, hour_byte, minutes, seconds, frames, 0xF7])


def quarter_frame_pieces(ms: int, fps: int) -> list[int]:
    """The eight quarter-frame data nibbles (pieces 0 to 7) for one time value."""
    hours, minutes, seconds, frames = _split(ms, fps)
    return [
        frames & 0x0F,
        frames >> 4,
        seconds & 0x0F,
        seconds >> 4,
        minutes & 0x0F,
        minutes >> 4,
        hours & 0x0F,
        ((hours >> 4) & 0x01) | (RATE_CODES[fps] << 1),
    ]


def quarter_frame_message(piece: int, ms: int, fps: int) -> bytes:
    """One two-byte quarter-frame message: 0xF1 followed by piece number and nibble."""
    return bytes([0xF1, (piece << 4) | quarter_frame_pieces(ms, fps)[piece]])


def decode_quarter_frames(messages: list[bytes]) -> tuple[int, int]:
    """Rebuild (ms, fps) from eight quarter-frame messages. Used by the tests and for sanity checks."""
    nibbles = [m[1] & 0x0F for m in messages]
    frames = nibbles[0] | (nibbles[1] << 4)
    seconds = nibbles[2] | (nibbles[3] << 4)
    minutes = nibbles[4] | (nibbles[5] << 4)
    hours = nibbles[6] | ((nibbles[7] & 0x01) << 4)
    code = (nibbles[7] >> 1) & 0x03
    fps = {code_: rate for rate, code_ in RATE_CODES.items()}[code]
    total_frames = ((hours * 60 + minutes) * 60 + seconds) * fps + frames
    return round(total_frames * 1000 / fps), fps


class MtcGenerator:
    """Yields the MTC message sequence for a running clock.

    start() sends one full-frame message to set the position, then quarter-frame messages.
    next_message() returns the next message; interval_ms is how often to call it.
    """

    def __init__(self, start_ms: int, fps: int, send: Callable[[bytes], None]) -> None:
        if fps not in RATE_CODES:
            raise ValueError(f"Unsupported frame rate: {fps}")
        self.fps = fps
        self._send = send
        self._start_ms = start_ms
        self._piece = 0
        self._cycle_ms = start_ms  # time value for the cycle now being sent
        self._elapsed_frames = 0
        self._running = False

    @property
    def interval_ms(self) -> float:
        return 1000 / (4 * self.fps)

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._piece = 0
        self._send(full_frame(self._start_ms, self.fps))

    def stop(self) -> None:
        self._running = False

    def tick(self) -> None:
        """Send the next quarter-frame message. Call this every interval_ms while running."""
        if not self._running:
            return
        if self._piece == 0:
            # Each cycle of eight messages covers two frames.
            self._cycle_ms = self._start_ms + round(self._elapsed_frames * 1000 / self.fps)
        self._send(quarter_frame_message(self._piece, self._cycle_ms, self.fps))
        self._piece += 1
        if self._piece == 8:
            self._piece = 0
            self._elapsed_frames += 2


# --- Receiving: timecode triggers cue lists ----------------------------------

class QuarterFrameDecoder:
    """Rebuilds the running clock from incoming MTC.

    feed() takes one raw MIDI message as bytes. It returns (ms, fps) when a position is
    known: after a full-frame SysEx, or after each complete set of eight quarter-frames.
    Other messages return None.
    """

    def __init__(self) -> None:
        self._pieces = [0] * 8
        self._seen = 0

    def feed(self, data: bytes) -> tuple[int, int] | None:
        if len(data) == 10 and data[:5] == bytes([0xF0, 0x7F, 0x7F, 0x01, 0x01]):
            hour_byte, minutes, seconds, frames = data[5], data[6], data[7], data[8]
            fps = {code: rate for rate, code in RATE_CODES.items()}.get(hour_byte >> 5)
            if fps is None:
                return None
            hours = hour_byte & 0x1F
            self._seen = 0
            return _to_ms(hours, minutes, seconds, frames, fps), fps
        if len(data) == 2 and data[0] == 0xF1:
            piece, nibble = data[1] >> 4, data[1] & 0x0F
            if piece != self._seen:
                self._seen = 0  # out of step: wait for piece 0 again
                if piece != 0:
                    return None
            self._pieces[piece] = nibble
            self._seen = piece + 1
            if piece == 7:
                self._seen = 0
                return self._from_pieces()
        return None

    def _from_pieces(self) -> tuple[int, int] | None:
        p = self._pieces
        frames = p[0] | (p[1] << 4)
        seconds = p[2] | (p[3] << 4)
        minutes = p[4] | (p[5] << 4)
        hours = p[6] | ((p[7] & 0x01) << 4)
        fps = {code: rate for rate, code in RATE_CODES.items()}.get((p[7] >> 1) & 0x03)
        if fps is None:
            return None
        return _to_ms(hours, minutes, seconds, frames, fps), fps


def _to_ms(hours: int, minutes: int, seconds: int, frames: int, fps: int) -> int:
    total = ((hours * 60 + minutes) * 60 + seconds) * fps + frames
    return round(total * 1000 / fps)


class TimecodeTriggers:
    """Cues that fire when incoming timecode passes their time.

    Triggers fire only when the clock moves forward past them. A rewind or a locate
    re-arms them without firing, so a cue fires again when the clock next passes its time.
    """

    def __init__(self) -> None:
        self._items: list[tuple[int, object]] = []
        self._last: int | None = None

    def set_items(self, items: list[tuple[int, object]]) -> None:
        """items: (trigger ms, key). Keeps the clock position so nothing re-fires."""
        self._items = sorted(items, key=lambda item: item[0])

    def advance(self, position_ms: int) -> list[object]:
        fired: list[object] = []
        if self._last is not None and position_ms > self._last:
            fired = [key for ms, key in self._items if self._last < ms <= position_ms]
        self._last = position_ms
        return fired


class TimecodeInput:
    """Listens on a MIDI input port and reports each position to on_position(ms, fps)."""

    def __init__(self, port_name: str, on_position) -> None:
        import mido

        self._decoder = QuarterFrameDecoder()
        self._on_position = on_position
        self._port = mido.open_input(port_name, callback=self._callback)

    def _callback(self, message) -> None:
        result = self._decoder.feed(bytes(message.bytes()))
        if result is not None:
            self._on_position(*result)

    def close(self) -> None:
        self._port.close()
