"""Plain text over the network for Network cues: one message over UDP or TCP.

TCP messages end with a newline, which most listening software expects. UDP messages are sent
as they are.
"""

from __future__ import annotations

import socket

from cuerunner.osc import parse_target

NETWORK_PROTOCOLS = ("udp", "tcp")
TCP_TIMEOUT_S = 3.0


def send(protocol: str, target: str, message: str) -> None:
    if protocol not in NETWORK_PROTOCOLS:
        raise ValueError(f"'{protocol}' is not a network protocol. Use udp or tcp.")
    host, port = parse_target(target)
    data = message.encode("utf-8")
    if protocol == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(data, (host, port))
    else:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S) as sock:
            sock.sendall(data + b"\n")
