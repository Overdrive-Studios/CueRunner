"""Waveform view for the inspector's Time & Loops tab.

Peaks are read straight from the audio file and cached, so selecting the same cue again
is instant. The view draws the loop region and lets the loop points be dragged.

The peak reader is plain numpy and soundfile, so it can be tested without a screen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

BUCKETS = 700  # peak pairs across the width; more than any sensible window has pixels
_CACHE: dict[tuple[str, float, int], np.ndarray] = {}
CACHE_LIMIT = 24


def peaks(path: str | Path, buckets: int = BUCKETS) -> np.ndarray:
    """Min and max of the mixed-down file per bucket, shape (buckets, 2), in -1 to 1.

    Returns an empty array for an unreadable or empty file; the caller shows nothing.
    """
    import soundfile as sf

    name = str(path)
    try:
        info = sf.info(name)
    except Exception:
        return np.zeros((0, 2), dtype=np.float32)
    if info.frames <= 0 or buckets <= 0:
        return np.zeros((0, 2), dtype=np.float32)

    key = (name, Path(name).stat().st_mtime if Path(name).exists() else 0.0, buckets)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    per = max(1, info.frames // buckets)
    rows = []
    try:
        with sf.SoundFile(name) as handle:
            while len(rows) < buckets:
                block = handle.read(per, dtype="float32", always_2d=True)
                if len(block) == 0:
                    break
                mono = block.mean(axis=1)
                rows.append((float(mono.min()), float(mono.max())))
    except Exception:
        return np.zeros((0, 2), dtype=np.float32)

    result = np.array(rows, dtype=np.float32) if rows else np.zeros((0, 2), dtype=np.float32)
    if len(_CACHE) >= CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def clear_cache() -> None:
    _CACHE.clear()


class WaveformView(QWidget):
    """Draws a file's waveform with its loop in and out points, which can be dragged.

    markersChanged carries the loop in and out in seconds. An out point of 0 means the
    end of the file, which is how cues store it.
    """

    markersChanged = Signal(float, float)
    GRAB_PIXELS = 6  # how close a click must be to grab a marker

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._peaks = np.zeros((0, 2), dtype=np.float32)
        self._duration = 0.0
        self._loop_in = 0.0
        self._loop_out = 0.0
        self._message = "No audio file"
        self._dragging = ""  # "in", "out" or "" while nothing is being dragged

    def set_clip(self, path: str, duration_s: float) -> None:
        self._peaks = peaks(path) if path else np.zeros((0, 2), dtype=np.float32)
        self._duration = max(0.0, duration_s)
        if not path:
            self._message = "No audio file"
        elif len(self._peaks) == 0:
            self._message = "No waveform for this file"
        else:
            self._message = ""
        self.update()

    def set_markers(self, loop_in: float, loop_out: float) -> None:
        self._loop_in, self._loop_out = loop_in, loop_out
        self.update()

    def markers(self) -> tuple[float, float]:
        return self._loop_in, self._loop_out

    def is_dragging(self) -> bool:
        """True while a marker is being dragged, so a refresh does not fight the mouse."""
        return bool(self._dragging)

    def has_waveform(self) -> bool:
        return len(self._peaks) > 0

    # --- geometry -------------------------------------------------------
    def _out_seconds(self) -> float:
        """The loop out as a real time: 0 means the end of the file."""
        return self._loop_out if self._loop_out > 0 else self._duration

    def _x_for(self, seconds: float) -> float:
        if self._duration <= 0:
            return 0.0
        fraction = min(max(seconds / self._duration, 0.0), 1.0)
        return fraction * max(self.width() - 1, 1)

    def _seconds_for(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        fraction = min(max(x / max(self.width() - 1, 1), 0.0), 1.0)
        return round(fraction * self._duration, 3)

    # --- painting -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2e2e32"))
        middle = self.height() / 2
        if self._message:
            painter.setPen(QColor("#8a8a90"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return

        # The part of the file the loop covers, behind the wave.
        start_x, end_x = self._x_for(self._loop_in), self._x_for(self._out_seconds())
        if end_x > start_x:
            painter.fillRect(int(start_x), 0, int(end_x - start_x), self.height(), QColor("#36507a"))

        painter.setPen(QPen(QColor("#8ab4f8"), 1))
        count = len(self._peaks)
        width = max(self.width(), 1)
        for x in range(width):
            low, high = self._peaks[min(int(x / width * count), count - 1)]
            top = middle - float(high) * (middle - 2)
            bottom = middle - float(low) * (middle - 2)
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))

        painter.setPen(QPen(QColor("#5a5a60"), 1))
        painter.drawLine(QPointF(0, middle), QPointF(width, middle))
        for x in (start_x, end_x):
            painter.setPen(QPen(QColor("#f2c14e"), 2))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    # --- dragging -------------------------------------------------------
    def _grab(self, x: float) -> str:
        if abs(x - self._x_for(self._loop_in)) <= self.GRAB_PIXELS:
            return "in"
        if abs(x - self._x_for(self._out_seconds())) <= self.GRAB_PIXELS:
            return "out"
        return ""

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.isEnabled() or not self.has_waveform():
            return
        x = event.position().x()
        # Clicking away from both markers moves the nearer one, so a loop can be set in one go.
        self._dragging = self._grab(x) or (
            "in" if abs(x - self._x_for(self._loop_in)) <= abs(x - self._x_for(self._out_seconds()))
            else "out"
        )
        self._move_to(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._dragging:
            self._move_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._dragging = ""

    def _move_to(self, x: float) -> None:
        seconds = self._seconds_for(x)
        if self._dragging == "in":
            self._loop_in = min(seconds, max(self._out_seconds() - 0.001, 0.0))
        elif self._dragging == "out":
            # Dropping the out marker at the very end stores 0, meaning "to the end of the file".
            self._loop_out = 0.0 if seconds >= self._duration else max(seconds, self._loop_in + 0.001)
        else:
            return
        self.update()
        self.markersChanged.emit(self._loop_in, self._loop_out)
