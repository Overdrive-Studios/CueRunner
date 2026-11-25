"""Audio effects: a small EQ chain for the inspector's Audio FX tab.

Biquad coefficients follow the standard RBJ audio EQ formulas. Filtering is done with
scipy's second-order-section filter, which keeps its state between blocks, so a cue
sounds the same whether the sound card asks for 64 frames at a time or 2048.

Pure numpy and scipy, no audio device, so the whole thing can be tested without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import sosfilt, sosfilt_zi

# A band's gain is treated as flat inside this many dB, to skip pointless filtering.
FLAT_DB = 0.05


@dataclass
class Band:
    """One EQ band. kind is "lowshelf", "peaking" or "highshelf"."""

    kind: str
    freq: float  # centre (peaking) or corner (shelf) frequency in Hz
    gain_db: float = 0.0
    q: float = 0.707

    def flat(self) -> bool:
        """A filter always does something; a shelf or peak at 0 dB does not."""
        if self.kind in FILTER_KINDS:
            return False
        return abs(self.gain_db) < FLAT_DB


def _biquad(band: Band, rate: int) -> np.ndarray:
    """One normalised second-order section [b0, b1, b2, 1, a1, a2]."""
    freq = min(max(band.freq, 1.0), rate / 2 - 1)
    amp = 10 ** (band.gain_db / 40)
    w0 = 2 * np.pi * freq / rate
    cos_w0, sin_w0 = np.cos(w0), np.sin(w0)
    alpha = sin_w0 / (2 * max(band.q, 0.05))
    if band.kind in ("highpass", "lowpass"):
        # Filters cut rather than boost, so their gain setting is not used.
        cos_plus = (1 + cos_w0) if band.kind == "highpass" else (1 - cos_w0)
        sign = -1 if band.kind == "highpass" else 1
        b = [cos_plus / 2, sign * cos_plus, cos_plus / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif band.kind == "peaking":
        b = [1 + alpha * amp, -2 * cos_w0, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cos_w0, 1 - alpha / amp]
    elif band.kind in ("lowshelf", "highshelf"):
        # Shelves use a fixed slope of 1, which gives this alpha.
        alpha = sin_w0 / 2 * np.sqrt(2)
        shared = 2 * np.sqrt(amp) * alpha
        sign = 1 if band.kind == "lowshelf" else -1
        b = [
            amp * ((amp + 1) - sign * (amp - 1) * cos_w0 + shared),
            2 * amp * sign * ((amp - 1) - sign * (amp + 1) * cos_w0),
            amp * ((amp + 1) - sign * (amp - 1) * cos_w0 - shared),
        ]
        a = [
            (amp + 1) + sign * (amp - 1) * cos_w0 + shared,
            -2 * sign * ((amp - 1) + sign * (amp + 1) * cos_w0),
            (amp + 1) + sign * (amp - 1) * cos_w0 - shared,
        ]
    else:
        raise ValueError(f"unknown band type: {band.kind}")
    section = np.array([b[0], b[1], b[2], a[0], a[1], a[2]], dtype=np.float64)
    return section / a[0]


# What the Audio FX tab offers, with sensible starting values for each.
EFFECT_TYPES = [
    ("Low shelf", "lowshelf", 120.0),
    ("Peaking EQ", "peaking", 1000.0),
    ("High shelf", "highshelf", 6000.0),
    ("High pass", "highpass", 80.0),
    ("Low pass", "lowpass", 12000.0),
]
FILTER_KINDS = ("highpass", "lowpass")  # these cut by frequency; gain does not apply


def effect_label(kind: str) -> str:
    return next((label for label, value, _ in EFFECT_TYPES if value == kind), kind)


def new_effect(kind: str) -> dict:
    freq = next((f for _, value, f in EFFECT_TYPES if value == kind), 1000.0)
    return {"kind": kind, "freq": freq, "gain_db": 0.0, "q": 0.707}


def bands_from(effects: list) -> list[Band]:
    """Turn a cue's saved effect list into bands the chain can use."""
    bands = []
    for effect in effects or []:
        kind = str(effect.get("kind", ""))
        if kind not in {value for _, value, _ in EFFECT_TYPES}:
            continue
        bands.append(Band(
            kind=kind,
            freq=float(effect.get("freq", 1000.0)),
            gain_db=float(effect.get("gain_db", 0.0)),
            q=float(effect.get("q", 0.707)),
        ))
    return bands


def default_bands() -> list[Band]:
    """The three bands the inspector shows, all flat."""
    return [
        Band("lowshelf", 120.0),
        Band("peaking", 1000.0),
        Band("highshelf", 6000.0),
    ]


@dataclass
class EqChain:
    """A stateful EQ for one voice. Flat bands are skipped entirely."""

    bands: list[Band] = field(default_factory=default_bands)
    rate: int = 48000
    channels: int = 2
    _sos: np.ndarray | None = field(default=None, repr=False)
    _state: np.ndarray | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        active = [b for b in self.bands if not b.flat()]
        if not active:
            return
        self._sos = np.stack([_biquad(b, self.rate) for b in active])
        # One filter state per section per channel, started so a steady signal does not thump.
        zi = sosfilt_zi(self._sos)  # (sections, 2)
        self._state = np.repeat(zi[:, :, None], self.channels, axis=2) * 0.0

    def flat(self) -> bool:
        return self._sos is None

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter a (frames, channels) block, carrying state over to the next call."""
        if self._sos is None or len(block) == 0:
            return block
        if block.shape[1] != self.channels:  # the clip changed shape; start again
            self.channels = block.shape[1]
            self.__post_init__()
            if self._sos is None:
                return block
        out, self._state = sosfilt(self._sos, block.astype(np.float64), axis=0, zi=self._state)
        return out.astype(np.float32)


def db_to_gain(db: float) -> float:
    """-inf dB is silence; 0 dB is unity."""
    if db <= -60:
        return 0.0
    return float(10 ** (db / 20))


def gain_to_db(gain: float) -> float:
    if gain <= 0:
        return -60.0
    return float(20 * np.log10(gain))
