"""MIDI triggering: map notes and control changes to CueRunner commands.

The mapping functions are pure, so they're tested without a MIDI port. Callbacks arrive on the
MIDI thread and only queue commands; the UI thread runs them.
"""

from __future__ import annotations

import threading

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


# --- MIDI cues: send a message to a MIDI output port -----------------------------

def output_port_names() -> list[str]:
    import mido

    return list(mido.get_output_names())


# MIDI Show Control: F0 7F <device> 02 <command format> <command> <data> F7
MSC_COMMANDS = {"go": 0x01, "stop": 0x02, "reset": 0x0A, "gooff": 0x0B}
MSC_ALL_DEVICES = 0x7F
MSC_ALL_TYPES = 0x7F


def _sysex_spec(values: list[int]) -> dict:
    """A full SysEx message, F0 to F7 inclusive, as a mido spec without the framing bytes."""
    if len(values) < 2 or values[0] != 0xF0 or values[-1] != 0xF7:
        raise ValueError("SysEx must start with F0 and end with F7, for example: sysex F0 7F 01 02 01 01 F7")
    body = values[1:-1]
    if any(b > 0x7F for b in body):
        raise ValueError("SysEx data bytes must be 00 to 7F.")
    return {"type": "sysex", "data": body}


def msc_spec(command: str, q_number: str = "") -> dict:
    """'msc go 3.5' -> a MIDI Show Control SysEx for GO cue 3.5 to all devices and all command types."""
    name = command.lower()
    if name not in MSC_COMMANDS:
        raise ValueError("MSC command must be one of: go, stop, reset, gooff.")
    try:
        data = list(q_number.encode("ascii"))
    except UnicodeEncodeError:
        raise ValueError("MSC cue numbers must be plain ASCII.")
    body = [MSC_ALL_DEVICES, 0x02, MSC_ALL_TYPES, MSC_COMMANDS[name], *data]
    return {"type": "sysex", "data": body}


def parse_out(text: str) -> dict:
    """'note_on 60 127 2' -> a mido message spec. Channels run 1 to 16 and default to 1."""
    parts = text.split()
    if not parts:
        raise ValueError("Enter a MIDI message, for example: note_on 60 127")
    if parts[0].lower() == "sysex":
        try:
            values = [int(token, 16) for token in parts[1:]]
        except ValueError:
            raise ValueError("SysEx bytes are hex, for example: F0 7F 01 02 01 01 F7")
        return _sysex_spec(values)
    if parts[0].lower() == "msc":
        if len(parts) < 2:
            raise ValueError("Enter an MSC command, for example: msc go 3.5")
        return msc_spec(parts[1], " ".join(parts[2:]))
    kind, args = parts[0].lower(), parts[1:]
    numbers = []
    for token in args:
        if not token.isdigit():
            raise ValueError(f"'{token}' is not a number.")
        numbers.append(int(token))

    def take(count: int, name: str) -> list[int]:
        if len(numbers) < count:
            raise ValueError(f"{kind} needs {count} numbers: {name}.")
        return numbers[:count]

    if kind == "note_on":
        note, velocity = take(2, "note and velocity")
        channel = numbers[2] if len(numbers) > 2 else 1
        spec = {"type": "note_on", "note": note, "velocity": velocity}
    elif kind == "note_off":
        note = take(1, "note")[0]
        channel = numbers[2] if len(numbers) > 2 else 1
        spec = {"type": "note_off", "note": note, "velocity": numbers[1] if len(numbers) > 1 else 0}
    elif kind in ("cc", "control"):
        control, value = take(2, "control and value")
        channel = numbers[2] if len(numbers) > 2 else 1
        spec = {"type": "control_change", "control": control, "value": value}
    elif kind == "program":
        program = take(1, "program")[0]
        channel = numbers[1] if len(numbers) > 1 else 1
        spec = {"type": "program_change", "program": program}
    else:
        raise ValueError(f"'{kind}' is not a MIDI message. Use note_on, note_off, cc or program.")

    for name, value, top in (("note", spec.get("note"), 127), ("velocity", spec.get("velocity"), 127),
                             ("control", spec.get("control"), 127), ("value", spec.get("value"), 127),
                             ("program", spec.get("program"), 127)):
        if value is not None and not 0 <= value <= top:
            raise ValueError(f"{name} {value} is outside 0 to {top}.")
    if not 1 <= channel <= 16:
        raise ValueError(f"MIDI channel {channel} is outside 1 to 16.")
    spec["channel"] = channel - 1
    return spec


def send_out(port_name: str, text: str) -> None:
    import mido

    if not port_name:
        raise ValueError("Choose a MIDI output port for this cue.")
    spec = parse_out(text)
    with mido.open_output(port_name) as port:
        port.send(mido.Message(**spec))


# --- MIDI file cues: play a standard MIDI file out to a port ------------------------

class MidiFilePlayer:
    """Plays a standard MIDI file out to a port on a background thread, at the file's own tempo.

    stop() takes effect before the next message; it can wait out one long gap in the file.
    """

    def __init__(self, path: str, port_name: str) -> None:
        import mido

        if not port_name:
            raise ValueError("Choose a MIDI output port for this cue.")
        self._file = mido.MidiFile(path)
        self._port_name = port_name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import mido

        with mido.open_output(self._port_name) as port:
            for message in self._file.play():
                if self._stop.is_set():
                    break
                if not message.is_meta:
                    port.send(message)
            if self._stop.is_set():
                port.reset()  # release notes that were still sounding
