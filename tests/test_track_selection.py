"""Selecting a track highlights it; clicking elsewhere clears the highlight but GO keeps its place."""

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


def _three_cues(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="a", kind="memo"),
        Cue(number="2", name="b", kind="memo"),
        Cue(number="3", name="c", kind="memo"),
    ])
    window._refresh_table()


def test_clicking_elsewhere_clears_the_highlight_but_keeps_the_playhead(window):
    _three_cues(window)
    window.table.selectRow(1)
    assert window._selected_row() == 1
    window._on_outside_click(window)  # a click on the window background, outside the table
    assert window.table.selectedIndexes() == []
    assert window._selected_row() == -1
    assert window._playhead_row() == 1  # GO still continues from the last track clicked


def test_clicks_inside_the_table_keep_the_selection(window):
    _three_cues(window)
    window.table.selectRow(2)
    window._on_outside_click(window.table)
    assert window._selected_row() == 2


def test_clicks_inside_the_inspector_keep_the_selection(window):
    _three_cues(window)
    window.table.selectRow(0)
    window._on_outside_click(window.inspector_bar)
    assert window._selected_row() == 0


def test_playhead_starts_at_the_first_cue_before_anything_is_clicked(window):
    _three_cues(window)
    assert window._playhead_row() == 0


def test_inspector_shows_the_basics_tab_for_the_selected_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="7", name="Song", kind="audio", duration_ms=203_500,
                                      then="follow", notes="Fade out early"))
    window._refresh_table()
    window.table.selectRow(0)
    assert window.insp_tabs.tabText(0) == "Basics"
    assert window.insp_duration.text() == "00:03:23.500"
    assert window.insp_continue.currentData() == "follow"
    assert window.insp_notes.toPlainText() == "Fade out early"
    assert window.insp_number.isReadOnly()


def test_editing_the_notes_writes_them_back(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="memo"))
    window._refresh_table()
    window.table.selectRow(0)
    window._set_mode(editing=True)
    window.insp_notes.setPlainText("Check levels")
    assert window.show_model.cues[0].notes == "Check levels"


def test_tab_pages_line_up_with_the_tab_row(window):
    from cuerunner.main import INSPECTOR_TABS

    assert window.insp_stack.count() == len(INSPECTOR_TABS)
    from cuerunner.main import TAB_LABELS

    assert [window.insp_tabs.tabText(i) for i in range(len(INSPECTOR_TABS))] == TAB_LABELS
    wanted = INSPECTOR_TABS.index("Time & Loops")
    window.insp_tabs.setCurrentIndex(wanted)
    assert window.insp_stack.currentIndex() == wanted


def test_loop_and_timecode_trigger_write_back(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio"))
    window._refresh_table()
    window.table.selectRow(0)
    window._set_mode(editing=True)
    window.insp_loop.setChecked(True)
    window.insp_loop_out.setValue(12.5)
    window.insp_trigger_tc.setText("00:01:30:00")
    window.insp_trigger_tc.editingFinished.emit()
    cue = window.show_model.cues[0]
    assert cue.loop is True and cue.loop_out == pytest.approx(12.5)
    assert cue.trigger_tc == "00:01:30:00"


def test_invalid_timecode_trigger_is_not_saved(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio", trigger_tc="00:00:10:00"))
    window._refresh_table()
    window.table.selectRow(0)
    window._set_mode(editing=True)
    window.insp_trigger_tc.setText("not a time")
    window.insp_trigger_tc.editingFinished.emit()
    assert window.show_model.cues[0].trigger_tc == "00:00:10:00"
