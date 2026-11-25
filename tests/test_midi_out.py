"""MIDI cue messages: parsing and validation. Sending needs a real port, so it is not tested here."""

import pytest

from cuerunner import midi


def test_note_on_defaults_to_channel_one():
    assert midi.parse_out("note_on 60 127") == {"type": "note_on", "note": 60, "velocity": 127, "channel": 0}


def test_note_on_with_channel():
    spec = midi.parse_out("note_on 60 100 3")
    assert spec["channel"] == 2


def test_control_change_and_program():
    assert midi.parse_out("cc 7 90")["type"] == "control_change"
    assert midi.parse_out("program 5 2") == {"type": "program_change", "program": 5, "channel": 1}


def test_bad_messages_are_rejected():
    with pytest.raises(ValueError):
        midi.parse_out("")
    with pytest.raises(ValueError):
        midi.parse_out("sysex 1 2")
    with pytest.raises(ValueError):
        midi.parse_out("note_on 128 127")
    with pytest.raises(ValueError):
        midi.parse_out("note_on 60")
    with pytest.raises(ValueError):
        midi.parse_out("note_on 60 127 17")
