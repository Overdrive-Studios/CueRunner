"""OSC messages over UDP, so a cue can drive other software or hardware that listens for OSC.

A message is an address such as "/cue/1/start" plus arguments. Whole numbers are sent as int32,
other numbers as float32, and everything else as a string. Only the send side is needed here.
"""

from __future__ import annotations

import re
import socket
import struct

_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d*\.\d+$|^-?\d+\.\d*$")


def _string(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\0"
    return raw + b"\0" * ((4 - len(raw) % 4) % 4)


def encode(address: str, args: list) -> bytes:
    if not address.startswith("/"):
        raise ValueError("An OSC address starts with /, for example /cue/1/start.")
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "T" if arg else "F"
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        else:
            tags += "s"
            payload += _string(str(arg))
    return _string(address) + _string(tags) + payload


def parse_message(text: str) -> tuple[str, list]:
    """'/fader/1 0.5 3 hello' -> ('/fader/1', [0.5, 3, 'hello'])."""
    parts = text.split()
    if not parts:
        raise ValueError("The OSC message is empty.")
    args: list = []
    for token in parts[1:]:
        if _INT.match(token):
            args.append(int(token))
        elif _FLOAT.match(token):
            args.append(float(token))
        else:
            args.append(token)
    return parts[0], args


def parse_target(text: str) -> tuple[str, int]:
    """'192.168.1.10:8000' -> ('192.168.1.10', 8000)."""
    host, sep, port = text.strip().rpartition(":")
    if not sep or not host or not port.isdigit():
        raise ValueError("The OSC target must look like 192.168.1.10:8000.")
    number = int(port)
    if not 1 <= number <= 65535:
        raise ValueError("The OSC port must be between 1 and 65535.")
    return host, number


def send(target: str, message: str) -> None:
    host, port = parse_target(target)
    address, args = parse_message(message)
    packet = encode(address, args)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(packet, (host, port))
