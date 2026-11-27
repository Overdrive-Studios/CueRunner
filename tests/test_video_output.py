"""The video output window: why it showed black, and why black stayed after Escape.

Two faults with one root each. The camera session and the media player were both given
the same widget, and whichever is set second takes the video sink, so the player drew
nowhere and a video cue played its sound over a black screen. And the window was put up
with showFullScreen(), which on a Mac asks for a native full screen Space: hiding the
window leaves the Space behind, so the screen stayed black after the cue stopped.
"""

from __future__ import annotations

import inspect
import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from cuerunner import video_out  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def bare_output():
    """An _Output without touching the sound card, which a test machine may not have."""
    out = video_out._Output.__new__(video_out._Output)
    out.widget = mock.Mock()
    out.widget.windowHandle.return_value = mock.Mock()
    out.player = mock.Mock()
    out.session = mock.Mock()
    out.audio = mock.Mock()
    out.camera = None
    out.text_window = None
    out.cue_number = ""
    out.stop_at_ms = 0
    out.showing = ""
    out.screen = mock.Mock()
    return out


# --- how the window is put up -----------------------------------------
def test_the_output_never_asks_for_a_native_full_screen(app):
    """showFullScreen leaves a black Space behind on a Mac when the window is hidden."""
    import ast

    tree = ast.parse(inspect.getsource(video_out))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "showFullScreen" not in calls


def test_the_window_covers_the_screen_and_sits_above_the_app(app):
    widget = QtWidgets.QWidget()
    screen = QGuiApplication.primaryScreen()
    try:
        video_out._stage_window(widget, screen)
        flags = widget.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert widget.isVisible()
        assert not widget.isFullScreen()  # a plain window, not a Space of its own
        assert widget.geometry().size() == screen.geometry().size()
    finally:
        widget.close()


def test_hiding_the_window_puts_the_desktop_straight_back(app):
    widget = QtWidgets.QWidget()
    screen = QGuiApplication.primaryScreen()
    try:
        video_out._stage_window(widget, screen)
        assert widget.isVisible()
        widget.hide()
        assert not widget.isVisible()
    finally:
        widget.close()


# --- who holds the widget ---------------------------------------------
def test_only_one_of_the_player_and_the_camera_holds_the_widget(app):
    out = bare_output()
    video_out._Output.take_sink(out, "video")
    out.session.setVideoOutput.assert_called_once_with(None)
    out.player.setVideoOutput.assert_called_once_with(out.widget)
    assert out.showing == "video"

    out.player.reset_mock()
    out.session.reset_mock()
    video_out._Output.take_sink(out, "camera")
    out.player.setVideoOutput.assert_called_once_with(None)
    out.session.setVideoOutput.assert_called_once_with(out.widget)
    assert out.showing == "camera"


def test_building_an_output_does_not_hand_the_widget_to_the_camera_session(app):
    """Doing that at build time is what made every video cue play over a black screen."""
    source = inspect.getsource(video_out._Output.__init__)
    assert "session.setVideoOutput" not in source


def test_playing_a_video_takes_the_sink_for_the_player(app):
    outputs = video_out.VideoOutputs()
    out = bare_output()
    with mock.patch.object(video_out.VideoOutputs, "_output_for", return_value=out):
        outputs.play("screen:0", "/x.mp4")
    assert out.showing == "video"
    out.player.setVideoOutput.assert_called_once_with(out.widget)


def test_showing_a_camera_takes_the_sink_for_the_camera(app):
    outputs = video_out.VideoOutputs()
    out = bare_output()
    with mock.patch.object(video_out.VideoOutputs, "_output_for", return_value=out), \
         mock.patch.object(video_out, "_find_camera", return_value=mock.Mock()), \
         mock.patch.object(video_out, "QCamera", return_value=mock.Mock()):
        outputs.show_camera("screen:0", "Some camera")
    assert out.showing == "camera"
    out.session.setVideoOutput.assert_called_once_with(out.widget)


# --- clearing the screen ----------------------------------------------
def test_blanking_takes_everything_off_the_display(app):
    out = bare_output()
    out.text_window = mock.Mock()
    out.showing = "video"
    video_out._Output.blank(out)
    assert out.player.stop.called
    assert out.text_window.hide.called
    assert out.widget.hide.called
    assert out.showing == ""


def test_panic_clears_every_display(app):
    outputs = video_out.VideoOutputs()
    first, second = bare_output(), bare_output()
    outputs._outputs = {0: first, 1: second}
    outputs.stop_all()
    for out in (first, second):
        assert out.widget.hide.called
        assert out.player.stop.called
        assert out.showing == ""


