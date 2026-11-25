"""Update download dialog: Cancel is under the bar and only a user cancel emits canceled."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def _dialog():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from cuerunner.main import _DownloadDialog

    return _DownloadDialog("Downloading CueRunner v0.2.4...", None)


def test_cancel_button_is_below_the_bar_on_the_right():
    dialog = _dialog()
    dialog.show()
    cancel = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "Cancel")
    bar = dialog._bar
    assert cancel.geometry().top() > bar.geometry().bottom()
    assert cancel.geometry().right() > dialog.width() // 2
    dialog.close()


def test_user_cancel_emits_once():
    dialog = _dialog()
    seen = []
    dialog.canceled.connect(lambda: seen.append(True))
    cancel = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "Cancel")
    cancel.click()
    assert seen == [True]


def test_finishing_the_download_does_not_count_as_cancel():
    dialog = _dialog()
    seen = []
    dialog.canceled.connect(lambda: seen.append(True))
    dialog.setValue(100)
    dialog.finish()
    assert seen == []
