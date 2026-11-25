"""Allen & Heath control over MIDI-over-TCP.

Each console family has its own spec, so each has its own command builders. The transport
(a TCP socket that sends raw MIDI bytes) is shared.

SQ commands follow "SQ MIDI Protocol Issue 5" (SQ firmware V1.5.0 and later): port 51325,
MIDI channel set in Utility > General > MIDI, scene recall as bank + program change,
and mute/level as NRPN-style control changes.

Command builders are pure functions, so they are tested without a console.
"""

from __future__ import annotations

import socket
import threading

SQ_PORT = 51325

# Linear-taper level table from the SQ spec: (dB, VC, VF). Values between listed steps are not in the spec,
# so a level is rounded to the nearest listed dB.
SQ_LEVEL_TABLE: list[tuple[float, int, int]] = [
    (-89, 0x24, 0x16), (-85, 0x27, 0x71), (-80, 0x2C, 0x42), (-75, 0x31, 0x14), (-70, 0x35, 0x65),
    (-65, 0x3A, 0x37), (-60, 0x3F, 0x09), (-55, 0x43, 0x5A), (-50, 0x48, 0x2C), (-45, 0x4C, 0x7D),
    (-40, 0x51, 0x4F), (-38, 0x53, 0x3C), (-36, 0x55, 0x2A), (-35, 0x56, 0x21), (-34, 0x57, 0x17),
    (-33, 0x58, 0x0E), (-32, 0x59, 0x05), (-31, 0x59, 0x7C), (-30, 0x5A, 0x72), (-29, 0x5B, 0x69),
    (-28, 0x5C, 0x60), (-27, 0x5D, 0x56), (-26, 0x5E, 0x4D), (-25, 0x5F, 0x44), (-24, 0x60, 0x3B),
    (-23, 0x61, 0x31), (-22, 0x62, 0x28), (-21, 0x63, 0x1F), (-20, 0x64, 0x16), (-19, 0x65, 0x0C),
    (-18, 0x66, 0x03), (-17, 0x66, 0x7A), (-16, 0x67, 0x70), (-15, 0x68, 0x67), (-14, 0x69, 0x5E),
    (-13, 0x6A, 0x55), (-12, 0x6B, 0x4B), (-11, 0x6C, 0x42), (-10, 0x6D, 0x39), (-9, 0x6E, 0x2F),
    (-8, 0x6F, 0x26), (-7, 0x70, 0x1D), (-6, 0x71, 0x14), (-5, 0x72, 0x0A), (-4, 0x73, 0x01),
    (-3, 0x73, 0x78), (-2, 0x74, 0x6F), (-1, 0x75, 0x65), (0, 0x76, 0x5C), (1, 0x77, 0x53),
    (2, 0x78, 0x49), (3, 0x79, 0x40), (4, 0x7A, 0x37), (5, 0x7B, 0x2E), (6, 0x7C, 0x24),
    (7, 0x7D, 0x1B), (8, 0x7E, 0x12), (9, 0x7F, 0x08), (10, 0x7F, 0x7F),
]


DLIVE_PORT = 51325  # from the dLive MIDI over TCP spec (firmware V1.9 and later)


def _check(value: int, low: int, high: int, name: str) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low} to {high}, got {value}")


def _midi_channel(channel: int) -> int:
    """MIDI channels are 1 to 16 in the app and 0 to 15 on the wire."""
    _check(channel, 1, 16, "MIDI channel")
    return channel - 1


def sq_scene_recall(scene: int, channel: int = 1) -> bytes:
    """Recall scene 1 to 300: a bank select (CC 0) followed by a program change."""
    _check(scene, 1, 300, "Scene")
    n = _midi_channel(channel)
    bank, program = divmod(scene - 1, 128)
    return bytes([0xB0 | n, 0x00, bank, 0xC0 | n, program])


def sq_mute(input_no: int, mute: bool, channel: int = 1) -> bytes:
    """Mute or unmute input 1 to 48 into the main LR."""
    _check(input_no, 1, 48, "Input")
    n = _midi_channel(channel)
    return bytes([
        0xB0 | n, 0x63, 0x00,  # NRPN MSB: mute to LR
        0xB0 | n, 0x62, input_no - 1,  # NRPN LSB: input number minus one
        0xB0 | n, 0x06, 0x00,
        0xB0 | n, 0x26, 0x01 if mute else 0x00,
    ])


