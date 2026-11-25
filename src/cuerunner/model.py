"""Show data model: cues, soundboard pads, venue radio settings, and show files saved as JSON."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT_VERSION = 3
BANK_SIZE = 16
BANK_COUNT = 4
PAD_COUNT = BANK_SIZE * BANK_COUNT

# MIDI key to command. Keys are "note:<n>" or "cc:<n>"; commands are described in midi.py.
DEFAULT_MIDI_MAP = {
    "note:36": "go",
    "note:37": "stop",
    "note:38": "pause",
    "note:39": "panic",
}
DEFAULT_DUCK_LEVEL = 0.3
DEFAULT_REMOTE_PORT = 8080


@dataclass
class Cue:
    number: str
    name: str
    kind: str = "audio"  # audio, video, console, spotify, wait, memo
    file: str = ""
    fade_in: float = 0.0  # seconds
    fade_out: float = 0.0
    hotkey: str = ""
    duration_ms: int = 0  # filled in from the audio file when it is first probed
    loop: bool = False
    then: str = "none"  # "none", "follow" (start next when this ends), or "continue" (start next now)
    route: list = field(default_factory=list)  # per clip channel, the 1-based outputs it goes to; empty = default
    video_output: str = ""  # video cues: the display, e.g. "screen:1"; see video_out.py


@dataclass
class Pad:
    name: str = ""
    file: str = ""
    route: list = field(default_factory=list)  # per clip channel, the 1-based outputs; empty = default


@dataclass
class RadioSettings:
    music_folder: str = ""
    ident_folder: str = ""
    ident_every: int = 4
    route: list = field(default_factory=list)  # per clip channel, the 1-based outputs; empty = default


BACKUP_FOLDER = "backups"


def _backup(path: Path, keep: int) -> Path | None:
    """Copy the show file into a backups folder beside it before it is overwritten.

    Keeps the newest `keep` backups for that show. A failed backup never blocks the save.
    """
    target = _backup_target(path)
    try:
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(path, target)
        _prune(path, keep)
        return target
    except OSError:
        return None


def _backup_target(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return path.parent / BACKUP_FOLDER / f"{path.stem}-{stamp}{path.suffix}"


def _prune(path: Path, keep: int) -> None:
    """Delete the oldest backups for this show so only the newest `keep` remain."""
    folder = path.parent / BACKUP_FOLDER
    backups = sorted(folder.glob(f"{path.stem}-*{path.suffix}"))
    for stale in backups[:-keep] if keep > 0 else backups:
        stale.unlink()


def _empty_pads() -> list[Pad]:
    return [Pad() for _ in range(PAD_COUNT)]


@dataclass
class Show:
    name: str = "Untitled Show"
    cues: list[Cue] = field(default_factory=list)
    pads: list[Pad] = field(default_factory=_empty_pads)
    radio: RadioSettings = field(default_factory=RadioSettings)
    midi_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MIDI_MAP))
    duck_level: float = DEFAULT_DUCK_LEVEL  # music level while the talkback mic is open, 0 to 1
    remote_port: int = DEFAULT_REMOTE_PORT
    spotify_client_id: str = ""  # from the Spotify developer app; not a secret when using PKCE
    console_host: str = ""  # Allen & Heath console IP address
    console_family: str = "sq"  # "sq" or "dlive"
    video_default_output: str = "screen:0"  # display used for new video cues, e.g. "screen:1"

    def next_number(self) -> str:
        numbers = []
        for cue in self.cues:
            try:
                numbers.append(int(float(cue.number)))
            except ValueError:
                continue
        return str(max(numbers, default=0) + 1)

    def save(self, path: Path, keep_backups: int = 20) -> None:
        if path.exists():
            _backup(path, keep_backups)
        path.write_text(self.to_json(), encoding="utf-8")

    def snapshot(self, path: Path, keep: int = 20) -> Path | None:
        """Write the current state into the backups folder without touching the show file."""
        folder = path.parent / BACKUP_FOLDER
        target = _backup_target(path)
        try:
            folder.mkdir(exist_ok=True)
            target.write_text(self.to_json(), encoding="utf-8")
            _prune(path, keep)
            return target
        except OSError:
            return None

    def to_json(self) -> str:
        data = {
            "format_version": FORMAT_VERSION,
            "name": self.name,
            "cues": [asdict(c) for c in self.cues],
            "pads": [asdict(p) for p in self.pads],
            "radio": asdict(self.radio),
            "midi_map": dict(self.midi_map),
            "duck_level": self.duck_level,
            "remote_port": self.remote_port,
            "spotify_client_id": self.spotify_client_id,
            "console_host": self.console_host,
            "console_family": self.console_family,
            "video_default_output": self.video_default_output,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Show":
        data = json.loads(path.read_text(encoding="utf-8"))
        cue_fields = set(Cue.__dataclass_fields__)
        pad_fields = set(Pad.__dataclass_fields__)
        radio_fields = set(RadioSettings.__dataclass_fields__)
        cues = [Cue(**{k: v for k, v in c.items() if k in cue_fields}) for c in data.get("cues", [])]
        pads = [Pad(**{k: v for k, v in p.items() if k in pad_fields}) for p in data.get("pads", [])]
        pads = (pads + _empty_pads())[:PAD_COUNT]
        radio = RadioSettings(**{k: v for k, v in data.get("radio", {}).items() if k in radio_fields})
        midi_map = data.get("midi_map", DEFAULT_MIDI_MAP)
        duck_level = min(1.0, max(0.0, float(data.get("duck_level", DEFAULT_DUCK_LEVEL))))
        return cls(
            name=data.get("name", "Untitled Show"),
            cues=cues,
            pads=pads,
            radio=radio,
            midi_map=dict(midi_map),
            duck_level=duck_level,
            remote_port=int(data.get("remote_port", DEFAULT_REMOTE_PORT)),
            spotify_client_id=str(data.get("spotify_client_id", "")),
            console_host=str(data.get("console_host", "")),
            console_family=str(data.get("console_family", "sq")),
            video_default_output=str(data.get("video_default_output", "screen:0")),
        )
