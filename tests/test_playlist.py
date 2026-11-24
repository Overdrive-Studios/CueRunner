import random
from pathlib import Path

import pytest

from cuerunner.playlist import Playlist, scan_audio


def test_every_track_plays_before_repeats() -> None:
    playlist = Playlist(["a", "b", "c"], [], rng=random.Random(1))
    first_round = [playlist.next()[1] for _ in range(3)]
    assert sorted(first_round) == ["a", "b", "c"]


def test_no_immediate_repeat_across_decks() -> None:
    playlist = Playlist(["a", "b"], [], rng=random.Random(7))
    played = [playlist.next()[1] for _ in range(40)]
    assert all(x != y for x, y in zip(played, played[1:]))


def test_ident_every_n_tracks() -> None:
    playlist = Playlist(["a", "b", "c"], ["promo"], every=2, rng=random.Random(3))
    kinds = [playlist.next()[0] for _ in range(6)]
    assert kinds == ["track", "track", "ident", "track", "track", "ident"]


def test_idents_come_from_ident_list() -> None:
    playlist = Playlist(["a"], ["promo1", "promo2"], every=1, rng=random.Random(5))
    idents = [path for kind, path in (playlist.next() for _ in range(6)) if kind == "ident"]
    assert idents and set(idents) <= {"promo1", "promo2"}


def test_empty_music_list_is_an_error() -> None:
    with pytest.raises(ValueError):
        Playlist([], [])


def test_scan_audio_finds_only_audio_files(tmp_path: Path) -> None:
    (tmp_path / "song.mp3").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "Ident.WAV").write_bytes(b"")
    found = [Path(p).name for p in scan_audio(str(tmp_path))]
    assert found == ["Ident.WAV", "song.mp3"]


def test_scan_audio_missing_folder_is_empty() -> None:
    assert scan_audio("") == []
    assert scan_audio("/does/not/exist") == []
