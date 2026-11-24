from types import SimpleNamespace

from cuerunner.midi import DEFAULT_MAP, command_for, key_for


def msg(type_: str, **fields):
    return SimpleNamespace(type=type_, **fields)


def test_note_on_with_velocity_gives_a_note_key() -> None:
    assert key_for(msg("note_on", note=36, velocity=100)) == "note:36"


def test_note_on_with_zero_velocity_is_ignored() -> None:
    assert key_for(msg("note_on", note=36, velocity=0)) is None


def test_control_change_gives_a_cc_key_only_when_pressed() -> None:
    assert key_for(msg("control_change", control=20, value=127)) == "cc:20"
    assert key_for(msg("control_change", control=20, value=0)) is None


def test_other_messages_are_ignored() -> None:
    assert key_for(msg("clock")) is None


def test_default_go_note_maps_to_go() -> None:
    assert command_for(DEFAULT_MAP, "note:36") == "go"


def test_unmapped_or_missing_key_gives_no_command() -> None:
    assert command_for(DEFAULT_MAP, "note:99") is None
    assert command_for(DEFAULT_MAP, None) is None


def test_valid_keys_and_commands() -> None:
    from cuerunner.midi import valid_command, valid_key

    assert valid_key("note:36") and valid_key("cc:127")
    assert not valid_key("note:128") and not valid_key("pad:3")
    assert valid_command("go") and valid_command("cue:12") and valid_command("pad:63")
    assert not valid_command("pad:64") and not valid_command("explode")


def test_mapping_from_rows_reports_problems() -> None:
    from cuerunner.midi import mapping_from_rows

    mapping, problems = mapping_from_rows([("note:60", "cue:3"), ("", ""), ("bad", "go"), ("cc:7", "nope")])
    assert mapping == {"note:60": "cue:3"}
    assert len(problems) == 2


def test_mapping_from_rows_rejects_duplicate_keys() -> None:
    from cuerunner.midi import mapping_from_rows

    mapping, problems = mapping_from_rows([("note:36", "go"), ("note:36", "stop")])
    assert mapping == {"note:36": "go"}
    assert problems
