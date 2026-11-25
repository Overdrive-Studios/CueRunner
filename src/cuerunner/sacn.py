"""sACN (E1.31) lighting: DMX levels sent to a universe over UDP multicast.

Each universe has its own multicast group, 239.255.<high byte>.<low byte>, on port 5568. A packet
carries a start code of 0 and 512 channel levels. The packet has three layers: root, framing and
DMP. Only the sending side is implemented here.
"""

from __future__ import annotations

import socket
import struct
import uuid

SACN_PORT = 5568
DMX_CHANNELS = 512
_ACN_ID = b"ASC-E1.17\0\0\0"
_CID = uuid.UUID("3f6c1c5a-2b1e-4e6a-9d1c-7c0a5d1e7b21").bytes  # identifies CueRunner as the sender
_SOURCE = "CueRunner".encode("utf-8")


def _flags_length(length: int) -> bytes:
    """Each layer starts with 0x7 in the top four bits and its length in the low 12 bits."""
    return struct.pack(">H", 0x7000 | (length & 0x0FFF))


def encode_dmx(universe: int, levels: dict[int, int], sequence: int = 0, priority: int = 100) -> bytes:
    if not 1 <= universe <= 63999:
        raise ValueError("The sACN universe must be between 1 and 63999.")
    data = bytearray(DMX_CHANNELS)
    for channel, level in levels.items():
        if not 1 <= channel <= DMX_CHANNELS:
            raise ValueError(f"DMX channel {channel} is outside 1 to {DMX_CHANNELS}.")
        if not 0 <= level <= 255:
            raise ValueError(f"DMX level {level} is outside 0 to 255.")
        data[channel - 1] = level

    # DMP layer: flags and length (2), vector (1), address and data type (1), first address (2),
    # address increment (2), property count (2), then start code 0 and 512 levels.
    property_values = b"\x00" + bytes(data)
    dmp_body = (
        bytes([0x02, 0xA1])
        + struct.pack(">H", 0x0000)
        + struct.pack(">H", 0x0001)
        + struct.pack(">H", len(property_values))
        + property_values
    )
    dmp = _flags_length(len(dmp_body) + 2) + dmp_body  # length counts from the flags field to the end

    # Framing layer: flags and length (2), vector (4), source name (64), priority (1), sync
    # address (2), sequence (1), options (1), universe (2), then the DMP layer.
    framing_body = (
        struct.pack(">I", 0x02)
        + _SOURCE.ljust(64, b"\0")
        + bytes([priority])
        + struct.pack(">H", 0)
        + bytes([sequence & 0xFF, 0])
        + struct.pack(">H", universe)
        + dmp
    )
    framing = _flags_length(len(framing_body) + 2) + framing_body

    # Root layer: preamble (2), post-amble (2), ACN identifier (12), flags and length (2),
    # vector (4), sender CID (16), then the framing layer.
    root_body = struct.pack(">I", 0x04) + _CID + framing
    root = (
        struct.pack(">H", 0x0010)
        + struct.pack(">H", 0x0000)
        + _ACN_ID
        + _flags_length(len(root_body) + 2)
        + root_body
    )
    return root


def multicast_address(universe: int) -> str:
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def send(universe: int, levels: dict[int, int], host: str = "") -> None:
    packet = encode_dmx(universe, levels)
    target = host or multicast_address(universe)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(packet, (target, SACN_PORT))
