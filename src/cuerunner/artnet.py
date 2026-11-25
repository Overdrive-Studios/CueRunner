"""Art-Net lighting over UDP: sends DMX channel levels to a network node or lighting desk.

A light cue names the node's address and a set of channels, e.g. "1=255 2=128 10=0", on one
universe. Channels run from 1 to 512; levels run from 0 to 255. Ramps are not supported yet, so
the levels change at once.
"""

from __future__ import annotations

import socket
import struct

ARTNET_PORT = 6454
_HEADER = b"Art-Net\0"
_OP_DMX = 0x5000
_PROTOCOL = 14
DMX_CHANNELS = 512


def encode_dmx(universe: int, levels: dict[int, int], sequence: int = 0) -> bytes:
    """One ArtDmx packet. levels maps DMX channel (1-512) to level (0-255)."""
    if not 0 <= universe <= 0x7FFF:
        raise ValueError("The Art-Net universe must be between 0 and 32767.")
    data = bytearray(DMX_CHANNELS)
    for channel, level in levels.items():
        if not 1 <= channel <= DMX_CHANNELS:
            raise ValueError(f"DMX channel {channel} is outside 1 to {DMX_CHANNELS}.")
        if not 0 <= level <= 255:
            raise ValueError(f"DMX level {level} is outside 0 to 255.")
        data[channel - 1] = level
    header = _HEADER + struct.pack("<H", _OP_DMX) + struct.pack(">H", _PROTOCOL)
    header += bytes([sequence & 0xFF, 0]) + struct.pack("<H", universe) + struct.pack(">H", len(data))
    return header + bytes(data)


def parse_levels(text: str) -> dict[int, int]:
    """'1=255 2=128' -> {1: 255, 2: 128}. Commas also work as separators."""
    levels: dict[int, int] = {}
    for part in text.replace(",", " ").split():
        channel, sep, level = part.partition("=")
        if not sep or not channel.isdigit() or not level.isdigit():
            raise ValueError(f"'{part}' is not a channel and level like 1=255.")
        levels[int(channel)] = int(level)
    if not levels:
        raise ValueError("Set at least one channel, for example 1=255.")
    return levels


def send(host: str, universe: int, levels: dict[int, int]) -> None:
    if not host:
        raise ValueError("Enter the Art-Net node's address, for example 192.168.1.50.")
    packet = encode_dmx(universe, levels)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (host, ARTNET_PORT))
