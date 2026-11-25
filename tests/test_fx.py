"""The EQ chain behind the inspector's Audio FX tab."""

import numpy as np
import pytest

from cuerunner.fx import Band, EqChain, db_to_gain, default_bands, gain_to_db

RATE = 48000


def tone(freq: float, seconds: float = 0.5, channels: int = 1) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    wave = np.sin(2 * np.pi * freq * t).astype(np.float32)
    return np.repeat(wave[:, None], channels, axis=1)


def rms(block: np.ndarray) -> float:
    """Measured over the second half, so the filter has settled."""
    tail = block[len(block) // 2:]
    return float(np.sqrt(np.mean(tail.astype(np.float64) ** 2)))


def test_a_flat_chain_passes_audio_through_untouched():
    chain = EqChain(rate=RATE, channels=1)
    assert chain.flat()
    block = tone(1000)
    assert chain.process(block) is block


def test_a_low_shelf_boost_lifts_bass_and_leaves_treble_alone():
    bands = [Band("lowshelf", 120.0, gain_db=12.0)]
    low = rms(EqChain(bands=bands, rate=RATE, channels=1).process(tone(50)))
    high = rms(EqChain(bands=bands, rate=RATE, channels=1).process(tone(8000)))
    assert low / rms(tone(50)) == pytest.approx(db_to_gain(12), rel=0.15)
    assert high / rms(tone(8000)) == pytest.approx(1.0, rel=0.05)


def test_a_peaking_cut_reduces_its_own_band():
    bands = [Band("peaking", 1000.0, gain_db=-12.0, q=1.0)]
    cut = rms(EqChain(bands=bands, rate=RATE, channels=1).process(tone(1000)))
    assert cut / rms(tone(1000)) == pytest.approx(db_to_gain(-12), rel=0.15)


def test_a_high_shelf_boost_lifts_treble_and_leaves_bass_alone():
    bands = [Band("highshelf", 6000.0, gain_db=9.0)]
    high = rms(EqChain(bands=bands, rate=RATE, channels=1).process(tone(12000)))
    low = rms(EqChain(bands=bands, rate=RATE, channels=1).process(tone(100)))
    assert high / rms(tone(12000)) == pytest.approx(db_to_gain(9), rel=0.15)
    assert low / rms(tone(100)) == pytest.approx(1.0, rel=0.05)


def test_the_result_does_not_depend_on_the_block_size():
    """The sound card picks the block size, so the EQ must carry its state between calls."""
    bands = [Band("peaking", 800.0, gain_db=6.0, q=1.2)]
    block = tone(800, seconds=0.2)
    whole = EqChain(bands=bands, rate=RATE, channels=1).process(block)
    chain = EqChain(bands=bands, rate=RATE, channels=1)
    pieces = np.concatenate([chain.process(block[i:i + 64]) for i in range(0, len(block), 64)])
    assert np.allclose(whole, pieces, atol=1e-6)


def test_stereo_channels_are_filtered_independently():
    bands = [Band("lowshelf", 120.0, gain_db=12.0)]
    stereo = np.concatenate([tone(50), tone(8000)], axis=1)
    out = EqChain(bands=bands, rate=RATE, channels=2).process(stereo)
    assert rms(out[:, :1]) / rms(tone(50)) == pytest.approx(db_to_gain(12), rel=0.15)
    assert rms(out[:, 1:]) / rms(tone(8000)) == pytest.approx(1.0, rel=0.05)


def test_default_bands_are_flat():
    assert all(band.flat() for band in default_bands())
    assert EqChain(rate=RATE, channels=2).flat()


def test_decibels_convert_both_ways():
    assert db_to_gain(0) == pytest.approx(1.0)
    assert db_to_gain(-6) == pytest.approx(0.501, rel=0.01)
    assert db_to_gain(-60) == 0.0  # the bottom of the fader is silence
    assert gain_to_db(1.0) == pytest.approx(0.0)
    assert gain_to_db(0.0) == -60.0
