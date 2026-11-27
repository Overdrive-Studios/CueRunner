"""Video outputs: play video cues and camera cues full screen on a chosen display or route.

A display is named "screen:<n>", where n is its index in the order the system lists its displays.
A route is a named link to a display with a scaling mode ("full" stretches to the screen, "fit"
keeps the picture's shape). Cues store either kind of name in video_output, e.g. "route:Stage left".
Each display gets one window, created the first time a cue uses it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel
from PySide6.QtMultimedia import QAudioOutput, QCamera, QMediaCaptureSession, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

SCREEN_PREFIX = "screen:"
ROUTE_PREFIX = "route:"
FIT_MODES = ("full", "fit")


def screen_output(index: int) -> str:
    return f"{SCREEN_PREFIX}{index}"


def screen_index(output: str) -> int | None:
    """'screen:2' -> 2. Anything else (including an empty output) -> None."""
    if not output.startswith(SCREEN_PREFIX):
        return None
    try:
        return int(output[len(SCREEN_PREFIX):])
    except ValueError:
        return None


def route_output(name: str) -> str:
    return f"{ROUTE_PREFIX}{name}"


def _device_name(index: int, screen) -> str:
    """The system's name for the display, or a numbered fallback if it has none."""
    return screen.name() or f"Display {index + 1}"


def display_names() -> list[str]:
    """One label per connected display, in the same order screen_output() uses."""
    names = []
    for i, screen in enumerate(QGuiApplication.screens()):
        size = screen.size()
        names.append(f"Display {i + 1}: {_device_name(i, screen)} ({size.width()}×{size.height()})")
    return names


def display_details() -> list[tuple[str, str, str]]:
    """(device name, resolution, info) for each connected display, in screen_output() order."""
    rows = []
    primary = QGuiApplication.primaryScreen()
    for i, screen in enumerate(QGuiApplication.screens()):
        size = screen.size()
        info = []
        if screen.refreshRate():
            info.append(f"{screen.refreshRate():.0f} fps")
        if screen is primary:
            info.append("Main display")
        rows.append((_device_name(i, screen), f"{size.width()} × {size.height()}", ", ".join(info)))
    return rows


def output_choices(routes: list[dict]) -> list[tuple[str, str]]:
    """(label, value) for every place a video cue can go: each display, then each route."""
    choices = [(label, screen_output(i)) for i, label in enumerate(display_names())]
    for route in routes:
        name = route.get("name", "")
        if name:
            choices.append((f"Route: {name}", route_output(name)))
    return choices


def resolve_output(output: str, routes: list[dict]) -> tuple[int | None, str]:
    """Turn a cue's output into (screen index, fit mode). The index is None if it is not connected."""
    if output.startswith(ROUTE_PREFIX):
        name = output[len(ROUTE_PREFIX):]
        for route in routes:
            if route.get("name") == name:
                fit = route.get("fit") if route.get("fit") in FIT_MODES else "full"
                return screen_index(route.get("output", "")), fit
        return None, "full"
    return screen_index(output), "fit"


def camera_names() -> list[str]:
    return [device.description() for device in QMediaDevices.videoInputs()]


def _find_camera(name: str):
    for device in QMediaDevices.videoInputs():
        if device.description() == name:
            return device
    return None


def _stage_window(widget, screen) -> None:
    """Dress a window up as a video output: frameless, full screen, above the app.

    Deliberately not showFullScreen(). On a Mac that asks for a native full screen Space,
    and a window hidden out of one leaves the Space behind: the screen stays black after
    the cue has stopped. A frameless window the size of the screen, kept on top, covers
    the display just the same and goes away the moment it is hidden.
    """
    # Frameless, above the app, and never taking the keyboard: GO and Panic have to keep
    # working while a video plays. Only set when they are not already right, because
    # changing a window's flags tears its native window down and builds it again.
    wanted = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.WindowDoesNotAcceptFocus)
    if (widget.windowFlags() & wanted) != wanted:
        widget.setWindowFlags(widget.windowFlags() | wanted)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    widget.setScreen(screen)
    widget.setGeometry(screen.geometry())
    widget.show()
    handle = widget.windowHandle()
    if handle is not None:
        handle.setScreen(screen)
        handle.setGeometry(screen.geometry())
    widget.raise_()


