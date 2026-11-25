"""LTC: frames encoded to audio and decoded back give the same times."""

import pytest

from cuerunner import ltc


@pytest.mark.parametrize("fps", [24, 25, 30])
def test_round_trip_recovers_consecutive_frames(fps):
    start = 3_723_000  # 01:02:03.000
    samples = ltc.encode(start, fps, frame_count=5, rate=48000)  # the last frame is incomplete, so it is not decoded
    decoded = ltc.decode(samples, 48000, fps)
    first = round(start * fps / 1000)
    expected = [round((first + n) * 1000 / fps) for n in range(4)]
    assert decoded[:4] == expected


def test_frame_bits_carry_the_time_fields():
    bits = ltc.frame_bits(ltc.parse_timecode_ms("01:02:03:04", 25), 25)
    assert [bits[i] for i in range(4)] == [0, 0, 1, 0]  # frames 4 = 0100, LSB first
    assert ltc.SYNC == bits[64:80]


def test_silence_decodes_to_nothing():
    assert ltc.decode(__import__("numpy").zeros(48000), 48000, 25) == []
