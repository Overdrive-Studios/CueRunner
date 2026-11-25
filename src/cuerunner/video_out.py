"""Video outputs: play video cues full screen on a chosen display.

Each display gets one window and one player, created the first time a cue uses it. Outputs are
named "screen:<n>", where n is the index in the order the system lists its displays.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

SCREEN_PREFIX = "screen:"


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


class _Output:
    def __init__(self, screen) -> None:
        self.widget = QVideoWidget()
        self.widget.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.widget)
        self.widget.setGeometry(screen.geometry())
        self.screen = screen

    def show_on_screen(self) -> None:
        self.widget.setScreen(self.screen)
        self.widget.setGeometry(self.screen.geometry())
        self.widget.showFullScreen()


class VideoOutputs(QObject):
    """Plays video cues on displays. Owned by the main window; call close() when the show ends."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._outputs: dict[int, _Output] = {}

    def play(self, output: str, path: str) -> None:
        index = screen_index(output)
        screens = QGuiApplication.screens()
        if index is None or not 0 <= index < len(screens):
            raise RuntimeError(f"The display for this video cue is not connected ({output or 'not set'}).")
        out = self._outputs.get(index)
        if out is None:
            out = _Output(screens[index])
            self._outputs[index] = out
        out.show_on_screen()
        out.player.setSource(QUrl.fromLocalFile(path))
        out.player.play()

    def stop_all(self) -> None:
        for out in self._outputs.values():
            out.player.stop()
            out.widget.hide()

    def close(self) -> None:
        for out in self._outputs.values():
            out.player.stop()
            out.widget.close()
        self._outputs.clear()
