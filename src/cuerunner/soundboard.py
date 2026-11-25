"""Soundboard: 64 pads across 4 banks of 16. Pads play instantly, separate from the cue list."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cuerunner.model import BANK_SIZE, Pad


class Soundboard(QWidget):
    padPressed = Signal(int)  # absolute pad index, 0..63
    padAssign = Signal(int)
    padClear = Signal(int)
    padRouting = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pads: list[Pad] = []
        self._bank = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("PADS"))
        self.bank_box = QComboBox()
        self.bank_box.addItems([f"Bank {letter}" for letter in "ABCD"])
        self.bank_box.currentIndexChanged.connect(self._on_bank)
        top.addWidget(self.bank_box)
        top.addStretch(1)
        layout.addLayout(top)

        grid = QGridLayout()
        self.buttons: list[QPushButton] = []
        for i in range(BANK_SIZE):
            button = QPushButton()
            button.setMinimumHeight(48)
            button.clicked.connect(lambda _checked=False, i=i: self.padPressed.emit(self._index(i)))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, i=i, b=button: self._menu(i, b, pos))
            grid.addWidget(button, i // 8, i % 8)
            self.buttons.append(button)
        layout.addLayout(grid)

    def _index(self, slot: int) -> int:
        return self._bank * BANK_SIZE + slot

    def set_pads(self, pads: list[Pad]) -> None:
        self._pads = pads
        self.refresh()

    def refresh(self) -> None:
        for slot, button in enumerate(self.buttons):
            pad = self._pads[self._index(slot)] if self._pads else Pad()
            button.setText(pad.name or "-")
            button.setToolTip(pad.file or "Right-click to assign a file")

    def _on_bank(self, index: int) -> None:
        self._bank = index
        self.refresh()

    def _menu(self, slot: int, button: QPushButton, pos) -> None:
        menu = QMenu(self)
        index = self._index(slot)
        menu.addAction("Assign file...", lambda: self.padAssign.emit(index))
        menu.addAction("Routing...", lambda: self.padRouting.emit(index))
        menu.addAction("Clear", lambda: self.padClear.emit(index))
        menu.exec(button.mapToGlobal(pos))
