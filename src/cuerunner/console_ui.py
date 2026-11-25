"""Allen & Heath console control: a dialog for quick commands, and sending helpers for console cues."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
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


class ConsoleDialog(QDialog):
    def __init__(self, host: str, get_sender, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Allen & Heath Console")
        self.resize(420, 260)
        self._get_sender = get_sender
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Standard SQ over MIDI over TCP. Set the console's IP address and use the same MIDI channel as Utility > General > MIDI."
        ))
        form = QGridLayout()
        self.host = QLineEdit(host)
        self.host.setPlaceholderText("Console IP address, e.g. 192.168.1.50")
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.channel.setValue(1)
        form.addWidget(QLabel("Console IP"), 0, 0)
        form.addWidget(self.host, 0, 1)
        form.addWidget(QLabel("MIDI channel"), 1, 0)
        form.addWidget(self.channel, 1, 1)
        layout.addLayout(form)

        self.scene = QSpinBox()
        self.scene.setRange(1, 300)
        recall = QPushButton("Recall scene")
        recall.clicked.connect(lambda: self._send(ah.sq_scene_recall(self.scene.value(), self.channel.value())))
        row = QGridLayout()
        row.addWidget(QLabel("Scene"), 0, 0)
        row.addWidget(self.scene, 0, 1)
        row.addWidget(recall, 0, 2)

        self.input_no = QSpinBox()
        self.input_no.setRange(1, 48)
        self.mute = QCheckBox("Mute")
        apply_mute = QPushButton("Apply mute")
        apply_mute.clicked.connect(lambda: self._send(ah.sq_mute(self.input_no.value(), self.mute.isChecked(), self.channel.value())))
        row.addWidget(QLabel("Input"), 1, 0)
        row.addWidget(self.input_no, 1, 1)
        row.addWidget(self.mute, 1, 2)
        row.addWidget(apply_mute, 1, 3)

        self.level = QDoubleSpinBox()
        self.level.setRange(-90, 10)
        self.level.setSuffix(" dB")
        apply_level = QPushButton("Apply level")
        apply_level.clicked.connect(lambda: self._send(ah.sq_level_db(self.input_no.value(), self.level.value(), self.channel.value())))
        row.addWidget(QLabel("Level"), 2, 0)
        row.addWidget(self.level, 2, 1)
        row.addWidget(apply_level, 2, 2)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _send(self, data: bytes) -> None:
        sender = self._get_sender(self.host.text().strip())
        if sender is None:
            self.status.setText("Enter the console's IP address first.")
            return
        self.status.setText("Sending...")

        def done(_result, error) -> None:
            self.status.setText(f"Could not reach the console: {error}" if error is not None else "Sent.")

        run_async(self, lambda: sender.send(data), done)
