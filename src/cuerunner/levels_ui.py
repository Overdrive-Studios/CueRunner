"""The fader strip on the inspector's Levels tab.

One fader for the cue's own level ("main") and one per output channel, each with mute
and solo, as QLab lays them out. Kept apart from main.py so it can be tested on its own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

MIN_DB = -60
MAX_DB = 12


class OutputLevels(QWidget):
    """Faders for the cue level and each output. Values are dB; -60 is silence."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._strips: list[dict] = []
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(14)
        self._row.addStretch(1)
        self.set_outputs(2)

    # --- building ------------------------------------------------------
    def _make_strip(self, caption: str, with_buttons: bool) -> dict:
        column = QVBoxLayout()
        column.setSpacing(2)
        slider = QSlider(Qt.Orientation.Vertical)
        slider.setRange(MIN_DB, MAX_DB)
        slider.setValue(0)
        slider.setMinimumHeight(70)
        readout = QLabel("0")
        readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(caption)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons = QHBoxLayout()
        mute = solo = None
        if with_buttons:
            mute, solo = QPushButton("M"), QPushButton("S")
            for button in (mute, solo):
                button.setCheckable(True)
                button.setFixedWidth(26)
                button.toggled.connect(self._emit)
                buttons.addWidget(button)
        column.addWidget(slider, 1, Qt.AlignmentFlag.AlignHCenter)
        column.addLayout(buttons)
        column.addWidget(readout)
        column.addWidget(name)
        slider.valueChanged.connect(self._emit)
        self._row.insertLayout(self._row.count() - 1, column)
        return {"slider": slider, "readout": readout, "mute": mute, "solo": solo, "column": column}

    def set_outputs(self, count: int) -> None:
        """Rebuild the strip for a given number of outputs, keeping the main fader first."""
        count = max(0, int(count))
        if self._strips and len(self._strips) - 1 == count:
            return
        self._busy = True
        for strip in self._strips:
            _clear(strip["column"])
            self._row.removeItem(strip["column"])
        self._strips = [self._make_strip("main", False)]
        for index in range(count):
            self._strips.append(self._make_strip(str(index + 1), True))
        self._busy = False

    def output_count(self) -> int:
        return max(0, len(self._strips) - 1)

    # --- values --------------------------------------------------------
    def set_values(self, main_db: float, levels: list, mutes: list, solos: list) -> None:
        self._busy = True
        try:
            self._strips[0]["slider"].setValue(_clamp(main_db))
            for index, strip in enumerate(self._strips[1:]):
                strip["slider"].setValue(_clamp(levels[index] if index < len(levels) else 0.0))
                strip["mute"].setChecked(index < len(mutes) and bool(mutes[index]))
                strip["solo"].setChecked(index < len(solos) and bool(solos[index]))
            self._refresh_readouts()
        finally:
            self._busy = False

    def values(self) -> tuple[float, list[float], list[bool], list[bool]]:
        main = float(self._strips[0]["slider"].value())
        levels = [float(s["slider"].value()) for s in self._strips[1:]]
        mutes = [bool(s["mute"].isChecked()) for s in self._strips[1:]]
        solos = [bool(s["solo"].isChecked()) for s in self._strips[1:]]
        return main, levels, mutes, solos

    def set_all(self, db: float) -> None:
        """Set Default Levels (0 dB) and Set All Silent Levels (-60) both come through here."""
        for strip in self._strips:
            strip["slider"].setValue(_clamp(db))

    def clear_mutes(self) -> None:
        for strip in self._strips[1:]:
            strip["mute"].setChecked(False)

    def clear_solos(self) -> None:
        for strip in self._strips[1:]:
            strip["solo"].setChecked(False)

    def set_editable(self, editable: bool) -> None:
        for strip in self._strips:
            strip["slider"].setEnabled(editable)
            for key in ("mute", "solo"):
                if strip[key] is not None:
                    strip[key].setEnabled(editable)

    # --- internals -----------------------------------------------------
    def _refresh_readouts(self) -> None:
        for strip in self._strips:
            value = strip["slider"].value()
            strip["readout"].setText("-∞" if value <= MIN_DB else str(value))

    def _emit(self) -> None:
        self._refresh_readouts()
        if not self._busy:
            self.changed.emit()


def _clamp(db: float) -> int:
    return int(min(max(round(float(db)), MIN_DB), MAX_DB))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
        elif item.layout() is not None:
            _clear(item.layout())
