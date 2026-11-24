"""MIDI triggering: map notes and control changes to CueRunner commands.

The mapping functions are pure, so they're tested without a MIDI port. Callbacks arrive on the
MIDI thread and only queue commands; the UI thread runs them.
"""

from __future__ import annotations

from collections.abc import Callable

# Commands are strings: "go", "stop", "pause", "panic", "cue:<row>" or "pad:<index>".
DEFAULT_MAP: dict[str, str] = {
    "note:36": "go",
    "note:37": "stop",
    "note:38": "pause",
    "note:39": "panic",
}


def key_for(message) -> str | None:
    """A key for the mapping table: note-on with velocity above zero, or control change with value above zero."""
    if message.type == "note_on" and message.velocity > 0:
        return f"note:{message.note}"
    if message.type == "control_change" and message.value > 0:
        return f"cc:{message.control}"
    return None


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

    def _callback(self, message) -> None:
        command = command_for(self._mapping, key_for(message))
        if command is not None:
            self._on_command(command)

    def close(self) -> None:
        self._port.close()
