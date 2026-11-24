# CueRunner

Cue-based show control for Windows and macOS. Working name.

## What it does

- **Cue list:** audio cues with fade in and out, loops, a playhead, and a running clock. Hotkeys per cue.
- **Follow and continue:** a cue can start the next one when it ends (Follow), or start it at the same time (Continue).
- **Transport:** GO (Space), STOP, PAUSE, and PANIC (Esc), which stops everything with no fade.
- **Soundboard:** 64 pads across 4 banks. Right-click a pad to assign a file.
- **Countdown timer** for interval breaks.
- **Output device:** pick the sound card under Audio → Output Device.
- **Talkback mic:** auto-gain, gate, compressor, limiter, and feedback suppression. The music lowers while the mic is open. Turn it on under Audio → Talkback Mic On.
- **Venue radio:** shuffled music from a folder, with an ident or promo every N tracks. Set it up under Audio → Venue Radio Settings.
- **Multitrack recorder:** up to 48 tracks of 24-bit WAV. The file header refreshes every second, so a power cut loses at most about a second. Audio → Record Start.
- **Phone remote:** a PIN-protected page on your Wi-Fi with GO, STOP, PAUSE, PANIC, and live status. Audio → Start Phone Remote.
- **MIDI:** notes and control changes trigger the transport, a cue, or a pad. Audio → Start MIDI Input.
- **In-app updates:** Help → Check for Updates downloads the latest release and restarts to apply it.

## Known limits

- **Console control:** Yamaha, Allen & Heath, and Q-SYS control are not built yet. They need each manufacturer's protocol documentation and hardware to test against.
- **Spotify:** not built. It needs a developer app and a Premium account, and playback runs in a browser.
- **Stream Deck:** not built as a plugin. The phone remote's HTTP endpoint can be called from a Stream Deck web-request action, using the `X-PIN` header.
- **MIDI mapping:** the defaults are notes 36 to 39 (GO, STOP, PAUSE, PANIC). They can't be changed in the app yet.
- **Audio files:** each file is decoded fully into memory when it is first loaded. Very long files use a lot of memory.
- **File types:** WAV, AIFF, FLAC, OGG, and MP3 are read by the audio library. M4A and AAC are not supported.
- **Routing:** mono clips go to outputs 1 and 2, and stereo clips go to 1 and 2 as left and right. Routing for more outputs is not built into the app yet.
- **ASIO and Dante:** these depend on the audio library being built with ASIO support, and on your interface's driver. Check on your own machine.
- **Untested on hardware:** the logic is covered by tests, but the window, audio output, mic, recorder, and remote have not been run on real Windows or macOS hardware.

## Development

```
pip install -r requirements.txt
python run_cuerunner.py
python -m pytest
```

## Downloads

Pushing a tag such as `v0.1.2` publishes both builds to a GitHub Release:

- `CueRunner.exe` for Windows
- `CueRunner.dmg` for macOS

Builds are unsigned, so Windows SmartScreen and macOS Gatekeeper will warn on first launch.
