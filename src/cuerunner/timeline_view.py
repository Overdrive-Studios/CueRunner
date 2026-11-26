"""The Timeline tab: a Timeline Group cue's children laid out on a time ruler.

A Timeline Group starts every child at once, so what separates them is each child's
pre-wait. That makes the group a timeline, and this is the editor for it: one row per
child, each drawn as a bar that begins at its pre-wait and runs for as long as it plays.

Dragging a bar changes that child's pre-wait. Dragging either end trims the cue.
Holding Alt over an audio or video cue slips the file inside the bar without moving it.
Dragging the bottom edge scales the row, which is purely how it looks.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from cuerunner.model import Cue

RULER = 18  # the time scale across the top, in pixels
ROW = 26  # a row at scale 1.0
ROW_GAP = 4
EDGE = 6  # how close to an end counts as a trim grab, in pixels
BOTTOM = 4  # how close to the bottom counts as a scale grab
SNAP_PIXELS = 8  # a guideline this near pulls the drag onto it
MIN_SPAN = 10.0  # seconds shown even when everything happens at once
MIN_BAR = 3.0  # pixels, so a cue with no duration is still visible and grabbable
SCALE_RANGE = (0.6, 3.0)
NUDGE = 0.1
FINE_NUDGE = 0.01

KIND_BADGES = {
    "audio": "A", "video": "V", "camera": "C", "group": "G", "midi": "M",
    "osc": "O", "timecode": "T", "light": "L", "network": "N", "text": "X",
    "fade": "F", "devamp": "D", "target": "→", "mic": "µ",
    "spotify": "S", "console": "K", "script": "P", "wait": "W",
}


def cue_length(cue: Cue) -> float:
    """How long the cue plays for, in seconds, at its rate.

    Zero for cues that fire and are done (MIDI, OSC and the rest); they draw as a mark.
    """
    end = cue.trim_out if cue.trim_out > 0 else (cue.duration_ms or 0) / 1000
    span = max(end - cue.trim_in, 0.0)
    rate = cue.rate or 1.0
    return span / rate if rate > 0 else span


def cue_start(cue: Cue) -> float:
    """Where the cue begins on the group's timeline: its pre-wait."""
    return max(cue.pre_wait_ms, 0) / 1000


def cue_end(cue: Cue) -> float:
    return cue_start(cue) + cue_length(cue)


def slice_times(cue: Cue) -> list[float]:
    """Each slice point as a time on the group's timeline, not a time in the file."""
    rate = cue.rate or 1.0
    start = cue_start(cue)
    length = cue_length(cue)
    out = []
    for point in cue.slices:
        try:
            offset = (float(point) - cue.trim_in) / (rate if rate > 0 else 1.0)
        except (TypeError, ValueError):
            continue
        if 0 < offset < length:
            out.append(round(start + offset, 6))
    return sorted(out)


