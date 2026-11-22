from pathlib import Path

from cuerunner.model import Cue, Show


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    show = Show(name="Test", cues=[Cue(number="1", name="Intro", file="/music/intro.wav", fade_out=2.5)])
    path = tmp_path / "show.cuerunner.json"

    show.save(path)
    loaded = Show.load(path)

    assert loaded == show


def test_next_number_follows_highest_cue() -> None:
    show = Show(cues=[Cue(number="1", name="a"), Cue(number="7", name="b")])
    assert show.next_number() == "8"


def test_next_number_empty_show() -> None:
    assert Show().next_number() == "1"
