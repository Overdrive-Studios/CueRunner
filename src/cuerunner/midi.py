"""MIDI triggering: map notes and control changes to CueRunner commands.

The mapping functions are pure, so they're tested without a MIDI port. Callbacks arrive on the
MIDI thread and only queue commands; the UI thread runs them.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from cuerunner.model import DEFAULT_MIDI_MAP

# Commands are strings: "go", "stop", "pause", "panic", "cue:<row>" or "pad:<index>".
DEFAULT_MAP: dict[str, str] = dict(DEFAULT_MIDI_MAP)


def key_for(message) -> str | None:
    """A key for the mapping table: note-on with velocity above zero, or control change with value above zero."""
    if message.type == "note_on" and message.velocity > 0:
        return f"note:{message.note}"
    if message.type == "control_change" and message.value > 0:
        return f"cc:{message.control}"
    return None


_KEY_RE = re.compile(r"^(note|cc):(\d{1,3})$")
_COMMAND_RE = re.compile(r"^(go|stop|pause|panic|cue:\d+|pad:\d+)$")


def valid_key(key: str) -> bool:
    """A key the mapping table can hold: note or control number 0 to 127."""
    match = _KEY_RE.match(key.strip())
    return match is not None and int(match.group(2)) <= 127


def valid_command(command: str) -> bool:
    """A command the window knows how to run, with cue and pad numbers in range."""
    command = command.strip()
    if not _COMMAND_RE.match(command):
        return False
    if command.startswith("pad:"):
        return int(command.split(":", 1)[1]) < 64
    return True


def mapping_from_rows(rows: list[tuple[str, str]]) -> tuple[dict[str, str], list[str]]:
    """Build a mapping from (key, command) pairs. Returns the mapping and any problems found."""
    mapping: dict[str, str] = {}
    problems: list[str] = []
    for key, command in rows:
        key, command = key.strip(), command.strip()
        if not key and not command:
            continue
        if not valid_key(key):
            problems.append(f"'{key}' is not a note or control number (use note:36 or cc:7).")
            continue
        if not valid_command(command):
            problems.append(f"'{command}' is not a command (use go, stop, pause, panic, cue:N or pad:N).")
            continue
        if key in mapping:
            problems.append(f"'{key}' is set more than once.")
            continue
        mapping[key] = command
    return mapping, problems


def command_for(mapping: dict[str, str], key: str | None) -> str | None:
    if key is None:
        return None
    return mapping.get(key)


def input_port_names() -> list[str]:
    import mido

    return list(mido.get_input_names())


class MidiInput:
    def __init__(self, port_name: str, on_command: Callable[[str], None], mapping: dict[str, str] | None = None) -> None:
        import mido

        self._mapping = dict(DEFAULT_MAP if mapping is None else mapping)
        self._on_command = on_command
        self._port = mido.open_input(port_name, callback=self._callback)

    def set_mapping(self, mapping: dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def _callback(self, message) -> None:
        command = command_for(self._mapping, key_for(message))
        if command is not None:
            self._on_command(command)

    def close(self) -> None:
        self._port.close()
