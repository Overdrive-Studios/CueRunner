"""The waveform view on the inspector's Time & Loops tab."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from cuerunner import waveform  # noqa: E402

RATE = 48000


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def wav(tmp_path):
    """Four seconds: a quiet first half and a loud second half, so peaks are easy to check."""
    import soundfile as sf

    quiet = np.full(RATE * 2, 0.1, dtype=np.float32)
    loud = np.full(RATE * 2, 0.9, dtype=np.float32)
    path = tmp_path / "tone.wav"
    sf.write(path, np.concatenate([quiet, loud])[:, None], RATE)
    waveform.clear_cache()
    return path


def test_peaks_follow_the_shape_of_the_file(wav):
    data = waveform.peaks(wav, buckets=100)
    assert data.shape == (100, 2)
    assert data[10][1] == pytest.approx(0.1, abs=0.02)  # quiet first half
    assert data[90][1] == pytest.approx(0.9, abs=0.02)  # loud second half


def test_peaks_are_cached_between_calls(wav):
    first = waveform.peaks(wav, buckets=50)
    assert waveform.peaks(wav, buckets=50) is first


def test_an_unreadable_file_gives_no_peaks_instead_of_raising(tmp_path):
    missing = waveform.peaks(tmp_path / "nope.wav")
    assert missing.shape == (0, 2)
    rubbish = tmp_path / "notaudio.wav"
    rubbish.write_text("this is not audio")
    assert waveform.peaks(rubbish).shape == (0, 2)


def test_the_view_says_so_when_there_is_nothing_to_draw(app):
    view = waveform.WaveformView()
    view.set_clip("", 0.0)
    assert not view.has_waveform()


def test_the_view_loads_a_files_waveform(app, wav):
    view = waveform.WaveformView()
    view.set_clip(str(wav), 4.0)
    assert view.has_waveform()


def click(view, x: float, kind=QEvent.Type.MouseButtonPress):
    point = QPointF(x, 10.0)
    event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    if kind == QEvent.Type.MouseButtonPress:
        view.mousePressEvent(event)
    else:
        view.mouseMoveEvent(event)


def ready_view(wav, width=400):
    view = waveform.WaveformView()
    view.resize(width, 80)
    view.set_clip(str(wav), 4.0)
    view.set_markers(0.0, 0.0)
    return view


def test_dragging_sets_the_loop_in_point(app, wav):
    view = ready_view(wav)
    seen = []
    view.markersChanged.connect(lambda a, b: seen.append((a, b)))
    click(view, 100)  # a quarter of the way along four seconds
    assert view.markers()[0] == pytest.approx(1.0, abs=0.02)
    assert seen and seen[-1][0] == pytest.approx(1.0, abs=0.02)


def test_dragging_the_out_marker_stores_zero_at_the_very_end(app, wav):
    view = ready_view(wav)
    click(view, 300)  # nearer the end, so this grabs the out marker
    assert view.markers()[1] == pytest.approx(3.0, abs=0.02)
    click(view, 399)  # back to the end of the file
    assert view.markers()[1] == 0.0  # 0 means "play to the end", as cues store it


def test_the_loop_points_cannot_cross(app, wav):
    view = ready_view(wav)
    view.set_markers(1.0, 2.0)
    click(view, 390)  # drag the in point past the out point
    assert view.markers()[0] < view.markers()[1]


def test_a_disabled_view_ignores_clicks(app, wav):
    view = ready_view(wav)
    view.setEnabled(False)
    click(view, 200)
    assert view.markers() == (0.0, 0.0)


def test_the_time_scale_uses_round_numbers(app, wav):
    view = ready_view(wav, width=600)
    assert view.tick_seconds() in (1.0, 2.0, 5.0)  # a round step for a four second file
    assert waveform.WaveformView.label_for(0) == "0:00"
    assert waveform.WaveformView.label_for(95) == "1:35"
