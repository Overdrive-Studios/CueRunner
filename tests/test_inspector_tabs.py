"""The inspector's tabs: I/O, Levels, Trim, Objects and Audio FX, and what they do to playback."""

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cuerunner.engine import Clip, Voice, default_matrix  # noqa: E402


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w.close()


def select(window, cue):
    window.show_model.cues.append(cue)
    window._refresh_table()
    window.table.selectRow(len(window.show_model.cues) - 1)
    window._set_mode(editing=True)


def a_voice(frames: int = 48000, channels: int = 1) -> tuple[Voice, Clip]:
    clip = Clip(np.zeros((frames, channels), dtype=np.float32))
    return Voice(clip=clip, matrix=default_matrix(channels, 2)), clip


def test_every_tab_is_enabled_and_has_its_own_page(window):
    from cuerunner.main import INSPECTOR_TABS

    assert window.insp_stack.count() == len(INSPECTOR_TABS)
    for index in range(len(INSPECTOR_TABS)):
        assert window.insp_tabs.isTabEnabled(index), INSPECTOR_TABS[index]
        window.insp_tabs.setCurrentIndex(index)
        assert window.insp_stack.currentIndex() == index


def test_the_levels_tab_sets_the_cues_own_level(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Song", kind="audio"))
    window.insp_volume.setValue(-6.0)
    assert window.show_model.cues[0].volume == pytest.approx(0.501, rel=0.01)
    window.insp_volume.setValue(-60.0)  # the bottom of the range is silence
    assert window.show_model.cues[0].volume == 0.0


def test_the_level_reaches_the_playing_voice(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="Song", kind="audio", volume=0.25)
    voice, clip = a_voice()
    window._apply_levels(voice, cue, clip)
    assert voice.gain == pytest.approx(0.25)


def test_the_trim_tab_sets_where_the_file_starts_and_stops(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Song", kind="audio"))
    window.insp_trim_in.setValue(2.0)
    window.insp_trim_out.setValue(5.0)
    cue = window.show_model.cues[0]
    assert (cue.trim_in, cue.trim_out) == (2.0, 5.0)
    voice, clip = a_voice(frames=48000 * 10)
    window._apply_levels(voice, cue, clip)
    assert voice.position == 2 * 48000
    assert voice.end == 5 * 48000


def test_a_trimmed_voice_stops_at_its_trim_out():
    clip = Clip(np.ones((1000, 1), dtype=np.float32))
    voice = Voice(clip=clip, matrix=default_matrix(1, 2), end=400)
    assert len(voice.read(300)) == 300
    assert len(voice.read(300)) == 100  # only up to the trim out
    assert voice.should_end()


def test_the_audio_fx_tab_only_builds_an_eq_when_a_band_is_moved(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Song", kind="audio"))
    cue = window.show_model.cues[0]
    voice, clip = a_voice()
    window._apply_levels(voice, cue, clip)
    assert voice.fx is None  # flat bands cost nothing at playback

    window.insp_eq_low.setValue(6.0)
    assert cue.eq_low == 6.0
    voice, clip = a_voice()
    window._apply_levels(voice, cue, clip)
    assert voice.fx is not None and not voice.fx.flat()


def test_the_io_tab_shows_the_cues_routing(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Song", kind="audio", route=[[1, 2], [3]]))
    assert window.insp_route_label.text() == "In 1 → 1, 2; In 2 → 3"


def test_the_objects_tab_follows_the_cue_type(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Clip", kind="video"))
    assert window.insp_opacity.isEnabled()
    window.insp_opacity.setValue(50)
    assert window.show_model.cues[0].opacity == pytest.approx(0.5)

    select(window, Cue(number="2", name="Note", kind="memo"))
    assert not window.insp_opacity.isEnabled()
    assert "memo cue" in window.insp_objects_note.text()


def test_inspector_fields_are_read_only_in_show_mode(window):
    from cuerunner.model import Cue

    select(window, Cue(number="1", name="Song", kind="audio"))
    window._set_mode(editing=False)
    assert not window.insp_volume.isEnabled()
    assert not window.insp_trim_in.isEnabled()
    assert not window.insp_route_button.isEnabled()


def test_the_new_cue_settings_survive_saving_and_reopening(tmp_path):
    from cuerunner.model import Cue, Show

    show = Show()
    show.cues.append(Cue(number="1", name="Song", kind="audio", volume=0.5, trim_in=1.0,
                         trim_out=4.0, eq_low=3.0, eq_mid=-2.0, eq_high=1.5, notes="Check levels"))
    path = tmp_path / "show.json"
    show.save(path)
    cue = Show.load(path).cues[0]
    assert (cue.volume, cue.trim_in, cue.trim_out) == (0.5, 1.0, 4.0)
    assert (cue.eq_low, cue.eq_mid, cue.eq_high) == (3.0, -2.0, 1.5)
    assert cue.notes == "Check levels"


def test_the_inspector_is_capped_so_the_cue_list_keeps_its_room(window):
    """At the smallest window size the inspector scrolls rather than squeezing out the cues."""
    from cuerunner.main import INSPECTOR_MAX_HEIGHT

    assert window.insp_scroll.maximumHeight() == INSPECTOR_MAX_HEIGHT
    assert window.table.minimumHeight() >= 140
    window.resize(900, 620)
    window.show()
    QtWidgets.QApplication.processEvents()
    assert window.table.height() >= 140


@pytest.fixture
def wav_file(tmp_path):
    import soundfile as sf

    from cuerunner import waveform

    path = tmp_path / "cue.wav"
    sf.write(path, np.full((48000 * 4, 1), 0.5, dtype=np.float32), 48000)
    waveform.clear_cache()
    return path


def open_time_tab(window):
    from cuerunner.main import INSPECTOR_TABS

    window.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Time & Loops"))


def test_the_time_and_loops_tab_shows_the_waveform(window, wav_file):
    from cuerunner.model import Cue

    open_time_tab(window)
    select(window, Cue(number="1", name="Song", kind="audio", file=str(wav_file),
                       duration_ms=4000, loop_in=1.0, loop_out=3.0))
    assert window.insp_wave.has_waveform()
    assert window.insp_wave.markers() == (1.0, 3.0)


def test_no_waveform_is_loaded_while_another_tab_is_open(window, wav_file):
    """Reading peaks means reading the file, so it waits until the tab is actually showing."""
    from cuerunner.model import Cue

    window.insp_tabs.setCurrentIndex(0)  # Basics
    select(window, Cue(number="1", name="Song", kind="audio", file=str(wav_file), duration_ms=4000))
    assert not window.insp_wave.has_waveform()
    open_time_tab(window)  # switching to the tab loads it
    assert window.insp_wave.has_waveform()


def test_dragging_a_marker_updates_the_cue(window, wav_file):
    from cuerunner.model import Cue

    open_time_tab(window)
    select(window, Cue(number="1", name="Song", kind="audio", file=str(wav_file), duration_ms=4000))
    window.insp_wave.markersChanged.emit(1.5, 2.5)
    cue = window.show_model.cues[0]
    assert (cue.loop_in, cue.loop_out) == (1.5, 2.5)
    assert window.insp_loop_in.value() == pytest.approx(1.5)


def test_a_cue_with_no_audio_file_shows_no_waveform(window):
    from cuerunner.model import Cue

    open_time_tab(window)
    select(window, Cue(number="1", name="Note", kind="memo"))
    assert not window.insp_wave.has_waveform()
