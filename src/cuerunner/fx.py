"""Audio effects for the inspector's Audio FX tab.

QLab offers Apple's AudioUnits, which only exist on a Mac. These are CueRunner's own,
written in numpy and scipy so they work the same on Windows and macOS and can be tested
without a sound card.

Every effect declares the parameters it has, so the inspector builds its controls from
the effect rather than knowing about each one. Effects keep their state between blocks,
so a cue sounds the same whatever block size the sound card asks for.

Filter maths follows the standard RBJ audio EQ formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.signal import butter, lfilter, sosfilt

FLAT_DB = 0.05  # a shelf or peak inside this many dB is treated as doing nothing


# --------------------------------------------------------------------------- parameters
@dataclass(frozen=True)
class Param:
    """One setting on an effect, and the range the inspector should offer for it."""

    key: str
    label: str
    low: float
    high: float
    default: float
    suffix: str = ""
    decimals: int = 1


GAIN = Param("gain_db", "Gain", -24.0, 24.0, 0.0, " dB", 1)
Q = Param("q", "Q", 0.1, 10.0, 0.707, "", 2)
MIX = Param("mix", "Mix", 0.0, 100.0, 50.0, " %", 0)


# --------------------------------------------------------------------------- biquads
def _biquad(kind: str, freq: float, gain_db: float, q: float, rate: int) -> np.ndarray:
    """One normalised second-order section [b0, b1, b2, 1, a1, a2]."""
    freq = min(max(freq, 1.0), rate / 2 - 1)
    amp = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq / rate
    cos_w0, sin_w0 = np.cos(w0), np.sin(w0)
    alpha = sin_w0 / (2 * max(q, 0.05))

    if kind in ("highpass", "lowpass"):
        shared = (1 + cos_w0) if kind == "highpass" else (1 - cos_w0)
        sign = -1 if kind == "highpass" else 1
        b = [shared / 2, sign * shared, shared / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * cos_w0, 1.0]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "peaking":
        b = [1 + alpha * amp, -2 * cos_w0, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cos_w0, 1 - alpha / amp]
    elif kind in ("lowshelf", "highshelf"):
        alpha = sin_w0 / 2 * np.sqrt(2)  # a fixed shelf slope of 1
        shared = 2 * np.sqrt(amp) * alpha
        sign = 1 if kind == "lowshelf" else -1
        b = [amp * ((amp + 1) - sign * (amp - 1) * cos_w0 + shared),
             2 * amp * sign * ((amp - 1) - sign * (amp + 1) * cos_w0),
             amp * ((amp + 1) - sign * (amp - 1) * cos_w0 - shared)]
        a = [(amp + 1) + sign * (amp - 1) * cos_w0 + shared,
             -2 * sign * ((amp - 1) + sign * (amp + 1) * cos_w0),
             (amp + 1) + sign * (amp - 1) * cos_w0 - shared]
    else:
        raise ValueError(f"unknown filter: {kind}")
    section = np.array([b[0], b[1], b[2], a[0], a[1], a[2]], dtype=np.float64)
    return section / a[0]


class _Sos:
    """A stack of second-order sections that keeps its filter state between blocks."""

    def __init__(self, sos: np.ndarray, channels: int) -> None:
        self.sos = np.atleast_2d(sos)
        self.channels = channels
        self._state = np.zeros((len(self.sos), 2, channels))

    def run(self, block: np.ndarray) -> np.ndarray:
        if block.shape[1] != self.channels:  # the clip changed shape; start again
            self.channels = block.shape[1]
            self._state = np.zeros((len(self.sos), 2, self.channels))
        out, self._state = sosfilt(self.sos, block.astype(np.float64), axis=0, zi=self._state)
        return out.astype(np.float32)


# --------------------------------------------------------------------------- effects
class Effect:
    """Base class. process() is handed (frames, channels) and returns the same shape."""

    def process(self, block: np.ndarray) -> np.ndarray:  # pragma: no cover - overridden
        return block

    def flat(self) -> bool:
        """True when this effect would leave the audio untouched, so it can be skipped."""
        return False


class Filter(Effect):
    """Any of the single biquad shapes: shelves, peaks, pass filters, bandpass, notch."""

    SHAPED = ("lowshelf", "peaking", "highshelf")  # these do nothing at 0 dB

    def __init__(self, kind: str, params: dict, rate: int, channels: int) -> None:
        self.kind = kind
        self.gain_db = float(params.get("gain_db", 0.0))
        gain = self.gain_db if kind in self.SHAPED else 0.0
        self._sos = _Sos(_biquad(kind, float(params.get("freq", 1000.0)), gain,
                                 float(params.get("q", 0.707)), rate), channels)

    def flat(self) -> bool:
        return self.kind in self.SHAPED and abs(self.gain_db) < FLAT_DB

    def process(self, block: np.ndarray) -> np.ndarray:
        return self._sos.run(block)


class GraphicEq(Effect):
    """Ten fixed bands, as a graphic EQ on a rack would have."""

    BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.gains = [float(params.get(f"b{i}", 0.0)) for i in range(len(self.BANDS))]
        sections = [_biquad("peaking", freq, gain, 1.4, rate)
                    for freq, gain in zip(self.BANDS, self.gains) if abs(gain) >= FLAT_DB]
        self._sos = _Sos(np.stack(sections), channels) if sections else None

    def flat(self) -> bool:
        return self._sos is None

    def process(self, block: np.ndarray) -> np.ndarray:
        return block if self._sos is None else self._sos.run(block)


def _one_pole(seconds: float, rate: int) -> float:
    """The coefficient for a smoother that settles over roughly this long."""
    return float(np.exp(-1.0 / max(seconds * rate, 1.0)))


class Compressor(Effect):
    """Turns down whatever is louder than the threshold.

    The level is followed with a smoother that reacts at the attack rate and lets go at
    the release rate. Both channels are driven by the same level so the stereo image
    does not wander.
    """

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.threshold = float(params.get("threshold_db", -20.0))
        self.ratio = max(float(params.get("ratio", 4.0)), 1.0)
        self.makeup = float(params.get("makeup_db", 0.0))
        self._attack = _one_pole(float(params.get("attack_ms", 10.0)) / 1000, rate)
        self._release = _one_pole(float(params.get("release_ms", 150.0)) / 1000, rate)
        self._env = 0.0

    def _follow(self, level: np.ndarray) -> np.ndarray:
        env = np.empty_like(level)
        value = self._env
        attack, release = self._attack, self._release
        for index, sample in enumerate(level):
            coeff = attack if sample > value else release
            value = sample + coeff * (value - sample)
            env[index] = value
        self._env = float(value)
        return env

    def process(self, block: np.ndarray) -> np.ndarray:
        level = np.max(np.abs(block), axis=1).astype(np.float64)
        env = self._follow(level)
        env_db = 20 * np.log10(np.maximum(env, 1e-7))
        over = env_db - self.threshold
        reduction = np.where(over > 0, over * (1.0 / self.ratio - 1.0), 0.0)
        gain = 10 ** ((reduction + self.makeup) / 20)
        return (block * gain[:, None]).astype(np.float32)


class Limiter(Compressor):
    """A compressor set to hold a ceiling: a high ratio and a very fast attack."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        super().__init__({
            "threshold_db": params.get("threshold_db", -3.0),
            "ratio": 40.0,
            "attack_ms": 1.0,
            "release_ms": params.get("release_ms", 80.0),
            "makeup_db": 0.0,
        }, rate, channels)


