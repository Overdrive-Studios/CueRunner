"""A cue cart: a grid of cues with no playhead and no order.

A cart is for firing things as and when you want them, as many times as you like, rather
than working down a list. Clicking a cue selects it; the button in its corner, or GO,
starts it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QPushButton, QSizePolicy, QWidget

from cuerunner.model import Cue

CELL_SIZES = {"small": 64, "medium": 84, "large": 110}


class CartView(QWidget):
    """The grid for one cart. Rows and columns come from the cart itself."""

    selected = Signal(int)  # the index of the cue now selected, or -1
    triggered = Signal(int)  # start the cue at this index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._cues: list[Cue] = []
        self._selected = -1
        self._cell = CELL_SIZES["medium"]
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(6)
        self._columns, self._rows = 8, 4
        self._build()

    # --- building ------------------------------------------------------
    def _build(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # not setParent(None): that would make it a window of its own
                widget.deleteLater()
        self._buttons = []
        for slot in range(self._columns * self._rows):
            button = QPushButton("")
            button.setObjectName("cartCell")
            button.setMinimumSize(self._cell, self._cell)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=slot: self._clicked(i))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._grid.addWidget(button, slot // self._columns, slot % self._columns)
            self._buttons.append(button)
        self._paint()

    def set_shape(self, columns: int, rows: int) -> None:
        columns, rows = max(1, int(columns)), max(1, int(rows))
        if (columns, rows) != (self._columns, self._rows):
            self._columns, self._rows = columns, rows
            self._build()

    def set_cell_size(self, name: str) -> None:
        self._cell = CELL_SIZES.get(name, CELL_SIZES["medium"])
        for button in self._buttons:
            button.setMinimumSize(self._cell, self._cell)

    # --- contents ------------------------------------------------------
    def set_cues(self, cues: list) -> None:
        self._cues = list(cues)
        if self._selected >= len(self._cues):
            self._selected = len(self._cues) - 1
        self._paint()

    def _paint(self) -> None:
        for slot, button in enumerate(self._buttons):
            if slot < len(self._cues):
                cue = self._cues[slot]
                button.setText(f"{cue.number}\n{cue.name}")
                button.setToolTip(cue.name)
                button.setEnabled(True)
                button.setProperty("filled", True)
            else:
                button.setText("")
                button.setToolTip("")
                button.setEnabled(False)
                button.setProperty("filled", False)
            button.setChecked(slot == self._selected)
            button.style().unpolish(button)
            button.style().polish(button)

    # --- interaction ---------------------------------------------------
    def _clicked(self, slot: int) -> None:
        if slot >= len(self._cues):
            self._buttons[slot].setChecked(False)
            return
        self._selected = slot
        self._paint()
        self.selected.emit(slot)

    def selected_index(self) -> int:
        return self._selected if 0 <= self._selected < len(self._cues) else -1

    def select(self, index: int) -> None:
        self._selected = index if 0 <= index < len(self._cues) else -1
        self._paint()

    def trigger_selected(self) -> None:
        """GO in a cart starts whatever is selected; there is no playhead to advance."""
        if self.selected_index() >= 0:
            self.triggered.emit(self._selected)

    def capacity(self) -> int:
        return self._columns * self._rows
