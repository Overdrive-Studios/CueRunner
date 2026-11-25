# CueRunner

**Show control for live sound, lighting and AV, on Windows and macOS.**

[Download for Windows](https://github.com/Overdrive-Studios/CueRunner/releases/latest/download/CueRunner.exe) · [Download for Mac](https://github.com/Overdrive-Studios/CueRunner/releases/latest/download/CueRunner.dmg) · [Website](https://overdrive-studios.github.io/CueRunner/)

## Features

- **Cue list:** audio cues with fades, loops, follow and continue, hotkeys and a countdown.
- **Soundboard:** 64 pads in four banks. Pads play straight away and can be routed to any outputs.
- **Venue radio:** shuffled music from a folder, with idents between tracks.
- **Talkback:** mic chain with auto gain, gate, compressor, limiter and feedback suppression. Music ducks while the mic is open.
- **Recorder:** up to 48 tracks of 24-bit WAV.
- **Consoles:** Allen & Heath SQ, dLive, Avantis and Qu-5/6/7 over the network.
- **Control:** MIDI, a PIN-protected phone remote and a Stream Deck plugin.
- **Spotify:** play Spotify tracks and playlists as cues (Spotify Premium required).
- **Audio:** Dante, ASIO and USB audio interfaces.
- **Updates:** Help > Check for Updates installs new versions for you.

## Install

**Mac**

1. Open `CueRunner.dmg` and drag CueRunner into Applications.
2. The first time, right-click CueRunner, choose **Open**, then click **Open** again.

**Windows**

1. Run `CueRunner.exe`.
2. If Windows shows a warning, choose **More info**, then **Run anyway**.

Full instructions and help are on the [website](https://overdrive-studios.github.io/CueRunner/help.html).

## Known limits

- Console control is built from manufacturer documents. Test it on your own desk before relying on it.
- Older Qu-16/24/32 consoles, Yamaha and Q-SYS are not supported yet.

## Developing

See [DEVELOPING.md](DEVELOPING.md) for the build, the tests and the technical notes.

```
pip install -r requirements.txt
python run_cuerunner.py
python -m pytest
```

## Licence

CueRunner is released under the GNU General Public License v3 — see `LICENSE`.

It hosts VST3 and Audio Unit plugins through
[pedalboard](https://github.com/spotify/pedalboard), which is GPLv3 because it builds on
JUCE. Shipping that inside CueRunner means CueRunner is GPLv3 too: anyone given a copy of
the app is entitled to its source and may change and pass it on.
