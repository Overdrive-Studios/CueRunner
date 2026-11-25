"""Devamp: clearing a voice's loop lets it play on to the end of its clip."""

import numpy as np

from cuerunner.engine import Clip, Mixer, Voice


def test_devamp_plays_the_rest_of_the_clip_once():
    mixer = Mixer(rate=48000, out_channels=2)
    clip = Clip(np.arange(10, dtype=np.float32).reshape(-1, 1))
    voice = Voice(clip=clip, matrix=np.ones((1, 2), dtype=np.float32), loop=True)
    voice.position = 6
    mixer.devamp(voice)
    assert voice.loop is False
    tail = voice.read(100)
    assert tail is not None and len(tail) == 4  # positions 6..9, then the voice ends
    assert voice.natural_end
    assert voice.read(10) is None


def test_devamp_cue_clears_only_its_target_cues_loop():
    import os
    from unittest import mock

    import pytest

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow
    from cuerunner.model import Cue

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
        try:
            window.show_model.cues.extend([
                Cue(number="1", name="bed", kind="audio", loop=True),
                Cue(number="2", name="other", kind="audio", loop=True),
                Cue(number="3", name="devamp", kind="devamp", target="1"),
            ])
            window._refresh_table()
            clip = Clip(np.zeros((100, 1), dtype=np.float32))
            target_voice = Voice(clip=clip, matrix=np.ones((1, 2), dtype=np.float32), loop=True, tag=window.show_model.cues[0])
            other_voice = Voice(clip=clip, matrix=np.ones((1, 2), dtype=np.float32), loop=True, tag=window.show_model.cues[1])
            window.mixer = mixer = Mixer(rate=48000, out_channels=2)
            window._cue_voices = [target_voice, other_voice]
            window._devamp_cue(window.show_model.cues[2])
            assert target_voice.loop is False
            assert other_voice.loop is True
            assert mixer is window.mixer
        finally:
            window.close()
