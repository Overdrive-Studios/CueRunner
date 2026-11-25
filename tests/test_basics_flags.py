"""The Basics tab's Flagged, Auto-load and Skip if disarmed checkboxes."""

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
        w._mark_saved()
        w.close()


def test_the_checkboxes_write_back_to_the_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="memo"))
    window._refresh_table()
    window.table.selectRow(0)
    window._set_mode(editing=True)
    window.insp_flagged.setChecked(True)
    window.insp_auto_load.setChecked(True)
    window.insp_skip.setChecked(True)
    cue = window.show_model.cues[0]
    assert (cue.flagged, cue.auto_load, cue.skip_if_disarmed) == (True, True, True)


def test_a_flagged_cue_is_marked_on_its_row(window):
    from cuerunner.main import NAME_COL
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="7", name="Song", kind="memo", flagged=True))
    window._refresh_table()
    assert "⚑" in window.table.item(0, NAME_COL - 1).text()


def test_a_disarmed_cue_does_not_fire_and_the_playhead_moves_on(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="off", kind="memo", armed=False),
        Cue(number="2", name="next", kind="memo"),
    ])
    window._refresh_table()
    with mock.patch.object(window, "_finish") as finish:
        window.go_row(0)
    finish.assert_not_called()  # neither cue ran: the GO was used up


def test_skip_if_disarmed_carries_go_on_to_the_next_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="off", kind="memo", armed=False, skip_if_disarmed=True),
        Cue(number="2", name="next", kind="memo"),
    ])
    window._refresh_table()
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert any(call.args and call.args[0] == 1 for call in go.call_args_list)


def test_auto_load_readies_the_cue_when_the_playhead_reaches_it(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio",
                                      file="song.wav", auto_load=True))
    with mock.patch.object(window, "_preload") as preload:
        window._refresh_table()
    preload.assert_called_once()


def test_a_cue_without_auto_load_is_left_alone(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio", file="song.wav"))
    with mock.patch.object(window, "_preload") as preload:
        window._refresh_table()
    preload.assert_not_called()
