"""Video cues, and the Time & Loops panel for files whose wave cannot be drawn.

Three reported faults: a video played its sound but showed no picture when the Mac had no
second display; Time & Loops showed nothing for a video cue; and setting a start time did
nothing. All three were the same story - video cues went through a path that ignored
almost everything the inspector said, and the waveform gave up on any file it could not
read peaks from.
"""

from __future__ import annotations

import os
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
soundfile = pytest.importorskip("soundfile")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from cuerunner import video_probe, waveform  # noqa: E402
from cuerunner.engine import Mixer  # noqa: E402
from cuerunner.main import INSPECTOR_TABS, MainWindow  # noqa: E402
from cuerunner.model import Cue  # noqa: E402

RATE = 48000


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    seconds = np.arange(RATE * 6) / RATE
    soundfile.write(path, (0.4 * np.sin(2 * np.pi * 220 * seconds)).astype("float32"), RATE)
    return str(path)


@pytest.fixture(scope="module")
def movie(tmp_path_factory):
    """A file the wave reader cannot open, standing in for a video."""
    path = tmp_path_factory.mktemp("video") / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
    return str(path)


def view(app, duration=90.0, peaks=0) -> waveform.WaveformView:
    widget = waveform.WaveformView()
    widget.resize(820, 130)
    widget._duration = duration
    widget._peaks = np.zeros((peaks, 2), dtype=np.float32)
    widget._message = "" if peaks else "No waveform for this file"
    return widget


def drag(widget, from_x, to_x):
    y = widget.height() - 10
    for kind, button, buttons in (
            (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)):
        x = from_x if kind == QEvent.Type.MouseButtonPress else to_x
        event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: widget.mousePressEvent,
         QEvent.Type.MouseMove: widget.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: widget.mouseReleaseEvent}[kind](event)


# --- the waveform panel ----------------------------------------------
def test_a_file_with_no_readable_wave_still_gets_its_scale_and_markers(app):
    widget = view(app)
    assert widget.is_usable()
    assert not widget.has_waveform()
    widget.set_range(12.0, 70.0)
    widget.set_markers(20.0, 55.0)
    widget.grab()  # used to draw a message and nothing else


def test_with_no_file_at_all_there_is_nothing_to_draw(app):
    widget = waveform.WaveformView()
    widget.resize(820, 130)
    widget.set_clip("", 0.0)
    assert not widget.is_usable()
    widget.grab()


def test_the_start_marker_can_be_dragged_on_a_file_with_no_wave(app):
    widget = view(app)
    widget.set_range(10.0, 0.0)
    seen = []
    widget.rangeChanged.connect(lambda s, e: seen.append((s, e)))
    drag(widget, widget._x_for(10.0), widget._x_for(30.0))
    assert seen
    assert widget.range()[0] == pytest.approx(30.0, abs=0.3)


def test_the_end_marker_stores_zero_when_dragged_to_the_very_end(app):
    widget = view(app)
    widget.set_range(0.0, 40.0)
    drag(widget, widget._x_for(40.0), widget.width() + 20)
    assert widget.range()[1] == 0.0  # 0 means "to the end of the file"


def test_the_start_cannot_be_dragged_past_the_end(app):
    widget = view(app)
    widget.set_range(5.0, 20.0)
    drag(widget, widget._x_for(5.0), widget._x_for(60.0))
    start, end = widget.range()
    assert start < end


def test_the_loop_markers_still_work_alongside_the_new_ones(app):
    widget = view(app)
    widget.set_range(0.0, 0.0)
    widget.set_markers(30.0, 60.0)
    seen = []
    widget.markersChanged.connect(lambda a, b: seen.append((a, b)))
    drag(widget, widget._x_for(30.0), widget._x_for(40.0))
    assert seen
    assert widget.markers()[0] == pytest.approx(40.0, abs=0.3)


def test_the_nearest_marker_is_the_one_that_gets_grabbed(app):
    widget = view(app)
    widget.set_range(10.0, 80.0)
    widget.set_markers(12.0, 60.0)
    assert widget._grab(widget._x_for(10.0)) == "start"
    assert widget._grab(widget._x_for(12.0)) == "in"
    assert widget._grab(widget._x_for(45.0)) == ""


# --- the inspector ----------------------------------------------------
@pytest.fixture
def window(app, tone, movie):
    def fake_output(self):
        self.mixer = Mixer(rate=RATE, out_channels=2)
        self.output = None
        self._clips = {}
        self._cue_voices = []

    with mock.patch.object(MainWindow, "_open_default_output", fake_output):
        w = MainWindow()
    w.show_model.cues.append(Cue(number="1", name="bed", kind="audio", file=tone))
    w.show_model.cues.append(Cue(number="2", name="clip", kind="video", file=movie))
    w._editing = True
    w._refresh_table()
    w.show()
    app.processEvents()
    yield w
    w.hard_stop()
    w._mark_saved()
    w.close()


