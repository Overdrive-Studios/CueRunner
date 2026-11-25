"""Run Program cues: start a chosen program with arguments, no shell."""

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
        w.close()


def test_program_cue_runs_the_program_with_split_arguments(window):
    from cuerunner.model import Cue

    cue = Cue(number="1", name="say", kind="program", file="/usr/bin/say", text='-v "Alex" hello')
    with mock.patch("subprocess.Popen") as popen:
        window._run_program(cue)
    args = popen.call_args.args[0]
    assert args == ["/usr/bin/say", "-v", "Alex", "hello"]
    assert "shell" not in popen.call_args.kwargs


def test_program_cue_needs_a_program(window):
    from cuerunner.model import Cue

    with pytest.raises(ValueError):
        window._run_program(Cue(number="1", name="none", kind="program", file="  "))
