"""The parameter editor on the inspector's Audio FX tab.

Each effect declares the settings it has, so this builds a row of controls to match
whichever effect is selected rather than knowing about any of them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QLabel, QWidget

from cuerunner import fx

COLUMNS = 3  # pairs of label and control across the panel, so tall effects stay short


class EffectParams(QWidget):
    """Controls for one effect's settings. Emits changed when the user moves one."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._kind = None
        self._spins: dict[str, QDoubleSpinBox] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        self._empty = QLabel("Add an effect to set it up.")
        self._grid.addWidget(self._empty, 0, 0, 1, COLUMNS * 2)

    def show_effect(self, effect: dict | None) -> None:
        """Build controls for this effect, or clear them when nothing is selected."""
        kind = None if effect is None else str(effect.get("kind", ""))
        if kind != self._kind:
            self._build(kind)
        self._load(effect)

    def _build(self, kind: str | None) -> None:
        self._busy = True
        try:
            while self._grid.count():
                item = self._grid.takeAt(0)
                if item.widget() is not None:
                    item.widget().setParent(None)
            self._spins = {}
            self._kind = kind
            spec = fx.EFFECTS.get(kind or "")
            if spec is None:
                self._empty = QLabel("Add an effect to set it up.")
                self._grid.addWidget(self._empty, 0, 0, 1, COLUMNS * 2)
                return
            for index, param in enumerate(spec.params):
                row, column = divmod(index, COLUMNS)
                spin = QDoubleSpinBox()
                spin.setRange(param.low, param.high)
                spin.setDecimals(param.decimals)
                spin.setSuffix(param.suffix)
                spin.setSingleStep(_step(param))
                spin.setMaximumWidth(130)
                spin.valueChanged.connect(self._emit)
                self._spins[param.key] = spin
                self._grid.addWidget(QLabel(f"{param.label}:"), row, column * 2,
                                     Qt.AlignmentFlag.AlignRight)
                self._grid.addWidget(spin, row, column * 2 + 1)
            self._grid.setColumnStretch(COLUMNS * 2 - 1, 1)
        finally:
            self._busy = False

    def _load(self, effect: dict | None) -> None:
        if effect is None:
            return
        spec = fx.EFFECTS.get(str(effect.get("kind", "")))
        if spec is None:
            return
        self._busy = True
        try:
            for param in spec.params:
                spin = self._spins.get(param.key)
                if spin is not None:
                    spin.setValue(float(effect.get(param.key, param.default)))
        finally:
            self._busy = False

    def values(self) -> dict:
        """What the controls currently say, ready to write back onto the effect."""
        return {key: spin.value() for key, spin in self._spins.items()}

    def set_editable(self, editable: bool) -> None:
        for spin in self._spins.values():
            spin.setEnabled(editable)

    def _emit(self) -> None:
        if not self._busy:
            self.changed.emit()


def _step(param) -> float:
    """A step size that suits the range, so dragging a control feels right."""
    span = param.high - param.low
    if param.decimals == 0:
        return max(1.0, round(span / 200))
    return 0.1 if span <= 100 else 1.0
