"""The five ways a group cue can behave, and where each leaves the playhead."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.model import Cue, group_mode_of  # noqa: E402


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()
        w.close()


def a_group(window, mode, children=3):
    """A group with children, and one plain cue after it to advance onto."""
    window.show_model.cues.append(Cue(number="1", name="group", kind="group", group_mode=mode))
    for i in range(children):
        window.show_model.cues.append(
            Cue(number=f"1.{i + 1}", name=f"child {i}", kind="memo", parent="1"))
    window.show_model.cues.append(Cue(number="2", name="after", kind="memo"))
    window._refresh_table()


def started(go):
    return [c.args[0] for c in go.call_args_list]


# ----------------------------------------------------------------- modes
def test_timeline_starts_every_child_at_once(window):
    a_group(window, "timeline")
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert started(go) == [0, 1, 2, 3]  # the group, then all three children


def test_playlist_runs_its_children_one_after_another(window):
    """Children that take time must not all start together, as a timeline group would."""
    window.show_model.cues.append(Cue(number="1", name="group", kind="group",
                                      group_mode="playlist"))
    for i in range(3):
        window.show_model.cues.append(Cue(number=f"1.{i + 1}", name=f"child {i}", kind="wait",
                                          duration_ms=5000, parent="1"))
    window.show_model.cues.append(Cue(number="2", name="after", kind="memo"))
    window._refresh_table()
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert started(go) == [0, 1]  # only the first; the rest follow as each finishes
    assert window._playlist_next_row(1) == 2  # and the chain knows what comes next
    assert window._playlist_next_row(3) is None  # nothing after the last child


def test_start_first_starts_only_the_first_child(window):
    a_group(window, "start_first")
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert started(go) == [0, 1]


def test_start_random_starts_exactly_one_child(window):
    a_group(window, "start_random")
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert len(started(go)) == 2
    assert started(go)[1] in (1, 2, 3)


def test_start_random_passes_over_a_disarmed_child(window):
    a_group(window, "start_random")
    for row in (1, 2):
        window.show_model.cues[row].armed = False
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert started(go)[1] == 3  # the only one left armed


def test_start_random_with_nothing_available_starts_nothing(window):
    a_group(window, "start_random")
    for row in (1, 2, 3):
        window.show_model.cues[row].armed = False
    with mock.patch.object(window, "go_row", wraps=window.go_row) as go:
        window.go_row(0)
    assert started(go) == [0]


# ----------------------------------------------------------------- the playhead
@pytest.mark.parametrize("mode", ["timeline", "playlist", "start_first", "start_random"])
def test_most_modes_leave_the_playhead_after_the_group(window, mode):
    a_group(window, mode)
    window.go_row(0)
    assert window._selected_row() == 4  # the cue after the group and its children


def test_start_first_and_enter_steps_into_the_group(window):
    a_group(window, "start_first_enter")
    window.go_row(0)
    assert window._selected_row() == 1  # the first child


def test_an_empty_group_still_moves_the_playhead_on(window):
    window.show_model.cues.append(Cue(number="1", name="group", kind="group",
                                      group_mode="timeline"))
    window.show_model.cues.append(Cue(number="2", name="after", kind="memo"))
    window._refresh_table()
    window.go_row(0)
    assert window._selected_row() == 1


# ----------------------------------------------------------------- older shows
def test_a_group_saved_as_all_still_means_timeline():
    assert group_mode_of(Cue(number="1", name="g", kind="group", group_mode="all")) == "timeline"


def test_an_unknown_mode_falls_back_to_timeline():
    assert group_mode_of(Cue(number="1", name="g", kind="group", group_mode="rubbish")) == "timeline"


def test_a_new_group_is_a_timeline_group():
    assert group_mode_of(Cue(number="1", name="g", kind="group")) == "timeline"


def test_the_mode_is_saved_with_the_show(tmp_path):
    from cuerunner.model import Show

    show = Show()
    show.cues.append(Cue(number="1", name="g", kind="group", group_mode="start_random"))
    path = tmp_path / "show.json"
    show.save(path)
    assert group_mode_of(Show.load(path).cues[0]) == "start_random"


def test_the_row_says_which_kind_of_group_it_is(window):
    a_group(window, "playlist")
    assert "Playlist group of 3 cues" == window._target_text(window.show_model.cues[0])


# ------------------------------------------------------------- timeline tab
def _timeline_index():
    from cuerunner.main import INSPECTOR_TABS
    return INSPECTOR_TABS.index("Timeline")


def test_the_timeline_tab_only_shows_for_a_timeline_group(window):
    a_group(window, "timeline")
    index = _timeline_index()
    window.table.selectRow(0)
    assert window.insp_tabs.isTabVisible(index)
    window.show_model.cues[0].group_mode = "playlist"
    window._refresh_inspector()
    assert not window.insp_tabs.isTabVisible(index)
    window.table.selectRow(1)  # a plain cue inside the group
    assert not window.insp_tabs.isTabVisible(index)


def test_the_timeline_tab_shows_the_groups_children_in_order(window):
    a_group(window, "timeline")
    window.table.selectRow(0)
    assert [c.number for c in window.insp_timeline.cues()] == ["1.1", "1.2", "1.3"]
    assert window.insp_timeline.cues()[0] is window.show_model.cues[1]


def test_leaving_a_timeline_group_empties_the_timeline_and_leaves_the_tab(window):
    a_group(window, "timeline")
    index = _timeline_index()
    window.table.selectRow(0)
    window.insp_tabs.setCurrentIndex(index)
    window.table.selectRow(4)  # the cue after the group
    assert window.insp_timeline.cues() == []
    assert window.insp_tabs.currentIndex() == 0  # the tab it was on has gone


def test_the_timeline_follows_edit_mode(window):
    a_group(window, "timeline")
    window.table.selectRow(0)
    window._editing = True
    window._refresh_inspector()
    assert window.insp_timeline.isEnabled()
    window._editing = False
    window._refresh_inspector()
    assert not window.insp_timeline.isEnabled()


def test_a_timeline_nudge_writes_through_to_the_show_and_can_be_undone(window):
    a_group(window, "timeline")
    window._editing = True
    window.table.selectRow(0)
    window.insp_timeline.select([1])
    window.insp_timeline.nudge(0.5)
    assert window.show_model.cues[2].pre_wait_ms == 500
    window.undo()
    assert window.show_model.cues[2].pre_wait_ms == 0


def test_the_timeline_playhead_tracks_a_running_child(window):
    from unittest import mock as _mock
    a_group(window, "timeline")
    window.table.selectRow(0)
    child = window.show_model.cues[2]
    child.pre_wait_ms = 2000
    rate = window.mixer.rate if window.mixer is not None else 48000
    voice = _mock.Mock(done=False, preloaded=False, tag=child, position=rate * 3)
    window._cue_voices = [voice]
    if window.mixer is None:
        window.mixer = _mock.Mock(rate=rate)
    window._refresh_inspector()
    assert window.insp_timeline.playhead() == pytest.approx(5.0)
