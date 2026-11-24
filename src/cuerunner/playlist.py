"""Venue radio playlist: shuffled music with an ident every N tracks. No audio device needed."""

from __future__ import annotations

import random
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg"}


def scan_audio(folder: str) -> list[str]:
    """Audio files directly inside a folder, sorted by name. Empty if the folder is missing."""
    path = Path(folder)
    if not folder or not path.is_dir():
        return []
    return sorted(str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)


class Playlist:
    """Shuffled tracks with idents. Tracks never repeat back to back, and every deck is played out before it refills."""

    def __init__(self, tracks: list[str], idents: list[str], every: int = 4, rng: random.Random | None = None) -> None:
        if not tracks:
            raise ValueError("No music files found in the music folder.")
        self._tracks = list(tracks)
        self._idents = list(idents)
        self._every = max(1, every)
        self._rng = rng or random.Random()
        self._track_deck: list[str] = []
        self._ident_deck: list[str] = []
        self._since_ident = 0
        self._last_track: str | None = None

    def next(self) -> tuple[str, str]:
        """Return ("track" or "ident", file path) for whatever plays next."""
        if self._idents and self._since_ident >= self._every:
            self._since_ident = 0
            return "ident", self._draw(self._ident_deck, self._idents, None)
        self._since_ident += 1
        track = self._draw(self._track_deck, self._tracks, self._last_track)
        self._last_track = track
        return "track", track

    def _draw(self, deck: list[str], source: list[str], avoid: str | None) -> str:
        if not deck:
            deck.extend(self._rng.sample(source, len(source)))
            # Avoid playing the same file twice in a row across the deck boundary.
            if avoid is not None and len(deck) > 1 and deck[-1] == avoid:
                deck[0], deck[-1] = deck[-1], deck[0]
        return deck.pop()
