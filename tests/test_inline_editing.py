"""Typing straight into the cue list, the way QLab does it, with no dialog.

Double clicking a cue used to open the full editor window. Now the cell itself is edited:
the number, the name, the waits and the fades. The File column still opens the editor,
because picking a file needs one.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402

from cuerunner.main import (  # noqa: E402
    COLUMNS, EDITABLE_COLUMNS, CueCellDelegate, MainWindow,
)
from cuerunner.model import Cue  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
    w.show_model.cues.append(Cue(number="1", name="Preshow", kind="memo"))
    w.show_model.cues.append(Cue(number="2", name="Door slam", kind="memo"))
    w._editing = True
    w._refresh_table()
    w.show()
    app.processEvents()
    yield w
    w._mark_saved()
    w.close()


def cell(window, row: int, column: str):
    return window.table.item(row, COLUMNS.index(column))


def type_into(app, window, row: int, column: str, text: str) -> None:
    cell(window, row, column).setText(text)
    app.processEvents()


# --- what can be typed into ------------------------------------------
def test_a_name_typed_into_the_list_lands_on_the_cue(app, window):
    type_into(app, window, 0, "Name", "Overture")
    assert window.show_model.cues[0].name == "Overture"


def test_the_number_can_be_changed_in_place(app, window):
    type_into(app, window, 0, "#", "1.5")
    assert window.show_model.cues[0].number == "1.5"


def test_renumbering_a_group_takes_its_children_with_it(app, window):
    window.show_model.cues[0].kind = "group"
    child = Cue(number="1.1", name="inside", kind="memo")
    child.parent = "1"
    window.show_model.cues.insert(1, child)
    window._refresh_table()
    type_into(app, window, 0, "#", "7")
    assert window.show_model.cues[1].parent == "7"


def test_the_waits_and_fades_can_be_typed_in_seconds(app, window):
    type_into(app, window, 0, "Pre-Wait", "2.5")
    type_into(app, window, 0, "Post-Wait", "1")
    window._show_column("Fade In", True)
    window._show_column("Fade Out", True)
    type_into(app, window, 0, "Fade In", "3")
    type_into(app, window, 0, "Fade Out", "0.25")
    cue = window.show_model.cues[0]
    assert (cue.pre_wait_ms, cue.post_wait_ms) == (2500, 1000)
    assert (cue.fade_in, cue.fade_out) == (3.0, 0.25)


def test_a_time_typed_with_its_unit_still_reads(app, window):
    type_into(app, window, 0, "Pre-Wait", "4 s")
    assert window.show_model.cues[0].pre_wait_ms == 4000


def test_a_hotkey_can_be_typed_in(app, window):
    window._show_column("Hotkey", True)
    type_into(app, window, 0, "Hotkey", "Ctrl+Shift+9")
    assert window.show_model.cues[0].hotkey == "Ctrl+Shift+9"


# --- what cannot ------------------------------------------------------
def test_the_worked_out_columns_cannot_be_typed_into(window):
    for column in ("Length", "Then", "Remaining", "File"):
        assert column not in EDITABLE_COLUMNS
        assert not cell(window, 0, column).flags() & Qt.ItemFlag.ItemIsEditable


def test_a_time_that_is_not_a_number_is_put_back(app, window):
    type_into(app, window, 0, "Pre-Wait", "sometime later")
    assert window.show_model.cues[0].pre_wait_ms == 0
    assert cell(window, 0, "Pre-Wait").text() == ""


def test_a_number_already_in_use_is_refused(app, window):
    type_into(app, window, 0, "#", "2")
    assert window.show_model.cues[0].number == "1"


def test_an_empty_name_is_put_back(app, window):
    type_into(app, window, 0, "Name", "   ")
    assert window.show_model.cues[0].name == "Preshow"


def test_nothing_can_be_typed_in_show_mode(app, window):
    window._set_mode(editing=False)
    assert not cell(window, 0, "Name").flags() & Qt.ItemFlag.ItemIsEditable
    assert window.table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    window._cell_edited(cell(window, 0, "Name"))
    assert window.show_model.cues[0].name == "Preshow"


def test_switching_back_to_edit_unlocks_the_cells_again(app, window):
    window._set_mode(editing=False)
    window._set_mode(editing=True)
    assert cell(window, 0, "Name").flags() & Qt.ItemFlag.ItemIsEditable


# --- the editor box ---------------------------------------------------
def test_the_editor_opens_on_the_plain_value_not_the_dressed_up_one(app, window):
    window.show_model.cues[0].flagged = True
    window.show_model.cues[0].pre_wait_ms = 2500
    window._refresh_table()
    assert "⚑" in cell(window, 0, "#").text()  # the flag shows in the list
    assert cell(window, 0, "#").data(CueCellDelegate.RAW) == "1"  # but not in the editor
    assert cell(window, 0, "Pre-Wait").text() == "2.5 s"
    assert cell(window, 0, "Pre-Wait").data(CueCellDelegate.RAW) == "2.5"


def test_a_childs_name_is_edited_without_its_indent(app, window):
    window.show_model.cues[1].parent = "1"
    window._refresh_table()
    assert cell(window, 1, "Name").text().startswith(" ")
    assert cell(window, 1, "Name").data(CueCellDelegate.RAW) == "Door slam"


def test_the_delegate_hands_the_plain_value_to_the_box(app, window):
    delegate = window.table.itemDelegate()
    editor = QtWidgets.QLineEdit()
    index = window.table.model().index(0, COLUMNS.index("Pre-Wait"))
    window.show_model.cues[0].pre_wait_ms = 1500
    window._refresh_table()
    delegate.setEditorData(editor, index)
    assert editor.text() == "1.5"


# --- undo and the dialog ---------------------------------------------
def test_an_inline_edit_can_be_undone(app, window):
    type_into(app, window, 0, "Name", "Overture")
    assert window.show_model.cues[0].name == "Overture"
    window.undo()
    assert window.show_model.cues[0].name == "Preshow"


def test_a_refused_edit_does_not_leave_an_undo_step_behind(app, window):
    type_into(app, window, 0, "Name", "Overture")
    depth = len(window._undo)
    type_into(app, window, 0, "Pre-Wait", "rubbish")
    type_into(app, window, 0, "#", "2")
    assert len(window._undo) == depth


def test_double_clicking_a_cue_does_not_open_a_window(app, window):
    with mock.patch.object(MainWindow, "edit_row") as dialog:
        for column in ("Name", "#", "Pre-Wait", "Length"):
            window._cell_double_clicked(cell(window, 0, column))
    assert not dialog.called


def test_double_clicking_the_file_column_opens_the_editor(app, window):
    with mock.patch.object(MainWindow, "edit_row") as dialog:
        window._cell_double_clicked(cell(window, 0, "File"))
    assert dialog.called


def test_the_editor_is_still_reachable_from_the_menu(app, window):
    with mock.patch.object(MainWindow, "edit_row") as dialog:
        window.table.selectRow(0)
        window.edit_selected()
    assert dialog.called
