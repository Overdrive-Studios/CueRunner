"""Incoming MIDI Timecode: decoding positions and firing cue triggers."""

import pytest

from cuerunner import mtc


def _run_cycle(decoder, ms, fps):
    result = None
    for piece in range(8):
        result = decoder.feed(mtc.quarter_frame_message(piece, ms, fps)) or result
    return result


@pytest.mark.parametrize("fps", [24, 25, 30])
def test_decoder_reads_a_full_quarter_frame_cycle(fps):
    decoder = mtc.QuarterFrameDecoder()
    ms = 3_723_000  # 01:02:03.000
    decoded, rate = _run_cycle(decoder, ms, fps)
    assert rate == fps
    assert decoded == ms


def test_decoder_reads_full_frame_position():
    decoder = mtc.QuarterFrameDecoder()
    ms = mtc.parse_timecode("00:10:00:00", 25)
    assert decoder.feed(mtc.full_frame(ms, 25)) == (ms, 25)


def test_decoder_ignores_unrelated_messages():
    decoder = mtc.QuarterFrameDecoder()
    assert decoder.feed(bytes([0x90, 60, 100])) is None


def test_triggers_fire_once_when_the_clock_passes_them():
    triggers = mtc.TimecodeTriggers()
    triggers.set_items([(1000, "a"), (2000, "b")])
    assert triggers.advance(0) == []
    assert triggers.advance(1500) == ["a"]
    assert triggers.advance(1600) == []
    assert triggers.advance(2500) == ["b"]


def test_a_rewind_rearms_triggers_without_firing():
    triggers = mtc.TimecodeTriggers()
    triggers.set_items([(1000, "a")])
    triggers.advance(0)
    assert triggers.advance(1200) == ["a"]
    assert triggers.advance(200) == []  # rewound: nothing fires
    assert triggers.advance(1100) == ["a"]  # passed again
