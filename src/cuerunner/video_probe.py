"""Finding out how long a video file is, without putting the app at risk.

The wave reader cannot open video files, so their length has to come from the media
engine, and that answer arrives later rather than straight away.

Media backends differ by machine and are easy to upset. Three rules keep this safe: the
player is never touched from inside one of its own signals, every file is asked about once
and only once, and anything that goes wrong leaves the length unknown rather than taking
the app with it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

_CACHE: dict[str, int] = {}


def known_duration_ms(path: str) -> int | None:
    """The length already worked out for this file, or None if it has not been asked yet."""
    return _CACHE.get(path)


def remember(path: str, duration_ms: int) -> None:
    _CACHE[path] = max(0, int(duration_ms))


def clear_cache() -> None:
    _CACHE.clear()


class DurationProbe(QObject):
    """Asks the media engine how long video files are, one at a time, in the background."""

    ready = Signal(str, int)  # path, milliseconds

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = None
        self._queue: list[str] = []
        self._current = ""
        self._asked: set[str] = set()

    def _ensure_player(self) -> bool:
        """Build the player the first time one is needed. False if this machine cannot."""
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            self._player = QMediaPlayer(self)
            self._player.durationChanged.connect(self._got_duration)
            self._player.errorOccurred.connect(lambda *_: self._give_up())
        except Exception:
            self._player = None
            return False
        return True

    def request(self, path: str) -> int | None:
        """Ask for a file's length. Returns it at once if it is already known."""
        if not path:
            return None
        known = _CACHE.get(path)
        if known is not None:
            return known
        if path in self._asked:
            return None  # already on its way, or already given up on
        self._asked.add(path)
        self._queue.append(path)
        # Off the current call stack: this is often reached from a refresh or a paint, and
        # starting a media pipeline from inside one is asking for trouble.
        QTimer.singleShot(0, self._next)
        return None

    def _next(self) -> None:
        if self._current or not self._queue:
            return
        if not self._ensure_player():
            self._queue.clear()
            return
        self._current = self._queue.pop(0)
        try:
            self._player.setSource(QUrl.fromLocalFile(self._current))
        except Exception:
            QTimer.singleShot(0, self._give_up)

    def _got_duration(self, duration_ms: int) -> None:
        if not self._current or duration_ms <= 0:
            return
        path, self._current = self._current, ""
        remember(path, duration_ms)
        self.ready.emit(path, int(duration_ms))
        QTimer.singleShot(0, self._release)  # never from inside the player's own signal

    def _give_up(self) -> None:
        """A file the engine could not open. Not asked again in this run, but not written off.

        Nothing is written to the shared cache here. A probe cut short by a window closing
        would otherwise mark a perfectly good file as having no length for the rest of the
        session, and nothing would ever ask again.
        """
        if not self._current:
            return
        path, self._current = self._current, ""
        self.ready.emit(path, 0)
        QTimer.singleShot(0, self._release)

    def _release(self) -> None:
        """Let go of the file, then move on to the next one."""
        if self._player is not None:
            try:
                self._player.setSource(QUrl())
            except Exception:
                pass
        self._next()

    def close(self) -> None:
        if self._current:
            self._asked.discard(self._current)  # cut short, so it may be asked again later
        self._queue.clear()
        self._current = ""
        if self._player is not None:
            try:
                self._player.setSource(QUrl())
            except Exception:
                pass
