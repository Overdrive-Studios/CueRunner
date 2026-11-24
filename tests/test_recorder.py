from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from cuerunner.recorder import MAX_TRACKS, Recorder


def test_recording_round_trip_keeps_track_count_and_length(tmp_path: Path) -> None:
    path = tmp_path / "take.wav"
    recorder = Recorder(path, rate=1000, tracks=4)
    block = np.full((100, 4), 0.25, dtype=np.float32)
    recorder.push(block)
    recorder.push(block)
    recorder.close()

    info = sf.info(str(path))
    assert info.channels == 4
    assert info.frames == 200
    assert info.samplerate == 1000


def test_flushed_file_is_readable_before_close(tmp_path: Path) -> None:
    path = tmp_path / "live.wav"
    recorder = Recorder(path, rate=1000, tracks=2)
    recorder.push(np.zeros((50, 2), dtype=np.float32))
    recorder._last_flush = 0.0  # force the header refresh on the next drain
    recorder.drain()

    # Read it while the recorder is still open, as if the machine had lost power right now.
    assert sf.info(str(path)).frames == 50
    recorder.close()


def test_track_count_is_limited(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.wav", rate=1000, tracks=0)
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.wav", rate=1000, tracks=MAX_TRACKS + 1)
