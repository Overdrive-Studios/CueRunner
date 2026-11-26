"""Keyboard shortcuts: QLab's playback keys and the menu shortcuts.

Nearly every menu item used to have no shortcut at all, and there were only two playback
keys. These check that each key reaches its action, that a single key does not fire while
the cursor is in a field being typed into, and that Escape still does even then.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from cuerunner.main import (  # noqa: E402
    ALWAYS_KEYS, CONTINUE_MODES, CUE_TYPE_KEYS, MainWindow, PLAYBACK_KEYS,
)
from cuerunner.model import Cue  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
    w.show_model.cues.append(Cue(number="1", name="bed", kind="audio", file="/x.wav"))
    w._refresh_table()
    w._editing = True
    w.table.selectRow(0)
    w.show()
    app.processEvents()
    yield w
    w._mark_saved()
    w.close()


def press(window, key: str):
    combo = QKeySequence(key)[0]
    QTest.keyClick(window, Qt.Key(combo.key()), combo.keyboardModifiers())


def menu_items(window) -> dict[str, str]:
    """Every menu item by its path, with the shortcut shown beside it."""
    found = {}

    def walk(menu, path):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu():
                walk(action.menu(), path + action.text() + " > ")
                continue
            found[path + action.text()] = action.shortcut().toString()

    for action in window.menuBar().actions():
        if action.menu():
            walk(action.menu(), action.text() + " > ")
    return found


# --- the playback keys ------------------------------------------------
@pytest.mark.parametrize("name,key", [(n, k) for n, k, _ in PLAYBACK_KEYS])
def test_every_playback_key_reaches_its_action(app, window, name, key):
    window.table.setFocus()
    with mock.patch.object(MainWindow, name) as action:
        press(window, key)
        app.processEvents()
    assert action.called, f"{key} did nothing"


@pytest.mark.parametrize("name,key", [(n, k) for n, k, _ in PLAYBACK_KEYS
                                      if n not in ALWAYS_KEYS])
def test_a_playback_key_does_not_fire_while_typing(app, window, name, key):
    window.insp_name.setFocus()
    with mock.patch.object(MainWindow, name) as action:
        press(window, key)
        app.processEvents()
    assert not action.called, f"{key} fired while the cursor was in the name field"


def test_escape_still_panics_while_typing(app, window):
    window.insp_name.setFocus()
    with mock.patch.object(MainWindow, "_panic_pressed") as action:
        press(window, "Esc")
        app.processEvents()
    assert action.called


def test_a_spin_box_counts_as_typing(app, window):
    window.insp_pre.setFocus()
    assert window._typing()
    window.insp_notes.setFocus()
    assert window._typing()
    window.table.setFocus()
    assert not window._typing()


def test_every_playback_key_is_a_real_method(window):
    for name, _, what in PLAYBACK_KEYS:
        assert callable(getattr(window, name)), name
        assert what.strip(), name


def test_no_two_playback_keys_share_a_key():
    keys = [QKeySequence(k).toString() for _, k, _ in PLAYBACK_KEYS]
    assert len(set(keys)) == len(keys)


# --- what the keys actually do ----------------------------------------
def test_f_flags_and_unflags_the_selected_cue(app, window):
    cue = window.show_model.cues[0]
    window.table.setFocus()
    press(window, "F")
    app.processEvents()
    assert cue.flagged
    press(window, "F")
    app.processEvents()
    assert not cue.flagged


def test_c_cycles_the_continue_mode_all_the_way_round(app, window):
    cue = window.show_model.cues[0]
    window.table.setFocus()
    order = [value for _, value in CONTINUE_MODES]
    seen = []
    for _ in range(len(order)):
        press(window, "C")
        app.processEvents()
        seen.append(cue.then)
    assert sorted(seen) == sorted(order)


def test_the_edit_keys_put_the_cursor_in_the_right_field(app, window):
    for key, field in (("Q", window.insp_name), ("O", window.insp_notes),
                       ("E", window.insp_pre), ("W", window.insp_post)):
        window.table.setFocus()
        press(window, key)
        app.processEvents()
        assert field.hasFocus(), key


def test_the_edit_keys_do_nothing_in_show_mode(app, window):
    window._set_mode(editing=False)
    window.table.setFocus()
    press(window, "Q")
    app.processEvents()
    assert not window.insp_name.hasFocus()
    press(window, "F")
    app.processEvents()
    assert not window.show_model.cues[0].flagged


def test_pause_all_and_resume_all_are_safe_with_no_output(app, window):
    window.mixer = None
    window.pause_all()
    window.resume_all()  # no output open: these do nothing rather than raising


def test_panic_selected_twice_in_a_row_cuts_the_cue_dead(app, window):
    cue = window.show_model.cues[0]
    window.mixer = mock.Mock()
    with mock.patch.object(MainWindow, "_stop_cue") as fade:
        window.panic_selected()
        assert fade.called  # the first press fades
    window.panic_selected()  # straight away: the second cuts
    window.mixer.kill.assert_called_once_with(cue)


def test_flagging_can_be_undone(app, window):
    cue = window.show_model.cues[0]
    window.table.setFocus()
    press(window, "F")
    app.processEvents()
    assert cue.flagged
    window.undo()
    assert not window.show_model.cues[0].flagged


# --- the menus --------------------------------------------------------
def test_the_file_and_edit_menus_carry_their_usual_shortcuts(window):
    items = menu_items(window)
    for name in ("File > New Show", "File > Open Show...", "File > Save Show",
                 "File > Save Show As...", "Edit > Undo", "Edit > Redo"):
        assert items.get(name), name


def test_each_cue_type_is_on_the_key_qlab_uses(window):
    items = menu_items(window)
    wanted = {"Add Audio Cue...": "1", "Add Video Cue...": "3", "Add Camera Cue...": "4",
              "Add Text Cue...": "5", "Add Light Cue...": "6", "Add Fade Cue...": "7",
              "Add Network Cue...": "8", "Add MIDI Cue...": "9", "Add Group Cue...": "0",
              "Add Mic Cue...": "2"}
    for label, key in wanted.items():
        shown = items.get("Cues > " + label, "")
        assert shown.endswith(key) and "Ctrl" in shown, f"{label}: {shown!r}"


def test_the_cue_type_keys_are_all_different():
    assert len(set(CUE_TYPE_KEYS.values())) == len(CUE_TYPE_KEYS)


def test_editing_and_removing_a_cue_have_shortcuts(window):
    items = menu_items(window)
    assert items.get("Cues > Edit Cue...")
    assert items.get("Cues > Remove Cue")
    assert items.get("Cues > Select All Cues")


def test_most_menu_items_that_should_have_a_shortcut_have_one(window):
    """A guard against the state this started in, where only five of them did."""
    items = menu_items(window)
    assert sum(1 for shortcut in items.values() if shortcut) >= 18


def test_the_help_menu_lists_the_shortcuts(app, window):
    items = menu_items(window)
    assert "Help > Keyboard Shortcuts" in items
    with mock.patch.object(QtWidgets.QMessageBox, "information") as box:
        window.show_shortcuts()
    body = box.call_args.args[2]
    for _, key, _ in PLAYBACK_KEYS:
        assert key in body, key


# --- the Edit / Show toggle -------------------------------------------
def test_the_mode_buttons_switch_the_show_between_edit_and_locked(app, window):
    from PySide6.QtTest import QTest as T
    assert window._editing
    T.mouseClick(window.show_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not window._editing
    assert window.show_button.isChecked() and not window.edit_button.isChecked()
    assert not window.insp_name.isEnabled()  # nothing can be edited during a show
    T.mouseClick(window.edit_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window._editing
    assert window.edit_button.isChecked() and not window.show_button.isChecked()
    assert window.insp_name.isEnabled()


def test_the_chosen_mode_looks_different_from_the_other_one(window):
    """Two shades of grey told nobody which mode they were in."""
    import re

    from cuerunner.main import MAIN_QSS

    def background(selector: str) -> str:
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", MAIN_QSS)
        assert block, selector
        colour = re.search(r"background\s*:\s*([^;]+)", block.group(1))
        return colour.group(1).strip() if colour else ""

    chosen = background("QPushButton#modeButton:checked")
    plain = background("QPushButton#modeButton")
    assert chosen and plain
    assert chosen != plain


def test_pressing_a_control_does_not_deselect_the_cue(app, window):
    """Every button in the window used to clear the cue selection when clicked.

    That emptied the inspector, so Edit, Show, Stop and the rest all looked inert.
    """
    controls = [window.edit_button, window.show_button, window.go_button,
                window.stop_button, window.pause_button, window.panic_button]
    for control in controls:
        window.table.selectRow(0)
        window._on_outside_click(control)
        assert window.table.selectedIndexes(), control.text()


def test_clicking_the_window_background_still_clears_the_selection(app, window):
    window.table.selectRow(0)
    window._on_outside_click(window)
    assert not window.table.selectedIndexes()
    window.table.selectRow(0)
    window._on_outside_click(window.centralWidget())
    assert not window.table.selectedIndexes()
