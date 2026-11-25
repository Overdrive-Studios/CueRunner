"""The Triggers tab: MIDI, wall clock and what happens to other cues when a cue starts."""

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.engine import Clip, Mixer, Voice, default_matrix  # noqa: E402


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()
        w.close()


def with_mixer(window):
    """The real app always has a mixer; tests stub out the sound card, so supply one."""
    window.mixer = Mixer(rate=48000, out_channels=2)
    return window.mixer


def a_voice(window, cue):
    clip = Clip(np.ones((48000, 1), dtype=np.float32))
    voice = Voice(clip=clip, matrix=default_matrix(1, 2), tag=cue)
    window._cue_voices.append(voice)
    return voice


def test_a_midi_note_runs_the_cue_listening_for_it(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="other", kind="memo"),
        Cue(number="2", name="mine", kind="memo", midi_trigger_on=True,
            midi_trigger_kind="note_on", midi_trigger_number=60, midi_trigger_channel=1),
    ])
    with mock.patch.object(window, "go_row") as go:
        window._fire_midi_trigger(("note_on", 60, 1))
    go.assert_called_once_with(1)


def test_the_wrong_note_or_channel_does_nothing(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(
        Cue(number="1", name="mine", kind="memo", midi_trigger_on=True,
            midi_trigger_kind="note_on", midi_trigger_number=60, midi_trigger_channel=2))
    with mock.patch.object(window, "go_row") as go:
        window._fire_midi_trigger(("note_on", 61, 2))   # wrong note
        window._fire_midi_trigger(("note_on", 60, 3))   # wrong channel
        window._fire_midi_trigger(("control_change", 60, 2))  # wrong kind
    go.assert_not_called()


def test_channel_zero_listens_on_every_channel(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(
        Cue(number="1", name="mine", kind="memo", midi_trigger_on=True,
            midi_trigger_number=7, midi_trigger_channel=0))
    with mock.patch.object(window, "go_row") as go:
        window._fire_midi_trigger(("note_on", 7, 11))
    go.assert_called_once_with(0)


def test_a_wall_clock_cue_fires_once_a_day(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="chime", kind="memo", wall_clock="00:00:01"))
    with mock.patch.object(window, "go_row") as go:
        window._check_wall_clock()
        window._check_wall_clock()  # the clock is still past it, but it has had its turn today
    go.assert_called_once()


def test_a_time_still_to_come_does_not_fire(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="chime", kind="memo", wall_clock="23:59:59"))
    with mock.patch.object(window, "go_row") as go:
        window._check_wall_clock()
    go.assert_not_called()


def test_fade_and_stop_fades_the_other_cues_out(window):
    from cuerunner.model import Cue

    with_mixer(window)
    mine = Cue(number="1", name="mine", kind="audio", fade_stop_others=True, fade_stop_time=2.0)
    theirs = Cue(number="2", name="theirs", kind="audio")
    a_voice(window, mine)
    other = a_voice(window, theirs)
    with mock.patch.object(window.mixer, "fade_out") as fade:
        window._start_options(mine)
    fade.assert_called_once_with(other, 2.0)


def test_ducking_lowers_the_others_then_restores_them(window):
    from cuerunner.model import Cue

    with_mixer(window)
    mine = Cue(number="1", name="mine", kind="audio", duck_others=True, duck_by_db=-12.0, duck_time=0.0)
    theirs = Cue(number="2", name="theirs", kind="audio")
    mine_voice = a_voice(window, mine)
    other = a_voice(window, theirs)
    window._start_options(mine)
    assert other.env == pytest.approx(0.251, rel=0.05)  # -12 dB

    mine_voice.done = True
    window._check_ducking()
    assert other.env == pytest.approx(1.0)  # back up once the ducking cue finished


def test_a_second_trigger_can_be_set_to_leave_a_running_cue_alone(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="mine", kind="audio", second_trigger="nothing")
    a_voice(window, cue)
    assert window._second_trigger(cue) is True  # the GO is used up, the cue plays on


def test_a_second_trigger_can_stop_a_running_cue(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="mine", kind="audio", second_trigger="stop")
    a_voice(window, cue)
    with mock.patch.object(window, "_reset_cue") as reset:
        assert window._second_trigger(cue) is True
    reset.assert_called_once_with(cue)


def test_restart_stops_the_cue_and_lets_the_go_start_it_again(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="mine", kind="audio", second_trigger="restart")
    a_voice(window, cue)
    with mock.patch.object(window, "_reset_cue") as reset:
        assert window._second_trigger(cue) is False  # False: carry on and start it
    reset.assert_called_once_with(cue)


def test_a_cue_that_is_not_running_is_simply_started(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="mine", kind="audio", second_trigger="stop")
    assert window._second_trigger(cue) is False
