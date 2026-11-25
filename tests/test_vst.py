"""Hosting VST3 and Audio Unit plugins.

No real plugin is installed on a build machine, so these cover the parts that can be
checked anywhere: finding plugins, surviving a plugin that will not open, keeping the
saved settings, and handing audio over the right way round.
"""

import base64
import sys
from unittest import mock

import numpy as np
import pytest

from cuerunner import fx, vst


class FakePlugin:
    """Stands in for a real plugin: doubles the audio and remembers how it was called."""

    def __init__(self, channels: int = 2) -> None:
        self.calls = []
        self.raw_state = b"saved settings"
        self._channels = channels

    def process(self, audio, rate, reset=True):
        self.calls.append({"shape": audio.shape, "rate": rate, "reset": reset})
        return np.asarray(audio) * 2.0

    def show_editor(self):
        pass


def a_block(frames: int = 256, channels: int = 2) -> np.ndarray:
    return np.full((frames, channels), 0.25, dtype=np.float32)


# ----------------------------------------------------------------- finding plugins
def test_the_plugin_folders_suit_the_platform():
    folders = [str(f) for f in vst.plugin_folders()]
    assert folders
    if sys.platform == "darwin":
        assert any("Plug-Ins" in f for f in folders)


def test_installed_lists_plugins_once_each_in_name_order(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
    (first / "Zebra.vst3").touch()
    (first / "Alpha.vst3").touch()
    (second / "Alpha.vst3").touch()  # the same plugin in a second folder
    (first / "notes.txt").touch()    # not a plugin
    with mock.patch.object(vst, "plugin_folders", return_value=[first, second]):
        found = [p.name for p in vst.installed()]
    assert found == ["Alpha.vst3", "Zebra.vst3"]


def test_a_missing_folder_is_passed_over(tmp_path):
    with mock.patch.object(vst, "plugin_folders", return_value=[tmp_path / "nope"]):
        assert vst.installed() == []


def test_the_plugin_name_drops_the_extension():
    assert vst.plugin_name("/x/y/Pro-Q 3.vst3") == "Pro-Q 3"


# ----------------------------------------------------------------- loading
def test_a_plugin_that_will_not_open_gives_nothing_rather_than_raising():
    assert vst.load("/not/a/real/plugin.vst3") is None


def test_saved_settings_are_handed_back_to_the_plugin():
    plugin = FakePlugin()
    state = base64.b64encode(b"my settings").decode("ascii")
    with mock.patch.dict(sys.modules, {"pedalboard": mock.MagicMock(
            load_plugin=mock.Mock(return_value=plugin))}):
        loaded = vst.load("/x/Thing.vst3", state)
    assert loaded is plugin
    assert plugin.raw_state == b"my settings"


def test_settings_the_plugin_rejects_do_not_stop_it_loading():
    plugin = FakePlugin()
    with mock.patch.dict(sys.modules, {"pedalboard": mock.MagicMock(
            load_plugin=mock.Mock(return_value=plugin))}):
        assert vst.load("/x/Thing.vst3", "not base64 at all!!") is plugin


def test_save_state_turns_the_plugin_settings_into_text():
    assert vst.save_state(FakePlugin()) == base64.b64encode(b"saved settings").decode("ascii")


def test_save_state_copes_with_a_plugin_that_has_none():
    assert vst.save_state(object()) == ""


# ----------------------------------------------------------------- processing
def test_audio_is_handed_over_the_right_way_round_and_keeps_its_state():
    """pedalboard wants (channels, frames), and resetting each block would cut tails off."""
    plugin = FakePlugin()
    effect = vst.VstEffect({"path": "/x/Thing.vst3"}, 48000, 2)
    effect.plugin = plugin
    out = effect.process(a_block(256, 2))
    call = plugin.calls[0]
    assert call["shape"] == (2, 256)   # channels first, as pedalboard expects
    assert call["reset"] is False      # state carries over between blocks
    assert call["rate"] == 48000
    assert out.shape == (256, 2)
    assert np.allclose(out, 0.5)       # the fake doubled it


def test_a_cue_still_plays_when_the_plugin_is_missing():
    effect = vst.VstEffect({"path": "/gone.vst3"}, 48000, 2)
    assert effect.flat()
    block = a_block()
    assert np.array_equal(effect.process(block), block)


def test_a_plugin_that_fails_mid_show_is_dropped_rather_than_stopping_the_cue():
    class Broken(FakePlugin):
        def process(self, audio, rate, reset=True):
            raise RuntimeError("plugin crashed")

    effect = vst.VstEffect({"path": "/x/Thing.vst3"}, 48000, 2)
    effect.plugin = Broken()
    block = a_block()
    assert np.array_equal(effect.process(block), block)  # passes through, dry
    assert effect.plugin is None                          # and is not tried again


def test_a_plugin_returning_short_blocks_does_not_change_the_block_size():
    class Short(FakePlugin):
        def process(self, audio, rate, reset=True):
            return np.asarray(audio)[:, :100]  # held some audio back

    effect = vst.VstEffect({"path": "/x/Thing.vst3"}, 48000, 2)
    effect.plugin = Short()
    out = effect.process(a_block(256, 2))
    assert out.shape == (256, 2)


def test_a_bypassed_plugin_is_left_out_of_the_chain():
    effect = vst.VstEffect({"path": "/x/Thing.vst3", "bypass": True}, 48000, 2)
    effect.plugin = FakePlugin()
    assert effect.flat()


# ----------------------------------------------------------------- in the chain
def test_a_plugin_is_an_effect_like_any_other():
    assert fx.VST_KIND in fx.EFFECTS
    effect = fx.new_effect(fx.VST_KIND)
    assert effect["kind"] == fx.VST_KIND


def test_the_effect_list_shows_the_plugin_name():
    assert fx.describe({"kind": fx.VST_KIND, "name": "Pro-Q 3"}) == "Pro-Q 3"
    assert "not set" in fx.describe({"kind": fx.VST_KIND})


def test_a_chain_with_an_unavailable_plugin_is_treated_as_empty():
    chain = fx.Chain(effects=[{"kind": fx.VST_KIND, "path": "/gone.vst3"}],
                     rate=48000, channels=2)
    assert chain.flat()


def test_a_plugin_in_a_chain_processes_the_audio():
    plugin = FakePlugin()
    with mock.patch.object(vst, "load", return_value=plugin):
        chain = fx.Chain(effects=[{"kind": fx.VST_KIND, "path": "/x/Thing.vst3"}],
                         rate=48000, channels=2)
        assert not chain.flat()
        out = chain.process(a_block(128, 2))
    assert np.allclose(out, 0.5)


def test_hosting_reports_whether_it_is_available():
    assert vst.available() in (True, False)
    with mock.patch.dict(sys.modules, {"pedalboard": None}):
        assert vst.available() is False


# ----------------------------------------------------------------- in the inspector
@pytest.fixture
def window():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    widgets.QApplication.instance() or widgets.QApplication([])
    from cuerunner.main import MainWindow

    with mock.patch.object(MainWindow, "_open_default_output", lambda self: None):
        w = MainWindow()
        yield w
        w._mark_saved()
        w.close()


def a_cue(window):
    from cuerunner.model import Cue

    window.show_model.cues.append(Cue(number="1", name="Song", kind="audio"))
    window._refresh_table()
    window.table.selectRow(0)
    window._set_mode(editing=True)
    return window.show_model.cues[0]


def test_choosing_a_plugin_adds_it_to_the_cue(window, tmp_path):
    cue = a_cue(window)
    plugin_file = tmp_path / "Pro-Q 3.vst3"
    plugin_file.touch()
    with mock.patch.object(vst, "available", return_value=True), \
         mock.patch.object(vst, "installed", return_value=[plugin_file]), \
         mock.patch.object(vst, "load", return_value=FakePlugin()), \
         mock.patch("cuerunner.main.QInputDialog.getItem", return_value=("Pro-Q 3", True)):
        window._add_plugin()
    assert cue.effects[0]["kind"] == fx.VST_KIND
    assert cue.effects[0]["name"] == "Pro-Q 3"
    assert window.insp_fx_list.item(0).text() == "Pro-Q 3"


def test_a_plugin_that_will_not_open_is_not_added(window, tmp_path):
    cue = a_cue(window)
    plugin_file = tmp_path / "Broken.vst3"
    plugin_file.touch()
    with mock.patch.object(vst, "available", return_value=True), \
         mock.patch.object(vst, "installed", return_value=[plugin_file]), \
         mock.patch.object(vst, "load", return_value=None), \
         mock.patch("cuerunner.main.QInputDialog.getItem", return_value=("Broken", True)), \
         mock.patch("cuerunner.main.QMessageBox.warning") as warned:
        window._add_plugin()
    assert cue.effects == []
    warned.assert_called_once()


def test_the_panel_offers_a_way_into_the_plugin_window(window):
    cue = a_cue(window)
    cue.effects.append({"kind": fx.VST_KIND, "path": "/x/Thing.vst3", "name": "Thing"})
    window._refresh_effects(cue)
    assert window.insp_fx_params._open_editor is not None
    assert window.insp_fx_params._bypass is not None


def test_bypassing_a_plugin_is_saved_on_the_cue(window):
    cue = a_cue(window)
    cue.effects.append({"kind": fx.VST_KIND, "path": "/x/Thing.vst3", "name": "Thing",
                        "bypass": False})
    window._refresh_effects(cue)
    window.insp_fx_params._bypass.setChecked(True)
    assert cue.effects[0]["bypass"] is True


def test_settings_made_in_the_plugin_window_are_kept(window):
    cue = a_cue(window)
    cue.effects.append({"kind": fx.VST_KIND, "path": "/x/Thing.vst3", "name": "Thing",
                        "state": ""})
    window._refresh_effects(cue)
    with mock.patch.object(vst, "load", return_value=FakePlugin()):
        window._open_plugin_editor()
    assert cue.effects[0]["state"] == base64.b64encode(b"saved settings").decode("ascii")


def test_a_plugin_cue_survives_saving_and_reopening(tmp_path):
    from cuerunner.model import Cue, Show

    show = Show()
    show.cues.append(Cue(number="1", name="Song", kind="audio", effects=[
        {"kind": fx.VST_KIND, "path": "/x/Thing.vst3", "name": "Thing",
         "state": "c2V0dGluZ3M=", "bypass": True}]))
    path = tmp_path / "show.json"
    show.save(path)
    effect = Show.load(path).cues[0].effects[0]
    assert effect["name"] == "Thing"
    assert effect["state"] == "c2V0dGluZ3M="
    assert effect["bypass"] is True
