"""Audio engine: loads clips, mixes voices with fades, loops, ducking and channel routing.

Pure numpy, no audio device, so it can be tested without hardware. audio_io.py connects
the mixer to a sound card.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Clip:
    data: np.ndarray  # shape (frames, channels), float32, at the mixer's sample rate

    @property
    def frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def channels(self) -> int:
        return int(self.data.shape[1])


def resample(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resampling. Good enough for playback; not a studio-grade resampler."""
    if source_rate == target_rate or len(data) == 0:
        return data.astype(np.float32)
    count = max(1, round(len(data) * target_rate / source_rate))
    old_x = np.arange(len(data))
    new_x = np.linspace(0, len(data) - 1, count)
    channels = [np.interp(new_x, old_x, data[:, c]) for c in range(data.shape[1])]
    return np.stack(channels, axis=1).astype(np.float32)


def load_clip(path: str | Path, rate: int) -> Clip:
    """Decode a whole file into memory at the mixer's sample rate."""
    import soundfile as sf

    data, source_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return Clip(np.ascontiguousarray(resample(data, int(source_rate), rate)))


def probe_duration_ms(path: str | Path) -> int:
    """Length of a file in milliseconds, read from its header without decoding it."""
    import soundfile as sf

    info = sf.info(str(path))
    return round(info.frames * 1000 / info.samplerate)


def route_matrix(route: list[list[int]], clip_channels: int, out_channels: int) -> np.ndarray:
    """Matrix from a cue's route: route[i] is the list of 1-based outputs that clip channel i goes to.

    An empty route uses the default. Outputs beyond the device's count are ignored. Clip channels
    with no entry in route stay silent.
    """
    if not route:
        return default_matrix(clip_channels, out_channels)
    matrix = np.zeros((clip_channels, out_channels), dtype=np.float32)
    for i in range(min(clip_channels, len(route))):
        for out in route[i]:
            if 1 <= out <= out_channels:
                matrix[i, out - 1] = 1.0
    return matrix


def parse_route(text: str, out_channels: int) -> list[list[int]]:
    """Parse "1 2" (clip 1 to output 1, clip 2 to output 2) or "1+2 3" (clip 1 to outputs 1 and 2).

    Empty text means the default routing. Raises ValueError with a readable message on bad input.
    """
    text = text.strip()
    if not text:
        return []
    route: list[list[int]] = []
    for group in text.split():
        outputs = []
        for part in group.split("+"):
            if not part.isdigit() or not 1 <= int(part) <= out_channels:
                raise ValueError(f"Output '{part}' must be a number from 1 to {out_channels}.")
            outputs.append(int(part))
        route.append(outputs)
    return route


def format_route(route: list[list[int]]) -> str:
    return " ".join("+".join(str(o) for o in group) for group in route)


def default_matrix(clip_channels: int, out_channels: int) -> np.ndarray:
    """Routing from clip channels to output channels.

    Stereo clips map left and right to outputs 1 and 2. A mono clip goes to both of them.
    Anything beyond that can be routed by changing voice.matrix.
    """
    matrix = np.zeros((clip_channels, out_channels), dtype=np.float32)
    if clip_channels == 1:
        matrix[0, : min(2, out_channels)] = 1.0
    else:
        for i in range(min(clip_channels, out_channels)):
            matrix[i, i] = 1.0
    return matrix


