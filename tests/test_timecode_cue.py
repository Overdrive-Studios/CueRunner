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


def test_incoming_timecode_fires_the_cue_it_passes(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="early", kind="memo"),
        Cue(number="2", name="at five", kind="memo", trigger_tc="00:00:05:00"),
    ])
    window._refresh_table()
    with mock.patch.object(window, "go_row") as go_row:
        window._tc_queue.put((0, 25))
        window._poll_timecode()
        assert go_row.call_count == 0
        window._tc_queue.put((5_100, 25))  # 5.1 s: passes 00:00:05:00 at 25 fps
        window._poll_timecode()
        go_row.assert_called_once_with(1)


def test_trigger_dialog_rejects_bad_timecode(window):
    from cuerunner.model import Cue
    from cuerunner.main import CueDialog

    cue = Cue(number="1", name="t", kind="memo")
    dialog = CueDialog(cue, window)
    dialog.trigger_tc_edit.setText("00:01:90")
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as warning, mock.patch.object(dialog, "accept") as accept:
        dialog._accept_checked()
    warning.assert_called_once()
    accept.assert_not_called()
    dialog.trigger_tc_edit.setText("00:01:30:00")
    with mock.patch.object(dialog, "accept") as accept:
        dialog._accept_checked()
    accept.assert_called_once()
