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
