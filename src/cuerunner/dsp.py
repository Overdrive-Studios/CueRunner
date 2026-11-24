"""Talkback mic processing: auto-gain, gate, compressor and limiter. Pure numpy, no audio device needed.

Feedback suppression is not included yet. It needs a frequency-domain detector, which is a separate piece of work.
"""

from __future__ import annotations

import numpy as np


def _db(linear: float) -> float:
    return 20.0 * np.log10(max(linear, 1e-12))


def _linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))


class MicChain:
    """Process one block of mono mic audio at a time. Levels are measured per block, so this is simple but not sample-accurate."""

    def __init__(
        self,
        target_db: float = -20.0,
        gate_db: float = -50.0,
        comp_threshold_db: float = -18.0,
        ratio: float = 3.0,
        ceiling: float = 0.89,
        auto_gain: bool = True,
    ) -> None:
        self.target_db = target_db
        self.gate_db = gate_db
        self.comp_threshold_db = comp_threshold_db
        self.ratio = max(1.0, ratio)
        self.ceiling = ceiling
        self.auto_gain = auto_gain
        self.gate_open = False
        self._auto_db = 0.0
        self._gate_gain = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32)
        if len(block) == 0:
            return block

        rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) + 1e-12
        level_db = _db(rms)

        # Gate with hysteresis: opens above gate_db, closes 6 dB below it.
        if self.gate_open:
            self.gate_open = level_db > self.gate_db - 6.0
        else:
            self.gate_open = level_db > self.gate_db

        # Auto-gain: nudge the level toward the target, only while there is signal.
        if self.auto_gain and self.gate_open:
            desired = float(np.clip(self.target_db - level_db, -12.0, 12.0))
            self._auto_db += (desired - self._auto_db) * 0.1
        x = block * _linear(self._auto_db)
        x_db = _db(rms * _linear(self._auto_db))

        # Compressor: reduce anything above the threshold by the ratio.
        over = max(0.0, x_db - self.comp_threshold_db)
        x = x * _linear(-over * (1.0 - 1.0 / self.ratio))

        # Gate gain moves smoothly so the gate doesn't click.
        target_gate = 1.0 if self.gate_open else 0.0
        self._gate_gain += (target_gate - self._gate_gain) * 0.3
        x = x * self._gate_gain

        # Limiter: hard ceiling.
        return np.clip(x, -self.ceiling, self.ceiling).astype(np.float32)
