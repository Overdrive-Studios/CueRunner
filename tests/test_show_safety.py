"""A cue that cannot run must never freeze a running show."""

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


def a_broken_cue(window, kind="video"):
    """A cue with nothing to play, so running it fails."""
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Broken", kind=kind, file=""))
    window._refresh_table()


def test_a_failing_cue_in_show_mode_does_not_open_a_dialog(window):
    a_broken_cue(window)
    window._set_mode(editing=False)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as dialog:
        window.go_row(0)
    dialog.assert_not_called()  # a dialog here would stop the show dead


def test_the_problem_is_shown_on_the_banner_instead(window):
    a_broken_cue(window)
    window._set_mode(editing=False)
    window.go_row(0)
    assert window.alert_label.isVisibleTo(window)
    assert "Cannot play cue" in window.alert_label.text()
    assert "Broken" in window.alert_label.text()


def test_the_banner_clears_itself(window):
    a_broken_cue(window)
    window._set_mode(editing=False)
    window.go_row(0)
    assert window._alert_timer.isActive()
    window._alert_timer.timeout.emit()
    assert not window.alert_label.isVisibleTo(window)


def test_in_edit_mode_a_failing_cue_still_says_so_plainly(window):
    """While setting cues up there is no show to interrupt, so a dialog is right."""
    a_broken_cue(window)
    window._set_mode(editing=True)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as dialog:
        window.go_row(0)
    dialog.assert_called_once()


def test_failures_are_kept_so_they_can_be_looked_at_later(window):
    a_broken_cue(window)
    window._set_mode(editing=False)
    window.go_row(0)
    assert any("Broken" in p for p in window.recent_problems())


def test_the_list_of_failures_does_not_grow_for_ever(window):
    window._set_mode(editing=False)
    for _ in range(80):
        window._cue_problem("Cannot play cue", "something went wrong")
    assert len(window.recent_problems()) <= 50


def test_the_show_carries_on_after_a_cue_fails(window):
    """The next GO must still work once a cue has failed."""
    from cuerunner.model import Cue

    a_broken_cue(window)
    window.show_model.cues.append(Cue(number="2", name="Fine", kind="memo"))
    window._refresh_table()
    window._set_mode(editing=False)
    window.go_row(0)
    window.go_row(1)  # must not raise
    assert len(window.recent_problems()) == 1


@pytest.mark.parametrize("kind", ["video", "camera", "network", "midi", "timecode",
                                  "light", "osc", "midifile", "program", "target"])
def test_no_cue_kind_can_block_a_running_show(window, kind):
    """Every cue type that can fail must report it without a dialog."""
    a_broken_cue(window, kind)
    window._set_mode(editing=False)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as warned, \
         mock.patch.object(QtWidgets.QMessageBox, "critical") as crit, \
         mock.patch.object(QtWidgets.QMessageBox, "information") as info:
        window.go_row(0)
    warned.assert_not_called()
    crit.assert_not_called()
    info.assert_not_called()


def test_a_console_cue_that_fails_does_not_block(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Desk", kind="control"))
    window._refresh_table()
    window._set_mode(editing=False)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as dialog:
        window.go_row(0)
    dialog.assert_not_called()


def test_a_soundboard_pad_that_fails_does_not_block(window):
    from cuerunner.model import Pad

    window.show_model.pads = [Pad(name="x", file="/not/here.wav")]
    window.soundboard.set_pads(window.show_model.pads)
    window._set_mode(editing=False)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as dialog:
        window._on_pad_pressed(0)
    dialog.assert_not_called()


def test_the_radio_never_opens_a_dialog_even_while_editing(window):
    """It runs on a timer, so a dialog could appear with nobody touching the app."""
    window._set_mode(editing=True)
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as dialog:
        window._cue_problem("Venue Radio", "could not play", modal_when_editing=False)
    dialog.assert_not_called()
    assert "Venue Radio" in window.alert_label.text()


def test_a_show_with_fewer_pads_than_the_board_does_not_crash(window):
    """An older or hand-edited show file may carry a short pad list."""
    from cuerunner.model import Pad

    window.show_model.pads = [Pad(name="only one", file="")]
    window.soundboard.set_pads(window.show_model.pads)  # must not raise
    assert window._pad_at(0) is not None
    assert window._pad_at(10) is None  # past the end, reported rather than crashing
    window._on_pad_pressed(10)  # must not raise
    window._clear_pad(3)  # fills the gap rather than failing
    assert len(window.show_model.pads) >= 4


def test_an_empty_pad_list_still_draws_the_board(window):
    window.show_model.pads = []
    window.soundboard.set_pads([])
    assert window._pad_at(0) is None
    window._on_pad_pressed(0)  # must not raise
