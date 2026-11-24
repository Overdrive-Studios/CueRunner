"""Show data model: cues and show files saved as JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT_VERSION = 1


@dataclass
class Cue:
    number: str
    name: str
    kind: str = "audio"  # audio, wait, memo (only audio is playable so far)
    file: str = ""
    fade_in: float = 0.0  # seconds; fades are not yet applied during playback
    fade_out: float = 0.0
    hotkey: str = ""
    duration_ms: int = 0  # filled in from the audio file when it is first probed
    loop: bool = False


@dataclass
class Show:
    name: str = "Untitled Show"
    cues: list[Cue] = field(default_factory=list)

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
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Show":
        data = json.loads(path.read_text(encoding="utf-8"))
        known = set(Cue.__dataclass_fields__)
        cues = [Cue(**{k: v for k, v in c.items() if k in known}) for c in data.get("cues", [])]
        return cls(name=data.get("name", "Untitled Show"), cues=cues)
