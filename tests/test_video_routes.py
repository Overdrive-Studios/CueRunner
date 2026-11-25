"""Output routes, camera patches and the Camera cue's saved fields."""

from pathlib import Path

from cuerunner import video_out
from cuerunner.model import Cue, Show


def test_resolve_display_is_fit_mode():
    assert video_out.resolve_output("screen:1", []) == (1, "fit")


def test_resolve_route_uses_its_display_and_fit():
    routes = [{"name": "Stage left", "output": "screen:2", "fit": "full"}]
    assert video_out.resolve_output("route:Stage left", routes) == (2, "full")


def test_resolve_route_with_bad_fit_falls_back_to_full():
    routes = [{"name": "Foh", "output": "screen:0", "fit": "weird"}]
    assert video_out.resolve_output("route:Foh", routes) == (0, "full")


def test_unknown_route_is_not_connected():
    assert video_out.resolve_output("route:Missing", []) == (None, "full")


def test_empty_output_is_not_connected():
    assert video_out.resolve_output("", []) == (None, "fit")


def test_output_choices_lists_routes_after_displays(monkeypatch):
    monkeypatch.setattr(video_out, "display_names", lambda: ["Display 1: A (800×600)"])
    routes = [{"name": "Stage", "output": "screen:0", "fit": "full"}]
    assert video_out.output_choices(routes) == [
        ("Display 1: A (800×600)", "screen:0"),
        ("Route: Stage", "route:Stage"),
    ]


def test_routes_and_inputs_save_and_load(tmp_path: Path):
    show = Show(
        video_routes=[{"name": "Stage", "output": "screen:1", "fit": "fit"}],
        video_inputs=[{"name": "Cam 1", "device": "FaceTime HD"}],
    )
    show.cues.append(Cue(number="1", name="Cam", kind="camera", video_input="Cam 1", video_output="route:Stage"))
    path = tmp_path / "show.cuerunner.json"
    show.save(path)

    loaded = Show.load(path)
    assert loaded.video_routes == [{"name": "Stage", "output": "screen:1", "fit": "fit"}]
    assert loaded.video_inputs == [{"name": "Cam 1", "device": "FaceTime HD"}]
    assert loaded.cues[0].kind == "camera"
    assert loaded.cues[0].video_input == "Cam 1"
    assert loaded.cues[0].video_output == "route:Stage"


def test_older_show_without_routes_loads_empty(tmp_path: Path):
    path = tmp_path / "old.cuerunner.json"
    path.write_text('{"format_version": 1, "name": "Old", "cues": []}', encoding="utf-8")
    loaded = Show.load(path)
    assert loaded.video_routes == []
    assert loaded.video_inputs == []
