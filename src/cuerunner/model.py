"""Show data model: cues, soundboard pads, and show files saved as JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT_VERSION = 2
BANK_SIZE = 16
BANK_COUNT = 4
PAD_COUNT = BANK_SIZE * BANK_COUNT


@dataclass
class Cue:
    number: str
    name: str
    kind: str = "audio"  # audio, wait, memo (only audio is playable so far)
    file: str = ""
    fade_in: float = 0.0  # seconds
    fade_out: float = 0.0
    hotkey: str = ""
    duration_ms: int = 0  # filled in from the audio file when it is first probed
    loop: bool = False
    then: str = "none"  # "none", or "follow" to start the next cue when this one ends


@dataclass
class Pad:
    name: str = ""
    file: str = ""


def _empty_pads() -> list[Pad]:
    return [Pad() for _ in range(PAD_COUNT)]


@dataclass
class Show:
    name: str = "Untitled Show"
    cues: list[Cue] = field(default_factory=list)
    pads: list[Pad] = field(default_factory=_empty_pads)

    def next_number(self) -> str:
        numbers = []
        for cue in self.cues:
            try:
                numbers.append(int(float(cue.number)))
            except ValueError:
                continue
        return str(max(numbers, default=0) + 1)

    def save(self, path: Path) -> None:
        data = {
            "format_version": FORMAT_VERSION,
            "name": self.name,
            "cues": [asdict(c) for c in self.cues],
            "pads": [asdict(p) for p in self.pads],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Show":
        data = json.loads(path.read_text(encoding="utf-8"))
        cue_fields = set(Cue.__dataclass_fields__)
        pad_fields = set(Pad.__dataclass_fields__)
        cues = [Cue(**{k: v for k, v in c.items() if k in cue_fields}) for c in data.get("cues", [])]
        pads = [Pad(**{k: v for k, v in p.items() if k in pad_fields}) for p in data.get("pads", [])]
        pads = (pads + _empty_pads())[:PAD_COUNT]
        return cls(name=data.get("name", "Untitled Show"), cues=cues, pads=pads)