class TimelineView(QWidget):
    """One Timeline Group's children, drawn on a shared time ruler and editable."""

    about_to_edit = Signal()  # a drag or nudge is about to change a cue, for undo
    changed = Signal()  # a cue's timing was edited
    selected = Signal(int)  # the row in the view that is now selected, or -1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cues: list[Cue] = []
        self._playhead = 0.0
        self._editable = False
        self._selection: list[int] = []
        self._scales: dict[str, float] = {}
        self._drag: dict | None = None
        self._guides: list[float] = []
        self._focus_row = -1
        self.setMinimumHeight(RULER + ROW + ROW_GAP * 2)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --- contents ------------------------------------------------------
    def set_cues(self, cues: list[Cue]) -> None:
        self._cues = list(cues)
        self._selection = [i for i in self._selection if i < len(self._cues)]
        self.setMinimumHeight(int(RULER + ROW_GAP + self._stack_height()))
        self.updateGeometry()
        self.update()

    def cues(self) -> list[Cue]:
        return list(self._cues)

    def set_playhead(self, seconds: float) -> None:
        self._playhead = max(seconds, 0.0)
        self.update()

    def playhead(self) -> float:
        return self._playhead

    def set_editable(self, editable: bool) -> None:
        self._editable = bool(editable)
        if not editable:
            self._drag = None
            self._guides = []
        self.update()

    def selection(self) -> list[int]:
        return list(self._selection)

    def select(self, rows: list[int]) -> None:
        self._selection = [r for r in rows if 0 <= r < len(self._cues)]
        self.update()

    def guidelines(self) -> list[float]:
        """The snap lines showing during a drag; empty when nothing is being dragged."""
        return list(self._guides)

    def is_dragging(self) -> bool:
        return self._drag is not None

    def scale_of(self, row: int) -> float:
        if not 0 <= row < len(self._cues):
            return 1.0
        return self._scales.get(self._cues[row].number, 1.0)

    # --- geometry ------------------------------------------------------
    def span(self) -> float:
        """The seconds the view covers: everything in the group plus a little room."""
        latest = 0.0
        for cue in self._cues:
            latest = max(latest, cue_end(cue))
        latest = max(latest, self._playhead)
        return max(latest * 1.1, MIN_SPAN)

    def _x_for(self, seconds: float) -> float:
        return (seconds / self.span()) * max(self.width() - 1, 1)

    def _seconds_for(self, x: float) -> float:
        fraction = x / max(self.width() - 1, 1)
        return round(fraction * self.span(), 4)

    def _row_height(self, row: int) -> float:
        return ROW * self.scale_of(row)

    def _stack_height(self) -> float:
        total = 0.0
        for row in range(len(self._cues)):
            total += self._row_height(row) + ROW_GAP
        return total or ROW + ROW_GAP

    def row_rect(self, row: int) -> QRectF:
        """Where this cue's row sits, full width, ruler excluded."""
        top = RULER + ROW_GAP
        for index in range(row):
            top += self._row_height(index) + ROW_GAP
        return QRectF(0, top, self.width(), self._row_height(row))

    def bar_rect(self, row: int) -> QRectF:
        """Where this cue's bar sits inside its row."""
        if not 0 <= row < len(self._cues):
            return QRectF()
        cue = self._cues[row]
        band = self.row_rect(row)
        left = self._x_for(cue_start(cue))
        width = max(self._x_for(cue_end(cue)) - left, MIN_BAR)
        return QRectF(left, band.top(), width, band.height())

    def row_at(self, y: float) -> int:
        for row in range(len(self._cues)):
            band = self.row_rect(row)
            if band.top() - ROW_GAP / 2 <= y <= band.bottom() + ROW_GAP / 2:
                return row
        return -1

    # --- what the pointer is over ---------------------------------------
    def zone_at(self, x: float, y: float, slip: bool = False) -> tuple[str, int]:
        """What a press here would do: move, trim an end, slip the file, or scale the row."""
        row = self.row_at(y)
        if row < 0:
            return ("", -1)
        bar = self.bar_rect(row)
        if not (bar.left() - EDGE <= x <= bar.right() + EDGE):
            return ("", row)
        cue = self._cues[row]
        if y >= bar.bottom() - BOTTOM:
            return ("scale", row)
        if slip and cue.kind in ("audio", "video") and not cue.slices:
            return ("slip", row)
        if cue_length(cue) > 0:
            if abs(x - bar.left()) <= EDGE:
                return ("trim_start", row)
            if abs(x - bar.right()) <= EDGE:
                return ("trim_end", row)
        return ("move", row)

    def _cursor_for(self, zone: str) -> Qt.CursorShape:
        return {
            "move": Qt.CursorShape.OpenHandCursor,
            "trim_start": Qt.CursorShape.SizeHorCursor,
            "trim_end": Qt.CursorShape.SizeHorCursor,
            "slip": Qt.CursorShape.SizeHorCursor,
            "scale": Qt.CursorShape.SizeVerCursor,
        }.get(zone, Qt.CursorShape.ArrowCursor)

    # --- snapping -------------------------------------------------------
    def snap_times(self, moving: int, focus: int = -1) -> list[float]:
        """Every time a dragged cue should snap to: the other cues, their slices, the playhead.

        focus is the lane the pointer is over. Dragging over one cue's lane narrows the
        guidelines down to that cue, which is how QLab lets you line two cues up exactly.
        """
        times = [0.0, self._playhead]
        for row, cue in enumerate(self._cues):
            if row == moving:
                continue
            if focus >= 0 and row != focus:
                continue
            times.append(cue_start(cue))
            if cue_length(cue) > 0:
                times.append(cue_end(cue))
            times.extend(slice_times(cue))
        return sorted({round(t, 6) for t in times if t >= 0})

    def _snapped(self, seconds: float, times: list[float]) -> float:
        """Pull a time onto the nearest guideline, if one is within the snap distance."""
        if not times:
            return seconds
        window = SNAP_PIXELS * self.span() / max(self.width() - 1, 1)
        best = min(times, key=lambda t: abs(t - seconds))
        return best if abs(best - seconds) <= window else seconds

    # --- mouse ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        pos = event.position()
        slip = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        zone, row = self.zone_at(pos.x(), pos.y(), slip=slip)
        if row < 0:
            self._selection = []
            self.selected.emit(-1)
            self.update()
            return
        adding = bool(event.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                           | Qt.KeyboardModifier.ControlModifier))
        if adding and not slip:
            if row in self._selection:
                self._selection.remove(row)
            else:
                self._selection.append(row)
        else:
            self._selection = [row]
        self.selected.emit(row)
        if zone and self._editable:
            cue = self._cues[row]
            self._drag = {
                "zone": zone, "row": row, "x": pos.x(), "y": pos.y(),
                "pre": cue.pre_wait_ms / 1000, "trim_in": cue.trim_in,
                "trim_out": cue.trim_out if cue.trim_out > 0 else cue_length(cue) + cue.trim_in,
                "scale": self.scale_of(row), "moved": False,
            }
            self._focus_row = -1
            self._guides = self.snap_times(row)
            if zone != "scale":  # scaling a row changes nothing about the show
                self.about_to_edit.emit()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        pos = event.position()
        slip = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self._drag is None:
            self.setCursor(self._cursor_for(self.zone_at(pos.x(), pos.y(), slip=slip)[0]))
            return
        free = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        zone = self._drag["zone"]
        if zone in ("move", "trim_start", "trim_end", "slip"):
            lane = self.row_at(pos.y())
            focus = lane if lane >= 0 and lane != self._drag["row"] else -1
            if focus != self._focus_row:
                self._focus_row = focus
                self._guides = self.snap_times(self._drag["row"], focus)
        self._apply_drag(pos, free)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        moved = bool(self._drag and self._drag.get("moved"))
        self._drag = None
        self._guides = []
        self._focus_row = -1
        if moved:
            self.changed.emit()
        self.update()

    def _apply_drag(self, pos: QPointF, free: bool) -> None:
        drag = self._drag
        row = drag["row"]
        cue = self._cues[row]
        delta = self._seconds_for(pos.x()) - self._seconds_for(drag["x"])
        times = [] if free else self._guides
        zone = drag["zone"]

        if zone == "scale":
            step = (pos.y() - drag["y"]) / ROW
            scale = min(max(drag["scale"] + step, SCALE_RANGE[0]), SCALE_RANGE[1])
            self._scales[cue.number] = round(scale, 3)
            self.setMinimumHeight(int(RULER + ROW_GAP + self._stack_height()))
            drag["moved"] = False  # purely visual, so nothing to undo and nothing to save
            return

        if zone == "move":
            start = self._snapped(max(drag["pre"] + delta, 0.0), times)
            cue.pre_wait_ms = int(round(max(start, 0.0) * 1000))
        elif zone == "trim_start":
            # The start of the file and the pre-wait move together, so the end stays put.
            start = self._snapped(max(drag["pre"] + delta, 0.0), times)
            shift = start - drag["pre"]
            trim_in = max(drag["trim_in"] + shift, 0.0)
            if trim_in < drag["trim_out"] - 0.01:
                cue.pre_wait_ms = int(round(start * 1000))
                cue.trim_in = round(trim_in, 3)
        elif zone == "trim_end":
            # Snap where the end lands on the timeline, then turn that back into a file time.
            rate = cue.rate or 1.0
            was = drag["pre"] + (drag["trim_out"] - drag["trim_in"]) / rate
            lands = self._snapped(max(was + delta, drag["pre"]), times)
            wanted = drag["trim_in"] + (lands - drag["pre"]) * rate
            limit = (cue.duration_ms or 0) / 1000
            if limit:
                wanted = min(wanted, limit)
            cue.trim_out = round(max(wanted, cue.trim_in + 0.01), 3)
        elif zone == "slip":
            # The bar stays where it is; the file slides inside it.
            length = drag["trim_out"] - drag["trim_in"]
            limit = (cue.duration_ms or 0) / 1000
            trim_in = max(drag["trim_in"] + delta * (cue.rate or 1.0), 0.0)
            if limit:
                trim_in = min(trim_in, max(limit - length, 0.0))
            cue.trim_in = round(trim_in, 3)
            cue.trim_out = round(trim_in + length, 3)
        drag["moved"] = True

    # --- keyboard -------------------------------------------------------
    def nudge(self, seconds: float) -> bool:
        """Shift every selected cue's pre-wait, the way the arrow shortcuts do."""
        if not self._editable or not self._selection:
            return False
        self.about_to_edit.emit()
        for row in self._selection:
            cue = self._cues[row]
            start = max(cue.pre_wait_ms / 1000 + seconds, 0.0)
            cue.pre_wait_ms = int(round(start * 1000))
        self.changed.emit()
        self.update()
        return True

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier and event.key() in (
                Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = FINE_NUDGE if mods & Qt.KeyboardModifier.ShiftModifier else NUDGE
            if event.key() == Qt.Key.Key_Left:
                step = -step
            if self.nudge(step):
                event.accept()
                return
        super().keyPressEvent(event)

    # --- painting -------------------------------------------------------
    def tick_seconds(self) -> float:
        rough = self.span() / max(self.width() / 90, 1)
        for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if step >= rough:
                return float(step)
        return 3600.0

    @staticmethod
    def label_for(seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}:{secs:02d}"

    def _paint_ruler(self, painter: QPainter) -> None:
        painter.fillRect(0, 0, self.width(), RULER, QColor("#26262a"))
        step = self.tick_seconds()
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        mark = 0.0
        while mark <= self.span():
            x = self._x_for(mark)
            painter.setPen(QPen(QColor("#5a5a60"), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, RULER))
            painter.setPen(QColor("#9a9aa0"))
            painter.drawText(QPointF(x + 3, RULER - 4), self.label_for(mark))
            mark += step

    def _paint_bar(self, painter: QPainter, row: int) -> None:
        cue = self._cues[row]
        bar = self.bar_rect(row)
        chosen = QColor(cue.colour) if cue.colour else QColor()
        base = chosen if chosen.isValid() else QColor("#3d7ea6")
        if not cue.armed:
            base = base.darker(170)
        painter.setPen(QPen(base.lighter(140), 1))
        painter.setBrush(base)
        painter.drawRoundedRect(bar, 3, 3)

        if row in self._selection:
            painter.setPen(QPen(QColor("#f4d35e"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bar.adjusted(1, 1, -1, -1), 3, 3)

        self._paint_slices(painter, row, bar)
        self._paint_fades(painter, row, bar)
        self._paint_label(painter, row, bar)

    def _paint_slices(self, painter: QPainter, row: int, bar: QRectF) -> None:
        painter.setPen(QPen(QColor("#e8e8ec"), 1, Qt.PenStyle.DashLine))
        for when in slice_times(self._cues[row]):
            x = self._x_for(when)
            if bar.left() < x < bar.right():
                painter.drawLine(QPointF(x, bar.top() + 2), QPointF(x, bar.bottom() - 2))

    def _paint_fades(self, painter: QPainter, row: int, bar: QRectF) -> None:
        """The fades drawn as the shape they really are: the part faded down is shaded off.

        Shading rather than a line keeps the cue's name readable underneath, and shows at
        a glance how much of the cue is still coming up.
        """
        cue = self._cues[row]
        smooth = cue.integrated_fade and cue.fade_curve == "smooth"
        shade = QColor(18, 18, 20, 150)
        for seconds, rising in ((cue.fade_in, True), (cue.fade_out, False)):
            if seconds <= 0:
                continue
            width = min(self._x_for(seconds) - self._x_for(0), bar.width())
            if width < 2:
                continue
            x0 = bar.left() if rising else bar.right() - width
            top, bottom = bar.top() + 1, bar.bottom() - 1
            steps = 16
            points = []
            for step in range(steps + 1):
                part = step / steps
                level = part if rising else 1 - part
                if smooth:
                    level = level * level * (3 - 2 * level)
                points.append(QPointF(x0 + part * width, bottom - level * (bottom - top)))
            wedge = QPainterPath()
            wedge.moveTo(points[0])
            for point in points[1:]:
                wedge.lineTo(point)
            wedge.lineTo(x0 + width, top)
            wedge.lineTo(x0, top)
            wedge.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(wedge, shade)
            painter.setPen(QPen(QColor("#f0f0f4"), 1))
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)

    def _paint_label(self, painter: QPainter, row: int, bar: QRectF) -> None:
        cue = self._cues[row]
        badge = KIND_BADGES.get(cue.kind, cue.kind[:1].upper())
        text = f"{badge}  {cue.number}  {cue.name}".rstrip()
        metrics = QFontMetrics(painter.font())
        # Start the name past the fade-in where there is room, so the two do not overlap.
        lead = 6.0
        if cue.fade_in > 0:
            room = bar.width() - metrics.horizontalAdvance(text) - 12
            lead = max(lead, min(self._x_for(cue.fade_in) - self._x_for(0), max(room, 0)))
        inside = bar.width() - lead - 6 >= metrics.horizontalAdvance(text)
        painter.setPen(QColor("#f2f2f5") if inside else QColor("#b9b9c0"))
        if inside:
            painter.drawText(bar.adjusted(lead, 0, -6, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        else:
            room = QRectF(bar.right() + 6, bar.top(), max(self.width() - bar.right() - 8, 0),
                          bar.height())
            painter.drawText(room, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             text)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2e2e32"))
        self._paint_ruler(painter)
        if not self._cues:
            painter.setPen(QColor("#8a8a90"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "This group has no cues in it yet.")
            return

        painter.setPen(QPen(QColor("#38383d"), 1))
        for row in range(len(self._cues)):
            band = self.row_rect(row)
            painter.fillRect(band, QColor("#2a2a2e"))
        for row in range(len(self._cues)):
            self._paint_bar(painter, row)

        for when in self._guides:
            x = self._x_for(when)
            painter.setPen(QPen(QColor("#4aa3ff"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, RULER), QPointF(x, self.height()))

        x = self._x_for(self._playhead)
        painter.setPen(QPen(QColor("#e6c700"), 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
