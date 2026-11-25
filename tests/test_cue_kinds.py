"""Runs the new cue types through the real main window: memo, wait, group, control, fade and text."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    box = QtWidgets.QMessageBox
    with mock.patch.object(box, "warning") as warning, mock.patch.object(
        MainWindow, "_open_default_output", lambda self: None
    ):
        w = MainWindow()
        w.warning_mock = warning
        yield w
        w.close()


def _add(window, kind, number, **fields):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number=number, name=kind, kind=kind, **fields))
    window._refresh_table()
    return len(window.show_model.cues) - 1


def test_memo_and_group_fire_children_and_advance(window):
    group = _add(window, "group", "1")
    child = _add(window, "memo", "2", text="hello", parent="1")
    window.go_row(group)
    assert window.table.currentRow() == child  # playhead moved past the group
    assert window.show_model.cues[child].parent == "1"


def test_playlist_group_moves_to_the_next_child(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="g", kind="group", group_mode="playlist"),
        Cue(number="2", name="a", kind="memo", parent="1"),
        Cue(number="3", name="b", kind="memo", parent="1"),
    ])
    assert window._playlist_next_row(1) == 2
    assert window._playlist_next_row(2) is None


def test_goto_moves_the_playhead_without_firing(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="a", kind="memo"),
        Cue(number="2", name="b", kind="memo"),
        Cue(number="3", name="go", kind="control", target="1", action="goto"),
    ])
    window._refresh_table()
    window.go_row(2)
    assert window.table.currentRow() == 0


def test_light_ramp_sends_frames_then_the_final_levels(window):
    from unittest import mock
    from cuerunner import artnet

    row = _add(window, "light", "1", target="127.0.0.1", text="1=200", universe=0, seconds=0.12)
    with mock.patch.object(artnet, "send") as send:
        window.go_row(row)
        frames = [timer for timer in window._timers]
        assert len(frames) == 3
        for timer in frames:
            timer.timeout.emit()
    assert send.call_count == 3
    assert send.call_args.args[2] == {1: 200}


def test_fire_all_group_has_no_playlist_next(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="g", kind="group", group_mode="all"),
        Cue(number="2", name="a", kind="memo", parent="1"),
        Cue(number="3", name="b", kind="memo", parent="1"),
    ])
    assert window._playlist_next_row(1) is None


def test_wait_schedules_the_next_cue(window):
    wait = _add(window, "wait", "1", duration_ms=50)
    _add(window, "memo", "2")
    window.go_row(wait)
    assert len(window._timers) == 1
    assert window._timers[0].isActive()
    window.panic()
    assert window._timers == []


def test_pre_wait_delays_the_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="m", kind="memo", pre_wait_ms=50))
    window._refresh_table()
    window.go_row(0)
    assert len(window._timers) == 1
    assert window._timers[0].isActive()


def test_control_with_missing_target_warns(window):
    control = _add(window, "control", "1", target="99", action="start")
    window.go_row(control)
    assert window.warning_mock.called


def test_control_start_runs_target(window):
    target = _add(window, "memo", "5")
    control = _add(window, "control", "6", target="5", action="start")
    window.go_row(control)
    assert window.table.currentRow() == control  # last row: the playhead stays on it
    assert window.show_model.cues[target].kind == "memo"


def test_fade_and_text_cues_need_no_output_device(window):
    fade = _add(window, "fade", "1", target="3", level=0.0, seconds=2.0)
    window.go_row(fade)  # no mixer in tests: must not crash
    text = _add(window, "text", "2", text="Thank you", font_size=72, video_output="screen:0")
    with mock.patch.object(window._video, "show_text") as show_text:
        window.go_row(text)
    show_text.assert_called_once()
    assert show_text.call_args.args[:3] == ("screen:0", "Thank you", 72)


def test_target_column_describes_each_kind(window):
    from cuerunner.model import Cue

    window.show_model.cues.extend([
        Cue(number="1", name="f", kind="fade", target="3", level=0.5, seconds=4.0),
        Cue(number="2", name="c", kind="control", target="3", action="stop"),
        Cue(number="3", name="w", kind="wait", duration_ms=2500),
    ])
    assert window._target_text(window.show_model.cues[0]) == "Fade cue 3 to 50% over 4 s"
    assert window._target_text(window.show_model.cues[1]) == "Stop cue 3"
    assert window._target_text(window.show_model.cues[2]) == "Wait 2.5 s"


def test_osc_cue_sends_its_message(window):
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    port = listener.getsockname()[1]
    try:
        row = _add(window, "osc", "1", target=f"127.0.0.1:{port}", text="/lights/1 1")
        window.go_row(row)
        packet, _ = listener.recvfrom(1024)
    finally:
        listener.close()
    assert packet.startswith(b"/lights/1\0\0")


def test_control_pause_and_resume_flip_the_voice(window):
    from cuerunner.engine import Voice, default_matrix, Clip
    import numpy as np

    target = _add(window, "memo", "5")
    voice = Voice(clip=Clip(np.zeros((10, 1), dtype=np.float32)), matrix=default_matrix(1, 2), tag=window.show_model.cues[target])
    window._cue_voices.append(voice)
    window._pause_cue(window.show_model.cues[target], paused=True)
    assert voice.paused
    window._pause_cue(window.show_model.cues[target], paused=False)
    assert not voice.paused


def test_cue_colour_is_saved(tmp_path):
    from cuerunner.model import Cue, Show

    show = Show(cues=[Cue(number="1", name="x", colour="#2471a3")])
    path = tmp_path / "c.cuerunner.json"
    show.save(path)
    assert Show.load(path).cues[0].colour == "#2471a3"


def test_inspector_describes_the_selected_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="4", name="Opening", kind="fade", target="3", level=0.0, seconds=2.0,
                                      pre_wait_ms=500, hotkey="F4"))
    window._refresh_table()
    window.table.selectRow(0)
    text = window.inspector_label.text()
    assert "Cue 4: Opening" in text
    assert "Fade cue 3 to 0% over 2 s" in text
    assert "Pre-wait 0.5 s" in text
    assert "Hotkey F4" in text


def test_load_then_start_resumes_the_same_voice(window, tmp_path):
    import numpy as np
    import soundfile as sf

    from cuerunner.engine import Mixer
    from cuerunner.model import Cue

    wav = tmp_path / "tone.wav"
    sf.write(str(wav), np.full((2000, 1), 0.5, dtype=np.float32), 1000)
    window.mixer = Mixer(rate=1000, out_channels=2)
    window.show_model.cues.append(Cue(number="1", name="tone", kind="audio", file=str(wav)))
    window.show_model.cues.append(Cue(number="2", name="load", kind="control", target="1", action="load"))
    window._refresh_table()

    window.go_row(1)
    preloaded = [v for v in window._cue_voices if v.tag is window.show_model.cues[0]]
    assert len(preloaded) == 1 and preloaded[0].paused and preloaded[0].preloaded
    assert window._playing_cue is None  # loaded, not playing

    window.go_row(0)
    assert len(window._cue_voices) == 1  # no second voice
    assert not preloaded[0].paused and not preloaded[0].preloaded
    assert window._playing_cue is window.show_model.cues[0]


def test_mic_cue_starts_talkback_and_a_stop_cue_ends_it(window):
    from unittest import mock

    mic = _add(window, "mic", "1")
    stop = _add(window, "control", "2", target="1", action="stop")
    with mock.patch.object(window, "start_talkback") as start, mock.patch.object(window, "stop_talkback") as stop_mic:
        window.go_row(mic)
        start.assert_called_once()
        window.go_row(stop)
        stop_mic.assert_called_once()
