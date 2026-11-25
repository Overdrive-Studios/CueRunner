"""Spotify sign-in and playback controls. Network calls run on worker threads so the window stays responsive."""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from cuerunner import spotify as sp

TOKEN_PATH = Path.home() / ".cuerunner" / "spotify.json"


class _Bus(QObject):
    done = Signal(object, object)


def run_async(parent: QObject, work: Callable[[], object], callback: Callable[[object, object], None]) -> None:
    """Run work() on a thread, then call callback(result, error) on the UI thread."""
    bus = _Bus(parent)

    def finish(result, error) -> None:
        bus.deleteLater()
        callback(result, error)

    bus.done.connect(finish, Qt.ConnectionType.QueuedConnection)

    def run() -> None:
        try:
            result, error = work(), None
        except Exception as exc:  # reported to the UI below
            result, error = None, exc
        bus.done.emit(result, error)

    threading.Thread(target=run, daemon=True).start()


def load_client(client_id: str) -> sp.SpotifyClient | None:
    """A ready client if this computer has signed in before with this Client ID."""
    tokens = sp.load_tokens(TOKEN_PATH)
    if tokens is None or not client_id:
        return None
    return sp.SpotifyClient(client_id, tokens, TOKEN_PATH)


class SpotifyDialog(QDialog):
    def __init__(self, client_id: str, on_client: Callable[[sp.SpotifyClient], None], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Spotify")
        self.resize(460, 380)
        self._on_client = on_client
        self._client: sp.SpotifyClient | None = load_client(client_id)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Spotify needs a Premium account and the Spotify app open on the computer or speaker you want to use. "
            "Create a Client ID at developer.spotify.com with redirect URI http://127.0.0.1:8888/callback."
        ))
        form = QGridLayout()
        self.client_id = QLineEdit(client_id)
        self.client_id.setPlaceholderText("Spotify Client ID")
        connect = QPushButton("Connect to Spotify")
        connect.clicked.connect(self._connect)
        form.addWidget(QLabel("Client ID"), 0, 0)
        form.addWidget(self.client_id, 0, 1)
        form.addWidget(connect, 1, 1)
        layout.addLayout(form)

        device_row = QHBoxLayout()
        self.devices = QComboBox()
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._refresh_devices)
        device_row.addWidget(QLabel("Device"))
        device_row.addWidget(self.devices, 1)
        device_row.addWidget(refresh)
        layout.addLayout(device_row)

        self.uri = QLineEdit()
        self.uri.setPlaceholderText("Paste a Spotify link or spotify: URI (track, album, playlist)")
        play = QPushButton("Play")
        play.clicked.connect(self._play)
        layout.addWidget(self.uri)
        layout.addWidget(play)

        controls = QHBoxLayout()
        for label, action in [
            ("Previous", lambda c: c.previous()),
            ("Pause", lambda c: c.pause()),
            ("Resume", lambda c: c.resume(self._device_id())),
            ("Next", lambda c: c.next()),
        ]:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, a=action: self._action(a))
            controls.addWidget(button)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        if self._client is not None:
            self._on_client(self._client)
            self.status.setText("Signed in. Refresh devices to see your Spotify devices.")

    def _device_id(self) -> str | None:
        data = self.devices.currentData()
        return data or None

    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _connect(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id:
            QMessageBox.warning(self, "Spotify", "Enter the Client ID from your Spotify developer app first.")
            return
        verifier = sp.make_verifier()
        state = sp.make_verifier()[:24]
        webbrowser.open(sp.authorize_url(client_id, sp.challenge_for(verifier), state))
        self._say("Waiting for you to approve access in the browser...")

        def work():
            code = sp.wait_for_callback(state)
            tokens = sp.exchange_code(client_id, code, verifier, time.time())
            sp.save_tokens(TOKEN_PATH, tokens)
            return tokens

        def done(tokens, error) -> None:
            if error is not None:
                self._say(str(error))
                return
            self._client = sp.SpotifyClient(client_id, tokens, TOKEN_PATH)
            self._on_client(self._client)
            self._say("Connected to Spotify. Refresh devices to see your Spotify devices.")

        run_async(self, work, done)

    def _require_client(self) -> sp.SpotifyClient | None:
        if self._client is None:
            QMessageBox.information(self, "Spotify", "Connect to Spotify first.")
            return None
        return self._client

    def _refresh_devices(self) -> None:
        client = self._require_client()
        if client is None:
            return

        def done(devices, error) -> None:
            if error is not None:
                self._say(str(error))
                return
            self.devices.clear()
            self.devices.addItem("Active device", None)
            for device in devices or []:
                self.devices.addItem(device.get("name", "Device"), device.get("id"))
            self._say(f"Found {len(devices or [])} device(s).")

        run_async(self, client.devices, done)

    def _play(self) -> None:
        client = self._require_client()
        if client is None:
            return
        uri = sp.uri_from_text(self.uri.text())
        if uri is None:
            QMessageBox.warning(self, "Spotify", "That doesn't look like a Spotify link or URI.")
            return
        device = self._device_id()
        run_async(self, lambda: client.play(uri, device), self._done_action("Playing."))

    def _action(self, action) -> None:
        client = self._require_client()
        if client is None:
            return
        run_async(self, lambda: action(client), self._done_action(""))

    def _done_action(self, ok_text: str):
        def done(_result, error) -> None:
            self._say(str(error) if error is not None else ok_text)

        return done


def send(parent: QObject, client: sp.SpotifyClient | None, label: str, action: Callable[[sp.SpotifyClient], object]) -> None:
    """Run one playback action for a cue or the transport. Warns on the UI thread if it fails."""
    if client is None:
        QMessageBox.warning(parent, "Spotify", "Connect to Spotify first (Audio → Spotify).")
        return

    def done(_result, error) -> None:
        if error is not None:
            QMessageBox.warning(parent, "Spotify", f"{label}\n\n{error}")

    run_async(parent, lambda: action(client), done)
