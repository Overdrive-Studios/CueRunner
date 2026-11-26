"""The Basics tab's Flagged, Auto-load and Skip if disarmed checkboxes."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")


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


def test_double_clicking_a_name_renames_the_cue(window):
    from cuerunner.main import NAME_COL
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Old name", kind="memo"))
    window._refresh_table()
    item = window.table.item(0, NAME_COL)
    assert item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable
    item.setText("New name")
    assert window.show_model.cues[0].name == "New name"


def test_other_columns_cannot_be_typed_into(window):
    from cuerunner.main import NAME_COL
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="memo"))
    window._refresh_table()
    for col in range(window.table.columnCount()):
        item = window.table.item(0, col)
        if item is None or col == NAME_COL:
            continue
        assert not (item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable), col


def test_clearing_a_name_puts_the_old_one_back(window):
    from cuerunner.main import NAME_COL
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Keep me", kind="memo"))
    window._refresh_table()
    window.table.item(0, NAME_COL).setText("   ")
    assert window.show_model.cues[0].name == "Keep me"


def test_rebuilding_the_table_is_not_mistaken_for_typing(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="memo", parent="group"))
    window._refresh_table()
    window._refresh_table()
    assert window.show_model.cues[0].name == "Song"  # the indent prefix was not saved


def test_the_rows_are_numbered_down_the_left(window):
    from cuerunner.model import Cue

    for n in range(3):
        window.show_model.cues.append(Cue(number=f"{10 + n}", name=f"c{n}", kind="memo"))
    window._refresh_table()
    assert not window.table.verticalHeader().isHidden()
    labels = [window.table.verticalHeaderItem(r).text() for r in range(3)]
    assert labels == ["1", "2", "3"]  # position, whatever the cue's own number says
