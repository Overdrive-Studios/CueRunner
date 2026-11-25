"""Video opacity: the output window's opacity follows the cue, clamped to 0 to 1."""

from unittest import mock

from cuerunner.video_out import VideoOutputs


def test_set_opacity_clamps_and_applies_to_the_window():
    outputs = VideoOutputs.__new__(VideoOutputs)  # skip the Qt windows; only the window lookup is under test
    window = mock.MagicMock()
    out = mock.MagicMock(widget=window)
    outputs._output_for = mock.MagicMock(return_value=out)
    outputs.set_opacity("main", 0.4)
    window.setWindowOpacity.assert_called_with(0.4)
    outputs.set_opacity("main", 3)
    window.setWindowOpacity.assert_called_with(1.0)
    outputs.set_opacity("main", -1)
    window.setWindowOpacity.assert_called_with(0.0)


def test_video_cue_defaults_to_solid():
    from cuerunner.model import Cue

    assert Cue(number="1", name="v", kind="video").opacity == 1.0
