"""OSC encoding, parsing and sending over a real local UDP socket."""

import socket
import struct

import pytest

from cuerunner import osc


def test_encode_pads_strings_to_four_bytes():
    packet = osc.encode("/a", [])
    assert packet == b"/a\0\0" + b",\0\0\0"


def test_encode_types_int_float_and_string():
    packet = osc.encode("/x", [1, 0.5, "hi"])
    assert packet.startswith(b"/x\0\0")
    tags_start = packet.index(b",")
    assert packet[tags_start:tags_start + 4] == b",ifs"
    # The tags ",ifs" plus its terminator pad to 8 bytes, so arguments start 8 bytes after the comma.
    payload = tags_start + 8
    assert struct.unpack(">i", packet[payload:payload + 4]) == (1,)
    assert struct.unpack(">f", packet[payload + 4:payload + 8]) == (0.5,)
    assert packet[payload + 8:payload + 12] == b"hi\0\0"


def test_parse_message_detects_numbers():
    assert osc.parse_message("/fader/1 0.5 3 hello") == ("/fader/1", [0.5, 3, "hello"])


def test_parse_message_needs_an_address():
    with pytest.raises(ValueError):
        osc.parse_message("   ")


def test_encode_rejects_address_without_slash():
    with pytest.raises(ValueError):
        osc.encode("fader", [])


def test_parse_target():
    assert osc.parse_target("192.168.1.10:8000") == ("192.168.1.10", 8000)
    with pytest.raises(ValueError):
        osc.parse_target("192.168.1.10")
    with pytest.raises(ValueError):
        osc.parse_target("host:99999")


def test_send_delivers_a_packet_to_a_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    port = listener.getsockname()[1]
    try:
        osc.send(f"127.0.0.1:{port}", "/cue/1/go 0.5")
        packet, _ = listener.recvfrom(1024)
    finally:
        listener.close()
    assert packet.startswith(b"/cue/1/go\0\0")
    assert b",f\0\0" in packet
