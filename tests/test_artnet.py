"""Art-Net DMX packets, checked byte by byte and sent to a local listener."""

import socket
import struct

import pytest

from cuerunner import artnet


def test_packet_header_and_universe():
    packet = artnet.encode_dmx(3, {1: 255})
    assert packet.startswith(b"Art-Net\0")
    assert struct.unpack("<H", packet[8:10]) == (0x5000,)
    assert struct.unpack(">H", packet[10:12]) == (14,)
    assert struct.unpack("<H", packet[14:16]) == (3,)
    assert struct.unpack(">H", packet[16:18]) == (512,)


def test_levels_land_on_their_channels():
    packet = artnet.encode_dmx(0, {1: 255, 10: 128})
    data = packet[18:]
    assert len(data) == 512
    assert data[0] == 255
    assert data[9] == 128
    assert data[1] == 0


def test_bad_channel_and_level_are_rejected():
    with pytest.raises(ValueError):
        artnet.encode_dmx(0, {513: 1})
    with pytest.raises(ValueError):
        artnet.encode_dmx(0, {1: 256})


def test_parse_levels():
    assert artnet.parse_levels("1=255 2=128, 3=0") == {1: 255, 2: 128, 3: 0}
    with pytest.raises(ValueError):
        artnet.parse_levels("")
    with pytest.raises(ValueError):
        artnet.parse_levels("one=255")


def test_send_reaches_a_listener_on_the_given_port(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    monkeypatch.setattr(artnet, "ARTNET_PORT", listener.getsockname()[1])
    try:
        artnet.send("127.0.0.1", 0, {1: 200})
        packet, _ = listener.recvfrom(1024)
    finally:
        listener.close()
    assert packet[18] == 200


def test_ramp_levels_moves_between_start_and_end():
    assert artnet.ramp_levels({1: 0}, {1: 200}, 0.5) == {1: 100}
    assert artnet.ramp_levels({}, {2: 255}, 1.0) == {2: 255}
    assert artnet.ramp_levels({1: 50}, {1: 50}, 0.0) == {1: 50}