def test_a_video_cue_gets_its_file_into_the_waveform_panel(app, window):
    window.table.selectRow(1)
    window.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Time & Loops"))
    for _ in range(3):
        app.processEvents()
    # The length arrives from the media engine later, so stand it in here.
    window.show_model.cues[1].duration_ms = 90_000
    window._refresh_waveform()
    assert window.insp_wave.is_usable()


def test_dragging_the_start_marker_writes_the_cues_start_time(app, window):
    window.table.selectRow(0)
    window.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Time & Loops"))
    for _ in range(3):
        app.processEvents()
    cue = window.show_model.cues[0]
    window._wave_range_changed(2.5, 4.0)
    assert cue.trim_in == 2.5
    assert cue.trim_out == 4.0
    assert window.insp_start_time.value() == pytest.approx(2.5)
    assert window.insp_end_time.value() == pytest.approx(4.0)


def test_dragging_the_start_marker_can_be_undone(app, window):
    window.table.selectRow(0)
    window._wave_range_changed(2.5, 0.0)
    assert window.show_model.cues[0].trim_in == 2.5
    window.undo()
    assert window.show_model.cues[0].trim_in == 0.0


def test_the_markers_do_nothing_in_show_mode(app, window):
    window._set_mode(editing=False)
    window.table.selectRow(0)
    window._wave_range_changed(2.5, 0.0)
    assert window.show_model.cues[0].trim_in == 0.0


# --- video playback ---------------------------------------------------
def test_a_video_cue_passes_its_settings_through(app, window):
    cue = window.show_model.cues[1]
    cue.trim_in, cue.trim_out, cue.rate, cue.volume = 3.0, 9.0, 1.5, 0.5
    cue.play_count = 2
    with mock.patch.object(window._video, "play") as play, \
         mock.patch.object(window._video, "set_opacity"):
        window.go_row(1)
    kwargs = play.call_args.kwargs
    assert kwargs["start"] == 3.0
    assert kwargs["end"] == 9.0
    assert kwargs["rate"] == 1.5
    assert kwargs["volume"] == pytest.approx(0.5)
    assert kwargs["loops"] == 2


def test_a_looping_video_cue_asks_for_endless_loops(app, window):
    cue = window.show_model.cues[1]
    cue.loop = True
    with mock.patch.object(window._video, "play") as play, \
         mock.patch.object(window._video, "set_opacity"):
        window.go_row(1)
    assert play.call_args.kwargs["loops"] == 0


def test_the_output_window_is_brought_to_the_front(app):
    """On one display the output opened behind the main window: sound, no picture."""
    from cuerunner import video_out

    output = video_out._Output.__new__(video_out._Output)
    output.widget = mock.Mock()
    output.widget.windowHandle.return_value = mock.Mock()
    output.screen = mock.Mock()
    video_out._Output.show_on_screen(output, "fit")
    assert output.widget.showFullScreen.called
    assert output.widget.raise_.called
    # and it must not take the keyboard away from the main window
    assert not output.widget.activateWindow.called


def test_the_video_stops_at_its_end_time(app):
    from cuerunner import video_out

    output = video_out._Output.__new__(video_out._Output)
    output.player = mock.Mock()
    output.stop_at_ms = 5_000
    video_out._Output._check_end(output, 4_000)
    assert not output.player.stop.called
    video_out._Output._check_end(output, 5_100)
    assert output.player.stop.called
    assert output.stop_at_ms == 0  # and only once


def test_a_video_with_no_end_time_plays_right_through(app):
    from cuerunner import video_out

    output = video_out._Output.__new__(video_out._Output)
    output.player = mock.Mock()
    output.stop_at_ms = 0
    video_out._Output._check_end(output, 10_000_000)
    assert not output.player.stop.called


# --- how long is the video --------------------------------------------
def test_a_length_once_found_is_remembered(app, movie):
    video_probe.clear_cache()
    probe = video_probe.DurationProbe()
    try:
        assert probe.request(movie) is None  # not known yet, so it goes off to ask
        video_probe.remember(movie, 90_000)
        assert probe.request(movie) == 90_000
        assert video_probe.known_duration_ms(movie) == 90_000
    finally:
        probe.close()


def test_a_file_the_engine_cannot_open_is_not_asked_about_twice(app, movie):
    video_probe.clear_cache()
    probe = video_probe.DurationProbe()
    seen = []
    probe.ready.connect(lambda path, ms: seen.append((path, ms)))
    try:
        probe.request(movie)
        probe._current = movie
        probe._give_up()
        assert seen == [(movie, 0)]
        assert probe.request(movie) is None  # not asked again, but not written off either
        assert video_probe.known_duration_ms(movie) is None
    finally:
        probe.close()


def test_an_answer_fills_in_every_cue_using_that_file(app, window, movie):
    window.show_model.cues.append(Cue(number="3", name="same clip", kind="video", file=movie))
    window._refresh_table()
    window._video_duration_known(movie, 45_000)
    lengths = [c.duration_ms for c in window.show_model.cues if c.file == movie]
    assert lengths == [45_000, 45_000]


def test_asking_for_nothing_is_harmless(app):
    probe = video_probe.DurationProbe()
    try:
        assert probe.request("") is None
    finally:
        probe.close()
