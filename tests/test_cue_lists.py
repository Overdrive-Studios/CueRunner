"""Cue lists and cue carts."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.model import Cue, CueList, Show  # noqa: E402


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()
        w.close()


# ----------------------------------------------------------------- the model
def test_a_new_show_has_one_list():
    show = Show()
    assert len(show.lists) == 1
    assert show.current.name == "Main Cue List"
    assert not show.current.is_cart()


def test_cues_belong_to_whichever_list_is_active():
    show = Show()
    show.cues.append(Cue(number="1", name="in list", kind="memo"))
    show.cue_lists.append(CueList(name="Cart", kind="cart"))
    show.active_list = 1
    assert show.cues == []  # the cart starts empty
    show.cues.append(Cue(number="S1", name="in cart", kind="memo"))
    show.active_list = 0
    assert [c.name for c in show.cues] == ["in list"]
    assert [c.name for c in show.all_cues()] == ["in list", "in cart"]


def test_lists_and_carts_survive_saving(tmp_path):
    show = Show()
    show.cues.append(Cue(number="1", name="first", kind="memo"))
    show.cue_lists.append(CueList(name="SFX", kind="cart", columns=6, rows=3))
    show.active_list = 1
    show.cues.append(Cue(number="S1", name="bang", kind="memo"))
    path = tmp_path / "show.json"
    show.save(path)
    back = Show.load(path)
    assert [(e.name, e.kind) for e in back.lists] == [("Main Cue List", "list"), ("SFX", "cart")]
    assert back.lists[1].columns == 6 and back.lists[1].rows == 3
    assert back.active_list == 1
    assert [c.name for c in back.lists[1].cues] == ["bang"]


def test_a_show_from_before_lists_existed_still_opens(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"cues": [{"number": "1", "name": "legacy", "kind": "audio"}]}')
    show = Show.load(path)
    assert len(show.lists) == 1
    assert [c.name for c in show.cues] == ["legacy"]


def test_a_show_always_keeps_a_list():
    show = Show()
    show.cue_lists = []
    assert len(show.lists) == 1  # one is made rather than leaving nowhere to put cues


# ----------------------------------------------------------------- the window
def test_adding_a_cart_switches_to_it(window):
    window.add_cue_list("cart")
    assert window.show_model.current.is_cart()
    assert window.showing_cart()
    assert window.cue_area.currentWidget() is window.cart


def test_switching_back_shows_the_table(window):
    window.add_cue_list("cart")
    window._choose_list(0)
    assert not window.showing_cart()
    assert window.cue_area.currentWidget() is window.table


def test_the_sidebar_lists_everything_in_the_show(window):
    window.add_cue_list("cart")
    window.add_cue_list("list")
    assert window.list_sidebar.count() == 3
    assert "Main Cue List" in window.list_sidebar.item(0).text()


def test_cues_added_while_a_cart_is_open_go_into_the_cart(window):
    window.add_cue_list("cart")
    with mock.patch.object(window, "edit_row"):  # adding a cue opens its editor
        window.add_special_cue("memo")
    assert len(window.show_model.lists[1].cues) == 1
    assert window.show_model.lists[0].cues == []


def test_a_cart_cannot_hold_a_group_cue(window):
    """QLab's rule: carts take every cue type except groups."""
    window.add_cue_list("cart")
    with mock.patch.object(QtWidgets.QMessageBox, "information") as told:
        window.add_special_cue("group")
    told.assert_called_once()
    assert window.show_model.cues == []


def test_picking_a_cue_in_a_cart_shows_it_in_the_inspector(window):
    window.add_cue_list("cart")
    window.show_model.cues.extend([Cue(number="S1", name="Door", kind="memo"),
                                   Cue(number="S2", name="Thunder", kind="memo")])
    window._refresh_table()
    window.cart._clicked(1)
    assert window.insp_name.text() == "Thunder"


def test_go_in_a_cart_starts_what_is_selected_and_moves_nothing_on(window):
    window.add_cue_list("cart")
    window.show_model.cues.extend([Cue(number="S1", name="a", kind="memo"),
                                   Cue(number="S2", name="b", kind="memo")])
    window._refresh_table()
    window.cart._clicked(1)
    with mock.patch.object(window, "go_row") as go:
        window.cart.trigger_selected()
    go.assert_called_once_with(1)
    assert window.cart.selected_index() == 1  # nothing advanced


def test_with_nothing_picked_in_a_cart_go_does_nothing(window):
    window.add_cue_list("cart")
    window.show_model.cues.append(Cue(number="S1", name="a", kind="memo"))
    window._refresh_table()
    assert window._playhead_row() == -1


def test_the_last_list_cannot_be_deleted(window):
    with mock.patch.object(QtWidgets.QMessageBox, "information") as told:
        window.remove_cue_list()
    told.assert_called_once()
    assert len(window.show_model.lists) == 1


def test_deleting_a_list_asks_first_and_can_be_undone(window):
    window.add_cue_list("cart")
    window.show_model.cues.append(Cue(number="S1", name="bang", kind="memo"))
    with mock.patch.object(QtWidgets.QMessageBox, "question",
                           return_value=QtWidgets.QMessageBox.StandardButton.Cancel):
        window.remove_cue_list()
    assert len(window.show_model.lists) == 2  # cancelled, nothing lost

    with mock.patch.object(QtWidgets.QMessageBox, "question",
                           return_value=QtWidgets.QMessageBox.StandardButton.Yes):
        window.remove_cue_list()
    assert len(window.show_model.lists) == 1


def test_the_footer_counts_the_whole_show(window):
    window.show_model.cues.append(Cue(number="1", name="a", kind="memo"))
    window.add_cue_list("cart")
    window.show_model.cues.append(Cue(number="S1", name="b", kind="memo"))
    window._refresh_table()
    assert window.cue_count_label.text() == "2 cues in 2 lists"


def test_the_cart_grid_only_fills_the_cells_it_has(window):
    window.add_cue_list("cart")
    window.show_model.cues.extend([Cue(number=str(i), name=f"c{i}", kind="memo")
                                   for i in range(5)])
    window._refresh_table()
    assert window.cart.capacity() == 32  # eight across, four down
    assert window.cart._buttons[4].isEnabled()
    assert not window.cart._buttons[5].isEnabled()
