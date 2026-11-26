"""Waveform view for the inspector's Time & Loops tab.

Peaks are read straight from the audio file and cached, so selecting the same cue again
is instant. The view draws the loop region and lets the loop points be dragged.

The peak reader is plain numpy and soundfile, so it can be tested without a screen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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
    rangeChanged = Signal(float, float)  # the start and end time, in seconds
    seeked = Signal(float)  # a click in the body of the waveform, in seconds
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
        self._start = 0.0
        self._end = 0.0  # 0 means the end of the file, which is how cues store it
        self._message = "No audio file"
        self._dragging = ""  # which marker is being dragged, "" while none is
        self._slices: list[float] = []
        self._playhead = 0.0  # where the cue is up to, or where it was last clicked

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

    def set_playhead(self, seconds: float) -> None:
        if abs(seconds - self._playhead) < 0.0005:
            return
        self._playhead = max(0.0, float(seconds))
        self.update()

    def playhead(self) -> float:
        return self._playhead

    @staticmethod
    def time_label(seconds: float) -> str:
        """mm:ss.mmm, as QLab writes the position above the waveform."""
        minutes, rest = divmod(max(0.0, float(seconds)), 60)
        return f"{int(minutes):02d}:{rest:06.3f}"

    def set_slices(self, slices: list) -> None:
        self._slices = [float(x) for x in (slices or [])]
        self.update()

    def set_markers(self, loop_in: float, loop_out: float) -> None:
        self._loop_in, self._loop_out = loop_in, loop_out
        self.update()

    def markers(self) -> tuple[float, float]:
        return self._loop_in, self._loop_out

    def set_range(self, start: float, end: float) -> None:
        """Where the cue starts and ends in the file. An end of 0 means the file's end."""
        self._start, self._end = max(0.0, float(start)), max(0.0, float(end))
        self.update()

    def range(self) -> tuple[float, float]:
        return self._start, self._end

    def is_dragging(self) -> bool:
        """True while a marker is being dragged, so a refresh does not fight the mouse."""
        return bool(self._dragging)

    def has_waveform(self) -> bool:
        return len(self._peaks) > 0

    def is_usable(self) -> bool:
        """True once the length is known, even if the file's wave cannot be drawn.

        A video file, or an audio format the wave reader cannot open, still gets its scale
        and its markers, so the start and end can be set by dragging either way.
        """
        return self._duration > 0

    # --- geometry -------------------------------------------------------
    def _out_seconds(self) -> float:
        """The loop out as a real time: 0 means the end of the file."""
        return self._loop_out if self._loop_out > 0 else self._duration

    def _end_seconds(self) -> float:
        """The cue's end as a real time: 0 means the end of the file."""
        return self._end if self._end > 0 else self._duration

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
        if not self.is_usable():
            painter.setPen(QColor("#8a8a90"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        self._paint_ruler(painter)

        # The part of the file the loop covers, behind the wave.
        start_x, end_x = self._x_for(self._loop_in), self._x_for(self._out_seconds())
        if end_x > start_x:
            painter.fillRect(int(start_x), self.RULER, int(end_x - start_x),
                             self.height() - self.RULER, QColor("#36507a"))

        width = max(self.width(), 1)
        if self.has_waveform():
            painter.setPen(QPen(QColor("#8ab4f8"), 1))
            count = len(self._peaks)
            reach = (self.height() - self.RULER) / 2 - 2
            for x in range(width):
                low, high = self._peaks[min(int(x / width * count), count - 1)]
                painter.drawLine(QPointF(x, middle - float(high) * reach),
                                 QPointF(x, middle - float(low) * reach))
        else:
            # No wave to draw, but the scale and the markers are still worth having.
            painter.setPen(QColor("#6a6a72"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)

        painter.setPen(QPen(QColor("#5a5a60"), 1))
        painter.drawLine(QPointF(0, middle), QPointF(width, middle))

        # Everything before the start and after the end is shaded off: it does not play.
        begin_x, finish_x = self._x_for(self._start), self._x_for(self._end_seconds())
        shade = QColor(18, 18, 20, 140)
        if begin_x > 0:
            painter.fillRect(0, self.RULER, int(begin_x), self.height() - self.RULER, shade)
        if finish_x < width:
            painter.fillRect(int(finish_x), self.RULER, int(width - finish_x),
                             self.height() - self.RULER, shade)
        for mark in self._slices:
            painter.setPen(QPen(QColor("#c9d1d9"), 1, Qt.PenStyle.DashLine))
            x = self._x_for(mark)
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
        for x in (start_x, end_x):
            painter.setPen(QPen(QColor("#f2c14e"), 2))
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
        # The start and end of the cue, with a handle at the top to grab them by.
        for x in (begin_x, finish_x):
            painter.setPen(QPen(QColor("#58c470"), 2))
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, self.height()))
            painter.setBrush(QColor("#58c470"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(x - 3, self.RULER, 6, 6))

        # Where the cue is up to, with the time written on the scale above it.
        head_x = self._x_for(self._playhead)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawLine(QPointF(head_x, self.RULER), QPointF(head_x, self.height()))
        text = self.time_label(self._playhead)
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        box_width = painter.fontMetrics().horizontalAdvance(text) + 6
        box_x = min(max(head_x - box_width / 2, 0), max(width - box_width, 0))
        painter.fillRect(int(box_x), 0, int(box_width), self.RULER, QColor("#d8c24a"))
        painter.setPen(QColor("#1d1d20"))
        painter.drawText(QPointF(box_x + 3, self.RULER - 3), text)

    # --- dragging -------------------------------------------------------
    def _grab(self, x: float) -> str:
        """Which marker a press at this point takes hold of, nearest first."""
        near = [
            ("start", abs(x - self._x_for(self._start))),
            ("end", abs(x - self._x_for(self._end_seconds()))),
            ("in", abs(x - self._x_for(self._loop_in))),
            ("out", abs(x - self._x_for(self._out_seconds()))),
        ]
        name, distance = min(near, key=lambda pair: pair[1])
        return name if distance <= self.GRAB_PIXELS else ""

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.isEnabled() or not self.is_usable():
            return
        x = event.position().x()
        grabbed = self._grab(x)
        if grabbed:
            self._dragging = grabbed
            self._move_to(x)
            return
        # Anywhere else moves the playhead to that point in the file, as QLab does.
        self._dragging = ""
        self.set_playhead(self._seconds_for(x))
        self.seeked.emit(self._playhead)

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
        elif self._dragging == "start":
            self._start = min(seconds, max(self._end_seconds() - 0.001, 0.0))
            self._playhead = self._start  # the cue will begin here, so show it there
            self.update()
            self.rangeChanged.emit(self._start, self._end)
            return
        elif self._dragging == "end":
            # At the very end this stores 0, meaning "play to the end of the file".
            self._end = 0.0 if seconds >= self._duration else max(seconds, self._start + 0.001)
            self.update()
            self.rangeChanged.emit(self._start, self._end)
            return
        else:
            return
        self.update()
        self.markersChanged.emit(self._loop_in, self._loop_out)
