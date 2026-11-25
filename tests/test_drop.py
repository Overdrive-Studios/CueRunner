"""Drag and drop: dropped files become cues by type, and unknown files are reported."""
from pathlib import Path

from cuerunner.dropped import kind_for_path, split_drop


def test_extensions_map_to_cue_kinds_ignoring_case():
    assert kind_for_path("/show/song.WAV") == "audio"
    assert kind_for_path("/show/clip.mov") == "video"
    assert kind_for_path("/show/tune.mid") == "midifile"
    assert kind_for_path("/show/notes.txt") is None


def test_split_drop_keeps_supported_files_and_names_the_rest():
    accepted, skipped = split_drop(["/a/one.mp3", "/a/readme.pdf", "/a/two.mp4"])
    assert accepted == [("/a/one.mp3", "audio"), ("/a/two.mp4", "video")]
    assert skipped == ["readme.pdf"]


def test_dropped_files_insert_cues_after_the_selected_row(tmp_path):
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
            window.show_model.cues.append(Cue(number="1", name="first", kind="memo"))
            window._refresh_table()
            window.table.selectRow(0)
            with mock.patch.object(window, "_probe_duration"):
                window._add_dropped_files([("/show/a.wav", "audio"), ("/show/b.mid", "midifile")])
            names = [c.name for c in window.show_model.cues]
            kinds = [c.kind for c in window.show_model.cues]
            assert names == ["first", "a", "b"]
            assert kinds == ["memo", "audio", "midifile"]
            assert Path(window.show_model.cues[1].file) == Path("/show/a.wav")  # separators differ on Windows
        finally:
            window.close()
