import numpy as np

from cuerunner.feedback import FeedbackSuppressor

RATE = 48000


def tone(freq: float, amplitude: float, n: int, start: int = 0) -> np.ndarray:
    t = (np.arange(n) + start) / RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def peak_level(signal: np.ndarray, freq: float) -> float:
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / RATE)
    window = (freqs > freq - 40) & (freqs < freq + 40)
    return float(spectrum[window].max())


def test_steady_tone_gets_a_notch_and_is_reduced() -> None:
    suppressor = FeedbackSuppressor(rate=RATE)
    block = 1024
    start = 0
    for _ in range(60):
        suppressor.process(tone(2000.0, 0.2, block, start))
        start += block

    assert any(abs(f - 2000.0) < 30.0 for f in suppressor.notch_frequencies)

    before = tone(2000.0, 0.2, 8192, start)
    after = suppressor.process(before)
    assert peak_level(after, 2000.0) < peak_level(before, 2000.0) * 0.5


def test_noise_does_not_get_notched() -> None:
    rng = np.random.default_rng(0)
    suppressor = FeedbackSuppressor(rate=RATE)
    for _ in range(80):
        suppressor.process((rng.standard_normal(1024) * 0.05).astype(np.float32))
    assert suppressor.notch_frequencies == []


def test_notch_count_is_limited() -> None:
    suppressor = FeedbackSuppressor(rate=RATE, max_notches=2)
    start = 0
    for _ in range(60):
        mixed = tone(500.0, 0.2, 1024, start) + tone(1500.0, 0.2, 1024, start) + tone(3000.0, 0.2, 1024, start)
        suppressor.process(mixed)
        start += 1024
    assert len(suppressor.notch_frequencies) <= 2
