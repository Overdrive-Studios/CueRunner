"""Big multichannel rigs: a Dante Virtual Soundcard or a large interface.

Dante is not a protocol CueRunner speaks. Dante Virtual Soundcard, a Dante interface and
macOS AVB all present themselves as ordinary audio devices with a lot of channels, which
is exactly how QLab uses them too. What matters is that nothing caps out before 128.
"""

import os
from unittest import mock

import numpy as np
import pytest

from cuerunner.engine import Clip, Mixer, Voice, default_matrix, route_matrix

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_the_ceiling_matches_qlab():
    from cuerunner.main import MAX_CHANNELS

    assert MAX_CHANNELS >= 128


@pytest.mark.parametrize("outs", [2, 8, 64, 128])
def test_the_mixer_fills_every_output(outs):
    mixer = Mixer(rate=48000, out_channels=outs)
    mixer.add(Voice(clip=Clip(np.full((4800, 2), 0.3, dtype=np.float32)),
                    matrix=default_matrix(2, outs)))
    block = mixer.render(1024)
    assert block.shape == (1024, outs)
    assert float(np.max(np.abs(block))) > 0.1


def test_a_cue_can_be_sent_to_a_high_numbered_output():
    """Output 97 and 98 on a 128 channel rig."""
    mixer = Mixer(rate=48000, out_channels=128)
    mixer.add(Voice(clip=Clip(np.full((4800, 2), 0.5, dtype=np.float32)),
                    matrix=route_matrix([[97], [98]], 2, 128)))
    block = mixer.render(2400)
    sounding = [i + 1 for i in range(128) if np.max(np.abs(block[:, i])) > 0.01]
    assert sounding == [97, 98]


def test_a_full_rig_still_renders_well_inside_real_time():
    import time

    mixer = Mixer(rate=48000, out_channels=128)
    for _ in range(8):  # eight cues at once, as a busy show would have
        mixer.add(Voice(clip=Clip(np.full((48000, 2), 0.2, dtype=np.float32)),
                        matrix=default_matrix(2, 128)))
    mixer.render(1024)
    started = time.perf_counter()
    for _ in range(20):
        mixer.render(1024)
    spent = (time.perf_counter() - started) / 20 * 1000
    assert spent < 1024 / 48000 * 1000 * 0.5, f"{spent:.1f} ms for a 21.3 ms block"


def test_the_fader_strip_scrolls_rather_than_squashing(tmp_path):
    """128 faders are wider than any screen, so the strip has to scroll sideways."""
    widgets = pytest.importorskip("PySide6.QtWidgets")
    widgets.QApplication.instance() or widgets.QApplication([])
    from PySide6.QtCore import Qt

    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
    try:
        from cuerunner.model import AudioPatch, Cue

        window.show_model.audio_patches = [AudioPatch(name="Dante", channels=128)]
        window.show_model.cues.append(Cue(number="1", name="a", kind="audio", patch="Dante"))
        window._refresh_table()
        window.table.selectRow(0)
        assert window.insp_levels.output_count() == 128
        assert (window.insp_levels_scroll.horizontalScrollBarPolicy()
                != Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    finally:
        window._mark_saved()
        window.close()


def test_a_patch_can_be_set_to_a_full_dante_card(tmp_path):
    from cuerunner.model import AudioPatch, Show

    show = Show()
    show.audio_patches = [AudioPatch(name="Dante", device="Dante Virtual Soundcard", channels=128)]
    path = tmp_path / "show.json"
    show.save(path)
    patch = Show.load(path).audio_patches[0]
    assert patch.channels == 128
    assert patch.device == "Dante Virtual Soundcard"
