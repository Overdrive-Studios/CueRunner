"""Video output helpers and the video cue's saved fields."""

from pathlib import Path

from cuerunner import video_out
from cuerunner.model import Cue, Show


def test_screen_output_round_trips() -> None:
    assert video_out.screen_index(video_out.screen_output(3)) == 3


def test_screen_index_rejects_unknown_outputs() -> None:
    assert video_out.screen_index("") is None
    assert video_out.screen_index("ndi:Stage") is None
    assert video_out.screen_index("screen:abc") is None


def test_video_cue_keeps_its_display_through_save_and_load(tmp_path: Path) -> None:
    show = Show()
    show.cues.append(Cue(number="1", name="Intro", kind="video", file="/tmp/intro.mp4", video_output="screen:1"))
    path = tmp_path / "show.cuerunner.json"
    show.save(path)
    loaded = Show.load(path)
    assert loaded.cues[0].kind == "video"
    assert loaded.cues[0].video_output == "screen:1"