class Gate(Effect):
    """Silences anything quieter than the threshold, for cleaning up a noisy recording."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.threshold = float(params.get("threshold_db", -45.0))
        self._attack = _one_pole(float(params.get("attack_ms", 5.0)) / 1000, rate)
        self._release = _one_pole(float(params.get("release_ms", 120.0)) / 1000, rate)
        self._open = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        level = np.max(np.abs(block), axis=1).astype(np.float64)
        level_db = 20 * np.log10(np.maximum(level, 1e-7))
        wanted = (level_db > self.threshold).astype(np.float64)
        gate = np.empty_like(wanted)
        value = self._open
        for index, target in enumerate(wanted):
            coeff = self._attack if target > value else self._release
            value = target + coeff * (value - target)
            gate[index] = value
        self._open = float(value)
        return (block * gate[:, None]).astype(np.float32)


class Delay(Effect):
    """An echo, with how much of it feeds back round again."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.samples = max(1, int(round(float(params.get("time_ms", 250.0)) / 1000 * rate)))
        self.feedback = min(max(float(params.get("feedback", 30.0)) / 100, 0.0), 0.95)
        self.mix = min(max(float(params.get("mix", 35.0)) / 100, 0.0), 1.0)
        self._line = np.zeros((self.samples, channels), dtype=np.float32)
        self._at = 0

    def flat(self) -> bool:
        return self.mix <= 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        if self._line.shape[1] != block.shape[1]:
            self._line = np.zeros((self.samples, block.shape[1]), dtype=np.float32)
            self._at = 0
        out = np.empty_like(block)
        done = 0
        # Work in pieces no longer than the delay itself, so feedback stays in step.
        while done < len(block):
            take = min(len(block) - done, self.samples - self._at)
            piece = block[done:done + take]
            echo = self._line[self._at:self._at + take].copy()
            self._line[self._at:self._at + take] = piece + echo * self.feedback
            out[done:done + take] = piece * (1 - self.mix) + echo * self.mix
            self._at = (self._at + take) % self.samples
            done += take
        return out


