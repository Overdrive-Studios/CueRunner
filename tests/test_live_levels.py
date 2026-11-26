"""The Levels tab has to move the sound while the cue is playing, as QLab's faders do.

They used to be applied only when a cue started, so dragging a fader during a show did
nothing you could hear until you stopped and restarted the cue.
"""

from __future__ import annotations

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
soundfile = pytest.importorskip("soundfile")

from cuerunner import levels_ui  # noqa: E402
from cuerunner.engine import Mixer  # noqa: E402
from cuerunner.main import INSPECTOR_TABS, MainWindow  # noqa: E402
from cuerunner.model import Cue  # noqa: E402

RATE = 48000


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    seconds = np.arange(RATE * 5) / RATE
    data = np.stack([0.5 * np.sin(2 * np.pi * 220 * seconds),
                     0.5 * np.sin(2 * np.pi * 330 * seconds)], axis=1)
    soundfile.write(path, data.astype("float32"), RATE)
    return str(path)


@pytest.fixture
def window(app, tone):
    def fake_output(self):
        self.mixer = Mixer(rate=RATE, out_channels=2)
        self.output = None
        self._clips = {}
        self._cue_voices = []

    with mock.patch.object(MainWindow, "_open_default_output", fake_output):
        w = MainWindow()
    w.show_model.cues.append(Cue(number="1", name="bed", kind="audio", file=tone))
    w._refresh_table()
    w._editing = True
    w.table.selectRow(0)
    w.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Levels"))
    app.processEvents()
    yield w
    w.hard_stop()
    w._mark_saved()
    w.close()


def peak(window, blocks: int = 10) -> float:
    return float(np.abs(np.concatenate(
        [window.mixer.render(1024) for _ in range(blocks)])).max())


def per_channel(window, blocks: int = 10) -> np.ndarray:
    block = np.concatenate([window.mixer.render(1024) for _ in range(blocks)])
    return np.abs(block).max(axis=0)


def playing(window) -> float:
    window.go_row(0)
    for _ in range(4):
        window.mixer.render(1024)
    return peak(window)


def test_pulling_the_main_fader_down_is_heard_straight_away(app, window):
    loud = playing(window)
    assert loud > 0.1
    window.insp_levels.set_all(levels_ui.MIN_DB)
    app.processEvents()
    assert peak(window) < loud * 0.05


def test_pushing_the_fader_back_up_restores_the_level(app, window):
    loud = playing(window)
    window.insp_levels.set_all(levels_ui.MIN_DB)
    app.processEvents()
    assert peak(window) < loud * 0.05
    window.insp_levels.set_all(0)
    app.processEvents()
    assert peak(window) == pytest.approx(loud, rel=0.1)


def test_moving_a_fader_twice_does_not_stack_the_first_move(app, window):
    """The gains used to be multiplied into the matrix, so each change compounded."""
    loud = playing(window)
    for _ in range(4):
        window.insp_levels.set_all(-6)
        app.processEvents()
        window.insp_levels.set_all(0)
        app.processEvents()
    assert peak(window) == pytest.approx(loud, rel=0.1)


def test_muting_one_output_silences_only_that_output(app, window):
    playing(window)
    both = per_channel(window)
    assert both[0] > 0.1 and both[1] > 0.1
    window.insp_levels.set_values(0.0, [0.0, 0.0], [False, True], [False, False])
    window.insp_levels.changed.emit()
    app.processEvents()
    after = per_channel(window)
    assert after[0] > 0.1
    assert after[1] < 0.01
    window.insp_levels.clear_mutes()
    app.processEvents()
    assert per_channel(window)[1] > 0.1


def test_a_fader_on_a_cue_that_is_not_playing_changes_nothing_yet(app, window):
    window.insp_levels.set_all(levels_ui.MIN_DB)
    app.processEvents()
    assert window.show_model.cues[0].volume == pytest.approx(0.0, abs=1e-4)
    assert peak(window) == 0.0  # nothing is running, so there is nothing to hear


def test_live_levels_are_safe_with_no_output_open(app, window):
    window.mixer = None
    window._live_levels(window.show_model.cues[0])  # must not raise
