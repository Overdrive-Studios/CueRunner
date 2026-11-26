"""Settings: nothing in the dialog may be cut off, at any size it can be opened at.

The Spotify note used to run off the edge of its row and lose its last words, and the
console notes were doing the same. These walk every page at the dialog's smallest and
default sizes and measure whether each note actually fits the space it was given.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from cuerunner.model import Show  # noqa: E402
from cuerunner.settings_ui import NOTE_FLOOR, SettingsDialog  # noqa: E402

SLACK = 2  # a pixel of rounding in the font metrics is not a clipped note


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def clipped(app, width: int, height: int) -> list[str]:
    """Every note in the dialog at this size whose text does not fit its label."""
    dialog = SettingsDialog(Show())
    dialog.resize(width, height)
    dialog.show()
    bad = []
    try:
        for index in range(dialog.stack.count()):
            dialog.sidebar.setCurrentRow(index)
            for _ in range(3):
                app.processEvents()
            for note in dialog.stack.widget(index).findChildren(QLabel):
                if note.objectName() != "rowNote" or not note.isVisible():
                    continue
                metrics = QFontMetrics(note.font())
                needs = metrics.boundingRect(
                    0, 0, note.width(), 10_000, int(Qt.TextFlag.TextWordWrap), note.text())
                if needs.height() > note.height() + SLACK or needs.width() > note.width() + SLACK:
                    bad.append(f"page {index}: {note.text()[:50]!r} does not fit its label: "
                               f"got {note.width()}x{note.height()}, "
                               f"needs {needs.width()}x{needs.height()}")
                    continue
                # The label can be the right size and still be pushed off the edge of the
                # page, which is how the Spotify note lost its last words.
                right = note.mapTo(dialog, note.rect().bottomRight()).x()
                if right > dialog.width() - SLACK:
                    bad.append(f"page {index}: {note.text()[:50]!r} runs off the window: "
                               f"ends at {right} of {dialog.width()}")
    finally:
        dialog.close()
    return bad


def test_no_note_is_cut_off_at_the_default_size(app):
    dialog = SettingsDialog(Show())
    size = dialog.size()
    dialog.close()
    assert clipped(app, size.width(), size.height()) == []


def test_no_note_is_cut_off_at_the_smallest_size_allowed(app):
    dialog = SettingsDialog(Show())
    smallest = dialog.minimumSize()
    dialog.close()
    assert clipped(app, smallest.width(), smallest.height()) == []


def test_the_smallest_window_still_fits_the_widest_row(app):
    """The dialog cannot be opened narrower than its widest row needs to read properly."""
    dialog = SettingsDialog(Show())
    dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
    dialog.show()
    app.processEvents()
    try:
        chrome = dialog.width() - dialog.stack.width()  # sidebar and window edges
        widest = 0
        for index in range(dialog.stack.count()):
            for note in dialog.stack.widget(index).findChildren(QLabel):
                if note.objectName() != "rowNote":
                    continue
                row = note.parentWidget()
                # What the row needs with the note squeezed down to its floor.
                fixed = sum(child.sizeHint().width() for child in row.findChildren(QtWidgets.QWidget)
                            if child is not note and child.parentWidget() is row)
                widest = max(widest, fixed + NOTE_FLOOR + 80)
        assert widest > 0
        assert dialog.minimumWidth() >= widest + chrome - dialog.stack.width() \
            or dialog.stack.width() >= widest
    finally:
        dialog.close()


def test_the_dialog_can_be_resized(app):
    """It used to be a fixed size, which is what left no room for the longer notes."""
    dialog = SettingsDialog(Show())
    try:
        assert dialog.maximumWidth() > dialog.minimumWidth()
        assert dialog.maximumHeight() > dialog.minimumHeight()
    finally:
        dialog.close()


def test_every_settings_page_scrolls_instead_of_being_cut_off(app):
    dialog = SettingsDialog(Show())
    dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
    dialog.show()
    app.processEvents()
    try:
        for index in range(dialog.stack.count()):
            page = dialog.stack.widget(index)
            # Most pages are scrollers themselves. The Video page is a tab widget whose
            # tabs hold tables, which scroll on their own.
            if isinstance(page, QtWidgets.QScrollArea):
                assert page.widgetResizable()
                continue
            inner = page.findChildren(QtWidgets.QAbstractScrollArea)
            assert inner, f"page {index} cannot scroll and cannot be read if it overflows"
            for area in inner:
                if area.objectName() == "settingsPage":
                    assert area.widgetResizable()
    finally:
        dialog.close()


def test_notes_never_collapse_to_nothing(app):
    dialog = SettingsDialog(Show())
    dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
    dialog.show()
    app.processEvents()
    try:
        for index in range(dialog.stack.count()):
            dialog.sidebar.setCurrentRow(index)
            app.processEvents()
            for note in dialog.stack.widget(index).findChildren(QLabel):
                if note.objectName() == "rowNote" and note.isVisible():
                    assert note.width() >= NOTE_FLOOR, note.text()[:40]
    finally:
        dialog.close()