class SampleDelay(Effect):
    """A plain delay of a set number of samples, for lining up against another output."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.samples = max(0, int(params.get("samples", 0)))
        self._tail = np.zeros((self.samples, channels), dtype=np.float32)

    def flat(self) -> bool:
        return self.samples == 0

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.samples == 0:
            return block
        if self._tail.shape[1] != block.shape[1]:
            self._tail = np.zeros((self.samples, block.shape[1]), dtype=np.float32)
        joined = np.concatenate([self._tail, block])
        self._tail = joined[len(block):].copy()
        return joined[:len(block)].astype(np.float32)


class Distortion(Effect):
    """Soft clipping: drive pushes it harder, mix blends it against the clean sound."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.drive = max(float(params.get("drive", 5.0)), 0.1)
        self.mix = min(max(float(params.get("mix", 50.0)) / 100, 0.0), 1.0)

    def flat(self) -> bool:
        return self.mix <= 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        shaped = np.tanh(block * self.drive) / np.tanh(self.drive)
        return (block * (1 - self.mix) + shaped * self.mix).astype(np.float32)


class _Ring:
    """A delay line used by the reverb, processed in pieces so feedback stays in step."""

    def __init__(self, samples: int, channels: int) -> None:
        self.samples = max(1, samples)
        self._line = np.zeros((self.samples, channels), dtype=np.float32)
        self._at = 0
        self._damp = np.zeros((1, channels))

    def _fit(self, channels: int) -> None:
        if self._line.shape[1] != channels:
            self._line = np.zeros((self.samples, channels), dtype=np.float32)
            self._damp = np.zeros((1, channels))
            self._at = 0

    def comb(self, block, feedback, damping):
        """One comb filter: what comes back round is damped a little each time."""
        self._fit(block.shape[1])
        out = np.empty_like(block)
        done = 0
        while done < len(block):
            take = min(len(block) - done, self.samples - self._at)
            delayed = self._line[self._at:self._at + take].copy()
            # A one-pole lowpass on what comes back round, so the tail darkens as it fades.
            kept, self._damp = lfilter([1 - damping], [1.0, -damping], delayed,
                                       axis=0, zi=self._damp)
            self._line[self._at:self._at + take] = block[done:done + take] + kept * feedback
            out[done:done + take] = delayed
            self._at = (self._at + take) % self.samples
            done += take
        return out

    def allpass(self, block, gain):
        self._fit(block.shape[1])
        out = np.empty_like(block)
        done = 0
        while done < len(block):
            take = min(len(block) - done, self.samples - self._at)
            piece = block[done:done + take]
            delayed = self._line[self._at:self._at + take].copy()
            self._line[self._at:self._at + take] = piece + delayed * gain
            out[done:done + take] = delayed - piece
            self._at = (self._at + take) % self.samples
            done += take
        return out


