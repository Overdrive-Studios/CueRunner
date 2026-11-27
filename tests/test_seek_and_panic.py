"""Clicking the waveform to move through a track, and Escape fading rather than cutting."""

import os
from unittest import mock

import numpy as np
import pytest
import soundfile as sf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.engine import Mixer
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        w.mixer = Mixer(rate=48000, out_channels=2)
        yield w
        w._mark_saved()
        w.close()


@pytest.fixture
def tone(tmp_path):
    path = tmp_path / "tone.wav"
    sf.write(path, np.full((48000 * 4, 1), 0.4, dtype=np.float32), 48000)
    from cuerunner import waveform

    waveform.clear_cache()
    return str(path)


def playing_cue(window, tone, **fields):
    from cuerunner.main import INSPECTOR_TABS
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio", file=tone,
                                      duration_ms=4000, **fields))
    window._refresh_table()
    window.table.selectRow(0)
    window.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Time & Loops"))
    window.go_row(0)
    return window.show_model.cues[0]


def level(window, frames=4800):
    block = window.mixer.render(frames)
    return float(np.sqrt(np.mean(np.asarray(block, dtype=np.float64) ** 2))) if block is not None else 0.0


# ----------------------------------------------------------------- seeking
def test_clicking_the_waveform_jumps_the_playing_track(window, tone):
    cue = playing_cue(window, tone)
    window.mixer.render(2400)
    window._seek_selected_cue(3.0)
    voice = next(v for v in window._cue_voices if v.tag is cue)
    assert voice.position == pytest.approx(3.0 * 48000, abs=10)


def test_seeking_past_the_end_lands_inside_the_file(window, tone):
    cue = playing_cue(window, tone)
    window._seek_selected_cue(9999.0)
    voice = next(v for v in window._cue_voices if v.tag is cue)
    assert voice.position < voice.clip.frames


def test_the_playhead_follows_the_track_as_it_plays(window, tone):
    playing_cue(window, tone)
    window.mixer.render(48000)  # a second of audio
    window._follow_playhead()
    assert window.insp_wave.playhead() == pytest.approx(1.0, abs=0.05)


def test_the_playhead_starts_where_the_cue_starts(window, tone):
    playing_cue(window, tone, trim_in=2.0)
    assert window.insp_wave.playhead() == pytest.approx(2.0)


def test_seeking_a_cue_that_is_not_playing_does_nothing_bad(window, tone):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio", file=tone))
    window._refresh_table()
    window.table.selectRow(0)
    window._seek_selected_cue(2.0)  # must not raise


# ----------------------------------------------------------------- panic fades
def test_escape_fades_out_rather_than_cutting(window, tone):
    """A hard cut on a music bed is ugly; QLab fades over the panic duration."""
    window.show_model.panic_duration = 0.5
    playing_cue(window, tone)
    assert level(window) > 0.1
    window.panic()
    during = level(window)
    assert during > 0.01, "the sound stopped dead instead of fading"
    assert window._panicking


def test_the_fade_finishes_and_everything_stops(window, tone):
    window.show_model.panic_duration = 0.05
    playing_cue(window, tone)
    window.panic()
    window.mixer.render(48000)  # let the fade run out
    window._finish_panic()
    assert level(window) < 0.01
    assert not window._panicking


def test_pressing_escape_twice_cuts_it_dead(window, tone):
    window.show_model.panic_duration = 5.0
    playing_cue(window, tone)
    window._panic_pressed()
    window._panic_pressed()  # straight away, so it counts as a double tap
    assert level(window) == 0.0
    assert not window._panicking


def test_a_second_panic_during_the_fade_stops_at_once(window, tone):
    window.show_model.panic_duration = 5.0
    playing_cue(window, tone)
    window.panic()
    window.panic()
    assert level(window) == 0.0


def test_a_panic_duration_of_zero_is_an_immediate_stop(window, tone):
    window.show_model.panic_duration = 0.0
    playing_cue(window, tone)
    window.panic()
    assert level(window) == 0.0


def test_the_panic_duration_is_saved_with_the_show(tmp_path):
    from cuerunner.model import Show

    show = Show()
    show.panic_duration = 2.5
    path = tmp_path / "show.json"
    show.save(path)
    assert Show.load(path).panic_duration == 2.5


# --- the panic path must not be breakable -----------------------------
def test_escape_clears_the_screen_even_if_something_else_throws(window):
    """Everything in hard_stop stands alone, and the displays go first."""
    from unittest import mock as _mock

    order = []
    window._video = _mock.Mock()
    window._video.stop_all.side_effect = lambda: order.append("video")
    with _mock.patch.object(type(window), "_pause_spotify",
                            side_effect=RuntimeError("spotify is unwell")), \
         _mock.patch.object(type(window), "_stop_timecode",
                            side_effect=RuntimeError("timecode is unwell")):
        window.hard_stop()
    assert order == ["video"]
    assert window._playing_cue is None
    assert window._panicking is False
    assert window._timers == []


def test_the_displays_are_cleared_before_anything_that_might_fail(window):
    from unittest import mock as _mock

    order = []
    window._video = _mock.Mock()
    window._video.stop_all.side_effect = lambda: order.append("video")
    with _mock.patch.object(type(window), "_pause_spotify",
                            side_effect=lambda self=None: order.append("spotify")):
        window.hard_stop()
    assert order[0] == "video"


def test_escape_fades_the_picture_as_well_as_the_sound(window):
    from unittest import mock as _mock

    window._video = _mock.Mock()
    window.show_model.panic_duration = 1.0
    window.panic()
    window._video.fade_out.assert_called_once_with(1.0)


def test_a_video_fade_that_fails_clears_the_screen_outright(window):
    from unittest import mock as _mock

    window._video = _mock.Mock()
    window._video.fade_out.side_effect = RuntimeError("no fading today")
    window.show_model.panic_duration = 1.0
    window.panic()
    assert window._video.stop_all.called


def test_a_mixer_that_throws_does_not_stop_the_picture_being_cleared(window):
    from unittest import mock as _mock

    window._video = _mock.Mock()
    window.mixer = _mock.Mock()
    window.mixer.voices.side_effect = RuntimeError("the mixer is unwell")
    window.show_model.panic_duration = 1.0
    window.panic()
    assert window._video.fade_out.called
