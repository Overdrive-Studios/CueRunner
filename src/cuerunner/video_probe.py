"""Finding out how long a video file is.

Video files cannot be read by the wave reader, so their length has to come from the media
engine, and that answer arrives later rather than straight away. This asks once per file,
remembers the answer, and tells whoever is listening when it arrives.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

_CACHE: dict[str, int] = {}


def known_duration_ms(path: str) -> int | None:
    """The length already worked out for this file, or None if it has not been asked yet."""
    return _CACHE.get(path)


def remember(path: str, duration_ms: int) -> None:
    _CACHE[path] = max(0, int(duration_ms))


def clear_cache() -> None:
    _CACHE.clear()


class DurationProbe(QObject):
    """Asks the media engine how long video files are, one at a time, in the background.

    No audio output is attached: nothing is played, the file is only opened far enough to
    read its length.
    """

    ready = Signal(str, int)  # path, milliseconds

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._player.durationChanged.connect(self._got_duration)
        self._player.errorOccurred.connect(lambda *_: self._give_up())
        self._queue: list[str] = []
        self._current = ""

    def request(self, path: str) -> int | None:
        """Ask for a file's length. Returns it at once if it is already known."""
        if not path:
            return None
        known = _CACHE.get(path)
        if known is not None:
            return known
        if path != self._current and path not in self._queue:
            self._queue.append(path)
            self._next()
        return None

    def _next(self) -> None:
        if self._current or not self._queue:
            return
        self._current = self._queue.pop(0)
        try:
            self._player.setSource(QUrl.fromLocalFile(self._current))
        except Exception:
            self._give_up()

    def _got_duration(self, duration_ms: int) -> None:
        if not self._current or duration_ms <= 0:
            return
        path, self._current = self._current, ""
        remember(path, duration_ms)
        self._player.setSource(QUrl())
        self.ready.emit(path, int(duration_ms))
        self._next()

    def _give_up(self) -> None:
        """A file the engine cannot open is remembered as unknown, so it is not asked twice."""
        if not self._current:
            return
        path, self._current = self._current, ""
        remember(path, 0)
        self.ready.emit(path, 0)
        self._next()

    def close(self) -> None:
        self._queue.clear()
        self._current = ""
        self._player.setSource(QUrl())