class Reverb(Effect):
    """A room, built the classic way from comb filters feeding allpass filters."""

    COMBS = [1116, 1188, 1277, 1356]  # sample delays at 44100, scaled to the real rate
    ALLPASS = [556, 441]

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        size = min(max(float(params.get("room", 50.0)) / 100, 0.0), 1.0)
        self.damping = min(max(float(params.get("damping", 40.0)) / 100, 0.0), 0.95)
        self.mix = min(max(float(params.get("mix", 25.0)) / 100, 0.0), 1.0)
        self.feedback = 0.70 + 0.28 * size
        scale = rate / 44100
        self._combs = [_Ring(int(n * scale), channels) for n in self.COMBS]
        self._allpass = [_Ring(int(n * scale), channels) for n in self.ALLPASS]

    def flat(self) -> bool:
        return self.mix <= 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        wet = np.zeros_like(block, dtype=np.float32)
        for comb in self._combs:
            wet += comb.comb(block, self.feedback, self.damping)
        wet /= len(self._combs)
        for allpass in self._allpass:
            wet = allpass.allpass(wet, 0.5)
        return (block * (1 - self.mix) + wet * self.mix).astype(np.float32)


class MultibandCompressor(Effect):
    """Splits the sound into low, middle and high and compresses each on its own."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        nyquist = rate / 2 - 1
        low = min(max(float(params.get("low_cross", 200.0)), 20.0), nyquist - 11)
        high = min(max(float(params.get("high_cross", 3000.0)), low + 10.0), nyquist)
        self._bands = [
            _Sos(butter(2, low / nyquist, btype="lowpass", output="sos"), channels),
            _Sos(butter(2, [low / nyquist, high / nyquist], btype="bandpass", output="sos"), channels),
            _Sos(butter(2, high / nyquist, btype="highpass", output="sos"), channels),
        ]
        shared = {"ratio": params.get("ratio", 3.0), "attack_ms": 10.0,
                  "release_ms": 150.0, "makeup_db": 0.0}
        self._comps = [
            Compressor(dict(shared, threshold_db=params.get("low_threshold_db", -20.0)), rate, channels),
            Compressor(dict(shared, threshold_db=params.get("mid_threshold_db", -20.0)), rate, channels),
            Compressor(dict(shared, threshold_db=params.get("high_threshold_db", -20.0)), rate, channels),
        ]

    def process(self, block: np.ndarray) -> np.ndarray:
        out = np.zeros_like(block, dtype=np.float32)
        for split, comp in zip(self._bands, self._comps):
            out += comp.process(split.run(block))
        return out


class PitchShift(Effect):
    """Moves the pitch up or down without changing how long the cue runs."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        from cuerunner.pitch import PitchShifter, ratio_for

        self.semitones = float(params.get("semitones", 0.0))
        self._shifters = [PitchShifter(ratio_for(self.semitones)) for _ in range(channels)]
        self._channels = channels
        self._make = lambda: PitchShifter(ratio_for(self.semitones))

    def flat(self) -> bool:
        return abs(self.semitones) < 0.01

    def process(self, block: np.ndarray) -> np.ndarray:
        if block.shape[1] != self._channels:
            self._channels = block.shape[1]
            self._shifters = [self._make() for _ in range(self._channels)]
        out = np.empty_like(block)
        for channel, shifter in enumerate(self._shifters):
            out[:, channel] = shifter.process(block[:, channel])
        return out


