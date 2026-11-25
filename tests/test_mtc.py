"""MIDI Timecode: quarter-frame and full-frame encoding, and the generator's sequencing."""

import pytest

from cuerunner import mtc


@pytest.mark.parametrize("fps", [24, 25, 30])
def test_quarter_frames_round_trip(fps):
    ms = 3_723_000 + 12 * (1000 // fps)  # 01:02:03 plus 12 frames
    messages = [mtc.quarter_frame_message(p, ms, fps) for p in range(8)]
    decoded_ms, decoded_fps = mtc.decode_quarter_frames(messages)
    assert decoded_fps == fps
    assert abs(decoded_ms - ms) <= 1000 / fps


def test_full_frame_layout():
    # 01:02:03:04 at 25 fps: rate code 1 sits in bits 5-6 of the hour byte.
    ms = mtc.parse_timecode("01:02:03:04", 25)
    assert mtc.full_frame(ms, 25) == bytes([0xF0, 0x7F, 0x7F, 0x01, 0x01, 0x20 | 1, 2, 3, 4, 0xF7])


def test_parse_timecode_accepts_frames_below_rate():
    assert mtc.parse_timecode("00:00:01:00", 30) == 1000
    with pytest.raises(ValueError):
        mtc.parse_timecode("00:00:00:25", 25)
    with pytest.raises(ValueError):
        mtc.parse_timecode("nonsense", 25)


def test_generator_sends_full_frame_then_eight_pieces_per_two_frames():
    sent = []
    gen = mtc.MtcGenerator(0, 25, sent.append)
    gen.start()
    for _ in range(16):
        gen.tick()
    assert sent[0][1] == 0x7F  # full-frame SysEx first
    pieces = sent[1:]
    assert [m[0] for m in pieces] == [0xF1] * 16
    assert [m[1] >> 4 for m in pieces[:8]] == list(range(8))
    # The second cycle covers frames 2 and 3, so its time has moved on.
    first, second = pieces[:8], pieces[8:]
    assert mtc.decode_quarter_frames(first)[0] == 0
    assert mtc.decode_quarter_frames(second)[0] == 80  # two frames at 25 fps


def test_generator_stops_sending():
    sent = []
    gen = mtc.MtcGenerator(0, 30, sent.append)
    gen.start()
    gen.stop()
    gen.tick()
    assert len(sent) == 1  # only the full frame from start()
    assert not gen.running


def test_interval_is_a_quarter_frame():
    assert mtc.MtcGenerator(0, 25, lambda b: None).interval_ms == pytest.approx(10.0)
