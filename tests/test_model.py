from pathlib import Path

from cuerunner.model import PAD_COUNT, Cue, Pad, Show


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    show = Show(name="Test", cues=[Cue(number="1", name="Intro", file="/music/intro.wav", fade_out=2.5, then="follow")])
    show.pads[0] = Pad(name="Applause", file="/sfx/applause.wav")
    path = tmp_path / "show.cuerunner.json"

    show.save(path)
    loaded = Show.load(path)

    assert loaded == show


def test_loading_old_show_without_pads_gives_empty_pads(tmp_path: Path) -> None:
    path = tmp_path / "old.cuerunner.json"
    path.write_text('{"name": "Old", "cues": []}', encoding="utf-8")

    loaded = Show.load(path)

    assert len(loaded.pads) == PAD_COUNT
    assert all(p.file == "" for p in loaded.pads)


def test_next_number_follows_highest_cue() -> None:
    show = Show(cues=[Cue(number="1", name="a"), Cue(number="7", name="b")])
    assert show.next_number() == "8"


def test_next_number_empty_show() -> None:
    assert Show().next_number() == "1"
