"""The fader strip on the Levels tab."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.levels_ui import MIN_DB, OutputLevels  # noqa: E402


@pytest.fixture
def strip():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return OutputLevels()


def test_there_is_a_main_fader_plus_one_per_output(strip):
    strip.set_outputs(4)
    assert strip.output_count() == 4
    main, levels, mutes, solos = strip.values()
    assert len(levels) == len(mutes) == len(solos) == 4


def test_changing_the_output_count_rebuilds_the_strip(strip):
    strip.set_outputs(8)
    assert strip.output_count() == 8
    strip.set_outputs(2)
    assert strip.output_count() == 2


def test_values_go_in_and_come_back_out(strip):
    strip.set_outputs(2)
    strip.set_values(-3.0, [-6.0, 0.0], [False, True], [True, False])
    main, levels, mutes, solos = strip.values()
    assert main == -3.0
    assert levels == [-6.0, 0.0]
    assert mutes == [False, True]
    assert solos == [True, False]


def test_moving_a_fader_reports_a_change(strip):
    strip.set_outputs(2)
    seen = []
    strip.changed.connect(lambda: seen.append(1))
    strip.set_values(0.0, [0.0, 0.0], [False, False], [False, False])
    assert seen == []  # loading a cue is not an edit
    strip._strips[1]["slider"].setValue(-10)
    assert seen


def test_set_all_silent_and_set_default(strip):
    strip.set_outputs(2)
    strip.set_all(MIN_DB)
    main, levels, _, _ = strip.values()
    assert main == MIN_DB and levels == [MIN_DB, MIN_DB]
    strip.set_all(0)
    main, levels, _, _ = strip.values()
    assert main == 0 and levels == [0, 0]


def test_clearing_mutes_and_solos(strip):
    strip.set_outputs(2)
    strip.set_values(0.0, [0.0, 0.0], [True, True], [True, True])
    strip.clear_mutes()
    assert strip.values()[2] == [False, False]
    strip.clear_solos()
    assert strip.values()[3] == [False, False]


def test_silence_reads_as_minus_infinity(strip):
    strip.set_outputs(1)
    strip.set_values(MIN_DB, [MIN_DB], [False], [False])
    assert strip._strips[0]["readout"].text() == "-∞"


def test_show_mode_locks_the_faders(strip):
    strip.set_outputs(2)
    strip.set_editable(False)
    assert not strip._strips[0]["slider"].isEnabled()
    assert not strip._strips[1]["mute"].isEnabled()
