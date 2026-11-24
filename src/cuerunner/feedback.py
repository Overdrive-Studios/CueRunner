"""Feedback suppression for the talkback mic.

Feedback is a steady tone that keeps growing: it peaks well above its neighbours in the spectrum,
and it stays put from one analysis to the next. Speech moves around, so it doesn't trigger this.
When a steady peak persists, a narrow notch filter goes on that frequency.

The analysis uses a 4096-sample window, about 85 ms at 48 kHz. Blocks from the mic are too short
for the resolution needed to tell feedback apart from speech.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import iirnotch, lfilter


class FeedbackSuppressor:
    def __init__(
        self,
        rate: int = 48000,
        n_fft: int = 4096,
        hop: int = 1024,
        threshold_db: float = 12.0,
        persistence: int = 8,
        max_notches: int = 6,
        q: float = 30.0,
        min_hz: float = 150.0,
        max_hz: float = 8000.0,
    ) -> None:
        self.rate = rate
        self.n_fft = n_fft
        self.hop = hop
        self.threshold_db = threshold_db
        self.persistence = persistence
        self.max_notches = max_notches
        self.q = q
        self.min_hz = min_hz
        self.max_hz = max_hz
        self._window = np.hanning(n_fft)
        self._history = np.zeros(0, dtype=np.float32)
        self._since_analysis = 0
        self._hits: dict[int, int] = {}
        self._notches: list[dict] = []

    @property
    def notch_frequencies(self) -> list[float]:
        return [notch["freq"] for notch in self._notches]

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32)
        self._history = np.concatenate([self._history, block])[-self.n_fft :]
        self._since_analysis += len(block)
        while self._since_analysis >= self.hop:
            self._since_analysis -= self.hop
            if len(self._history) == self.n_fft:
                self._analyse()

        out = block
        for notch in self._notches:
            out, notch["zi"] = lfilter(notch["b"], notch["a"], out, zi=notch["zi"])
        return out.astype(np.float32)

    def _analyse(self) -> None:
        spectrum = np.fft.rfft(self._history * self._window)
        mag_db = 20.0 * np.log10(np.abs(spectrum) + 1e-9)
        # A 31-bin moving average stands in for the local noise floor around each peak.
        smooth = np.convolve(mag_db, np.ones(31) / 31.0, mode="same")
        excess = mag_db - smooth
        freqs = np.fft.rfftfreq(self.n_fft, 1.0 / self.rate)

        low = max(1, int(np.searchsorted(freqs, self.min_hz)))
        high = min(int(np.searchsorted(freqs, self.max_hz)), len(mag_db) - 1)
        hits = {
            i
            for i in range(low, high)
            if excess[i] > self.threshold_db and mag_db[i] >= mag_db[i - 1] and mag_db[i] >= mag_db[i + 1]
        }
        # A peak only counts if it appears again in each consecutive analysis.
        self._hits = {i: self._hits.get(i, 0) + 1 for i in hits}

        for i, count in self._hits.items():
            if count >= self.persistence and len(self._notches) < self.max_notches and not self._near_notch(freqs[i]):
                self._add_notch(float(freqs[i]))

    def _near_notch(self, freq: float) -> bool:
        return any(abs(notch["freq"] - freq) < 50.0 for notch in self._notches)

    def _add_notch(self, freq: float) -> None:
        b, a = iirnotch(freq, self.q, fs=self.rate)
        self._notches.append({"freq": freq, "b": b, "a": a, "zi": np.zeros(2)})
