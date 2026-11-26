"""The transport clock must survive a fractional playback position.

A voice's position is a float as soon as the cue's rate is not 1.0, and the clock ran it
straight into an integer format string. That raised ten times a second while a cue played,
and an exception inside a Qt slot takes the whole app down.
"""

from __future__ import annotations

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
soundfile = pytest.importorskip("soundfile")

from cuerunner.engine import Mixer  # noqa: E402
from cuerunner.main import MainWindow  # noqa: E402
from cuerunner.model import Cue  # noqa: E402
from cuerunner.timecode import (  # noqa: E402
    format_clock, format_duration, format_hms, format_mmss,
)

RATE = 48000


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    seconds = np.arange(RATE * 5) / RATE
    soundfile.write(path, (0.4 * np.sin(2 * np.pi * 220 * seconds)).astype("float32"), RATE)
    return str(path)


@pytest.mark.parametrize("fmt", [format_clock, format_duration, format_hms, format_mmss])
@pytest.mark.parametrize("value", [0, 0.0, 1.5, 1234.7, -3.2, 99999.999, 60_000.5])
def test_every_time_format_takes_a_float(fmt, value):
    assert isinstance(fmt(value), str)


def test_the_clock_survives_a_cue_playing_at_a_changed_rate(app, tone):
    def fake_output(self):
        self.mixer = Mixer(rate=RATE, out_channels=2)
        self.output = None
        self._clips = {}
        self._cue_voices = []

    with mock.patch.object(MainWindow, "_open_default_output", fake_output):
        window = MainWindow()
    try:
        cue = Cue(number="1", name="bed", kind="audio", file=tone)
        cue.rate = 1.37  # anything but 1.0 makes the position fractional
        window.show_model.cues.append(cue)
        window._refresh_table()
        window.go_row(0)
        for _ in range(10):
            window.mixer.render(1024)
            window._update_clock()  # the 100 ms tick, which used to raise here
        assert window.track_label.text() != "00:00.0"
    finally:
        window.hard_stop()
        window._mark_saved()
        window.close()


def test_the_progress_bar_takes_the_fractional_position_too(app, tone):
    def fake_output(self):
        self.mixer = Mixer(rate=RATE, out_channels=2)
        self.output = None
        self._clips = {}
        self._cue_voices = []

    with mock.patch.object(MainWindow, "_open_default_output", fake_output):
        window = MainWindow()
    try:
        cue = Cue(number="1", name="bed", kind="audio", file=tone)
        cue.rate = 0.75
        window.show_model.cues.append(cue)
        window._refresh_table()
        window._show_column("Progress", True)
        window.go_row(0)
        for _ in range(10):
            window.mixer.render(4096)
            window._update_clock()
    finally:
        window.hard_stop()
        window._mark_saved()
        window.close()
