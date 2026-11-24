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


def test_midi_map_and_duck_level_round_trip(tmp_path: Path) -> None:
    from cuerunner.model import Show

    show = Show(midi_map={"note:60": "cue:3"}, duck_level=0.5)
    path = tmp_path / "show.json"
    show.save(path)
    loaded = Show.load(path)
    assert loaded.midi_map == {"note:60": "cue:3"}
    assert loaded.duck_level == 0.5


def test_old_show_gets_default_midi_map_and_duck_level(tmp_path: Path) -> None:
    from cuerunner.model import DEFAULT_MIDI_MAP, DEFAULT_DUCK_LEVEL, Show

    path = tmp_path / "old.json"
    path.write_text('{"format_version": 3, "name": "Old", "cues": []}', encoding="utf-8")
    loaded = Show.load(path)
    assert loaded.midi_map == DEFAULT_MIDI_MAP
    assert loaded.duck_level == DEFAULT_DUCK_LEVEL
