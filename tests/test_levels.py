"""Per output levels, mutes and solos from the inspector's Levels tab."""

import numpy as np
import pytest

from cuerunner.engine import output_gains


def test_a_fader_at_zero_db_leaves_the_output_alone():
    gains = output_gains([0.0, 0.0], [False, False], [False, False], 2)
    assert np.allclose(gains, [1.0, 1.0])


def test_a_fader_lowers_only_its_own_output():
    gains = output_gains([-6.0, 0.0], [False, False], [False, False], 2)
    assert gains[0] == pytest.approx(0.501, rel=0.01)
    assert gains[1] == pytest.approx(1.0)


def test_the_bottom_of_a_fader_is_silence():
    assert output_gains([-60.0], [False], [False], 1)[0] == 0.0


def test_a_muted_output_is_silent():
    gains = output_gains([0.0, 0.0], [True, False], [False, False], 2)
    assert np.allclose(gains, [0.0, 1.0])


def test_a_solo_silences_everything_else():
    gains = output_gains([0.0, 0.0, 0.0], [False] * 3, [False, True, False], 3)
    assert np.allclose(gains, [0.0, 1.0, 0.0])


def test_mute_wins_over_solo_on_the_same_output():
    gains = output_gains([0.0, 0.0], [True, False], [True, False], 2)
    assert gains[0] == 0.0


def test_outputs_with_no_fader_set_play_at_full():
    gains = output_gains([], [], [], 4)
    assert np.allclose(gains, [1.0, 1.0, 1.0, 1.0])


def test_audio_patches_survive_saving_and_reopening(tmp_path):
    from cuerunner.model import AudioPatch, Cue, Show

    show = Show()
    show.audio_patches = [AudioPatch("Main", "Speakers", 4), AudioPatch("Foldback", "", 2)]
    show.cues.append(Cue(number="1", name="Song", kind="audio", patch="Foldback",
                         output_levels=[-3.0, 0.0], output_mutes=[True, False],
                         output_solos=[False, False]))
    path = tmp_path / "show.json"
    show.save(path)
    loaded = Show.load(path)
    assert [p.name for p in loaded.audio_patches] == ["Main", "Foldback"]
    assert loaded.audio_patches[0].channels == 4
    cue = loaded.cues[0]
    assert cue.patch == "Foldback"
    assert cue.output_levels == [-3.0, 0.0]
    assert cue.output_mutes == [True, False]


def test_a_show_always_keeps_at_least_one_patch(tmp_path):
    from cuerunner.model import Show

    show = Show()
    show.audio_patches = []
    path = tmp_path / "show.json"
    show.save(path)
    assert len(Show.load(path).audio_patches) == 1


def test_a_faster_rate_gets_through_the_file_sooner():
    """Rate on the Time & Loops tab: 2x consumes twice as much audio per block."""
    from cuerunner.engine import Clip, Voice, default_matrix

    clip = Clip(np.ones((1000, 1), dtype=np.float32))
    normal = Voice(clip=clip, matrix=default_matrix(1, 2))
    fast = Voice(clip=clip, matrix=default_matrix(1, 2), rate=2.0)
    normal.read(100)
    fast.read(100)
    assert normal.position == 100
    assert fast.position == pytest.approx(200)


def test_a_rate_change_still_returns_the_block_size_asked_for():
    from cuerunner.engine import Clip, Voice, default_matrix

    clip = Clip(np.ones((4800, 2), dtype=np.float32))
    voice = Voice(clip=clip, matrix=default_matrix(2, 2), rate=0.5)
    block = voice.read(256)
    assert block.shape == (256, 2)
    assert np.allclose(block, 1.0)  # a steady signal stays steady between samples


def test_a_slower_rate_reads_between_samples():
    from cuerunner.engine import Clip, Voice, default_matrix

    ramp = np.arange(100, dtype=np.float32)[:, None]
    voice = Voice(clip=Clip(ramp), matrix=default_matrix(1, 2), rate=0.5)
    block = voice.read(4)
    assert block[1][0] == pytest.approx(0.5)  # halfway between sample 0 and 1