class RogerBeep(Effect):
    """Puts a short beep on the end of each phrase, the way a two-way radio does."""

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.rate = rate
        self.threshold = float(params.get("threshold_db", -40.0))
        self.freq = float(params.get("freq", 1000.0))
        self.length = max(float(params.get("length_ms", 120.0)) / 1000, 0.01)
        self.level = db_to_gain(float(params.get("level_db", -12.0)))
        self._beep = self._make_beep()
        self._was_loud = False
        self._at = len(self._beep)  # not currently beeping
        self._quiet_for = 0

    def _make_beep(self) -> np.ndarray:
        count = max(int(self.length * self.rate), 1)
        t = np.arange(count) / self.rate
        fade = np.minimum(np.minimum(t, self.length - t) / 0.005, 1.0).clip(0, 1)
        return (np.sin(2 * np.pi * self.freq * t) * fade * self.level).astype(np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        level = np.max(np.abs(block), axis=1)
        loud = 20 * np.log10(np.maximum(level, 1e-7)) > self.threshold
        out = block.astype(np.float32).copy()
        for index in range(len(block)):
            if loud[index]:
                self._was_loud = True
                self._quiet_for = 0
            elif self._was_loud:
                # A short gap is a pause; a longer one means the phrase has ended.
                self._quiet_for += 1
                if self._quiet_for > self.rate // 50:
                    self._was_loud = False
                    self._at = 0
            if self._at < len(self._beep):
                out[index] += self._beep[self._at]
                self._at += 1
        return out


# --------------------------------------------------------------------------- the catalogue
@dataclass(frozen=True)
class EffectSpec:
    kind: str
    label: str
    params: tuple
    build: Callable[[dict, int, int], Effect]


def _filter_spec(kind: str, label: str, freq: float, shaped: bool) -> EffectSpec:
    params = [Param("freq", "Frequency", 20.0, 20000.0, freq, " Hz", 0)]
    if shaped:
        params.append(GAIN)
    params.append(Q)
    return EffectSpec(kind, label, tuple(params), lambda p, r, c, k=kind: Filter(k, p, r, c))


def _band_label(freq: int) -> str:
    return f"{freq} Hz" if freq < 1000 else f"{freq // 1000} kHz"


EFFECT_LIST = [
    _filter_spec("peaking", "Parametric EQ", 1000.0, True),
    _filter_spec("lowshelf", "Low shelf", 120.0, True),
    _filter_spec("highshelf", "High shelf", 6000.0, True),
    _filter_spec("highpass", "High pass", 80.0, False),
    _filter_spec("lowpass", "Low pass", 12000.0, False),
    _filter_spec("bandpass", "Band pass", 1000.0, False),
    _filter_spec("notch", "Notch", 1000.0, False),
    EffectSpec("graphiceq", "Graphic EQ (10 band)",
               tuple(Param(f"b{i}", _band_label(freq), -24.0, 24.0, 0.0, " dB", 1)
                     for i, freq in enumerate(GraphicEq.BANDS)),
               lambda p, r, c: GraphicEq(p, r, c)),
    EffectSpec("compressor", "Compressor", (
        Param("threshold_db", "Threshold", -60.0, 0.0, -20.0, " dB", 1),
        Param("ratio", "Ratio", 1.0, 20.0, 4.0, ":1", 1),
        Param("attack_ms", "Attack", 0.1, 200.0, 10.0, " ms", 1),
        Param("release_ms", "Release", 5.0, 2000.0, 150.0, " ms", 0),
        Param("makeup_db", "Make-up", 0.0, 24.0, 0.0, " dB", 1),
    ), lambda p, r, c: Compressor(p, r, c)),
    EffectSpec("limiter", "Peak limiter", (
        Param("threshold_db", "Ceiling", -24.0, 0.0, -3.0, " dB", 1),
        Param("release_ms", "Release", 5.0, 1000.0, 80.0, " ms", 0),
    ), lambda p, r, c: Limiter(p, r, c)),
    EffectSpec("gate", "Noise gate", (
        Param("threshold_db", "Threshold", -80.0, 0.0, -45.0, " dB", 1),
        Param("attack_ms", "Attack", 0.1, 100.0, 5.0, " ms", 1),
        Param("release_ms", "Release", 5.0, 2000.0, 120.0, " ms", 0),
    ), lambda p, r, c: Gate(p, r, c)),
    EffectSpec("multiband", "Multiband compressor", (
        Param("low_cross", "Low/mid split", 40.0, 1000.0, 200.0, " Hz", 0),
        Param("high_cross", "Mid/high split", 1000.0, 16000.0, 3000.0, " Hz", 0),
        Param("ratio", "Ratio", 1.0, 20.0, 3.0, ":1", 1),
        Param("low_threshold_db", "Low threshold", -60.0, 0.0, -20.0, " dB", 1),
        Param("mid_threshold_db", "Mid threshold", -60.0, 0.0, -20.0, " dB", 1),
        Param("high_threshold_db", "High threshold", -60.0, 0.0, -20.0, " dB", 1),
    ), lambda p, r, c: MultibandCompressor(p, r, c)),
    EffectSpec("delay", "Delay", (
        Param("time_ms", "Time", 1.0, 2000.0, 250.0, " ms", 0),
        Param("feedback", "Feedback", 0.0, 95.0, 30.0, " %", 0),
        Param("mix", "Mix", 0.0, 100.0, 35.0, " %", 0),
    ), lambda p, r, c: Delay(p, r, c)),
    EffectSpec("sampledelay", "Sample delay", (
        Param("samples", "Samples", 0.0, 48000.0, 0.0, "", 0),
    ), lambda p, r, c: SampleDelay(p, r, c)),
    EffectSpec("distortion", "Distortion", (
        Param("drive", "Drive", 0.1, 50.0, 5.0, "", 1),
        MIX,
    ), lambda p, r, c: Distortion(p, r, c)),
    EffectSpec("pitch", "Pitch shift", (
        Param("semitones", "Shift", -24.0, 24.0, 0.0, " semitones", 1),
    ), lambda p, r, c: PitchShift(p, r, c)),
    EffectSpec("rogerbeep", "Roger beep", (
        Param("threshold_db", "Threshold", -80.0, 0.0, -40.0, " dB", 1),
        Param("freq", "Beep pitch", 200.0, 4000.0, 1000.0, " Hz", 0),
        Param("length_ms", "Beep length", 20.0, 1000.0, 120.0, " ms", 0),
        Param("level_db", "Beep level", -40.0, 0.0, -12.0, " dB", 1),
    ), lambda p, r, c: RogerBeep(p, r, c)),
    EffectSpec("reverb", "Reverb", (
        Param("room", "Room size", 0.0, 100.0, 50.0, " %", 0),
        Param("damping", "Damping", 0.0, 95.0, 40.0, " %", 0),
        Param("mix", "Mix", 0.0, 100.0, 25.0, " %", 0),
    ), lambda p, r, c: Reverb(p, r, c)),
]
# A plugin is an effect like any other, but its settings live inside the plugin itself,
# so it has no parameters of its own here; its own window is where it is set up.
VST_KIND = "vst"


def _build_vst(params: dict, rate: int, channels: int) -> Effect:
    from cuerunner.vst import VstEffect

    return VstEffect(params, rate, channels)


EFFECT_LIST.append(EffectSpec(VST_KIND, "VST / Audio Unit plugin", (), _build_vst))
EFFECTS = {spec.kind: spec for spec in EFFECT_LIST}


def effect_label(kind: str) -> str:
    spec = EFFECTS.get(kind)
    return spec.label if spec else kind


def new_effect(kind: str) -> dict:
    """A fresh effect of this kind, with every parameter at its default."""
    spec = EFFECTS.get(kind)
    if spec is None:
        raise ValueError(f"unknown effect: {kind}")
    effect = {"kind": kind}
    for param in spec.params:
        effect[param.key] = param.default
    return effect


def describe(effect: dict) -> str:
    """A short line for the effect list, naming the setting that matters most."""
    kind = str(effect.get("kind", ""))
    spec = EFFECTS.get(kind)
    if spec is None:
        return kind
    if kind == VST_KIND:
        name = str(effect.get("name") or "").strip()
        return name or "Plugin (not set)"
    if not spec.params:
        return spec.label
    first = spec.params[0]
    value = float(effect.get(first.key, first.default))
    return f"{spec.label}  {value:.{first.decimals}f}{first.suffix}".rstrip()


# --------------------------------------------------------------------------- the chain
@dataclass
class Chain:
    """A cue's effects, run in order. Effects that would do nothing are left out."""

    effects: list = field(default_factory=list)
    rate: int = 48000
    channels: int = 2
    _built: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._built = []
        for effect in self.effects or []:
            spec = EFFECTS.get(str(effect.get("kind", "")))
            if spec is None:
                continue  # an effect from a newer version, or a typo in the show file
            try:
                built = spec.build(effect, self.rate, self.channels)
            except Exception:
                continue  # a bad setting should never stop a show
            if not built.flat():
                self._built.append(built)

    def flat(self) -> bool:
        return not self._built

    def process(self, block: np.ndarray) -> np.ndarray:
        if len(block) == 0:
            return block
        for effect in self._built:
            block = effect.process(block)
        return block


def db_to_gain(db: float) -> float:
    """-60 dB and below is silence; 0 dB is unity."""
    if db <= -60:
        return 0.0
    return float(10 ** (db / 20))


def gain_to_db(gain: float) -> float:
    if gain <= 0:
        return -60.0
    return float(20 * np.log10(gain))
