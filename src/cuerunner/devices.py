"""Audio device helpers that don't need sounddevice, so they're tested without audio hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import MutableMapping


@dataclass
class DeviceInfo:
    index: int | None  # None means the system default device
    name: str
    channels: int
    samplerate: float
    host_api: str = ""  # for example MME, Windows WASAPI, Windows WDM-KS or ASIO


def label(device: DeviceInfo) -> str:
    """What the device list shows: name, host API, and channel count."""
    api = f" [{device.host_api}]" if device.host_api else ""
    return f"{device.name}{api} ({device.channels} ch)"


def ordered(devices: list[DeviceInfo]) -> list[DeviceInfo]:
    """ASIO devices first (they give the most channels and lowest latency), then the rest in their original order."""
    return sorted(devices, key=lambda d: d.host_api != "ASIO")


def enable_asio(environ: MutableMapping[str, str]) -> bool:
    """Turn on ASIO in the bundled PortAudio. Must run before sounddevice is imported.

    The sounddevice Windows build reads SD_ENABLE_ASIO. Set CUERUNNER_NO_ASIO to turn it off.
    Returns True if ASIO was enabled.
    """
    if environ.get("CUERUNNER_NO_ASIO"):
        return False
    environ.setdefault("SD_ENABLE_ASIO", "1")
    return True


def is_asio(host_api: str) -> bool:
    return host_api.strip().upper() == "ASIO"


COMMON_RATES = (48000, 96000, 44100, 88200, 192000)


def rate_choices(supported: list[int]) -> list[int]:
    """Supported sample rates, 48 kHz first (SQ and Qu give their full channel count at 48 kHz), then the rest in order."""
    return sorted(set(supported), key=lambda r: (r != 48000, r))
