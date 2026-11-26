"""Pitch shifting: change the pitch of a cue without changing how long it runs.

This is a phase vocoder. Audio is cut into overlapping frames, each frame is moved into
the frequency domain, the phase of every bin is advanced by what the bin's true frequency
says it should be, and the frames are laid back down further apart than they were taken.
That stretches the sound in time without altering its pitch; reading the stretched audio
back faster then restores the original length and carries the pitch up with it.

It runs as a stream, so it keeps its buffers between blocks and gives back exactly as many
samples as it was handed, whatever block size the sound card asks for.
"""

from __future__ import annotations

import numpy as np

FRAME = 2048  # samples per analysis frame
HOP = 512  # how far apart frames are taken: a quarter of a frame, so four overlap
SEMITONE = 2 ** (1 / 12)


def ratio_for(semitones: float) -> float:
    """How much the pitch is multiplied by. +12 semitones doubles it."""
    return float(SEMITONE ** semitones)


def _wrap(phase: np.ndarray) -> np.ndarray:
    """Bring a phase difference back into -pi to +pi, where it means something."""
    return (phase + np.pi) % (2 * np.pi) - np.pi


class PitchShifter:
    """One channel's worth of shifting. Keeps its buffers between blocks.

    Frames are taken from the input closer together or further apart depending on the
    shift, and always laid back down HOP apart. Keeping the synthesis hop fixed means the
    frames always overlap four deep, which is what lets them add back up cleanly; the
    level then stays put whatever the shift. Reading the result back at the pitch ratio
    restores the original length.

    There is about one frame of delay through this, which is the price of the method.
    """

    def __init__(self, ratio: float) -> None:
        self.ratio = max(float(ratio), 0.05)
        self._window = np.hanning(FRAME + 1)[:FRAME].astype(np.float64)
        # Start with a frame of silence so the first block can be analysed right away.
        self._in = np.zeros(FRAME, dtype=np.float64)
        self._next = 0.0  # where the next frame starts, in fractional samples
        self._previous = 0.0  # where the last one started, to see how far we moved
        self._stretched = np.zeros(FRAME * 4, dtype=np.float64)
        self._weight = np.zeros(FRAME * 4, dtype=np.float64)
        self._laid = 0  # where the next frame is laid down, always a whole HOP apart
        self._read = 0.0  # where we are reading it back
        self._last_phase = np.zeros(FRAME // 2 + 1)
        self._sum_phase = np.zeros(FRAME // 2 + 1)
        self._bins = np.arange(FRAME // 2 + 1)

    # --- buffers -------------------------------------------------------
    def _make_room(self, needed: int) -> None:
        if needed <= len(self._stretched):
            return
        extra = needed - len(self._stretched) + FRAME * 2
        self._stretched = np.concatenate([self._stretched, np.zeros(extra)])
        self._weight = np.concatenate([self._weight, np.zeros(extra)])

    def _tidy(self) -> None:
        """Drop what has been read and what has been analysed, so nothing grows for ever."""
        spent = int(self._read) - FRAME
        if spent > FRAME:
            self._stretched = np.concatenate([self._stretched[spent:], np.zeros(spent)])
            self._weight = np.concatenate([self._weight[spent:], np.zeros(spent)])
            self._laid -= spent
            self._read -= spent
        used = int(self._next) - FRAME
        if used > FRAME:
            self._in = self._in[used:]
            self._next -= used
            self._previous -= used

    # --- the vocoder ---------------------------------------------------
    def _can_analyse(self) -> bool:
        return int(self._next) + FRAME <= len(self._in)

    def _analyse(self) -> None:
        at = int(self._next)
        step = max(at - self._previous, 1.0)  # how far we actually moved since last time
        self._previous = float(at)
        frame = self._in[at:at + FRAME] * self._window
        spectrum = np.fft.rfft(frame)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        # Work out each bin's true frequency from how far its phase moved, then advance
        # the output phase by that much scaled to the synthesis hop.
        omega = 2 * np.pi * step * self._bins / FRAME
        drift = _wrap(phase - self._last_phase - omega)
        self._last_phase = phase
        true_step = (omega + drift) / step  # radians per sample

        # Advance each peak in the spectrum by its own true frequency, then hold the bins
        # around it at the same phase relationship they arrived with. Without this the
        # partials drift apart and the frames cancel each other as they overlap, which
        # sounds like warbling and costs a lot of level.
        peaks = np.flatnonzero((magnitude[1:-1] >= magnitude[:-2])
                               & (magnitude[1:-1] > magnitude[2:])) + 1
        if len(peaks) == 0:
            peaks = np.array([int(np.argmax(magnitude))])
        advanced = _wrap(self._sum_phase[peaks] + true_step[peaks] * HOP)
        edges = (peaks[:-1] + peaks[1:] + 1) // 2
        owner = peaks[np.searchsorted(edges, self._bins)]
        self._sum_phase = _wrap(advanced[np.searchsorted(peaks, owner)]
                                + phase - phase[owner])

        rebuilt = np.fft.irfft(magnitude * np.exp(1j * self._sum_phase), FRAME)
        self._make_room(self._laid + FRAME)
        self._stretched[self._laid:self._laid + FRAME] += rebuilt * self._window
        self._weight[self._laid:self._laid + FRAME] += self._window ** 2
        self._laid += HOP
        self._next += HOP / self.ratio  # closer together to stretch, further to squash

    def _sample(self, index: int) -> float:
        if not 0 <= index < len(self._stretched):
            return 0.0
        weight = self._weight[index]
        return self._stretched[index] / weight if weight > 1e-6 else 0.0

    # --- the stream ----------------------------------------------------
    def process(self, samples: np.ndarray) -> np.ndarray:
        """Shift a run of samples, returning exactly as many as were given."""
        wanted = len(samples)
        if wanted == 0:
            return samples
        self._in = np.concatenate([self._in, np.asarray(samples, dtype=np.float64)])
        out = np.zeros(wanted, dtype=np.float64)
        done = 0
        while done < wanted:
            ready = self._laid - FRAME  # everything before this has all its frames
            if self._read + 1 < ready:
                base = int(self._read)
                frac = self._read - base
                out[done] = self._sample(base) * (1 - frac) + self._sample(base + 1) * frac
                self._read += self.ratio
                done += 1
            elif self._can_analyse():
                self._analyse()
            else:
                break  # still filling up: the rest of this block stays silent
        self._tidy()
        return out.astype(np.float32)
