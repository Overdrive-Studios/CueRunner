from pathlib import Path

from cuerunner.model import PAD_COUNT, Cue, Pad, RadioSettings, Show


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


def test_remote_port_round_trip_and_default(tmp_path: Path) -> None:
    from cuerunner.model import DEFAULT_REMOTE_PORT, Show

    path = tmp_path / "port.json"
    Show(remote_port=9090).save(path)
    assert Show.load(path).remote_port == 9090
    path.write_text('{"format_version": 3, "cues": []}', encoding="utf-8")
    assert Show.load(path).remote_port == DEFAULT_REMOTE_PORT


def test_pad_and_radio_routes_round_trip(tmp_path: Path) -> None:
    show = Show(name="Routes")
    show.pads[3] = Pad(name="Sting", file="/sfx/sting.wav", route=[[3], [4]])
    show.radio = RadioSettings(music_folder="/music", ident_folder="/idents", route=[[1, 2]])
    path = tmp_path / "routes.cuerunner.json"

    show.save(path)
    loaded = Show.load(path)

    assert loaded.pads[3].route == [[3], [4]]
    assert loaded.radio.route == [[1, 2]]
    assert loaded.pads[0].route == []


def test_save_keeps_a_backup_of_the_previous_version(tmp_path: Path) -> None:
    path = tmp_path / "gig.cuerunner.json"
    Show(name="First").save(path)
    Show(name="Second").save(path)

    backups = list((tmp_path / "backups").glob("gig.cuerunner-*.json"))
    assert len(backups) == 1
    assert Show.load(backups[0]).name == "First"
    assert Show.load(path).name == "Second"


def test_backups_are_capped(tmp_path: Path) -> None:
    path = tmp_path / "gig.cuerunner.json"
    for i in range(6):
        Show(name=f"v{i}").save(path, keep_backups=3)

    backups = list((tmp_path / "backups").glob("gig.cuerunner-*.json"))
    assert len(backups) == 3


def test_snapshot_saves_current_state_without_touching_show_file(tmp_path: Path) -> None:
    path = tmp_path / "gig.cuerunner.json"
    Show(name="Saved").save(path)
    unsaved = Show(name="Unsaved edits")

    target = unsaved.snapshot(path)

    assert target is not None and target.parent.name == "backups"
    assert Show.load(target).name == "Unsaved edits"
    assert Show.load(path).name == "Saved"
