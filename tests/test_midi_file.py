"""MIDI file cues: the player sends every channel message in order, and stops early when asked."""

import time
from unittest import mock

import mido

from cuerunner import midi


def _write_file(path):
    track = mido.MidiTrack([
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_off", note=60, velocity=0, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ])
    mid = mido.MidiFile(ticks_per_beat=480, tracks=[track])
    mid.save(path)


def test_player_sends_the_channel_messages_in_order(tmp_path):
    path = tmp_path / "tune.mid"
    _write_file(path)
    port = mock.MagicMock()
    port.__enter__.return_value = port
    with mock.patch("mido.open_output", return_value=port):
        player = midi.MidiFilePlayer(str(path), "Port")
        player.start()
        for _ in range(200):
            if not player.running:
                break
            time.sleep(0.01)
    sent = [c.args[0].type for c in port.send.call_args_list]
    assert sent == ["note_on", "note_off"]


def test_player_needs_a_port(tmp_path):
    import pytest

    path = tmp_path / "tune.mid"
    _write_file(path)
    with pytest.raises(ValueError):
        midi.MidiFilePlayer(str(path), "")


def test_midifile_cue_starts_a_player_and_stop_ends_it(tmp_path):
    import os
    from unittest import mock as m

    import pytest

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow
    from cuerunner.model import Cue

    path = tmp_path / "tune.mid"
    _write_file(path)
    with m.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
        try:
            cue = Cue(number="1", name="tune", kind="midifile", file=str(path), target="Port")
            window.show_model.cues.append(cue)
            with m.patch("cuerunner.midi.MidiFilePlayer") as player_class:
                window._start_midifile(cue)
            player_class.assert_called_once_with(str(path), "Port")
            player = player_class.return_value
            player.start.assert_called_once()
            window._stop_cue(cue)
            player.stop.assert_called_once()
        finally:
            window.close()
