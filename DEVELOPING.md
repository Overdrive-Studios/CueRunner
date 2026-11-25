# CueRunner: developer notes

The technical details behind CueRunner. For the download and an overview, see the [README](README.md).

## What it does

- **Cue list:** audio cues with fade in and out, loops, a playhead, and a running clock. Hotkeys per cue.
- **Follow and continue:** a cue can start the next one when it ends (Follow), or start it at the same time (Continue).
- **Transport:** GO (Space), STOP, PAUSE, and PANIC (Esc), which stops everything with no fade.
- **Soundboard:** 64 pads across 4 banks. Right-click a pad to assign a file.
- **Countdown timer** for interval breaks.
- **Output device:** pick the sound card under Audio → Output Device.
- **Talkback mic:** auto-gain, gate, compressor, limiter, and feedback suppression. The music lowers while the mic is open, by the amount set under Settings → General. Turn the mic on under Audio → Talkback Mic On.
- **Venue radio:** shuffled music from a folder, with an ident or promo every N tracks. Set it up under Audio → Venue Radio Settings.
- **Multitrack recorder:** up to 48 tracks of 24-bit WAV. The file header refreshes every second, so a power cut loses at most about a second. Audio → Record Start.
- **Phone remote:** a PIN-protected page on your Wi-Fi with GO, STOP, PAUSE, PANIC, and live status. Audio → Start Phone Remote. The port defaults to 8080 and can be changed under Settings → General.
- **MIDI:** notes and control changes trigger the transport, a cue, or a pad. Audio → Start MIDI Input. Edit the key table under Audio → MIDI Mapping.
- **In-app updates:** Help → Check for Updates downloads the latest release and restarts to apply it.

## Known limits

- **Allen & Heath SQ (standard):** built from the SQ MIDI Protocol Issue 5 document. Set the console's IP and family under Audio → Allen & Heath Console. Scene recall, mute, and level are supported, and console cues use commands such as `scene 7`, `mute 1`, or `level 1 -6`. Test on the desk before relying on it.
- **Allen & Heath dLive:** built from the dLive MIDI over TCP spec (firmware V1.9 and later, which the spec says also covers later firmware). Scene recall, mute, and a raw fader value (0 to 127). The spec has no dB table, so dLive levels are not in dB. Test on the desk before relying on it.
- **Allen & Heath Avantis (V2.0 and later):** built from its MIDI over TCP page. Scene recall, mute for inputs 1 to 96, and raw fader value. Its level table isn't in the app yet, so levels are raw.
- **Allen & Heath Qu-5/6/7 (V1.1 and later):** built from the Qu-5/6/7 MIDI protocol. Uses the SQ command formats, with scenes 1 to 128. Older Qu-16/24/32 consoles are not covered by these commands.
- **Allen & Heath SQ+:** not built. The manufacturer says its protocol is almost identical to the SQ's but hasn't published the document yet.
- **Yamaha and Q-SYS:** not built yet. They need each manufacturer's protocol documentation and hardware to test against.
- **Spotify:** needs a Spotify developer app (Client ID) and a Premium account. Set up under Audio → Spotify. Add cues with Add Spotify Cue. Playback runs on a Spotify device that is already open on the computer or speaker you choose.
- **Stream Deck:** not built as a plugin. The phone remote's HTTP endpoint can be called from a Stream Deck web-request action, using the `X-PIN` header.
- **MIDI mapping:** the defaults are notes 36 to 39 (GO, STOP, PAUSE, PANIC). Change them under Audio → MIDI Mapping. The mapping is saved with the show file.
- **Audio files:** each file is decoded fully into memory when it is first loaded. Very long files use a lot of memory.
- **File types:** WAV, AIFF, FLAC, OGG, and MP3 are read by the audio library. M4A and AAC are not supported.
- **Allen & Heath USB (SQ and Qu):** install Allen & Heath's ASIO driver from allen-heath.com before plugging in. With the ASIO driver the desk's full USB interface shows up (32 in and 32 out), so pick the ASIO device in Output Device. The WDM driver only gives USB channels 1 and 2 on SQ. Choose 48 kHz in the sample rate step: SQ and Qu give 32 channels at 48 kHz, and fewer at 96 kHz. Which channels the desk sends and receives is set in the desk's USB patching.
- **Routing:** each audio cue has its own Routing grid in the cue editor (Edit Cue → Outputs → Routing...). Tick which outputs each input goes to. Clear it to use the default, where mono goes to outputs 1 and 2 and stereo goes to 1 and 2. Routing covers 8 inputs and every output the device has. Pads and venue radio still use the default routing.
- **ASIO and Dante:** ASIO is switched on at startup, and ASIO devices are listed first in every device menu with their host API shown, such as `Dante Virtual Soundcard [ASIO] (64 ch)`. Dante Virtual Soundcard only shows that name in ASIO mode, so set it to ASIO in its own settings. Set `CUERUNNER_NO_ASIO=1` to turn ASIO off if a driver causes crashes. Networked Dante hardware is reached through Dante Virtual Soundcard or Dante Via on the same computer, not directly. Not yet tested on real Dante hardware.
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
