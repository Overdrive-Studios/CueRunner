"""Multitrack recorder: writes WAV files that stay readable if power is cut.

Blocks are queued from the audio thread and written from the UI thread. The file header is
refreshed with flush() about once a second, so a crash loses at most that last second.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import soundfile as sf

MAX_TRACKS = 48
FLUSH_EVERY_S = 1.0


class Recorder:
    def __init__(self, path: str | Path, rate: int, tracks: int) -> None:
        if not 1 <= tracks <= MAX_TRACKS:
            raise ValueError(f"Track count must be between 1 and {MAX_TRACKS}.")
        self.path = Path(path)
        self.tracks = tracks
        self.rate = rate
        self.frames_written = 0
        self._file = sf.SoundFile(
            str(self.path), mode="w", samplerate=rate, channels=tracks, subtype="PCM_24", format="WAV"
        )
        self._queue: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._closed = False

    def push(self, block: np.ndarray) -> None:
        """Called from the audio thread. Only queues the block; no disk I/O here."""
        self._queue.append(np.asarray(block, dtype=np.float32).reshape(len(block), self.tracks))

    def drain(self) -> None:
        """Called from the UI thread. Writes queued blocks and refreshes the header."""
        with self._lock:
            if self._closed:
                return
            while self._queue:
                block = self._queue.popleft()
                self._file.write(block)
                self.frames_written += len(block)
            if time.monotonic() - self._last_flush >= FLUSH_EVERY_S:
                self._file.flush()
                self._last_flush = time.monotonic()

    def close(self) -> None:
        self.drain()
        with self._lock:
            if not self._closed:
                self._file.close()
                self._closed = True

    @property
    def seconds(self) -> float:
        return self.frames_written / self.rate