class _Output:
    def __init__(self, screen) -> None:
        self.widget = QVideoWidget()
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        # The player and the camera session cannot both hold the widget: whichever is set
        # second takes the video sink and the other one draws nothing. That is why a video
        # cue played its sound over a black screen. They are swapped as they are used.
        self.session = QMediaCaptureSession()
        self.showing = ""  # "video", "camera", "text" or "" for nothing on screen
        self.camera: QCamera | None = None
        self.cue_number = ""
        self.stop_at_ms = 0  # where the cue's end time falls, or 0 to play to the end
        self.text_window: QLabel | None = None
        self.player.positionChanged.connect(self._check_end)
        self.player.mediaStatusChanged.connect(self._check_finished)
        self.widget.setGeometry(screen.geometry())
        self.screen = screen

    def show_text(self, text: str, size: int) -> None:
        if self.text_window is None:
            self.text_window = QLabel()
            self.text_window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            self.text_window.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.text_window.setWordWrap(True)
            self.text_window.setStyleSheet("background: black; color: white; padding: 40px;")
        font = self.text_window.font()
        font.setPointSize(max(8, size))
        self.text_window.setFont(font)
        self.text_window.setText(text)
        _stage_window(self.text_window, self.screen)
        self.widget.hide()
        self.showing = "text"

    def hide_text(self) -> None:
        if self.text_window is not None:
            self.text_window.hide()
            if self.text_window.isVisible():
                self.text_window.close()

    def show_on_screen(self, fit: str) -> None:
        self.widget.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatio if fit == "fit"
            else Qt.AspectRatioMode.IgnoreAspectRatio
        )
        _stage_window(self.widget, self.screen)

    def take_sink(self, who: str) -> None:
        """Give the widget to the player or to the camera, never to both at once."""
        if who == "video":
            self.session.setVideoOutput(None)
            self.player.setVideoOutput(self.widget)
        elif who == "camera":
            self.player.setVideoOutput(None)
            self.session.setVideoOutput(self.widget)
        self.showing = who

    def blank(self) -> None:
        """Take everything off the display and let the desktop back.

        The windows go first and the players afterwards. This is the panic path, so
        nothing that might fail is allowed to stand between pressing Escape and the screen
        being clear, and every step is on its own: one that fails cannot stop the others.
        """
        self.showing = ""
        for step in (self._hide_window, self.hide_text, self.player.stop, self.stop_camera):
            try:
                step()
            except Exception:
                pass

    def _hide_window(self) -> None:
        """Get the output window off the screen and keep it off.

        hide() alone has been known to leave a black window behind on a Mac, so this works
        its way up: hide, then close, then tear the native window down altogether. A window
        that no longer exists cannot be on screen. Each is built again by the next cue.
        """
        for widget in (self.widget, self.text_window):
            if widget is None:
                continue
            widget.setWindowOpacity(1.0)  # back to solid, ready for the next cue
            widget.hide()
            if widget.isVisible():
                widget.close()
            if widget.isVisible():
                widget.destroy()

    def _check_end(self, position_ms: int) -> None:
        """Stop at the cue's end time, which the media player knows nothing about.

        Deferred: stopping a player from inside its own position signal is a call back
        into the media engine while it is still in the middle of its own work.
        """
        if self.stop_at_ms and position_ms >= self.stop_at_ms:
            self.stop_at_ms = 0
            QTimer.singleShot(0, self.blank)

    def _check_finished(self, status) -> None:
        """When the video runs out, take it off the screen rather than leaving a still."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.showing == "video":
            QTimer.singleShot(0, self.blank)

    def stop_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.session.setCamera(None)


def _dim(out: "_Output", level: float) -> None:
    try:
        out.widget.setWindowOpacity(max(0.0, min(1.0, level)))
    except Exception:
        pass


class VideoOutputs(QObject):
    """Plays video and camera cues on displays. Owned by the main window; call close() when the show ends."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._outputs: dict[int, _Output] = {}

    def _output_for(self, output: str, routes: list[dict]) -> _Output:
        index, fit = resolve_output(output, routes)
        screens = QGuiApplication.screens()
        if index is None or not 0 <= index < len(screens):
            raise RuntimeError(f"The display for this cue is not connected ({output or 'not set'}).")
        out = self._outputs.get(index)
        if out is None:
            out = _Output(screens[index])
            self._outputs[index] = out
        out.show_on_screen(fit)
        return out

    def set_opacity(self, output: str, opacity: float, routes: list[dict] | None = None) -> None:
        """Fade the output window itself: 1 is solid, 0 is invisible. Values outside 0 to 1 are clamped."""
        out = self._output_for(output, routes or [])
        out.widget.setWindowOpacity(max(0.0, min(1.0, float(opacity))))

    def play(self, output: str, path: str, routes: list[dict] | None = None, number: str = "",
             start: float = 0.0, end: float = 0.0, volume: float = 1.0,
             rate: float = 1.0, loops: int = 1) -> None:
        """Play a video, honouring the cue's start and end, level, rate and play count.

        These all used to be ignored, so a video cue played the whole file at full level
        however the inspector was set.
        """
        out = self._output_for(output, routes or [])
        out.stop_camera()
        out.hide_text()
        out.take_sink("video")  # the player draws into the widget, not the camera session
        out.cue_number = number
        out.stop_at_ms = int(end * 1000) if end > 0 else 0
        out.audio.setVolume(max(0.0, min(1.0, float(volume))))
        out.player.setPlaybackRate(rate if rate and rate > 0 else 1.0)
        out.player.setLoops(QMediaPlayer.Loops.Infinite if loops <= 0 else max(1, int(loops)))
        out.player.setSource(QUrl.fromLocalFile(path))
        if start > 0:
            out.player.setPosition(int(start * 1000))
        out.player.play()

    def show_camera(self, output: str, camera_name: str, routes: list[dict] | None = None) -> None:
        device = _find_camera(camera_name)
        if device is None:
            raise RuntimeError(f"The camera \"{camera_name}\" is not connected.")
        out = self._output_for(output, routes or [])
        out.player.stop()
        out.hide_text()
        out.take_sink("camera")  # now the camera draws into the widget instead
        if out.camera is None:
            out.camera = QCamera(device)
        else:
            out.camera.setCameraDevice(device)
        out.session.setCamera(out.camera)
        out.camera.start()

    def volume(self, number: str) -> float | None:
        """The audio level of the video playing for this cue number, or None if none is playing."""
        for out in self._outputs.values():
            if out.cue_number == number and out.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                return out.audio.volume()
        return None

    def set_volume(self, number: str, volume: float) -> None:
        for out in self._outputs.values():
            if out.cue_number == number:
                out.audio.setVolume(min(1.0, max(0.0, volume)))

    def show_text(self, output: str, text: str, size: int, routes: list[dict] | None = None) -> None:
        out = self._output_for(output, routes or [])
        out.player.stop()
        out.stop_camera()
        out.show_text(text, size)

    def stop_all(self) -> None:
        """Clear every display. Nothing is left covering the screen, whatever else fails."""
        for out in list(self._outputs.values()):
            try:
                out.blank()
            except Exception:
                pass

    def fade_out(self, seconds: float) -> None:
        """Fade every display down over this long, then clear it, as QLab's panic does.

        Escape used to leave the picture up at full until the audio fade had finished,
        which looked like nothing had happened.
        """
        steps = max(1, min(30, int(seconds * 30)))
        for out in list(self._outputs.values()):
            if not out.widget.isVisible():
                continue
            for step in range(1, steps + 1):
                level = 1.0 - step / steps
                QTimer.singleShot(int(seconds * 1000 * step / steps),
                                  lambda o=out, lv=level: _dim(o, lv))
        QTimer.singleShot(int(seconds * 1000) + 30, self.stop_all)

    def close(self) -> None:
        for out in self._outputs.values():
            out.blank()
            if out.text_window is not None:
                out.text_window.close()
            out.widget.close()
        self._outputs.clear()
