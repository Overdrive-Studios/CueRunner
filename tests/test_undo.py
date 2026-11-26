"""Undo for cue edits, and cues layering when GO is pressed."""

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
        w._mark_saved()
        w.close()


def three_cues(window):
    from cuerunner.model import Cue

    for i in range(3):
        window.show_model.cues.append(Cue(number=str(i + 1), name=f"cue {i}", kind="memo"))
    window._refresh_table()


def names(window):
    return [c.name for c in window.show_model.cues]


def test_a_deleted_cue_comes_back(window):
    three_cues(window)
    window.table.selectRow(1)
    window.remove_cue()
    assert names(window) == ["cue 0", "cue 2"]
    window.undo()
    assert names(window) == ["cue 0", "cue 1", "cue 2"]


def test_a_deleted_cue_keeps_all_its_settings(window):
    """Undo must bring back the whole cue, not just its name."""
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="fancy", kind="audio", volume=0.25,
                                      trim_in=2.0, notes="keep me",
                                      effects=[{"kind": "peaking", "freq": 800.0}]))
    window._refresh_table()
    window.table.selectRow(0)
    window.remove_cue()
    window.undo()
    cue = window.show_model.cues[0]
    assert cue.volume == 0.25 and cue.trim_in == 2.0 and cue.notes == "keep me"
    assert cue.effects[0]["freq"] == 800.0


def test_redo_puts_the_change_back(window):
    three_cues(window)
    window.table.selectRow(0)
    window.remove_cue()
    window.undo()
    window.redo()
    assert names(window) == ["cue 1", "cue 2"]


def test_a_rename_can_be_undone(window):
    from cuerunner.main import NAME_COL

    three_cues(window)
    window.table.item(0, NAME_COL).setText("new name")
    assert window.show_model.cues[0].name == "new name"
    window.undo()
    assert window.show_model.cues[0].name == "cue 0"


def test_undo_does_nothing_when_there_is_nothing_to_undo(window):
    three_cues(window)
    window.undo()
    window.undo()
    assert names(window) == ["cue 0", "cue 1", "cue 2"]


def test_several_changes_undo_one_at_a_time(window):
    three_cues(window)
    for row in (2, 1):
        window.table.selectRow(row)
        window.remove_cue()
    assert names(window) == ["cue 0"]
    window.undo()
    assert names(window) == ["cue 0", "cue 1"]
    window.undo()
    assert names(window) == ["cue 0", "cue 1", "cue 2"]


def test_a_run_of_small_edits_undoes_as_one_step(window):
    """Dragging a slider must not fill the undo list with a hundred steps."""
    three_cues(window)
    window.table.selectRow(0)
    window._set_mode(editing=True)
    before = len(window._undo)
    for value in range(1, 12):
        window.insp_pre.setValue(value / 10)
    assert len(window._undo) - before <= 2


def test_the_menu_says_what_it_would_undo(window):
    three_cues(window)
    window.table.selectRow(0)
    window.remove_cue()
    assert window.undo_action.isEnabled()
    assert "Remove Cue" in window.undo_action.text()
    window.undo()
    assert "Remove Cue" in window.redo_action.text()


def test_a_new_change_clears_what_was_undone(window):
    three_cues(window)
    window.table.selectRow(0)
    window.remove_cue()
    window.undo()
    window.table.selectRow(2)
    window.remove_cue()
    assert not window._redo  # the old redo no longer applies
    assert names(window) == ["cue 0", "cue 1"]


def test_the_undo_list_does_not_grow_for_ever(window):
    from cuerunner.model import Cue

    for i in range(200):
        window.show_model.cues.append(Cue(number=str(i), name=f"c{i}", kind="memo"))
        window._remember(f"step {i}")  # a different label each time, so none are merged
    assert len(window._undo) <= window.UNDO_DEPTH


# ----------------------------------------------------------------- cues layering
def test_go_leaves_a_playing_cue_alone(window, tmp_path):
    """Firing an effect over music must not stop the music, as QLab does it."""
    import numpy as np
    import soundfile as sf

    from cuerunner.engine import Mixer
    from cuerunner.model import Cue

    tone = tmp_path / "t.wav"
    sf.write(tone, np.full((48000, 1), 0.3, dtype=np.float32), 48000)
    window.mixer = Mixer(rate=48000, out_channels=2)
    window.show_model.cues.append(Cue(number="1", name="music", kind="audio", file=str(tone)))
    window.show_model.cues.append(Cue(number="2", name="effect", kind="audio", file=str(tone)))
    window._refresh_table()

    window.go_row(0)
    window.mixer.render(2400)
    window.go_row(1)
    sounding = [v for v in window._cue_voices if not v.done and not v.stop_when_done]
    assert len(sounding) == 2
    window.panic()


def test_a_cue_can_still_clear_the_others_when_told_to(window, tmp_path):
    """The Triggers option is now the only thing that stops other cues."""
    import numpy as np
    import soundfile as sf

    from cuerunner.engine import Mixer
    from cuerunner.model import Cue

    tone = tmp_path / "t.wav"
    sf.write(tone, np.full((48000, 1), 0.3, dtype=np.float32), 48000)
    window.mixer = Mixer(rate=48000, out_channels=2)
    window.show_model.cues.append(Cue(number="1", name="music", kind="audio", file=str(tone)))
    window.show_model.cues.append(Cue(number="2", name="solo", kind="audio", file=str(tone),
                                      fade_stop_others=True, fade_stop_time=0.0))
    window._refresh_table()

    window.go_row(0)
    window.mixer.render(2400)
    window.go_row(1)
    window.mixer.render(4800)
    still_up = [v for v in window._cue_voices if not v.done and not v.stop_when_done]
    assert len(still_up) == 1
    window.panic()
