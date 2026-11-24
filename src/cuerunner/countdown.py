"""Countdown timer for interval breaks."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from cuerunner.timecode import format_mmss


class Countdown(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._remaining = 0

        self.display = QLabel(format_mmss(0))
        self.display.setObjectName("statusValue")
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 999)
        self.minutes.setSuffix(" min")
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        self.seconds.setSuffix(" s")
        self.seconds.setValue(30)
        self.minutes.valueChanged.connect(self._reset_from_inputs)
        self.seconds.valueChanged.connect(self._reset_from_inputs)

        start = QPushButton("Start / Pause")
        start.clicked.connect(self.toggle)
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        buttons = QHBoxLayout()
        buttons.addWidget(start)
        buttons.addWidget(reset)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("COUNTDOWN"))
        layout.addWidget(self.display)
        inputs = QHBoxLayout()
        inputs.addWidget(self.minutes)
        inputs.addWidget(self.seconds)
        layout.addLayout(inputs)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self._reset_from_inputs()

    def _start_seconds(self) -> int:
        return self.minutes.value() * 60 + self.seconds.value()

    def _reset_from_inputs(self) -> None:
        self.timer.stop()
        self._remaining = self._start_seconds()
        self.display.setText(format_mmss(self._remaining))

    def toggle(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
        elif self._remaining > 0:
            self.timer.start()

    def reset(self) -> None:
        self._reset_from_inputs()

    def _tick(self) -> None:
        if self._remaining > 0:
            self._remaining -= 1
        self.display.setText(format_mmss(self._remaining))
        if self._remaining == 0:
            self.timer.stop()
