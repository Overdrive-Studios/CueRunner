from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from cuerunner.engine import Clip, Mixer, Voice, default_matrix, load_clip, probe_duration_ms, resample


def mono(values) -> Clip:
    return Clip(np.array(values, dtype=np.float32).reshape(-1, 1))


def test_voice_plays_once_and_is_removed() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 10), matrix=default_matrix(1, 2))
    mixer.add(voice)

    out = mixer.render(10)

    assert np.allclose(out[:, 0], 0.5)
    assert np.allclose(out[:, 1], 0.5)
    assert mixer.voices() == []
    assert voice.natural_end


def test_loop_repeats_the_clip() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 10), matrix=default_matrix(1, 2), loop=True)
    mixer.add(voice)

    out = mixer.render(25)

    assert np.allclose(out[:, 0], 0.5)
    assert mixer.voices() == [voice]


def test_fade_in_ramps_up_to_full_level() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 100), matrix=default_matrix(1, 2), env=0.0)
    voice.fade(1.0, 0.01, 1000)  # 10 frames
    mixer.add(voice)

    out = mixer.render(10)

    assert out[0, 0] < out[-1, 0]
    assert out[-1, 0] == pytest.approx(0.5)


def test_fade_out_stops_the_voice() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 1000), matrix=default_matrix(1, 2))
    mixer.add(voice)
    mixer.fade_out(voice, 0.01)

    out = mixer.render(10)

    assert voice.done
    assert mixer.voices() == []
    assert out[-1, 0] == pytest.approx(0.0, abs=1e-6)


def test_routing_puts_channels_on_chosen_outputs() -> None:
    mixer = Mixer(rate=1000, out_channels=4)
    clip = Clip(np.array([[0.5, 0.25]] * 4, dtype=np.float32))
    matrix = np.zeros((2, 4), dtype=np.float32)
    matrix[0, 2] = 1.0
    matrix[1, 3] = 1.0
    mixer.add(Voice(clip=clip, matrix=matrix))

    out = mixer.render(4)

    assert np.allclose(out[:, 2], 0.5)
    assert np.allclose(out[:, 3], 0.25)
    assert np.allclose(out[:, 0], 0.0)


def test_voices_sum_together() -> None:
    mixer = Mixer(rate=1000, out_channels=1)
    mixer.add(Voice(clip=mono([0.25] * 10), matrix=default_matrix(1, 1)))
    mixer.add(Voice(clip=mono([0.25] * 10), matrix=default_matrix(1, 1)))

    out = mixer.render(10)

    assert np.allclose(out[:, 0], 0.5)


def test_pause_silences_and_holds_position() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 100), matrix=default_matrix(1, 2))
    mixer.add(voice)
    mixer.paused = True

    out = mixer.render(10)

    assert np.allclose(out, 0.0)
    assert voice.position == 0


def test_ducking_lowers_bed_voices_only() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    bed = Voice(clip=mono([1.0] * 100_000), matrix=default_matrix(1, 2), loop=True, bed=True)
    speech = Voice(clip=mono([1.0] * 100_000), matrix=default_matrix(1, 2), loop=True, bed=False)
    mixer.add(bed)
    mixer.add(speech)
    mixer.set_ducked(True, level=0.5)

    for _ in range(80):
        mixer.render(10)

    bed_only = Mixer(rate=1000, out_channels=2)
    bed_only.add(Voice(clip=mono([1.0] * 100_000), matrix=default_matrix(1, 2), loop=True, bed=True))
    bed_only.set_ducked(True, level=0.5)
    for _ in range(80):
        bed_only.render(10)
    ducked = bed_only.render(10)[0, 0]

    assert ducked == pytest.approx(0.5, abs=0.01)


def test_resample_changes_length() -> None:
    data = np.ones((4, 1), dtype=np.float32)
    assert len(resample(data, 1000, 2000)) == 8
    assert len(resample(data, 2000, 1000)) == 2


def test_load_and_probe_wav(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    sf.write(str(path), np.zeros((10, 2), dtype=np.float32), 1000)

    assert probe_duration_ms(path) == 10
    clip = load_clip(path, 2000)
    assert clip.frames == 20
    assert clip.channels == 2


def test_default_matrix_mono_goes_to_both_outputs() -> None:
    assert default_matrix(1, 2).tolist() == [[1.0, 1.0]]
    assert default_matrix(2, 2).tolist() == [[1.0, 0.0], [0.0, 1.0]]


def test_paused_voice_makes_no_sound_until_resumed() -> None:
    mixer = Mixer(rate=1000, out_channels=2)
    voice = Voice(clip=mono([0.5] * 10), matrix=default_matrix(1, 2))
    mixer.add(voice)
    voice.paused = True
    assert not mixer.render(4).any()
    assert voice.position == 0
    voice.paused = False
    assert mixer.render(4).any()
