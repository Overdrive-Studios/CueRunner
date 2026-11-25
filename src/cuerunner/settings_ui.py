"""Settings page: general options, device and routing shortcuts, Allen & Heath console and Spotify client ID."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cuerunner.console_ui import FAMILIES
from cuerunner.model import Show

REMOTE_PORT_RANGE = (1024, 65535)

# Shortcuts on the Devices tab: (button label, key the main window passes in).
DEVICE_ACTIONS = [
    ("Output device...", "output"),
    ("Talkback mic input...", "mic"),
    ("Recording input...", "record_input"),
    ("Venue radio settings...", "radio_settings"),
    ("Venue radio routing...", "radio_routing"),
    ("MIDI mapping...", "midi_mapping"),
]


def settings_from(show: Show) -> dict:
    return {
        "remote_port": show.remote_port,
        "duck_percent": round(show.duck_level * 100),
        "console_host": show.console_host,
        "console_family": show.console_family,
        "spotify_client_id": show.spotify_client_id,
    }


def apply_settings(show: Show, values: dict) -> None:
    low, high = REMOTE_PORT_RANGE
    show.remote_port = min(high, max(low, int(values["remote_port"])))
    show.duck_level = min(100, max(0, int(values["duck_percent"]))) / 100
    show.console_host = str(values["console_host"]).strip()
    show.console_family = values["console_family"]
    show.spotify_client_id = str(values["spotify_client_id"]).strip()


class SettingsDialog(QDialog):
    def __init__(self, show: Show, parent=None, actions: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 360)
        current = settings_from(show)
        actions = actions or {}

        tabs = QTabWidget()

        general = QWidget()
        general_form = QFormLayout(general)
        self.remote_port = QSpinBox()
        self.remote_port.setRange(*REMOTE_PORT_RANGE)
        self.remote_port.setValue(current["remote_port"])
        general_form.addRow("Phone remote port", self.remote_port)
        general_form.addRow(QLabel("Takes effect the next time the phone remote starts."))
        self.duck = QSpinBox()
        self.duck.setRange(0, 100)
        self.duck.setSuffix(" %")
        self.duck.setValue(current["duck_percent"])
        general_form.addRow("Music level while talkback is open", self.duck)
        tabs.addTab(general, "General")

        devices = QWidget()
        devices_layout = QVBoxLayout(devices)
        devices_layout.addWidget(QLabel("Each button opens its own window with the details."))
        for label, key in DEVICE_ACTIONS:
            if key in actions:
                button = QPushButton(label)
                button.clicked.connect(actions[key])
                devices_layout.addWidget(button)
        devices_layout.addStretch(1)
        tabs.addTab(devices, "Devices")

        console = QWidget()
        console_form = QFormLayout(console)
        self.console_host = QLineEdit(current["console_host"])
        self.console_host.setPlaceholderText("192.168.1.70")
        self.console_family = QComboBox()
        for label, value in FAMILIES:
            self.console_family.addItem(label, value)
        index = next((i for i, (_, value) in enumerate(FAMILIES) if value == current["console_family"]), 0)
        self.console_family.setCurrentIndex(index)
        console_form.addRow("Console address", self.console_host)
        console_form.addRow("Console type", self.console_family)
        tabs.addTab(console, "Console")

        spotify = QWidget()
        spotify_form = QFormLayout(spotify)
        self.spotify_client_id = QLineEdit(current["spotify_client_id"])
        spotify_form.addRow("Spotify client ID", self.spotify_client_id)
        spotify_form.addRow(QLabel("Sign in from Audio > Spotify after saving."))
        tabs.addTab(spotify, "Spotify")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "remote_port": self.remote_port.value(),
            "duck_percent": self.duck.value(),
            "console_host": self.console_host.text(),
            "console_family": self.console_family.currentData(),
            "spotify_client_id": self.spotify_client_id.text(),
        }
