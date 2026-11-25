"""Waveform view for the inspector's Time & Loops tab.

Peaks are read straight from the audio file and cached, so selecting the same cue again
is instant. The view draws the loop region and lets the loop points be dragged.

The peak reader is plain numpy and soundfile, so it can be tested without a screen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
        self.setMinimumHeight(86)
        self.RULER = 14  # pixels along the top for the time scale
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
    def tick_seconds(self) -> float:
        """A round spacing for the time scale that keeps the labels readable."""
        if self._duration <= 0:
            return 0.0
        rough = self._duration / max(self.width() / 90, 1)
        for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if step >= rough:
                return float(step)
        return 3600.0

    @staticmethod
    def label_for(seconds: float) -> str:
        """0:00 style, as the scale above the waveform shows it."""
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}:{secs:02d}"

    def _paint_ruler(self, painter: QPainter) -> None:
        painter.fillRect(0, 0, self.width(), self.RULER, QColor("#26262a"))
        step = self.tick_seconds()
        if step <= 0:
            return
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        mark = 0.0
        while mark <= self._duration:
            x = self._x_for(mark)
            painter.setPen(QPen(QColor("#5a5a60"), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.RULER))
            painter.setPen(QColor("#9a9aa0"))
            painter.drawText(QPointF(x + 3, self.RULER - 3), self.label_for(mark))
            mark += step

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2e2e32"))
        middle = self.RULER + (self.height() - self.RULER) / 2
        if self._message:
            painter.setPen(QColor("#8a8a90"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        self._paint_ruler(painter)

        # The part of the file the loop covers, behind the wave.
        start_x, end_x = self._x_for(self._loop_in), self._x_for(self._out_seconds())
        if end_x > start_x:
            painter.fillRect(int(start_x), self.RULER, int(end_x - start_x),
                             self.height() - self.RULER, QColor("#36507a"))

        painter.setPen(QPen(QColor("#8ab4f8"), 1))
        count = len(self._peaks)
        width = max(self.width(), 1)
        reach = (self.height() - self.RULER) / 2 - 2
        for x in range(width):
            low, high = self._peaks[min(int(x / width * count), count - 1)]
            painter.drawLine(QPointF(x, middle - float(high) * reach),
                             QPointF(x, middle - float(low) * reach))

        painter.setPen(QPen(QColor("#5a5a60"), 1))
        painter.drawLine(QPointF(0, middle), QPointF(width, middle))
        for x in (start_x, end_x):
            painter.setPen(QPen(QColor("#f2c14e"), 2))
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))

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
