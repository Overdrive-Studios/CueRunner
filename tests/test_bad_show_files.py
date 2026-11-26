"""Opening a file that is not a good show must say so, never crash."""

import pytest

from cuerunner.model import Show


def write(tmp_path, text, name="s.json"):
    path = tmp_path / name
    path.write_text(text)
    return path


@pytest.mark.parametrize("text", ["", "not json at all", "{", "\x00\x01"])
def test_a_file_that_is_not_json_is_refused_clearly(tmp_path, text):
    with pytest.raises(ValueError):  # the app catches this and says it cannot open it
        Show.load(write(tmp_path, text))


@pytest.mark.parametrize("text", ["[1,2,3]", "42", '"a string"', "null"])
def test_json_that_is_not_a_show_is_refused_clearly(tmp_path, text):
    with pytest.raises(ValueError, match="not a CueRunner show"):
        Show.load(write(tmp_path, text))


@pytest.mark.parametrize("text", [
    '{"cues": "nope"}',
    '{"cues": [1, 2]}',
    '{"cues": [null]}',
    '{"cues": [{"name": "no number"}]}',
    '{"radio": "nope", "cues": []}',
    '{"midi_map": 5, "cues": []}',
    '{"duck_level": "loud", "cues": []}',
    '{"audio_patches": "nope", "cues": []}',
    '{"pads": "nope", "cues": []}',
])
def test_odd_contents_are_passed_over_rather_than_crashing(tmp_path, text):
    show = Show.load(write(tmp_path, text))
    assert isinstance(show.cues, list)
    assert len(show.audio_patches) >= 1  # a show always keeps a patch


def test_the_good_cues_still_open_when_one_is_unreadable(tmp_path):
    text = ('{"cues": [{"number":"1","name":"keep me","kind":"audio"}, null, 7,'
            ' {"number":"2","name":"me too","kind":"memo"}]}')
    show = Show.load(write(tmp_path, text))
    assert [c.name for c in show.cues] == ["keep me", "me too"]


def test_a_field_from_a_newer_version_is_ignored(tmp_path):
    text = '{"cues": [{"number":"1","name":"a","kind":"audio","something_new":123}]}'
    assert Show.load(write(tmp_path, text)).cues[0].name == "a"


def test_the_window_reports_a_bad_file_instead_of_falling_over(tmp_path):
    import os
    from unittest import mock

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    widgets.QApplication.instance() or widgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        window = MainWindow()
    try:
        bad = write(tmp_path, "[1,2,3]")
        with mock.patch.object(widgets.QMessageBox, "critical") as told:
            window.open_show_path(bad)
        told.assert_called_once()
        assert window.show_path is None  # the old show is left alone
    finally:
        window._mark_saved()
        window.close()
