# CueRunner

Cue-based show control for Windows and macOS. Working name; not yet final.

## Status

Early scaffold (milestone 1 in progress):

- Cue list with add, remove, and save/load of show files (JSON)
- Audio cue playback with GO and Stop
- Not yet implemented: fades, hotkeys, routing, video, MIDI/OSC, scripting

## Development

```
pip install -r requirements.txt
python run_cuerunner.py
python -m pytest
```

## Downloads

Every push builds both platforms through GitHub Actions:

- Windows: `CueRunner.exe` (artifact on the workflow run)
- macOS: `CueRunner.dmg` (artifact on the workflow run)

Pushing a tag such as `v0.1.0` also publishes both files to a GitHub Release.

Builds are unsigned, so Windows SmartScreen and macOS Gatekeeper will warn on first launch.
