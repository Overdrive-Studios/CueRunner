"""The inspector fields edit the selected cue in Edit mode and leave it alone in Show mode."""

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


def _cue_at_row(window, number="1"):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number=number, name="Opening", kind="memo"))
    window._refresh_table()
    window.table.selectRow(0)
    return window.show_model.cues[0]


def test_fields_show_the_selected_cue(window):
    cue = _cue_at_row(window)
    cue.pre_wait_ms = 1500
    cue.colour = "#c0392b"
    window._refresh_inspector()
    assert window.insp_name.text() == "Opening"
    assert window.insp_pre.value() == pytest.approx(1.5)
    assert window.insp_colour.currentData() == "#c0392b"


def test_edits_apply_in_edit_mode(window):
    cue = _cue_at_row(window)
    window._set_mode(True)
    window.insp_name.setText("Intro")
    window.insp_name.editingFinished.emit()
    window.insp_post.setValue(2.0)
    assert cue.name == "Intro"
    assert cue.post_wait_ms == 2000


def test_edits_ignored_in_show_mode(window):
    cue = _cue_at_row(window)
    window._set_mode(False)
    assert not window.insp_name.isEnabled()
    window._inspector_apply()
    assert cue.name == "Opening"


def test_refresh_does_not_write_back(window):
    cue = _cue_at_row(window)
    cue.name = "Changed elsewhere"
    window._set_mode(True)
    window._refresh_inspector()
    assert cue.name == "Changed elsewhere"
