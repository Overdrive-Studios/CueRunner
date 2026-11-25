"""sACN packet layout, the multicast address, and Network cue sends over local UDP and TCP."""

import socket
import struct
import threading

import pytest

from cuerunner import netsend, sacn


def test_packet_is_638_bytes_with_the_acn_header():
    packet = sacn.encode_dmx(1, {1: 255})
    assert len(packet) == 638
    assert packet[4:16] == b"ASC-E1.17\0\0\0"
    assert struct.unpack(">I", packet[18:22]) == (0x04,)  # root vector follows the root flags and length


def test_layer_lengths_match_the_packet():
    packet = sacn.encode_dmx(1, {})
    root_length = struct.unpack(">H", packet[16:18])[0] & 0x0FFF
    assert root_length == len(packet) - 16
    framing_length = struct.unpack(">H", packet[38:40])[0] & 0x0FFF
    assert framing_length == len(packet) - 38
    dmp_length = struct.unpack(">H", packet[115:117])[0] & 0x0FFF  # DMP layer starts after the framing header
    assert dmp_length == 523


def test_levels_and_start_code():
    packet = sacn.encode_dmx(1, {1: 255, 512: 7})
    assert packet[-512] == 255
    assert packet[-1] == 7
    assert packet[-513] == 0  # start code
    assert packet[-512 - 1:-512] == b"\x00"


def test_universe_is_in_the_framing_layer():
    packet = sacn.encode_dmx(300, {})
    assert struct.unpack(">H", packet[113:115])[0] == 300  # universe sits after the 64-byte source name


def test_multicast_address_per_universe():
    assert sacn.multicast_address(1) == "239.255.0.1"
    assert sacn.multicast_address(300) == "239.255.1.44"


def test_bad_universe_and_levels_are_rejected():
    with pytest.raises(ValueError):
        sacn.encode_dmx(0, {})
    with pytest.raises(ValueError):
        sacn.encode_dmx(1, {1: 999})


def test_network_udp_sends_the_text():
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    port = listener.getsockname()[1]
    try:
        netsend.send("udp", f"127.0.0.1:{port}", "GO 3")
        data, _ = listener.recvfrom(1024)
    finally:
        listener.close()
    assert data == b"GO 3"


def test_network_tcp_sends_the_text_with_a_newline():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(2)
    port = server.getsockname()[1]
    received = []

    def accept():
        conn, _ = server.accept()
        with conn:
            received.append(conn.recv(1024))
        server.close()

    thread = threading.Thread(target=accept)
    thread.start()
    netsend.send("tcp", f"127.0.0.1:{port}", "PLAY 1")
    thread.join(timeout=3)
    assert received == [b"PLAY 1\n"]


def test_network_rejects_unknown_protocol():
    with pytest.raises(ValueError):
        netsend.send("serial", "127.0.0.1:9", "x")