def sq_level_db(input_no: int, db: float, channel: int = 1) -> bytes:
    """Set an input's level into the main LR, in dB. Rounds to the nearest dB step in the spec's table."""
    _check(input_no, 1, 48, "Input")
    if db <= -89.5:
        vc, vf = 0x00, 0x00  # -inf
    else:
        _, vc, vf = min(SQ_LEVEL_TABLE, key=lambda row: abs(row[0] - db))
    n = _midi_channel(channel)
    return bytes([
        0xB0 | n, 0x63, 0x40,  # NRPN MSB: level to LR
        0xB0 | n, 0x62, input_no - 1,
        0xB0 | n, 0x06, vc,
        0xB0 | n, 0x26, vf,
    ])


class MidiTcpSender:
    """Sends raw MIDI bytes to a console over TCP. Reconnects after a dropped connection."""

    def __init__(self, host: str, port: int, timeout: float = 3.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        return self._sock

    def send(self, data: bytes) -> None:
        with self._lock:
            try:
                self._connect().sendall(data)
            except OSError:
                self.close()
                self._connect().sendall(data)  # one retry on a fresh connection

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None


def parse_sq_command(text: str, channel: int = 1) -> bytes:
    """Parse a console cue command for the SQ.

    Accepted forms: "scene 7", "mute 1", "unmute 1", "level 1 -6", "level 1 -inf".
    Raises ValueError with a readable message if the text doesn't match.
    """
    parts = text.strip().lower().split()
    try:
        if len(parts) == 2 and parts[0] == "scene":
            return sq_scene_recall(int(parts[1]), channel)
        if len(parts) == 2 and parts[0] in ("mute", "unmute"):
            return sq_mute(int(parts[1]), parts[0] == "mute", channel)
        if len(parts) == 3 and parts[0] == "level":
            db = -120.0 if parts[2] in ("-inf", "-infinity") else float(parts[2])
            return sq_level_db(int(parts[1]), db, channel)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    raise ValueError("Use: scene 7, mute 1, unmute 1, level 1 -6 or level 1 -inf")


# --- dLive (MIDI over TCP spec, firmware V1.9 and later) -----------------------------

def _dlive_channel(channel: int) -> int:
    """dLive base MIDI channel: 1 to 12, mapped to 0 to B on the wire."""
    _check(channel, 1, 12, "dLive MIDI channel")
    return channel - 1


def dlive_scene_recall(scene: int, channel: int = 1) -> bytes:
    """Recall scene 1 to 500: bank select (CC 0, bank 00 to 03) then program change."""
    _check(scene, 1, 500, "Scene")
    n = _dlive_channel(channel)
    bank, program = divmod(scene - 1, 128)
    return bytes([0xB0 | n, 0x00, bank, 0xC0 | n, program])


def dlive_mute(input_no: int, mute: bool, channel: int = 1) -> bytes:
    """Mute or unmute input 1 to 128. The spec uses a Note On on the input's channel, then a Note Off."""
    _check(input_no, 1, 128, "Input")
    n = _dlive_channel(channel)
    ch = input_no - 1
    velocity = 0x7F if mute else 0x3F
    return bytes([0x90 | n, ch, velocity, 0x90 | n, ch, 0x00])


def dlive_fader_raw(input_no: int, value: int, channel: int = 1) -> bytes:
    """Set an input fader with the spec's raw value 0 to 127 (-inf to +10 dB). The spec gives no dB table."""
    _check(input_no, 1, 128, "Input")
    _check(value, 0, 127, "Fader value")
    n = _dlive_channel(channel)
    ch = input_no - 1
    return bytes([0xB0 | n, 0x63, ch, 0xB0 | n, 0x62, 0x17, 0xB0 | n, 0x06, value])


def parse_dlive_command(text: str, channel: int = 1) -> bytes:
    """Parse a dLive console cue: "scene 7", "mute 1", "unmute 1", "fader 1 64" (raw 0 to 127)."""
    parts = text.strip().lower().split()
    try:
        if len(parts) == 2 and parts[0] == "scene":
            return dlive_scene_recall(int(parts[1]), channel)
        if len(parts) == 2 and parts[0] in ("mute", "unmute"):
            return dlive_mute(int(parts[1]), parts[0] == "mute", channel)
        if len(parts) == 3 and parts[0] == "fader":
            return dlive_fader_raw(int(parts[1]), int(parts[2]), channel)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    raise ValueError("Use: scene 7, mute 1, unmute 1 or fader 1 64 (dLive fader is raw 0 to 127)")


def parse_command(family: str, text: str, channel: int = 1) -> bytes:
    """Route a console cue command to the right family's parser. family is 'sq' or 'dlive'."""
    if family == "dlive":
        return parse_dlive_command(text, channel)
    return parse_sq_command(text, channel)


def port_for(family: str) -> int:
    return DLIVE_PORT if family == "dlive" else SQ_PORT
