"""Closing, or opening another show, offers to save work in progress first."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QCloseEvent  # noqa: E402

Button = QtWidgets.QMessageBox.StandardButton


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()  # never leave a prompt waiting at the end of a test
        w.close()


def add_a_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="memo"))
    window._refresh_table()


def answer(button):
    """Patch the Save/Discard/Cancel prompt with the button the user picks."""
    return mock.patch.object(QtWidgets.QMessageBox, "question", return_value=button)


def test_a_new_show_has_nothing_to_save(window):
    assert not window.has_unsaved_changes()


def test_adding_a_cue_counts_as_unsaved_work(window):
    add_a_cue(window)
    assert window.has_unsaved_changes()
    assert "•" in window.windowTitle()


def test_saving_clears_the_unsaved_mark(window, tmp_path):
    add_a_cue(window)
    window.show_path = tmp_path / "show.json"
    window.save_show()
    assert not window.has_unsaved_changes()
    assert "•" not in window.windowTitle()


def test_reopening_a_saved_show_starts_clean(window, tmp_path):
    """Durations are filled in after loading, which must not look like an edit."""
    add_a_cue(window)
    path = tmp_path / "show.json"
    window.show_path = path
    window.save_show()
    window.open_show_path(path)
    assert not window.has_unsaved_changes()


def test_cancelling_the_prompt_keeps_the_window_open(window):
    add_a_cue(window)
    window.show()
    event = QCloseEvent()
    with answer(Button.Cancel):
        window.closeEvent(event)
    assert not event.isAccepted()  # the close was called off


def test_discarding_lets_the_window_close(window):
    add_a_cue(window)
    window.show()
    event = QCloseEvent()
    event.accept()
    with answer(Button.Discard):
        window.closeEvent(event)
    assert event.isAccepted()


def test_choosing_save_writes_the_show_then_closes(window, tmp_path):
    add_a_cue(window)
    window.show_path = tmp_path / "show.json"
    window.show()
    event = QCloseEvent()
    event.accept()
    with answer(Button.Save):
        window.closeEvent(event)
    assert event.isAccepted()
    assert (tmp_path / "show.json").exists()
    assert not window.has_unsaved_changes()


def test_closing_with_nothing_to_save_asks_nothing(window):
    window.show()
    event = QCloseEvent()
    event.accept()
    with mock.patch.object(QtWidgets.QMessageBox, "question") as asked:
        window.closeEvent(event)
    asked.assert_not_called()
    assert event.isAccepted()


def test_cancelling_keeps_the_cues_when_starting_a_new_show(window):
    add_a_cue(window)
    with answer(Button.Cancel):
        window.new_show()
    assert len(window.show_model.cues) == 1


def test_discarding_clears_the_cues_for_a_new_show(window):
    add_a_cue(window)
    with answer(Button.Discard):
        window.new_show()
    assert window.show_model.cues == []
    assert not window.has_unsaved_changes()


def test_cancelling_stops_another_show_being_opened(window):
    add_a_cue(window)
    with answer(Button.Cancel), mock.patch.object(
        QtWidgets.QFileDialog, "getOpenFileName"
    ) as chooser:
        window.open_show()
    chooser.assert_not_called()  # never even asks which file


def test_quitting_the_app_also_offers_to_save(window):
    """Cmd+Q does not go through closeEvent by itself, so the window intercepts it."""
    from PySide6.QtCore import QEvent

    add_a_cue(window)
    window.show()
    app = QtWidgets.QApplication.instance()
    with answer(Button.Cancel):
        blocked = window.eventFilter(app, QEvent(QEvent.Type.Quit))
    assert blocked is True  # the quit was called off
    assert window.isVisible()

    with answer(Button.Discard):
        blocked = window.eventFilter(app, QEvent(QEvent.Type.Quit))
    assert blocked is False  # the quit carries on
