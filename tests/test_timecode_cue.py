"""Timecode cues through the main window, with the MIDI port mocked."""

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


def _timecode(window, target="Test Port"):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="TC", kind="timecode", target=target, text="00:00:10:00", fps=25)
    window.show_model.cues.append(cue)
    window._refresh_table()
    return cue


def test_timecode_cue_starts_a_generator_and_stop_ends_it(window):
    cue = _timecode(window)
    port = mock.MagicMock()
    with mock.patch("mido.open_output", return_value=port):
        window._start_timecode(cue)
        assert id(cue) in window._timecode_runs
        _, gen, timer, _ = window._timecode_runs[id(cue)]
        assert gen.running and timer.isActive()
        assert timer.interval() == 10  # a quarter frame at 25 fps
        window._stop_cue(cue)
    assert id(cue) not in window._timecode_runs
    port.close.assert_called_once()


def test_first_message_is_a_full_frame_at_the_start_time(window):
    cue = _timecode(window)
    port = mock.MagicMock()
    with mock.patch("mido.open_output", return_value=port):
        window._start_timecode(cue)
        window._stop_timecode()
    first = port.send.call_args_list[0].args[0]
    assert first.type == "sysex"
    assert list(first.data)[-4:] == [32, 0, 10, 0]  # hour byte carries the 25 fps code (1 << 5); then mm ss ff
