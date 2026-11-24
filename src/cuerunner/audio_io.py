"""Hardware output through sounddevice (PortAudio). Only the app imports this, not the tests."""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd

from cuerunner.engine import Mixer

BLOCK_SIZE = 256  # roughly 5 ms at 48 kHz; smaller is lower latency but needs a faster machine


@dataclass
class DeviceInfo:
    index: int | None  # None means the system default device
    name: str
    channels: int
    samplerate: float


def output_devices() -> list[DeviceInfo]:
    devices = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0:
            devices.append(
                DeviceInfo(index, device["name"], int(device["max_output_channels"]), float(device["default_samplerate"]))
            )
    return devices


def input_devices() -> list[DeviceInfo]:
    devices = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            devices.append(
                DeviceInfo(index, device["name"], int(device["max_input_channels"]), float(device["default_samplerate"]))
            )
    return devices


class MicInput:
    """Capture from an input device. Each block goes to on_block on the input thread.

    With channels=1 the block is 1-D (the mic). With more channels it is (frames, channels),
    which is what the recorder uses.
    """

    def __init__(self, device: int | None, samplerate: int, on_block, channels: int = 1) -> None:
        self._on_block = on_block
        self._channels = channels
        self.stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            device=device,
            blocksize=BLOCK_SIZE,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time, status) -> None:
        if self._channels == 1:
            self._on_block(indata[:, 0].copy())
        else:
            self._on_block(indata.copy())

    def start(self) -> None:
        self.stream.start()

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


def default_output() -> DeviceInfo:
    device = sd.query_devices(kind="output")
    return DeviceInfo(None, device["name"], int(device["max_output_channels"]), float(device["default_samplerate"]))


class AudioOutput:
    def __init__(self, mixer: Mixer, device: DeviceInfo) -> None:
        self.mixer = mixer
        self.stream = sd.OutputStream(
            samplerate=mixer.rate,
            channels=mixer.out_channels,
            dtype="float32",
            device=device.index,
            blocksize=BLOCK_SIZE,
            callback=self._callback,
        )

    def _callback(self, outdata, frames, time, status) -> None:
        outdata[:] = self.mixer.render(frames)

    def start(self) -> None:
        self.stream.start()

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()
