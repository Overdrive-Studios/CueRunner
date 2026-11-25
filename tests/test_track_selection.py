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
