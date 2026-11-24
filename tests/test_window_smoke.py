"""Builds the real main window offscreen and runs its main actions. Catches broken wiring that unit tests miss."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_main_window_builds_and_runs_its_actions() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow
    from cuerunner.theme import apply_dark_theme

    box = QtWidgets.QMessageBox
    with mock.patch.object(box, "critical"), mock.patch.object(box, "information"), mock.patch.object(
        box, "warning"
    ), mock.patch.object(box, "question", return_value=box.StandardButton.No):
        apply_dark_theme(app)
        window = MainWindow()
        try:
            window.show()
            window._tick()
            window.new_show()
            window.toggle_pause()
            window.toggle_pause()
            window.stop()
            window.go()
            window._refresh_table()
            window.soundboard.refresh()
            window.countdown.toggle()
            window.countdown.reset()
            window._update_clock()
            window.check_for_updates()
        finally:
            window.close()
