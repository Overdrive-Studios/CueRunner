"""Runs the new cue types through the real main window: memo, wait, group, control, fade and text."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    box = QtWidgets.QMessageBox
    with mock.patch.object(box, "warning") as warning, mock.patch.object(
        MainWindow, "_open_default_output", lambda self: None
    ):
        w = MainWindow()
        w.warning_mock = warning
        yield w
        w.close()


def _add(window, kind, number, **fields):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number=number, name=kind, kind=kind, **fields))
    window._refresh_table()
    return len(window.show_model.cues) - 1


def test_memo_and_group_fire_children_and_advance(window):
    group = _add(window, "group", "1")
    child = _add(window, "memo", "2", text="hello", parent="1")
    window.go_row(group)
    assert window.table.currentRow() == child  # playhead moved past the group
    assert window.show_model.cues[child].parent == "1"


def test_wait_schedules_the_next_cue(window):
    wait = _add(window, "wait", "1", duration_ms=50)
    _add(window, "memo", "2")
    window.go_row(wait)
    assert len(window._timers) == 1
    assert window._timers[0].isActive()
    window.panic()
    assert window._timers == []


def test_pre_wait_delays_the_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="m", kind="memo", pre_wait_ms=50))
    window._refresh_table()
    window.go_row(0)
    assert len(window._timers) == 1
    assert window._timers[0].isActive()


def test_control_with_missing_target_warns(window):
    control = _add(window, "control", "1", target="99", action="start")
    window.go_row(control)
    assert window.warning_mock.called


def test_control_start_runs_target(window):
    target = _add(window, "memo", "5")
    control = _add(window, "control", "6", target="5", action="start")
    window.go_row(control)
    assert window.table.currentRow() == control  # last row: the playhead stays on it
    assert window.show_model.cues[target].kind == "memo"


def test_fade_and_text_cues_need_no_output_device(window):
    fade = _add(window, "fade", "1", target="3", level=0.0, seconds=2.0)
    window.go_row(fade)  # no mixer in tests: must not crash
    text = _add(window, "text", "2", text="Thank you", font_size=72, video_output="screen:0")
    with mock.patch.object(window._video, "show_text") as show_text:
        window.go_row(text)
    show_text.assert_called_once()
    assert show_text.call_args.args[:3] == ("screen:0", "Thank you", 72)


def test_target_column_describes_each_kind(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="f", kind="fade", target="3", level=0.5, seconds=4.0),
        Cue(number="2", name="c", kind="control", target="3", action="stop"),
        Cue(number="3", name="w", kind="wait", duration_ms=2500),
    ])
    assert window._target_text(window.show_model.cues[0]) == "Fade cue 3 to 50% over 4 s"
    assert window._target_text(window.show_model.cues[1]) == "Stop cue 3"
    assert window._target_text(window.show_model.cues[2]) == "Wait 2.5 s"
