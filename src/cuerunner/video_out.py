"""Video outputs: play video cues and camera cues full screen on a chosen display or route.

A display is named "screen:<n>", where n is its index in the order the system lists its displays.
A route is a named link to a display with a scaling mode ("full" stretches to the screen, "fit"
keeps the picture's shape). Cues store either kind of name in video_output, e.g. "route:Stage left".
Each display gets one window, created the first time a cue uses it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QUrl
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


class _Output:
    def __init__(self, screen) -> None:
        self.widget = QVideoWidget()
        self.widget.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.widget)
        self.session = QMediaCaptureSession()
        self.session.setVideoOutput(self.widget)
        self.camera: QCamera | None = None
        self.cue_number = ""
        self.text_window: QLabel | None = None
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
        self.text_window.setScreen(self.screen)
        self.text_window.setGeometry(self.screen.geometry())
        self.text_window.showFullScreen()
        self.text_window.raise_()
        self.widget.hide()

    def hide_text(self) -> None:
        if self.text_window is not None:
            self.text_window.hide()

    def show_on_screen(self, fit: str) -> None:
        self.widget.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatio if fit == "fit" else Qt.AspectRatioMode.IgnoreAspectRatio
        )
        self.widget.setScreen(self.screen)
        self.widget.setGeometry(self.screen.geometry())
        self.widget.showFullScreen()

    def stop_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.session.setCamera(None)


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

    def play(self, output: str, path: str, routes: list[dict] | None = None, number: str = "") -> None:
        out = self._output_for(output, routes or [])
        out.stop_camera()
        out.hide_text()
        out.cue_number = number
        out.player.setSource(QUrl.fromLocalFile(path))
        out.player.play()

    def show_camera(self, output: str, camera_name: str, routes: list[dict] | None = None) -> None:
        device = _find_camera(camera_name)
        if device is None:
            raise RuntimeError(f"The camera \"{camera_name}\" is not connected.")
        out = self._output_for(output, routes or [])
        out.player.stop()
        out.hide_text()
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
        for out in self._outputs.values():
            out.player.stop()
            out.stop_camera()
            out.hide_text()
            out.widget.hide()

    def close(self) -> None:
        for out in self._outputs.values():
            out.player.stop()
            out.stop_camera()
            out.hide_text()
            out.widget.close()
        self._outputs.clear()
