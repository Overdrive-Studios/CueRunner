"""Drive every control in the app and fail on anything that raises.

An unhandled exception inside a Qt slot aborts the whole process, so a handler that
raises is a crash on the user's machine, not a logged warning. These run with a working
mixer and real audio files: without those, most handlers return early and the run proves
nothing, which is how a crash on every GO went unnoticed.

Each stage gets a fresh window, so one bad state cannot cascade into the next.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("soundfile")

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox, QCheckBox, QComboBox, QPushButton, QSlider, QToolButton,
)

from conftest_audit import (  # noqa: E402
    Faults, KINDS, build_window, quiet_modals, write_tones, write_video,
)
from cuerunner import fx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    return write_tones(tmp_path_factory.mktemp("audio"))


@pytest.fixture(scope="module")
def movie(tmp_path_factory):
    return write_video(tmp_path_factory.mktemp("video"))


@pytest.fixture
def window(app, tones, movie):
    with quiet_modals():
        w = build_window(*tones, movie)
        w.show()
        app.processEvents()
        yield w
        w.hard_stop()
        w._mark_saved()
        w.close()
        app.processEvents()


def settle(app):
    for _ in range(3):
        app.processEvents()


def name_of(widget) -> str:
    return widget.text() or widget.objectName() or type(widget).__name__


# --- the controls -----------------------------------------------------
def test_every_button_and_checkbox_survives_being_pressed(app, window):
    window.table.selectRow(0)
    settle(app)
    buttons = (window.findChildren(QPushButton) + window.findChildren(QToolButton)
               + window.findChildren(QCheckBox))
    with Faults() as faults:
        for widget in buttons:
            if not widget.isEnabled():
                continue
            faults.run(f"click {name_of(widget)}", widget.click)
            settle(app)
    assert not faults.found, faults.report()


def test_every_button_survives_with_no_cue_selected(app, window):
    window.table.clearSelection()
    settle(app)
    buttons = (window.findChildren(QPushButton) + window.findChildren(QToolButton)
               + window.findChildren(QCheckBox))
    with Faults() as faults:
        for widget in buttons:
            if not widget.isEnabled():
                continue
            faults.run(f"click {name_of(widget)} with nothing selected", widget.click)
            settle(app)
    assert not faults.found, faults.report()


def test_every_menu_item_survives_being_chosen(app, window):
    with Faults() as faults:
        def walk(menu, path):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                if action.menu():
                    walk(action.menu(), path + action.text() + " > ")
                    continue
                if action.text().startswith(("Quit", "New Show", "Open Show")):
                    continue  # these replace or close the window the rest of the run needs
                faults.run(f"menu {path}{action.text()}", action.trigger)
                settle(app)

        for action in window.menuBar().actions():
            if action.menu():
                walk(action.menu(), action.text() + " > ")
    assert not faults.found, faults.report()


@pytest.mark.parametrize("kind", KINDS)
def test_every_inspector_field_survives_being_changed(app, window, kind):
    row = KINDS.index(kind)
    window.table.selectRow(row)
    settle(app)
    with Faults() as faults:
        for tab in range(window.insp_tabs.count()):
            if not window.insp_tabs.isTabVisible(tab):
                continue
            window.insp_tabs.setCurrentIndex(tab)
            settle(app)
            where = f"{kind} / {window.insp_tabs.tabText(tab)}"
            page = window.insp_stack.currentWidget()  # only this tab's own fields
            for box in page.findChildren(QAbstractSpinBox):
                if box.isEnabled():
                    faults.run(f"{where}: spin", box.stepUp)
                    faults.run(f"{where}: spin", box.stepDown)
            for combo in page.findChildren(QComboBox):
                if not combo.isEnabled():
                    continue
                for index in range(combo.count()):
                    faults.run(f"{where}: combo {combo.itemText(index)}",
                               lambda c=combo, i=index: c.setCurrentIndex(i))
            for slider in page.findChildren(QSlider):
                if not slider.isEnabled():
                    continue
                for value in (slider.minimum(), slider.maximum()):
                    faults.run(f"{where}: slider", lambda s=slider, v=value: s.setValue(v))
            settle(app)
    assert not faults.found, faults.report()


# --- playback ---------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_every_cue_kind_starts_renders_and_stops(app, window, kind):
    row = KINDS.index(kind)
    with Faults() as faults:
        faults.run(f"GO {kind}", lambda: window.go_row(row))
        settle(app)
        faults.run(f"render after {kind}",
                   lambda: [window.mixer.render(1024) for _ in range(20)])
        faults.run(f"clock while {kind} runs", window._update_clock)
        faults.run(f"stop {kind}", window.hard_stop)
        settle(app)
    assert not faults.found, faults.report()


@pytest.mark.parametrize("effect", [s.kind for s in fx.EFFECT_LIST if s.kind != fx.VST_KIND])
def test_every_effect_plays_without_raising(app, window, effect):
    cue = window.show_model.cues[0]
    cue.effects = [fx.new_effect(effect)]
    with Faults() as faults:
        faults.run(f"play with {effect}", lambda: window.go_row(0))
        faults.run(f"render with {effect}",
                   lambda: [window.mixer.render(1024) for _ in range(20)])
        faults.run(f"stop with {effect}", window.hard_stop)
    assert not faults.found, faults.report()


@pytest.mark.parametrize("trim_in,trim_out,rate,loop,count", [
    (0.0, 0.0, 1.0, False, 1),
    (0.5, 2.0, 1.0, False, 1),
    (0.0, 0.0, 2.0, False, 3),
    (1.0, 0.0, 0.5, True, 1),
    (0.0, 1.0, 1.0, False, 2),
    (3.9, 0.0, 1.0, False, 1),
    (0.0, 0.0, 1.0, False, 0),
    (0.0, 0.0, 0.03, False, 1),
    (0.0, 0.0, 33.0, False, 1),
])
def test_odd_trims_rates_and_loops_still_play(app, window, trim_in, trim_out, rate, loop, count):
    cue = window.show_model.cues[0]
    cue.trim_in, cue.trim_out, cue.rate, cue.loop, cue.play_count = (
        trim_in, trim_out, rate, loop, count)
    where = f"trim {trim_in}-{trim_out} rate {rate} loop {loop} count {count}"
    with Faults() as faults:
        faults.run(where, lambda: window.go_row(0))
        faults.run(where + " render",
                   lambda: [window.mixer.render(1024) for _ in range(60)])
        faults.run(where + " clock", window._update_clock)
        faults.run(where + " stop", window.hard_stop)
    assert not faults.found, faults.report()


@pytest.mark.parametrize("action", [
    "stop", "toggle_pause", "panic", "hard_stop", "pause_all", "resume_all",
    "panic_selected", "pause_selected", "load_selected", "go", "toggle_flag",
    "cycle_continue", "undo", "redo",
])
def test_every_transport_control_works_mid_cue(app, window, action):
    window.table.selectRow(0)
    settle(app)
    with Faults() as faults:
        faults.run("start", lambda: window.go_row(0))
        faults.run("render", lambda: [window.mixer.render(1024) for _ in range(5)])
        faults.run(action, getattr(window, action))
        faults.run("render after " + action,
                   lambda: [window.mixer.render(1024) for _ in range(5)])
        faults.run("clock after " + action, window._update_clock)
    assert not faults.found, faults.report()


def test_the_clock_runs_for_a_whole_cue_without_raising(app, window):
    """The crash that took the app down fired from the 100 ms tick, not from GO."""
    with Faults() as faults:
        faults.run("start", lambda: window.go_row(0))
        for _ in range(60):
            faults.run("render", lambda: window.mixer.render(2048))
            faults.run("tick", window._tick)
    assert not faults.found, faults.report()


# --- the paths that have actually crashed -----------------------------
@pytest.mark.parametrize("kind", ["audio", "video", "camera", "text", "group", "memo"])
def test_walking_the_inspector_tabs_with_a_real_file_is_safe(app, window, movie, kind):
    """Opening Time & Loops on a video cue crashed. Nothing is stubbed out of this path."""
    if kind == "video" and not movie:
        pytest.skip("no ffmpeg here to make a real video file with")
    row = KINDS.index(kind)
    window.table.selectRow(row)
    settle(app)
    with Faults() as faults:
        for _ in range(2):  # twice: the second pass has the probe's answer in hand
            for tab in range(window.insp_tabs.count()):
                if not window.insp_tabs.isTabVisible(tab):
                    continue
                name = window.insp_tabs.tabText(tab)
                faults.run(f"{kind} / open {name}",
                           lambda t=tab: window.insp_tabs.setCurrentIndex(t))
                settle(app)
                faults.run(f"{kind} / refresh {name}", window._refresh_inspector)
                faults.run(f"{kind} / waveform on {name}", window._refresh_waveform)
                faults.run(f"{kind} / playhead on {name}", window._follow_playhead)
                settle(app)
    assert not faults.found, faults.report()


def test_a_video_cue_gets_a_length_and_a_usable_panel(app, window, movie):
    if not movie:
        pytest.skip("no ffmpeg here to make a real video file with")
    from PySide6.QtCore import QEventLoop, QTimer

    from cuerunner.main import INSPECTOR_TABS

    row = KINDS.index("video")
    window.table.selectRow(row)
    window.insp_tabs.setCurrentIndex(INSPECTOR_TABS.index("Time & Loops"))
    settle(app)
    # The length comes back from the media engine, so give it a moment to arrive.
    loop = QEventLoop()
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    settle(app)
    window._refresh_table()
    assert window.show_model.cues[row].duration_ms > 0
    assert window.insp_wave.is_usable()


def test_typing_into_every_editable_cell_is_safe(app, window):
    """The cue list is typed into directly now, so every cell has to take any text."""
    from cuerunner.main import COLUMNS, EDITABLE_COLUMNS

    with Faults() as faults:
        for row in range(min(len(window.show_model.cues), 6)):
            for column in EDITABLE_COLUMNS:
                for text in ("", "   ", "7", "2.5", "4 s", "-3", "1e9", "nonsense",
                             "\u00e9\u00e8", "1" * 60):
                    # Fetched afresh each time: a redraw replaces the items.
                    item = window.table.item(row, COLUMNS.index(column))
                    if item is None:
                        continue
                    faults.run(f"type {text!r} into {column}",
                               lambda i=item, t=text: i.setText(t))
                    settle(app)
    assert not faults.found, faults.report()


def test_a_show_file_full_of_wrong_types_loads_and_opens(app, window, tmp_path):
    """Hand-edited and older show files used to send a float into a spin box and crash."""
    import json

    from cuerunner.model import Show

    bad = {"cue_lists": [{"name": "Main", "kind": "list", "cues": [{
        "number": 7, "name": 42, "kind": "audio", "file": "/nope.wav",
        "pre_wait_ms": 1500.7, "post_wait_ms": "250", "duration_ms": 3000.9,
        "fade_in": "2", "fade_out": None, "volume": "0.5", "rate": "1.5",
        "play_count": 2.0, "universe": "3", "font_size": 18.5, "level": "0.25",
        "loop": 1, "armed": "yes", "slices": "not a list", "effects": None,
        "output_levels": {}, "trim_in": "0.5", "opacity": "1",
    }]}]}
    path = tmp_path / "odd.cuerunner.json"
    path.write_text(json.dumps(bad))
    show = Show.load(path)
    cue = show.cues[0]
    assert isinstance(cue.number, str) and isinstance(cue.name, str)
    assert isinstance(cue.pre_wait_ms, int) and isinstance(cue.post_wait_ms, int)
    assert isinstance(cue.fade_in, float) and isinstance(cue.rate, float)
    assert isinstance(cue.play_count, int) and isinstance(cue.universe, int)
    assert isinstance(cue.font_size, int) and isinstance(cue.slices, list)

    window.show_model = show
    with Faults() as faults:
        faults.run("show the odd file", window._refresh_table)
        settle(app)
        window.table.selectRow(0)
        for tab in range(window.insp_tabs.count()):
            if window.insp_tabs.isTabVisible(tab):
                faults.run(f"tab {window.insp_tabs.tabText(tab)}",
                           lambda t=tab: window.insp_tabs.setCurrentIndex(t))
                settle(app)
    assert not faults.found, faults.report()