def test_a_video_that_runs_out_takes_itself_off_the_screen(app):
    from PySide6.QtMultimedia import QMediaPlayer

    out = bare_output()
    out.showing = "video"
    with mock.patch.object(video_out.QTimer, "singleShot") as later:
        video_out._Output._check_finished(out, QMediaPlayer.MediaStatus.EndOfMedia)
    assert later.called
    assert later.call_args.args[0] == 0  # deferred, not called inside the signal


def test_a_camera_is_not_taken_off_by_the_players_end_of_media(app):
    from PySide6.QtMultimedia import QMediaPlayer

    out = bare_output()
    out.showing = "camera"
    with mock.patch.object(video_out.QTimer, "singleShot") as later:
        video_out._Output._check_finished(out, QMediaPlayer.MediaStatus.EndOfMedia)
    assert not later.called


def test_reaching_the_end_time_clears_the_screen_rather_than_freezing_on_it(app):
    out = bare_output()
    out.stop_at_ms = 5_000
    with mock.patch.object(video_out.QTimer, "singleShot") as later:
        video_out._Output._check_end(out, 4_000)
        assert not later.called
        video_out._Output._check_end(out, 5_100)
    assert later.called
    assert not out.player.stop.called  # never from inside the player's own signal
    assert out.stop_at_ms == 0


def test_closing_the_show_closes_the_output_windows(app):
    outputs = video_out.VideoOutputs()
    out = bare_output()
    out.text_window = mock.Mock()
    outputs._outputs = {0: out}
    outputs.close()
    assert out.widget.close.called
    assert out.text_window.close.called
    assert outputs._outputs == {}


# --- Escape has to clear the screen, whatever else goes wrong ----------
def test_hiding_works_its_way_up_until_the_window_is_gone(app):
    out = bare_output()
    out.widget.isVisible.return_value = True  # a window that refuses to go quietly
    out.text_window = None
    video_out._Output._hide_window(out)
    assert out.widget.hide.called
    assert out.widget.close.called
    assert out.widget.destroy.called


def test_a_window_that_hides_is_left_alone(app):
    out = bare_output()
    out.widget.isVisible.return_value = False
    out.text_window = None
    video_out._Output._hide_window(out)
    assert out.widget.hide.called
    assert not out.widget.close.called
    assert not out.widget.destroy.called


def test_the_window_goes_before_the_player_is_stopped(app):
    """Nothing that might fail may stand between Escape and a clear screen."""
    out = bare_output()
    order = []
    out.widget.hide.side_effect = lambda: order.append("hide")
    out.player.stop.side_effect = lambda: order.append("stop")
    out.widget.isVisible.return_value = False
    video_out._Output.blank(out)
    assert order.index("hide") < order.index("stop")


def test_one_step_failing_does_not_stop_the_rest(app):
    out = bare_output()
    out.widget.isVisible.return_value = False
    out.player.stop.side_effect = RuntimeError("the media engine is unwell")
    out.text_window = mock.Mock()
    out.text_window.isVisible.return_value = False
    video_out._Output.blank(out)  # must not raise
    assert out.widget.hide.called
    assert out.showing == ""


def test_one_display_failing_does_not_leave_the_others_up(app):
    outputs = video_out.VideoOutputs()
    broken, fine = bare_output(), bare_output()
    broken.widget.hide.side_effect = RuntimeError("stuck")
    broken.widget.isVisible.return_value = False
    fine.widget.isVisible.return_value = False
    outputs._outputs = {0: broken, 1: fine}
    outputs.stop_all()
    assert fine.widget.hide.called


def test_panic_fades_the_picture_down_and_then_clears_it(app):
    outputs = video_out.VideoOutputs()
    out = bare_output()
    out.widget.isVisible.return_value = True
    outputs._outputs = {0: out}
    with mock.patch.object(video_out.QTimer, "singleShot") as later:
        outputs.fade_out(1.0)
    delays = [call.args[0] for call in later.call_args_list]
    assert len(delays) > 2  # a run of steps, not one jump
    assert max(delays) >= 1000  # and a final clear once the fade is done
    assert later.call_args_list[-1].args[1] == outputs.stop_all


def test_a_display_that_is_not_showing_anything_is_not_faded(app):
    outputs = video_out.VideoOutputs()
    out = bare_output()
    out.widget.isVisible.return_value = False
    outputs._outputs = {0: out}
    with mock.patch.object(video_out.QTimer, "singleShot") as later:
        outputs.fade_out(1.0)
    assert len(later.call_args_list) == 1  # just the clear at the end