@dataclass(eq=False)
class Voice:
    """One playing sound: a clip plus its position, gain, fades and routing."""

    clip: Clip
    matrix: np.ndarray
    loop: bool = False
    bed: bool = False  # bed voices are lowered while ducking is active
    gain: float = 1.0
    tag: Any = None  # for the UI, e.g. the Cue this voice is playing
    position: int = 0
    env: float = 1.0
    done: bool = False
    natural_end: bool = False  # reached the end of the clip without being stopped
    followed: bool = False
    stop_when_done: bool = False
    paused: bool = False  # a paused voice keeps its place and makes no sound until resumed
    _fade_start: float = 1.0
    _fade_target: float = 1.0
    _fade_total: int = 0
    _fade_done: int = 0
    _fade_complete: bool = False

    def fade(self, target: float, seconds: float, rate: int, stop_when_done: bool = False) -> None:
        self._fade_start = self.env
        self._fade_target = target
        self._fade_total = max(0, round(seconds * rate))
        self._fade_done = 0
        self._fade_complete = False
        self.stop_when_done = stop_when_done
        if self._fade_total == 0:
            self.env = target
            self._fade_complete = True

    def envelope(self, n: int) -> np.ndarray:
        if self._fade_complete or self._fade_total == 0:
            return np.full(n, self.env, dtype=np.float32)
        steps = np.minimum(np.arange(1, n + 1) + self._fade_done, self._fade_total) / self._fade_total
        env = self._fade_start + (self._fade_target - self._fade_start) * steps
        self._fade_done = min(self._fade_total, self._fade_done + n)
        self.env = float(env[-1])
        if self._fade_done >= self._fade_total:
            self._fade_complete = True
            self.env = self._fade_target
        return env.astype(np.float32)

    def read(self, n: int) -> np.ndarray | None:
        length = self.clip.frames
        if length == 0 or (not self.loop and self.position >= length):
            return None
        index = self.position + np.arange(n)
        if self.loop:
            index %= length
            self.position = (self.position + n) % length
        else:
            index = index[index < length]
            self.position += len(index)
            if self.position >= length:
                self.natural_end = True
        return self.clip.data[index]

    def should_end(self) -> bool:
        if self.stop_when_done and self._fade_complete:
            return True
        return not self.loop and self.position >= self.clip.frames


class Mixer:
    def __init__(self, rate: int = 48000, out_channels: int = 2) -> None:
        self.rate = rate
        self.out_channels = out_channels
        self.paused = False
        self._voices: list[Voice] = []
        self._lock = threading.Lock()
        self._duck_target = 1.0
        self._duck_current = 1.0

    def add(self, voice: Voice) -> None:
        with self._lock:
            self._voices.append(voice)

    def voices(self) -> list[Voice]:
        with self._lock:
            return list(self._voices)

    def fade_out(self, voice: Voice, seconds: float) -> None:
        with self._lock:
            voice.fade(0.0, seconds, self.rate, stop_when_done=True)

    def clear(self) -> None:
        """Stop every voice immediately, with no fade."""
        with self._lock:
            for voice in self._voices:
                voice.done = True
            self._voices = []

    def set_ducked(self, ducked: bool, level: float = 0.3) -> None:
        """Lower bed voices to `level` (linear gain) while ducked, and restore them afterwards."""
        self._duck_target = level if ducked else 1.0

    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, self.out_channels), dtype=np.float32)
        with self._lock:
            if self.paused:
                return out
            self._duck_current += (self._duck_target - self._duck_current) * 0.2
            for voice in self._voices:
                if voice.done or voice.paused:
                    continue
                chunk = voice.read(frames)
                if chunk is not None:
                    env = voice.envelope(len(chunk))
                    gain = voice.gain * (self._duck_current if voice.bed else 1.0)
                    out[: len(chunk)] += (chunk * (env * gain)[:, None]) @ voice.matrix
                if voice.should_end():
                    voice.done = True
            self._voices = [v for v in self._voices if not v.done]
        np.clip(out, -1.0, 1.0, out=out)
        return out


class LiveVoice(Voice):
    """A voice fed by live audio (talkback mic) instead of a decoded clip.

    Blocks are pushed in from the input thread and read out by the mixer. If the output falls
    behind, the oldest audio is dropped so latency cannot grow without limit.
    """

    MAX_BACKLOG_S = 0.2

    def __init__(self, channels: int, matrix: np.ndarray, rate: int, tag=None) -> None:
        super().__init__(clip=Clip(np.zeros((0, channels), dtype=np.float32)), matrix=matrix, tag=tag)
        self._rate = rate
        self._blocks: deque[np.ndarray] = deque()
        self._pending = np.zeros((0, channels), dtype=np.float32)
        self._lock = threading.Lock()

    def push(self, block: np.ndarray) -> None:
        with self._lock:
            self._blocks.append(np.asarray(block, dtype=np.float32).reshape(len(block), -1))

    def read(self, n: int) -> np.ndarray:
        with self._lock:
            while self._blocks:
                self._pending = np.concatenate([self._pending, self._blocks.popleft()])
            limit = int(self.MAX_BACKLOG_S * self._rate)
            if len(self._pending) > limit + n:
                self._pending = self._pending[-limit:]
            take = self._pending[:n]
            self._pending = self._pending[n:]
        if len(take) < n:
            take = np.concatenate([take, np.zeros((n - len(take), self.clip.channels), dtype=np.float32)])
        return take

    def should_end(self) -> bool:
        return self.done
