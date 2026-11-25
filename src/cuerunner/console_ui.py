"""Allen & Heath console control: a dialog for quick commands, and sending helpers for console cues."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from cuerunner import allenheath as ah
from cuerunner.spotify_ui import run_async

FAMILIES = [("SQ (standard)", "sq"), ("dLive", "dlive")]


class ConsoleDialog(QDialog):
    def __init__(self, host: str, family: str, get_sender, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Allen & Heath Console")
        self.resize(460, 300)
        self._get_sender = get_sender
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Sends MIDI over TCP. Use the same base MIDI channel as the console (Utility > General > MIDI on SQ, Utility > Control > MIDI on dLive)."
        ))
        form = QGridLayout()
        self.host = QLineEdit(host)
        self.host.setPlaceholderText("Console IP address, e.g. 192.168.1.50")
        self.family = QComboBox()
        for label, value in FAMILIES:
            self.family.addItem(label, value)
        self.family.setCurrentIndex(max(0, [v for _, v in FAMILIES].index(family)))
        self.family.currentIndexChanged.connect(self._family_changed)
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.channel.setValue(1)
        form.addWidget(QLabel("Console"), 0, 0)
        form.addWidget(self.family, 0, 1)
        form.addWidget(QLabel("Console IP"), 1, 0)
        form.addWidget(self.host, 1, 1)
        form.addWidget(QLabel("MIDI channel"), 2, 0)
        form.addWidget(self.channel, 2, 1)
        layout.addLayout(form)

        self.scene = QSpinBox()
        recall = QPushButton("Recall scene")
        recall.clicked.connect(lambda: self._send(self._build("scene", self.scene.value())))
        row = QGridLayout()
        row.addWidget(QLabel("Scene"), 0, 0)
        row.addWidget(self.scene, 0, 1)
        row.addWidget(recall, 0, 2)

        self.input_no = QSpinBox()
        self.input_no.setRange(1, 128)
        self.mute = QCheckBox("Mute")
        apply_mute = QPushButton("Apply mute")
        apply_mute.clicked.connect(lambda: self._send(self._build("mute", self.input_no.value(), self.mute.isChecked())))
        row.addWidget(QLabel("Input"), 1, 0)
        row.addWidget(self.input_no, 1, 1)
        row.addWidget(self.mute, 1, 2)
        row.addWidget(apply_mute, 1, 3)

        self.level = QDoubleSpinBox()
        apply_level = QPushButton("Apply level")
        apply_level.clicked.connect(lambda: self._send(self._build("level", self.input_no.value(), self.level.value())))
        self.level_label = QLabel("Level")
        row.addWidget(self.level_label, 2, 0)
        row.addWidget(self.level, 2, 1)
        row.addWidget(apply_level, 2, 2)
        layout.addLayout(row)
        self._family_changed()

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def current_family(self) -> str:
        return self.family.currentData()

    def _family_changed(self) -> None:
        dlive = self.current_family() == "dlive"
        self.scene.setRange(1, 500 if dlive else 300)
        self.channel.setRange(1, 12 if dlive else 16)
        self.channel.setValue(min(self.channel.value(), 12) if dlive else self.channel.value())
        self.input_no.setRange(1, 128 if dlive else 48)
        if dlive:
            self.level_label.setText("Fader (raw 0-127)")
            self.level.setRange(0, 127)
            self.level.setDecimals(0)
            self.level.setSuffix("")
        else:
            self.level_label.setText("Level")
            self.level.setRange(-90, 10)
            self.level.setDecimals(1)
            self.level.setSuffix(" dB")

    def _build(self, kind: str, *args) -> bytes:
        channel = self.channel.value()
        if self.current_family() == "dlive":
            if kind == "scene":
                return ah.dlive_scene_recall(args[0], channel)
            if kind == "mute":
                return ah.dlive_mute(args[0], args[1], channel)
            return ah.dlive_fader_raw(args[0], int(args[1]), channel)
        if kind == "scene":
            return ah.sq_scene_recall(args[0], channel)
        if kind == "mute":
            return ah.sq_mute(args[0], args[1], channel)
        return ah.sq_level_db(args[0], args[1], channel)

    def _send(self, data: bytes) -> None:
        sender = self._get_sender(self.host.text().strip(), self.current_family())
        if sender is None:
            self.status.setText("Enter the console's IP address first.")
            return
        self.status.setText("Sending...")

        def done(_result, error) -> None:
            self.status.setText(f"Could not reach the console: {error}" if error is not None else "Sent.")

        run_async(self, lambda: sender.send(data), done)
