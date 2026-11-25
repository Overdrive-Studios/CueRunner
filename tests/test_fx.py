"""CueRunner's own audio effects, measured on real signals."""

import numpy as np
import pytest

from cuerunner import fx
from cuerunner.fx import Chain, db_to_gain, gain_to_db, new_effect

RATE = 48000


def tone(freq: float, seconds: float = 0.5, channels: int = 1, amp: float = 1.0) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    wave = (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)
    return np.repeat(wave[:, None], channels, axis=1)


def rms(block: np.ndarray) -> float:
    """Measured over the second half, so the effect has settled."""
    tail = block[len(block) // 2:]
    return float(np.sqrt(np.mean(tail.astype(np.float64) ** 2)))


def run(effect: dict, block: np.ndarray, channels: int = 1) -> np.ndarray:
    return Chain(effects=[effect], rate=RATE, channels=channels).process(block)


def db_change(effect: dict, signal: np.ndarray) -> float:
    return gain_to_db(rms(run(effect, signal)) / rms(signal))


# ----------------------------------------------------------------- the catalogue
def test_every_effect_can_be_made_and_run():
    """Whatever is offered in the menu must build and pass audio without raising."""
    block = tone(1000, seconds=0.1, channels=2)
    for kind in fx.EFFECTS:
        out = Chain(effects=[new_effect(kind)], rate=RATE, channels=2).process(block)
        assert out.shape == block.shape, kind
        assert np.all(np.isfinite(out)), kind


def test_every_effect_has_a_label_and_sensible_parameters():
    for kind, spec in fx.EFFECTS.items():
        assert spec.label, kind
        # A plugin is set up in its own window, so it has no parameters here.
        assert spec.params or kind == fx.VST_KIND, kind
        for param in spec.params:
            assert param.low <= param.default <= param.high, f"{kind}.{param.key}"


def test_the_menu_covers_the_usual_kinds_of_processing():
    for kind in ("peaking", "graphiceq", "compressor", "limiter", "gate",
                 "delay", "distortion", "reverb", "multiband"):
        assert kind in fx.EFFECTS


def test_an_unknown_effect_is_skipped_rather_than_breaking_the_show():
    assert Chain(effects=[{"kind": "AUSomethingApple"}], rate=RATE, channels=1).flat()
    assert Chain(effects=[{}, None or {}], rate=RATE, channels=1).flat()


def test_describe_names_the_effect_and_its_main_setting():
    assert "Parametric EQ" in fx.describe(new_effect("peaking"))
    assert "250" in fx.describe(dict(new_effect("delay"), time_ms=250))


# ----------------------------------------------------------------- filters
def test_a_parametric_eq_boosts_only_its_own_band():
    boost = dict(new_effect("peaking"), freq=1000.0, gain_db=12.0, q=1.0)
    assert db_change(boost, tone(1000)) == pytest.approx(12, abs=1.5)
    assert db_change(boost, tone(60)) == pytest.approx(0, abs=1.0)


def test_shelves_lift_one_end_and_leave_the_other():
    low = dict(new_effect("lowshelf"), freq=120.0, gain_db=12.0)
    assert db_change(low, tone(50)) == pytest.approx(12, abs=1.5)
    assert db_change(low, tone(8000)) == pytest.approx(0, abs=1.0)
    high = dict(new_effect("highshelf"), freq=6000.0, gain_db=9.0)
    assert db_change(high, tone(12000)) == pytest.approx(9, abs=1.5)
    assert db_change(high, tone(100)) == pytest.approx(0, abs=1.0)


def test_pass_filters_remove_the_end_they_are_named_for():
    assert db_change(dict(new_effect("highpass"), freq=500.0), tone(60)) < -20
    assert db_change(dict(new_effect("highpass"), freq=500.0), tone(5000)) == pytest.approx(0, abs=1.0)
    assert db_change(dict(new_effect("lowpass"), freq=1000.0), tone(10000)) < -20
    assert db_change(dict(new_effect("lowpass"), freq=1000.0), tone(100)) == pytest.approx(0, abs=1.0)


def test_a_band_pass_keeps_its_band_and_drops_the_rest():
    band = dict(new_effect("bandpass"), freq=1000.0, q=1.0)
    assert db_change(band, tone(1000)) == pytest.approx(0, abs=1.5)
    assert db_change(band, tone(60)) < -20
    assert db_change(band, tone(16000)) < -20


def test_a_notch_removes_its_own_frequency_only():
    notch = dict(new_effect("notch"), freq=1000.0, q=3.0)
    assert db_change(notch, tone(1000)) < -20
    assert db_change(notch, tone(200)) == pytest.approx(0, abs=1.5)


def test_a_graphic_eq_moves_the_band_you_set():
    flat = new_effect("graphiceq")
    assert Chain(effects=[flat], rate=RATE, channels=1).flat()  # nothing set costs nothing
    boosted = dict(flat, b5=12.0)  # the 1 kHz band
    assert db_change(boosted, tone(1000)) > 6
    assert db_change(boosted, tone(60)) == pytest.approx(0, abs=1.5)


# ----------------------------------------------------------------- dynamics
def test_a_compressor_turns_down_what_is_over_the_threshold():
    comp = dict(new_effect("compressor"), threshold_db=-20.0, ratio=4.0,
                attack_ms=1.0, release_ms=50.0, makeup_db=0.0)
    loud = tone(500, seconds=1.0, amp=1.0)      # about -3 dB RMS
    quiet = tone(500, seconds=1.0, amp=0.01)    # well under the threshold
    assert db_change(comp, loud) < -8           # pulled down hard
    assert db_change(comp, quiet) == pytest.approx(0, abs=0.5)  # left alone


def test_a_higher_ratio_squeezes_harder():
    loud = tone(500, seconds=1.0)
    gentle = dict(new_effect("compressor"), threshold_db=-20.0, ratio=2.0, attack_ms=1.0)
    firm = dict(new_effect("compressor"), threshold_db=-20.0, ratio=10.0, attack_ms=1.0)
    assert db_change(firm, loud) < db_change(gentle, loud)


def test_make_up_gain_brings_the_level_back():
    loud = tone(500, seconds=1.0)
    plain = dict(new_effect("compressor"), threshold_db=-20.0, ratio=4.0, attack_ms=1.0)
    lifted = dict(plain, makeup_db=12.0)
    assert db_change(lifted, loud) - db_change(plain, loud) == pytest.approx(12, abs=0.5)


def test_a_limiter_holds_a_ceiling():
    limited = run(dict(new_effect("limiter"), threshold_db=-6.0, release_ms=20.0),
                  tone(500, seconds=1.0))
    settled = limited[len(limited) // 2:]
    assert float(np.max(np.abs(settled))) < db_to_gain(-3)  # held near the ceiling


def test_a_gate_silences_quiet_passages_and_passes_loud_ones():
    gate = dict(new_effect("gate"), threshold_db=-40.0, attack_ms=1.0, release_ms=10.0)
    assert db_change(gate, tone(500, seconds=1.0, amp=0.001)) < -20
    assert db_change(gate, tone(500, seconds=1.0, amp=0.5)) == pytest.approx(0, abs=1.0)


def test_a_multiband_compressor_can_squeeze_one_band_alone():
    loose = dict(new_effect("multiband"), low_threshold_db=0.0, mid_threshold_db=0.0,
                 high_threshold_db=0.0, ratio=4.0)
    squeezed = dict(loose, low_threshold_db=-40.0)
    bass = tone(80, seconds=1.0)
    assert db_change(squeezed, bass) < db_change(loose, bass) - 5


# ----------------------------------------------------------------- time and shape
def test_a_delay_repeats_the_sound_at_the_time_you_set():
    impulse = np.zeros((RATE, 1), dtype=np.float32)
    impulse[0] = 1.0
    out = run(dict(new_effect("delay"), time_ms=100.0, feedback=0.0, mix=100.0), impulse)
    echo_at = int(0.1 * RATE)
    assert out[echo_at][0] == pytest.approx(1.0, abs=0.01)
    assert abs(out[echo_at // 2][0]) < 0.01  # nothing in between


def test_delay_feedback_gives_a_second_quieter_echo():
    impulse = np.zeros((RATE, 1), dtype=np.float32)
    impulse[0] = 1.0
    out = run(dict(new_effect("delay"), time_ms=100.0, feedback=50.0, mix=100.0), impulse)
    first, second = out[int(0.1 * RATE)][0], out[int(0.2 * RATE)][0]
    assert second == pytest.approx(first * 0.5, abs=0.02)


def test_a_sample_delay_shifts_the_audio_by_exactly_that_many_samples():
    impulse = np.zeros((500, 1), dtype=np.float32)
    impulse[0] = 1.0
    out = run(dict(new_effect("sampledelay"), samples=64), impulse)
    assert out[64][0] == pytest.approx(1.0)
    assert out[0][0] == 0.0
    assert Chain(effects=[new_effect("sampledelay")], rate=RATE, channels=1).flat()


def test_distortion_adds_harmonics_that_were_not_there():
    clean = tone(500, seconds=0.5)
    dirty = run(dict(new_effect("distortion"), drive=20.0, mix=100.0), clean)
    spectrum = np.abs(np.fft.rfft(dirty[:, 0]))
    freqs = np.fft.rfftfreq(len(dirty), 1 / RATE)
    third = spectrum[np.argmin(np.abs(freqs - 1500))]
    assert third > spectrum[np.argmin(np.abs(freqs - 500))] * 0.02  # a third harmonic appeared


def test_reverb_keeps_sounding_after_the_audio_stops():
    block = np.zeros((RATE // 2, 1), dtype=np.float32)
    block[:1000] = 1.0  # a short burst, then silence
    out = run(dict(new_effect("reverb"), room=80.0, damping=20.0, mix=100.0), block)
    assert float(np.max(np.abs(out[RATE // 4:]))) > 1e-4  # a tail is still ringing


def test_an_effect_mixed_all_the_way_dry_is_skipped():
    for kind in ("delay", "distortion", "reverb"):
        assert Chain(effects=[dict(new_effect(kind), mix=0.0)], rate=RATE, channels=1).flat()


# ----------------------------------------------------------------- chain behaviour
def test_effects_run_in_the_order_they_are_listed():
    """A high pass after a low pass leaves nothing; the other way round is the same band."""
    both = [dict(new_effect("lowpass"), freq=300.0), dict(new_effect("highpass"), freq=3000.0)]
    out = Chain(effects=both, rate=RATE, channels=1).process(tone(1000))
    assert rms(out) / rms(tone(1000)) < 0.05


def test_the_result_does_not_depend_on_the_block_size():
    """The sound card picks the block size, so every effect must carry its state over."""
    for kind in ("peaking", "compressor", "delay", "reverb", "gate", "graphiceq"):
        effect = new_effect(kind)
        if kind == "peaking":
            effect = dict(effect, gain_db=6.0)
        if kind == "graphiceq":
            effect = dict(effect, b5=6.0)
        block = tone(800, seconds=0.2)
        whole = Chain(effects=[effect], rate=RATE, channels=1).process(block)
        chain = Chain(effects=[effect], rate=RATE, channels=1)
        pieces = np.concatenate([chain.process(block[i:i + 64]) for i in range(0, len(block), 64)])
        assert np.allclose(whole, pieces, atol=1e-5), kind


def test_stereo_is_handled_without_the_channels_bleeding_together():
    stereo = np.concatenate([tone(60), tone(8000)], axis=1)
    out = run(dict(new_effect("lowshelf"), freq=120.0, gain_db=12.0), stereo, channels=2)
    assert gain_to_db(rms(out[:, :1]) / rms(tone(60))) == pytest.approx(12, abs=1.5)
    assert gain_to_db(rms(out[:, 1:]) / rms(tone(8000))) == pytest.approx(0, abs=1.0)


def test_an_empty_chain_passes_audio_straight_through():
    chain = Chain(effects=[], rate=RATE, channels=1)
    assert chain.flat()
    block = tone(1000)
    assert chain.process(block) is block


def test_decibels_convert_both_ways():
    assert db_to_gain(0) == pytest.approx(1.0)
    assert db_to_gain(-6) == pytest.approx(0.501, rel=0.01)
    assert db_to_gain(-60) == 0.0
    assert gain_to_db(1.0) == pytest.approx(0.0)
    assert gain_to_db(0.0) == -60.0


def test_every_effect_keeps_up_with_real_time():
    """An effect runs inside the audio callback, so it must be far quicker than the audio.

    The bar is deliberately loose, since build machines vary; it is here to catch an
    effect that is accidentally hundreds of times slower than it should be.
    """
    import time

    frames = 1024
    budget_ms = frames / RATE * 1000
    block = (np.random.randn(frames, 2) * 0.2).astype(np.float32)
    for kind in fx.EFFECTS:
        chain = Chain(effects=[dict(new_effect(kind), gain_db=6.0, b5=6.0)], rate=RATE, channels=2)
        for _ in range(3):
            chain.process(block)  # let any warm-up happen first
        started = time.perf_counter()
        for _ in range(20):
            chain.process(block)
        spent_ms = (time.perf_counter() - started) / 20 * 1000
        assert spent_ms < budget_ms * 0.5, f"{kind} took {spent_ms:.1f} ms of a {budget_ms:.1f} ms block"
