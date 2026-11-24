import numpy as np

from cuerunner.dsp import MicChain


def sine(amplitude: float, n: int = 480) -> np.ndarray:
    t = np.arange(n) / 48000.0
    return (amplitude * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)


def test_gate_stays_closed_on_silence() -> None:
    chain = MicChain()
    out = None
    for _ in range(20):
        out = chain.process(np.zeros(480, dtype=np.float32))
    assert chain.gate_open is False
    assert np.allclose(out, 0.0)


def test_gate_opens_on_speech_level_signal() -> None:
    chain = MicChain()
    chain.process(sine(0.1))
    assert chain.gate_open is True


def test_limiter_never_exceeds_ceiling() -> None:
    chain = MicChain(auto_gain=False, comp_threshold_db=0.0, ceiling=0.5)
    out = chain.process(np.ones(480, dtype=np.float32))
    assert np.max(np.abs(out)) <= 0.5 + 1e-6


def test_auto_gain_raises_a_quiet_voice() -> None:
    chain = MicChain(target_db=-20.0, gate_db=-60.0, comp_threshold_db=0.0)
    first = chain.process(sine(0.01))
    last = None
    for _ in range(80):
        last = chain.process(sine(0.01))
    assert np.sqrt(np.mean(last**2)) > np.sqrt(np.mean(first**2)) * 2


def test_compressor_reduces_a_loud_peak() -> None:
    loud = sine(0.8)
    no_comp = MicChain(auto_gain=False, comp_threshold_db=0.0, ceiling=1.0).process(loud)
    comp = MicChain(auto_gain=False, comp_threshold_db=-30.0, ratio=4.0, ceiling=1.0).process(loud)
    assert np.sqrt(np.mean(comp**2)) < np.sqrt(np.mean(no_comp**2))
