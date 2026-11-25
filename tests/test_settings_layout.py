"""Settings rows: notes keep a readable width and every row fits the fixed-size settings window."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_setting_notes_have_a_readable_minimum_width():
    from cuerunner import settings_ui

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    row = settings_ui._row("Spotify client ID", QtWidgets.QLineEdit(), "Sign in from Audio > Spotify after saving.")
    notes = row.findChildren(QtWidgets.QLabel, "rowNote")
    assert notes and notes[0].minimumWidth() >= settings_ui.NOTE_MIN_WIDTH
    row.deleteLater()


def test_spotify_row_fits_the_settings_window():
    from cuerunner import settings_ui

    needed = settings_ui.LABEL_WIDTH + 16 + 260 + 16 + settings_ui.NOTE_MIN_WIDTH
    assert needed <= 820 - 40  # 820 is the settings window width; 40 covers the page margins
