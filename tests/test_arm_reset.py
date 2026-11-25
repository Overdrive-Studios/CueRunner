"""Arm, Disarm and Reset: disarmed cues are skipped by GO, and Reset stops one cue's voices at once."""

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.engine import Clip, Mixer, Voice  # noqa: E402


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w.close()


def test_mixer_kill_stops_only_that_cue():
    mixer = Mixer(rate=48000, out_channels=2)
    clip = Clip(np.zeros((100, 1), dtype=np.float32))
    mine, theirs = object(), object()
    a = Voice(clip=clip, matrix=np.ones((1, 2), dtype=np.float32), tag=mine)
    b = Voice(clip=clip, matrix=np.ones((1, 2), dtype=np.float32), tag=theirs)
    mixer.add(a)
    mixer.add(b)
    mixer.kill(mine)
    assert a.done and not b.done


def test_disarm_and_arm_through_a_control_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="song", kind="memo"),
        Cue(number="2", name="off", kind="control", action="disarm", target="1"),
        Cue(number="3", name="on", kind="control", action="arm", target="1"),
    ])
    window._control(window.show_model.cues[1])
    assert window.show_model.cues[0].armed is False
    window._control(window.show_model.cues[2])
    assert window.show_model.cues[0].armed is True


def test_go_skips_a_disarmed_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="off", kind="memo", armed=False),
        Cue(number="2", name="next", kind="memo"),
    ])
    window._refresh_table()
    with mock.patch.object(window, "_finish") as finish:
        window.go_row(0)
    finish.assert_not_called()
    assert window.table.currentRow() == 1


def test_reset_stops_the_target_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="song", kind="memo"),
        Cue(number="2", name="reset", kind="control", action="reset", target="1"),
    ])
    with mock.patch.object(window, "_reset_cue") as reset:
        window._control(window.show_model.cues[1])
    reset.assert_called_once_with(window.show_model.cues[0])
