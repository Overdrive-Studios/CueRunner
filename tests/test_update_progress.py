"""Downloading an update must show its progress."""

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def window():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()
        w.close()


@pytest.fixture
def release():
    from cuerunner import updater

    return updater.Release(tag="v9.9.9", asset_name="CueRunner.dmg",
                           asset_url="https://example.invalid/CueRunner.dmg")


def start(window, release):
    """Begin a download with the worker stubbed out, and hand back the dialog."""
    import cuerunner.main as main

    created = []

    class FakeWorker:
        progress = mock.Mock()
        done = mock.Mock()
        failed = mock.Mock()

        def __init__(self, *a, **k):
            created.append(self)

        def start(self):
            self.started = True

        def requestInterruption(self):  # noqa: N802 - matches QThread
            pass

    with mock.patch.object(main, "_DownloadWorker", FakeWorker):
        window._download_update(release, window)
    dialogs = [c for c in window.children() if isinstance(c, main._DownloadDialog)]
    return dialogs[-1], created[-1]


def test_the_progress_window_actually_appears(window, release):
    dialog, worker = start(window, release)
    assert dialog.isVisible(), "the download runs with nothing on screen"
    assert worker.started
    dialog.finish()


def test_the_bar_follows_the_download(window, release):
    dialog, _ = start(window, release)
    dialog.setValue(40)
    assert dialog._bar.value() == 40
    dialog.finish()


def test_an_unknown_size_shows_a_busy_bar(window, release):
    dialog, _ = start(window, release)
    dialog.setRange(0, 0)
    assert dialog.maximum() == 0
    dialog.finish()


def test_finishing_closes_it_without_looking_like_a_cancel(window, release):
    dialog, _ = start(window, release)
    seen = []
    dialog.canceled.connect(lambda: seen.append(1))
    dialog.finish()
    assert not dialog.isVisible()
    assert seen == []


def test_cancelling_says_so_once(window, release):
    dialog, _ = start(window, release)
    seen = []
    dialog.canceled.connect(lambda: seen.append(1))
    dialog.reject()
    assert seen == [1]
    assert not dialog.isVisible()
