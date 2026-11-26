"""Pitch shifting, and the Preserve pitch option that uses it."""

import numpy as np
import pytest

from cuerunner.pitch import PitchShifter, ratio_for

RATE = 48000


def tone(freq: float, seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def shift(signal: np.ndarray, semitones: float, block: int = 1024) -> np.ndarray:
    shifter = PitchShifter(ratio_for(semitones))
    return np.concatenate([shifter.process(signal[i:i + block])
                           for i in range(0, len(signal), block)])


def dominant(signal: np.ndarray, skip: int = RATE) -> float:
    """The strongest frequency, measured once the shifter has filled up."""
    tail = np.asarray(signal, dtype=np.float64)[skip:]
    if np.max(np.abs(tail)) < 1e-6:
        return 0.0
    spectrum = np.abs(np.fft.rfft(tail * np.hanning(len(tail))))
    return float(np.fft.rfftfreq(len(tail), 1 / RATE)[np.argmax(spectrum)])


def level(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(signal, dtype=np.float64)[RATE:] ** 2)))


def test_a_ratio_of_one_is_no_shift():
    assert ratio_for(0) == pytest.approx(1.0)
    assert ratio_for(12) == pytest.approx(2.0)
    assert ratio_for(-12) == pytest.approx(0.5)


@pytest.mark.parametrize("semitones", [0, 1, 3, 7, 12, -1, -5, -12, 24])
def test_the_pitch_lands_where_it_should(semitones):
    out = shift(tone(440), semitones)
    assert dominant(out) == pytest.approx(440 * ratio_for(semitones), rel=0.02)


@pytest.mark.parametrize("semitones", [0, 3, 7, 12, -5, -12])
def test_shifting_does_not_change_how_loud_it_is(semitones):
    """Changing pitch must not change level, or cues would jump about in a show."""
    assert level(shift(tone(440), semitones)) == pytest.approx(level(tone(440)), rel=0.1)


def test_the_result_is_a_clean_tone_rather_than_a_warble():
    """Frames that do not line up cancel each other and the sound wavers; this catches that."""
    out = np.asarray(shift(tone(440), 12), dtype=np.float64)[RATE:]
    crest = np.max(np.abs(out)) / np.sqrt(np.mean(out ** 2))
    assert crest == pytest.approx(np.sqrt(2), rel=0.15)  # a clean sine is about 1.41


def test_exactly_as_many_samples_come_back_as_went_in():
    for block in (64, 256, 1024, 4096):
        signal = tone(440, seconds=1.0)
        assert len(shift(signal, 5, block=block)) == len(signal)


def test_the_block_size_does_not_change_the_result():
    """The sound card picks the block size, so the shifter must carry its state over."""
    signal = tone(440, seconds=2.0)
    big = shift(signal, 7, block=2048)
    small = shift(signal, 7, block=256)
    assert dominant(big) == pytest.approx(dominant(small), rel=0.02)
    assert level(big) == pytest.approx(level(small), rel=0.1)


def test_silence_in_gives_silence_out():
    quiet = np.zeros(RATE, dtype=np.float32)
    assert np.max(np.abs(shift(quiet, 7))) < 1e-6


def test_a_shift_of_nothing_is_skipped_entirely():
    from cuerunner.fx import Chain, new_effect

    assert Chain(effects=[new_effect("pitch")], rate=RATE, channels=1).flat()
    assert not Chain(effects=[dict(new_effect("pitch"), semitones=5.0)],
                     rate=RATE, channels=1).flat()


def test_stereo_is_shifted_on_both_sides():
    from cuerunner.fx import Chain, new_effect

    stereo = np.stack([tone(440), tone(880)], axis=1)
    chain = Chain(effects=[dict(new_effect("pitch"), semitones=12.0)], rate=RATE, channels=2)
    out = np.concatenate([chain.process(stereo[i:i + 1024]) for i in range(0, len(stereo), 1024)])
    assert dominant(out[:, 0]) == pytest.approx(880, rel=0.03)
    assert dominant(out[:, 1]) == pytest.approx(1760, rel=0.03)


# ----------------------------------------------------------------- preserve pitch
def test_preserve_pitch_cancels_out_the_speed_change():
    """Playing at 2x raises the pitch an octave, so the cue shifts it back down one."""
    import os
    from unittest import mock

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    widgets.QApplication.instance() or widgets.QApplication([])
    from cuerunner.main import MainWindow
    from cuerunner.model import Cue

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
    try:
        cue = Cue(number="1", name="Song", kind="audio", rate=2.0, preserve_pitch=True)
        chain = window._cue_eq(cue, 2)
        assert chain is not None and not chain.flat()

        # Run a tone through it: played at 2x the pitch would double, so the chain
        # must pull it back down by an octave.
        out = np.concatenate([chain.process(np.stack([tone(880)[i:i + 1024]] * 2, axis=1))
                              for i in range(0, RATE * 2, 1024)])
        assert dominant(out[:, 0]) == pytest.approx(440, rel=0.03)

        cue.preserve_pitch = False
        assert window._cue_eq(cue, 2) is None  # nothing to do without it
    finally:
        window._mark_saved()
        window.close()
