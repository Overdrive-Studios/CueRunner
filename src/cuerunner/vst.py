"""Hosting VST3 and Audio Unit plugins, so a cue can use effects from outside CueRunner.

The hosting itself is done by pedalboard, which is optional: without it everything else
in the app still works and plugin cues simply pass audio through untouched.

Two details matter and are easy to get wrong:
  * pedalboard wants audio as (channels, frames); the mixer works in (frames, channels).
  * process() resets the plugin by default, which would wipe a reverb tail every block,
    so it is always called with reset off.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import numpy as np

# Where plugins normally live, by platform.
MAC_FOLDERS = [
    "/Library/Audio/Plug-Ins/VST3",
    "~/Library/Audio/Plug-Ins/VST3",
    "/Library/Audio/Plug-Ins/Components",
    "~/Library/Audio/Plug-Ins/Components",
]
WINDOWS_FOLDERS = [
    r"C:\Program Files\Common Files\VST3",
    r"C:\Program Files (x86)\Common Files\VST3",
]
LINUX_FOLDERS = ["/usr/lib/vst3", "/usr/local/lib/vst3", "~/.vst3"]
SUFFIXES = (".vst3", ".component")


def available() -> bool:
    """True when plugin hosting can be used at all."""
    try:
        import importlib

        importlib.import_module("pedalboard")
    except Exception:
        return False
    return True


def plugin_folders() -> list[Path]:
    """The folders this platform keeps plugins in, whether or not they exist."""
    if sys.platform == "darwin":
        names = MAC_FOLDERS
    elif os.name == "nt":
        names = WINDOWS_FOLDERS
    else:
        names = LINUX_FOLDERS
    return [Path(name).expanduser() for name in names]


def installed() -> list[Path]:
    """Every plugin found in the usual folders, sorted by name and without duplicates."""
    found: dict[str, Path] = {}
    for folder in plugin_folders():
        try:
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.suffix.lower() in SUFFIXES:
                    found.setdefault(entry.name.lower(), entry)
        except OSError:
            continue  # an unreadable folder is not worth stopping for
    return sorted(found.values(), key=lambda p: p.name.lower())


def plugin_name(path: str | Path) -> str:
    """The name to show for a plugin file, without its extension."""
    return Path(path).stem


def load(path: str, state: str = "") -> object | None:
    """Open a plugin, putting back the settings saved with the cue. None if it will not open.

    Loading a plugin can take a moment, so this is done when a cue is prepared rather
    than while audio is playing.
    """
    try:
        from pedalboard import load_plugin
    except Exception:
        return None
    try:
        plugin = load_plugin(str(path))
    except Exception:
        return None  # missing, broken, or an instrument rather than an effect
    if state:
        try:
            plugin.raw_state = base64.b64decode(state.encode("ascii"))
        except Exception:
            pass  # settings from another version of the plugin; its defaults will do
    return plugin


def save_state(plugin: object) -> str:
    """The plugin's own settings, as text that can live in the show file."""
    try:
        raw = plugin.raw_state  # type: ignore[attr-defined]
    except Exception:
        return ""
    if not raw:
        return ""
    return base64.b64encode(bytes(raw)).decode("ascii")


def has_editor(plugin: object) -> bool:
    return hasattr(plugin, "show_editor")


class VstEffect:
    """One plugin in a cue's effect chain.

    A plugin that will not open is skipped, so a show still runs on a machine where the
    plugin is not installed.
    """

    def __init__(self, params: dict, rate: int, channels: int) -> None:
        self.path = str(params.get("path", ""))
        self.rate = int(rate)
        self.channels = int(channels)
        self.plugin = load(self.path, str(params.get("state", ""))) if self.path else None
        self.bypass = bool(params.get("bypass", False))

    def flat(self) -> bool:
        return self.plugin is None or self.bypass

    def process(self, block: np.ndarray) -> np.ndarray:
        if self.plugin is None or self.bypass:
            return block
        try:
            # pedalboard works in (channels, frames); reset off keeps tails between blocks.
            out = self.plugin.process(np.ascontiguousarray(block.T), self.rate, reset=False)
        except Exception:
            self.plugin = None  # it failed mid-show; carry on dry rather than stopping
            return block
        shaped = np.ascontiguousarray(np.asarray(out).T, dtype=np.float32)
        if shaped.shape == block.shape:
            return shaped
        # A plugin may hand back fewer frames than it was given while it fills up.
        fixed = np.zeros_like(block)
        usable = min(len(shaped), len(block))
        if usable and shaped.ndim == 2 and shaped.shape[1] == block.shape[1]:
            fixed[:usable] = shaped[:usable]
        return fixed
