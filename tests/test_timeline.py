"""The Timeline tab: a Timeline Group's children laid out on a shared ruler.

These check the geometry maths and every way QLab lets you adjust a cue in the timeline,
by sending the same mouse and key events Qt would send.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cuerunner.model import Cue  # noqa: E402
from cuerunner import timeline_view as tv  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def child(number: str, pre: float = 0.0, seconds: float = 5.0, **extra) -> Cue:
    cue = Cue(number=number, name=f"Cue {number}", kind="audio", file=f"/x/{number}.wav")
    cue.parent = "1"
    cue.pre_wait_ms = int(pre * 1000)
    cue.duration_ms = int(seconds * 1000)
    for key, value in extra.items():
        setattr(cue, key, value)
    return cue


def view(app, cues, width=600, height=200, editable=True) -> tv.TimelineView:
    widget = tv.TimelineView()
    widget.resize(width, height)
    widget.set_cues(cues)
    widget.set_editable(editable)
    return widget


def press(widget, x, y, modifiers=Qt.KeyboardModifier.NoModifier):
    widget.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers))


def move(widget, x, y, modifiers=Qt.KeyboardModifier.NoModifier):
    widget.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers))


def release(widget, x, y):
    widget.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def drag(widget, row, from_x, to_x, modifiers=Qt.KeyboardModifier.NoModifier, zone_y=None):
    y = zone_y if zone_y is not None else widget.bar_rect(row).center().y()
    press(widget, from_x, y, modifiers)
    move(widget, to_x, y, modifiers)
    release(widget, to_x, y)


# --- the maths -------------------------------------------------------
def test_length_and_start_come_from_pre_wait_and_trim(app):
    cue = child("1.1", pre=2.0, seconds=10.0, trim_in=1.0, trim_out=6.0)
    assert tv.cue_start(cue) == 2.0
    assert tv.cue_length(cue) == 5.0
    assert tv.cue_end(cue) == 7.0


def test_rate_shortens_the_bar(app):
    cue = child("1.1", seconds=10.0, rate=2.0)
    assert tv.cue_length(cue) == 5.0


def test_a_cue_with_no_duration_has_no_length(app):
    cue = Cue(number="1.1", name="Fire", kind="midi")
    assert tv.cue_length(cue) == 0.0


def test_slices_land_on_the_group_timeline_not_the_file(app):
    cue = child("1.1", pre=3.0, seconds=10.0, trim_in=2.0, slices=[4.0, 7.0])
    # 4s and 7s into the file, 2s of which is trimmed off, offset by the 3s pre-wait.
    assert tv.slice_times(cue) == [5.0, 8.0]


def test_slices_outside_the_played_part_are_dropped(app):
    cue = child("1.1", seconds=10.0, trim_in=0.0, trim_out=4.0, slices=[2.0, 9.0])
    assert tv.slice_times(cue) == [2.0]


def test_span_covers_everything_with_room_to_spare(app):
    widget = view(app, [child("1.1", pre=0), child("1.2", pre=40.0, seconds=20.0)])
    assert widget.span() > 60.0


def test_rows_stack_without_overlapping(app):
    widget = view(app, [child("1.1"), child("1.2"), child("1.3")])
    bands = [widget.row_rect(i) for i in range(3)]
    for first, second in zip(bands, bands[1:]):
        assert second.top() >= first.bottom()
    assert widget.row_at(bands[1].center().y()) == 1


def test_a_cue_with_no_duration_is_still_wide_enough_to_grab(app):
    widget = view(app, [Cue(number="1.1", name="Fire", kind="midi", parent="1")])
    assert widget.bar_rect(0).width() >= tv.MIN_BAR


# --- what the pointer is over ----------------------------------------
def test_zones_follow_the_edges_of_the_bar(app):
    widget = view(app, [child("1.1", pre=2.0, seconds=10.0)])
    bar = widget.bar_rect(0)
    y = bar.center().y()
    assert widget.zone_at(bar.left() + 1, y)[0] == "trim_start"
    assert widget.zone_at(bar.right() - 1, y)[0] == "trim_end"
    assert widget.zone_at(bar.center().x(), y)[0] == "move"
    assert widget.zone_at(bar.center().x(), bar.bottom() - 1)[0] == "scale"
    assert widget.zone_at(bar.center().x(), y, slip=True)[0] == "slip"


def test_a_cue_with_slices_cannot_be_slipped(app):
    widget = view(app, [child("1.1", seconds=10.0, slices=[2.0])])
    bar = widget.bar_rect(0)
    assert widget.zone_at(bar.center().x(), bar.center().y(), slip=True)[0] == "move"


def test_clicking_past_the_bars_clears_the_selection(app):
    widget = view(app, [child("1.1", seconds=1.0)])
    press(widget, widget.bar_rect(0).center().x(), widget.bar_rect(0).center().y())
    assert widget.selection() == [0]
    press(widget, widget.width() - 2, widget.height() - 2)
    assert widget.selection() == []


# --- dragging --------------------------------------------------------
def test_dragging_a_cue_sets_its_pre_wait(app):
    cue = child("1.1", pre=2.0, seconds=4.0)
    widget = view(app, [cue, child("1.2", pre=30.0, seconds=4.0)])
    bar = widget.bar_rect(0)
    moved = []
    widget.changed.connect(lambda: moved.append(True))
    drag(widget, 0, bar.center().x(), bar.center().x() + 100)
    assert cue.pre_wait_ms > 2000
    assert moved == [True]


def test_a_cue_cannot_be_dragged_before_zero(app):
    cue = child("1.1", pre=1.0, seconds=4.0)
    widget = view(app, [cue, child("1.2", pre=40.0)])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.center().x(), bar.center().x() - 400)
    assert cue.pre_wait_ms == 0


def test_dragging_snaps_to_another_cues_start(app):
    first = child("1.1", pre=0.0, seconds=4.0)
    second = child("1.2", pre=12.0, seconds=4.0)
    widget = view(app, [first, second])
    # Aim a whisker short of the second cue's start; the snap should close the gap.
    target_x = widget._x_for(12.0) - 3
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.left() + bar.width() / 2, target_x + bar.width() / 2)
    assert first.pre_wait_ms == 12000


def test_holding_ctrl_turns_snapping_off(app):
    first = child("1.1", pre=0.0, seconds=4.0)
    widget = view(app, [first, child("1.2", pre=12.0, seconds=4.0)])
    bar = widget.bar_rect(0)
    target_x = widget._x_for(12.0) - 3
    drag(widget, 0, bar.left() + bar.width() / 2, target_x + bar.width() / 2,
         modifiers=Qt.KeyboardModifier.ControlModifier)
    assert first.pre_wait_ms != 12000


def test_guidelines_appear_while_dragging_and_go_away_after(app):
    widget = view(app, [child("1.1", seconds=4.0), child("1.2", pre=9.0, seconds=4.0)])
    bar = widget.bar_rect(0)
    press(widget, bar.center().x(), bar.center().y())
    assert 9.0 in widget.guidelines()
    assert 13.0 in widget.guidelines()  # the other cue's end
    release(widget, bar.center().x(), bar.center().y())
    assert widget.guidelines() == []


def test_dragging_over_one_lane_narrows_the_guidelines_to_that_cue(app):
    widget = view(app, [child("1.1", seconds=4.0), child("1.2", pre=9.0, seconds=4.0),
                        child("1.3", pre=20.0, seconds=4.0)])
    bar = widget.bar_rect(0)
    press(widget, bar.center().x(), bar.center().y())
    move(widget, bar.center().x() + 5, widget.bar_rect(2).center().y())
    guides = widget.guidelines()
    assert 20.0 in guides and 24.0 in guides
    assert 9.0 not in guides


def test_the_playhead_is_a_snap_line(app):
    cue = child("1.1", pre=0.0, seconds=4.0)
    widget = view(app, [cue])
    widget.set_playhead(6.0)
    bar = widget.bar_rect(0)
    target_x = widget._x_for(6.0) - 2  # a shade shy of the playhead
    drag(widget, 0, bar.center().x(), target_x + bar.width() / 2)
    assert cue.pre_wait_ms == 6000


def test_nothing_moves_when_editing_is_off(app):
    cue = child("1.1", pre=2.0, seconds=4.0)
    widget = view(app, [cue], editable=False)
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.center().x(), bar.center().x() + 120)
    assert cue.pre_wait_ms == 2000


def test_a_drag_records_one_undo_step_before_it_changes_anything(app):
    cue = child("1.1", pre=2.0, seconds=4.0)
    widget = view(app, [cue])
    seen = []
    widget.about_to_edit.connect(lambda: seen.append(cue.pre_wait_ms))
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.center().x(), bar.center().x() + 80)
    assert seen == [2000]  # the signal arrives while the old value is still in place


# --- trimming, slipping, scaling -------------------------------------
def test_trimming_the_start_moves_the_pre_wait_and_the_file_together(app):
    cue = child("1.1", pre=2.0, seconds=10.0, trim_in=0.0, trim_out=10.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.left() + 1, bar.left() + 61,
         modifiers=Qt.KeyboardModifier.ControlModifier)
    assert cue.pre_wait_ms > 2000
    assert cue.trim_in > 0.0
    # The end of the cue has not moved, which is the point of trimming the start.
    assert round(tv.cue_end(cue), 1) == 12.0


def test_trimming_the_end_shortens_the_cue_without_moving_it(app):
    cue = child("1.1", pre=2.0, seconds=10.0, trim_out=10.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.right() - 1, bar.right() - 61,
         modifiers=Qt.KeyboardModifier.ControlModifier)
    assert cue.pre_wait_ms == 2000
    assert 0 < cue.trim_out < 10.0


def test_the_end_cannot_be_dragged_past_the_start(app):
    cue = child("1.1", pre=0.0, seconds=10.0, trim_in=4.0, trim_out=6.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.right() - 1, bar.left() - 200,
         modifiers=Qt.KeyboardModifier.ControlModifier)
    assert cue.trim_out > cue.trim_in


def test_the_end_cannot_be_dragged_past_the_end_of_the_file(app):
    cue = child("1.1", pre=0.0, seconds=10.0, trim_out=9.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.right() - 1, widget.width() - 2,
         modifiers=Qt.KeyboardModifier.ControlModifier)
    assert cue.trim_out <= 10.0


def test_slipping_slides_the_file_and_leaves_the_bar_alone(app):
    cue = child("1.1", pre=3.0, seconds=20.0, trim_in=2.0, trim_out=7.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    before = tv.cue_length(cue)
    drag(widget, 0, bar.center().x(), bar.center().x() + 60,
         modifiers=Qt.KeyboardModifier.AltModifier)
    assert cue.pre_wait_ms == 3000
    assert round(tv.cue_length(cue), 3) == round(before, 3)
    assert cue.trim_in > 2.0


def test_slipping_stops_at_the_end_of_the_file(app):
    cue = child("1.1", seconds=10.0, trim_in=0.0, trim_out=4.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    drag(widget, 0, bar.center().x(), widget.width() - 2,
         modifiers=Qt.KeyboardModifier.AltModifier)
    assert cue.trim_out <= 10.0
    assert round(tv.cue_length(cue), 2) == 4.0


def test_scaling_a_row_changes_its_height_and_nothing_else(app):
    cue = child("1.1", pre=2.0, seconds=4.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    saved = []
    widget.changed.connect(lambda: saved.append(True))
    tall_before = widget.row_rect(0).height()
    drag(widget, 0, bar.center().x(), bar.center().x(), zone_y=bar.bottom() - 1)
    # The pointer has to move down for the scale to change, so do it explicitly.
    press(widget, bar.center().x(), bar.bottom() - 1)
    move(widget, bar.center().x(), bar.bottom() + 20)
    release(widget, bar.center().x(), bar.bottom() + 20)
    assert widget.row_rect(0).height() > tall_before
    assert cue.pre_wait_ms == 2000
    assert saved == []  # purely visual, so it is not an edit to the show


def test_the_row_scale_is_kept_inside_sensible_limits(app):
    cue = child("1.1", seconds=4.0)
    widget = view(app, [cue])
    bar = widget.bar_rect(0)
    press(widget, bar.center().x(), bar.bottom() - 1)
    move(widget, bar.center().x(), bar.bottom() + 2000)
    release(widget, bar.center().x(), bar.bottom() + 2000)
    assert widget.scale_of(0) == tv.SCALE_RANGE[1]


# --- nudging ---------------------------------------------------------
def test_alt_arrows_nudge_the_selected_cues(app):
    first = child("1.1", pre=1.0, seconds=4.0)
    second = child("1.2", pre=5.0, seconds=4.0)
    widget = view(app, [first, second])
    widget.select([0, 1])
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                                   Qt.KeyboardModifier.AltModifier))
    assert (first.pre_wait_ms, second.pre_wait_ms) == (1100, 5100)
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                                   Qt.KeyboardModifier.AltModifier))
    assert (first.pre_wait_ms, second.pre_wait_ms) == (1000, 5000)


def test_shift_makes_the_nudge_finer(app):
    cue = child("1.1", pre=1.0, seconds=4.0)
    widget = view(app, [cue])
    widget.select([0])
    widget.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right,
        Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier))
    assert cue.pre_wait_ms == 1010


def test_a_nudge_never_pushes_a_cue_before_zero(app):
    cue = child("1.1", pre=0.05, seconds=4.0)
    widget = view(app, [cue])
    widget.select([0])
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                                   Qt.KeyboardModifier.AltModifier))
    assert cue.pre_wait_ms == 0


def test_nothing_is_nudged_with_no_selection_or_no_editing(app):
    cue = child("1.1", pre=1.0, seconds=4.0)
    widget = view(app, [cue])
    assert widget.nudge(0.1) is False
    widget.select([0])
    widget.set_editable(False)
    assert widget.nudge(0.1) is False
    assert cue.pre_wait_ms == 1000


# --- painting --------------------------------------------------------
def test_it_paints_with_cues_colours_slices_fades_and_an_empty_group(app):
    cues = [
        child("1.1", pre=0.0, seconds=8.0, colour="#2471a3", fade_in=2.0, fade_out=1.5),
        child("1.2", pre=3.0, seconds=6.0, slices=[1.0, 3.0], integrated_fade=True,
              fade_curve="smooth", fade_in=1.0),
        child("1.3", pre=9.0, seconds=0.2, colour="not a colour", armed=False),
        Cue(number="1.4", name="A very long cue name that will not fit in a short bar",
            kind="midi", parent="1"),
    ]
    widget = view(app, cues)
    widget.select([1])
    widget.grab()  # paints the whole widget; a raise here would fail the test
    widget.set_cues([])
    widget.grab()
