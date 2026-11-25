"""Target cues: changing what another cue points at when the Target cue fires."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w.close()


def test_target_cue_points_a_control_cue_at_a_new_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="A1", name="start", kind="control", action="start", target="intro"),
        Cue(number="A2", name="retarget", kind="target", target="A1", new_target="outro"),
    ])
    window._retarget_cue(window.show_model.cues[1])
    assert window.show_model.cues[0].target == "outro"


def test_target_cue_swaps_the_file_of_an_audio_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="song", kind="audio", file="/old.wav"),
        Cue(number="2", name="swap", kind="target", target="1", new_target="/new.wav"),
    ])
    window._retarget_cue(window.show_model.cues[1])
    assert window.show_model.cues[0].file == "/new.wav"


def test_target_cue_reports_a_missing_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="2", name="swap", kind="target", target="9", new_target="x"))
    with pytest.raises(ValueError):
        window._retarget_cue(window.show_model.cues[0])
