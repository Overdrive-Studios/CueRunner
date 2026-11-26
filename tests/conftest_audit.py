"""Shared rig for the audit tests: a real mixer, real audio, and every modal answered."""

from __future__ import annotations

import contextlib
import sys
import traceback
from unittest import mock

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import (QDialog, QFileDialog, QInputDialog, QMessageBox)

from cuerunner.engine import Mixer
from cuerunner.main import MainWindow
from cuerunner.model import Cue, CueList

RATE = 48000

# One cue of every kind CueRunner can make, so no handler goes unvisited.
KINDS = ["audio", "video", "camera", "group", "memo", "midi", "osc", "light", "network",
         "text", "fade", "devamp", "target", "timecode", "mic", "control", "wait",
         "spotify", "console", "midifile", "program"]


def write_tones(folder):
    mono = folder / "mono.wav"
    stereo = folder / "stereo.wav"
    seconds = np.arange(RATE * 4) / RATE
    sf.write(mono, (0.4 * np.sin(2 * np.pi * 220 * seconds)).astype("float32"), RATE)
    sf.write(stereo, np.stack([0.4 * np.sin(2 * np.pi * 220 * seconds),
                               0.4 * np.sin(2 * np.pi * 330 * seconds)],
                              axis=1).astype("float32"), RATE)
    return str(mono), str(stereo)


class _StubVideoOutput:
    """QAudioOutput needs a sound device, which a test machine may not have."""

    def __init__(self, screen) -> None:
        self.widget = mock.Mock()
        self.player = mock.Mock()
        self.cue_number = ""
        self.screen = screen

    def show_text(self, *args, **kwargs) -> None: ...
    def hide_text(self) -> None: ...
    def show_on_screen(self, *args, **kwargs) -> None: ...
    def stop_camera(self) -> None: ...


@contextlib.contextmanager
def quiet_modals():
    """Answer every dialog instead of showing it, so a headless run never blocks."""
    from cuerunner import video_out

    ok = QMessageBox.StandardButton.Ok
    with mock.patch.object(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))), \
         mock.patch.object(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))), \
         mock.patch.object(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], ""))), \
         mock.patch.object(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "")), \
         mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: ok)), \
         mock.patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: ok)), \
         mock.patch.object(QMessageBox, "critical", staticmethod(lambda *a, **k: ok)), \
         mock.patch.object(QMessageBox, "question",
                           staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)), \
         mock.patch.object(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))), \
         mock.patch.object(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False))), \
         mock.patch.object(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False))), \
         mock.patch.object(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected), \
         mock.patch.object(video_out, "_Output", _StubVideoOutput):
        yield


def build_window(mono: str, stereo: str) -> MainWindow:
    """A window with a working mixer and a cue of every kind, but no sound card."""
    def fake_output(self):
        self.mixer = Mixer(rate=RATE, out_channels=2)
        self.output = None
        self._clips = {}
        self._cue_voices = []

    with mock.patch.object(MainWindow, "_open_default_output", fake_output):
        window = MainWindow()
    for index, kind in enumerate(KINDS, start=1):
        window.show_model.cues.append(Cue(
            number=str(index), name=f"{kind} cue", kind=kind,
            file=(mono if index % 2 else stereo) if kind in ("audio", "video") else "",
        ))
    window.show_model.cue_lists.append(CueList(name="Cart", kind="cart"))
    window._editing = True
    window._refresh_table()
    return window


class Faults:
    """Collects what went wrong instead of letting the first one stop the run.

    An exception inside a Qt slot aborts the process rather than propagating, so the hook
    is installed as well as the try/except around each call.
    """

    def __init__(self) -> None:
        self.found: list[str] = []
        self.where = "start"
        self._previous = None

    def __enter__(self):
        self._previous = sys.excepthook
        sys.excepthook = self._hook
        return self

    def __exit__(self, *exc):
        sys.excepthook = self._previous
        return False

    def _hook(self, kind, value, tb):
        self.record(kind, value, tb)

    def record(self, kind, value, tb) -> None:
        lines = traceback.format_exception(kind, value, tb)
        self.found.append(f"{self.where}\n{''.join(lines).strip()}")

    def run(self, where: str, call) -> None:
        self.where = where
        try:
            call()
        except Exception:
            self.record(*sys.exc_info())

    def report(self) -> str:
        if not self.found:
            return ""
        return f"{len(self.found)} failed:\n\n" + "\n\n".join(self.found[:10])
