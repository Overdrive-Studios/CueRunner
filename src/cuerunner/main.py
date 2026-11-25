"""CueRunner main window: transport, cue list, soundboard, countdown, follow and continue cues, updates.

Playback runs on cuerunner.engine. Cues and soundboard pads are voices in one mixer, so
they can overlap, and the mixer is fed to the sound card by audio_io.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cuerunner import allenheath, console_ui, launcher, settings_ui, spotify, spotify_ui, updater, video_out
from cuerunner.devices import DeviceInfo, label as devices_label
from cuerunner.countdown import Countdown
from cuerunner.dsp import MicChain
from cuerunner.feedback import FeedbackSuppressor
from cuerunner.engine import (
    Clip,
    LiveVoice,
    Mixer,
    Voice,
    default_matrix,
    load_clip,
    probe_duration_ms,
    route_matrix,
)
from cuerunner.model import Cue, Pad, RadioSettings, Show
from cuerunner.playlist import Playlist, scan_audio
import queue

from cuerunner.midi import MidiInput
from cuerunner.recorder import MAX_TRACKS, Recorder
from cuerunner.remote import RemoteServer, RemoteState, lan_ip
from cuerunner.soundboard import Soundboard
from cuerunner.theme import apply_dark_theme
from cuerunner.timecode import format_clock, format_duration, format_hms
from cuerunner.version import __version__

BACKUP_INTERVAL_MS = 15 * 60 * 1000  # automatic show snapshot every 15 minutes
COLUMNS = ["#", "Name", "Length", "Fade In", "Fade Out", "Loop", "Then", "Progress", "Remaining", "Hotkey", "File"]
PROGRESS_COL = 7
REMAINING_COL = 8
NAME_COL = 1
MAX_CHANNELS = 64

SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.flac *.ogg);;All files (*)"
VIDEO_FILTER = "Video files (*.mp4 *.mov *.m4v *.avi *.mkv);;All files (*)"
THEN_LABELS = {"none": "", "follow": "Follow", "continue": "Continue"}

PLAYING_COLOUR = QColor(30, 84, 48)
PLAYHEAD_MARK = "▶"


class _DownloadWorker(QThread):
    progress = Signal(int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, dest: Path, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest = dest

    def run(self) -> None:
        try:
            path = updater.download(
                self._url, self._dest, progress=self.progress.emit, cancelled=self.isInterruptionRequested
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if path is None:  # cancelled by the user: nothing to report
            return
        self.done.emit(str(path))


class RoutingDialog(QDialog):
    """Grid of input channels against outputs. Tick an output to send that input there.

    Clearing every box (or leaving them all empty) uses the default routing.
    """

    INPUTS = 8

    def __init__(self, route: list[list[int]], out_channels: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Routing")
        self.resize(min(900, 80 + 34 * out_channels), 360)
        self._out = out_channels
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Tick the outputs each input goes to. Clear all boxes for the default routing. "
            "Stereo files use inputs 1 and 2."
        ))
        self.table = QTableWidget(self.INPUTS, out_channels)
        self.table.setVerticalHeaderLabels([f"In {i + 1}" for i in range(self.INPUTS)])
        self.table.setHorizontalHeaderLabels([str(o + 1) for o in range(out_channels)])
        self.table.horizontalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setDefaultSectionSize(26)
        for r in range(self.INPUTS):
            for c in range(out_channels):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                on = r < len(route) and (c + 1) in route[r]
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                self.table.setItem(r, c, item)
        layout.addWidget(self.table)

        presets = QHBoxLayout()
        stereo = QPushButton("Stereo 1+2")
        stereo.clicked.connect(lambda: self._preset([[1], [2]]))
        mono = QPushButton("Mono to 1+2")
        mono.clicked.connect(lambda: self._preset([[1, 2]]))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._preset([]))
        for button in (stereo, mono, clear):
            presets.addWidget(button)
        presets.addStretch(1)
        layout.addLayout(presets)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _preset(self, route: list[list[int]]) -> None:
        for r in range(self.INPUTS):
            for c in range(self._out):
                on = r < len(route) and (c + 1) in route[r]
                self.table.item(r, c).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def result_route(self) -> list[list[int]]:
        route: list[list[int]] = []
        for r in range(self.INPUTS):
            outs = [c + 1 for c in range(self._out) if self.table.item(r, c).checkState() == Qt.CheckState.Checked]
            route.append(outs)
        while route and not route[-1]:
            route.pop()
        if all(not group for group in route):
            return []
        return route


class CueDialog(QDialog):
    """Edit a cue's name, fades, loop, follow/continue, hotkey and outputs."""

    def __init__(self, cue: Cue, parent=None, out_channels: int = MAX_CHANNELS,
                 video_choices: list[tuple[str, str]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cue {cue.number}")
        self._out_channels = out_channels

        self.name_edit = QLineEdit(cue.name)
        self.fade_in = self._fade_spin(cue.fade_in)
        self.fade_out = self._fade_spin(cue.fade_out)
        self.loop_box = QCheckBox("Loop until stopped")
        self.loop_box.setChecked(cue.loop)
        self.then_box = QComboBox()
        self.then_box.addItem("Nothing", "none")
        self.then_box.addItem("Follow: start the next cue when this one ends", "follow")
        self.then_box.addItem("Continue: start the next cue at the same time", "continue")
        self.then_box.setCurrentIndex(max(0, self.then_box.findData(cue.then)))
        self.hotkey_edit = QKeySequenceEdit(QKeySequence(cue.hotkey))
        self._route: list[list[int]] = [list(group) for group in cue.route]
        self._cue_kind = cue.kind
        self.display_box = QComboBox()
        for label, value in video_choices or []:
            self.display_box.addItem(label, value)
        current = max(0, self.display_box.findData(cue.video_output))
        self.display_box.setCurrentIndex(current)
        self.route_label = QLabel(self._route_summary())
        self.route_button = QPushButton("Routing...")
        self.route_button.clicked.connect(self._edit_routing)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Fade in", self.fade_in)
        form.addRow("Fade out", self.fade_out)
        form.addRow("", self.loop_box)
        form.addRow("Then", self.then_box)
        form.addRow("Hotkey", self.hotkey_edit)
        outputs_row = QHBoxLayout()
        outputs_row.addWidget(self.route_label, 1)
        outputs_row.addWidget(self.route_button)
        form.addRow("Outputs", outputs_row)
        if self._cue_kind in ("video", "camera"):
            form.addRow("Display", self.display_box)
        form.addRow(buttons)

    def _route_summary(self) -> str:
        if not self._route:
            return "Default"
        return "; ".join(
            f"In {i + 1} → " + (", ".join(str(o) for o in group) if group else "none")
            for i, group in enumerate(self._route)
        )

    def _edit_routing(self) -> None:
        dialog = RoutingDialog(self._route, self._out_channels, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._route = dialog.result_route()
            self.route_label.setText(self._route_summary())

    @staticmethod
    def _fade_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 600.0)
        spin.setDecimals(1)
        spin.setSuffix(" s")
        spin.setValue(value)
        return spin

    def apply_to(self, cue: Cue) -> None:
        cue.name = self.name_edit.text().strip() or cue.name
        cue.fade_in = self.fade_in.value()
        cue.fade_out = self.fade_out.value()
        cue.loop = self.loop_box.isChecked()
        cue.then = self.then_box.currentData()
        cue.hotkey = self.hotkey_edit.keySequence().toString()
        cue.route = [list(group) for group in self._route]
        if cue.kind in ("video", "camera") and self.display_box.count():
            cue.video_output = self.display_box.currentData()


class MidiMappingDialog(QDialog):
    """Table of MIDI keys and the commands they run. Validation happens in midi.mapping_from_rows."""

    def __init__(self, mapping: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIDI Mapping")
        self.resize(420, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Key: note:<number> or cc:<number>. Command: go, stop, pause, panic, cue:<number> or pad:<number>."))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["MIDI key", "Command"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for key, command in sorted(mapping.items()):
            self._add_row(key, command)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_row("", ""))
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._map: dict[str, str] = {}

    def _add_row(self, key: str, command: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(command))

    def _remove_selected(self) -> None:
        for index in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(index)

    def _try_accept(self) -> None:
        from cuerunner.midi import mapping_from_rows

        rows = []
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            command_item = self.table.item(row, 1)
            rows.append((key_item.text() if key_item else "", command_item.text() if command_item else ""))
        mapping, problems = mapping_from_rows(rows)
        if problems:
            QMessageBox.warning(self, "MIDI Mapping", "\n".join(problems))
            return
        self._map = mapping
        self.accept()

    def result_map(self) -> dict[str, str]:
        return dict(self._map)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.show_model = Show()
        self.show_path: Path | None = None
        self._shortcuts: list[QShortcut] = []
        self._clips: dict[str, Clip] = {}
        self._cue_voices: list[Voice] = []
        self._video = video_out.VideoOutputs(self)
        self._playing_cue: Cue | None = None
        self._row_bars: list[QProgressBar] = []
        self.mixer: Mixer | None = None
        self.output = None
        self._update_worker = None
        self._radio: Playlist | None = None
        self._radio_voice: Voice | None = None
        self._mic_input = None
        self._mic_chain: MicChain | None = None
        self._mic_voice: LiveVoice | None = None
        self._mic_device = None
        self._recorder: Recorder | None = None
        self._recorder_input = None
        self._record_device = None
        self._record_tracks = MAX_TRACKS
        self._remote: RemoteServer | None = None
        self._remote_state: RemoteState | None = None
        self._midi: MidiInput | None = None
        self._midi_commands: queue.Queue[str] = queue.Queue()
        self._spotify: spotify.SpotifyClient | None = None
        self._spotify_playing = False
        self._console: allenheath.MidiTcpSender | None = None

        self._build_toolbar()
        self._build_menus()
        self._build_central()

        go_key = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        go_key.activated.connect(self.go)
        panic_key = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        panic_key.activated.connect(self.panic)

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(100)
        self.clock_timer.timeout.connect(self._tick)
        self.clock_timer.start()

        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(BACKUP_INTERVAL_MS)
        self.backup_timer.timeout.connect(self._auto_backup)
        self.backup_timer.start()

        self._open_default_output()
        self._refresh_title()
        self._fit_to_screen()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._video.close()
        super().closeEvent(event)

    def _fit_to_screen(self) -> None:
        """Size the window to the screen it opens on so no controls run off the edge."""
        self.setMinimumSize(900, 620)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 820)
            return
        area = screen.availableGeometry()
        width = min(1200, area.width() - 40)
        height = min(820, area.height() - 60)
        self.resize(max(900, width), max(620, height))
        frame = self.frameGeometry()
        frame.moveCenter(area.center())
        self.move(frame.topLeft())

    # --- Audio output -------------------------------------------------

    def _open_default_output(self) -> None:
        try:
            from cuerunner import audio_io

            self._open_output(audio_io.default_output())
        except Exception as exc:
            QMessageBox.critical(self, "Audio output", f"No audio output could be opened:\n{exc}")

    def _open_output(self, device) -> None:
        from cuerunner import audio_io

        channels = min(device.channels, MAX_CHANNELS)
        mixer = Mixer(rate=int(device.samplerate), out_channels=channels)
        output = audio_io.AudioOutput(mixer, device)
        output.start()
        if self.output is not None:
            self.output.close()
        self.output = output
        self.mixer = mixer
        self._clips = {}
        self._cue_voices = []
        self._playing_cue = None

    def choose_output(self) -> None:
        try:
            from cuerunner import audio_io

            devices = audio_io.output_devices()
        except Exception as exc:
            QMessageBox.critical(self, "Audio output", f"Could not list audio devices:\n{exc}")
            return
        if not devices:
            QMessageBox.information(self, "Audio output", "No audio output devices were found.")
            return
        names = [devices_label(d) for d in devices]
        name, ok = QInputDialog.getItem(self, "Output device", "Choose the output device:", names, 0, False)
        if not ok:
            return
        device = devices[names.index(name)]
        try:
            from cuerunner import audio_io
            from cuerunner.devices import rate_choices

            channels = min(device.channels, MAX_CHANNELS)
            rates = rate_choices(audio_io.supported_rates(device.index, channels))
        except Exception as exc:
            QMessageBox.critical(self, "Audio output", f"Could not check that device's sample rates:\n{exc}")
            return
        if not rates:
            QMessageBox.warning(self, "Audio output", "That device did not accept any of the usual sample rates.")
            return
        labels = [f"{rate} Hz" + ("  (32 channels on SQ and Qu)" if rate == 48000 else "") for rate in rates]
        picked, ok = QInputDialog.getItem(
            self,
            "Sample rate",
            "Sample rate. SQ and Qu USB give their full channel count at 48 kHz; 96 kHz gives fewer channels.",
            labels,
            0,
            False,
        )
        if not ok:
            return
        rate = rates[labels.index(picked)]
        device = DeviceInfo(device.index, device.name, device.channels, float(rate), device.host_api)
        try:
            self._open_output(device)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Audio output",
                f"Could not open that device:\n{exc}\n\n"
                "For an ASIO device (such as Dante Virtual Soundcard), check that the driver's sample rate "
                "and channel count match the device, and that no other program is holding it.",
            )

    def _clip(self, path: str) -> Clip:
        if self.mixer is None:
            raise RuntimeError("No audio output is open.")
        if path not in self._clips:
            self._clips[path] = load_clip(path, self.mixer.rate)
        return self._clips[path]

    # --- UI setup -----------------------------------------------------

    def _build_toolbar(self) -> None:
        """Top bar: file actions on the left, Pads and Settings on the right. Cue commands live in the Cues menu."""
        bar = QToolBar("File", self)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        bar.addAction("New", self.new_show)
        bar.addAction("Open...", self.open_show)
        bar.addAction("Save", self.save_show)
        bar.addSeparator()
        add_button = QToolButton()
        add_button.setText("Add Cue")
        add_button.setToolTip("Add an audio, video, Spotify or console cue")
        add_menu = QMenu(add_button)
        add_menu.addAction("Audio Cue...", self.add_audio_cue)
        add_menu.addAction("Video Cue...", self.add_video_cue)
        add_menu.addAction("Camera Cue...", self.add_camera_cue)
        add_menu.addAction("Spotify Cue...", self.add_spotify_cue)
        add_menu.addAction("Console Cue...", self.add_console_cue)
        add_button.setMenu(add_menu)
        add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_button.setStyleSheet("QToolButton { padding: 4px 10px; }")
        bar.addWidget(add_button)
        bar.addSeparator()
        self.pads_action = bar.addAction("Pads")
        self.pads_action.setCheckable(True)
        self.pads_action.setChecked(True)
        self.pads_action.setToolTip("Show or hide the soundboard pads")
        self.pads_action.toggled.connect(self._toggle_pads)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        settings_action = bar.addAction("Settings...", self.open_settings)
        settings_action.setToolTip("Audio, video, console and other settings")

    def _camera_for(self, cue: Cue) -> str:
        """The camera name for a camera cue, looked up from its video input patch."""
        for patch in self.show_model.video_inputs:
            if patch.get("name") == cue.video_input:
                return str(patch.get("device", ""))
        raise RuntimeError(f"The video input \"{cue.video_input}\" no longer exists.")

    def _toggle_pads(self, shown: bool) -> None:
        self.soundboard.setVisible(shown)

    def _build_menus(self) -> None:
        cues_menu = self.menuBar().addMenu("Cues")
        cues_menu.addAction("Add Audio Cue...", self.add_audio_cue)
        cues_menu.addAction("Add Video Cue...", self.add_video_cue)
        cues_menu.addAction("Add Camera Cue...", self.add_camera_cue)
        cues_menu.addAction("Add Spotify Cue...", self.add_spotify_cue)
        cues_menu.addAction("Add Console Cue...", self.add_console_cue)
        cues_menu.addSeparator()
        cues_menu.addAction("Edit Cue...", self.edit_selected)
        cues_menu.addAction("Remove Cue", self.remove_cue)
        audio_menu = self.menuBar().addMenu("Audio")
        audio_menu.addAction("Output Device...", self.choose_output)
        audio_menu.addAction("Choose Mic Input...", self.choose_mic)
        audio_menu.addSeparator()
        talkback = audio_menu.addMenu("Talkback")
        talkback.addAction("Mic On", self.start_talkback)
        talkback.addAction("Mic Off", self.stop_talkback)
        recording = audio_menu.addMenu("Recording")
        recording.addAction("Start", self.start_recording)
        recording.addAction("Stop", self.stop_recording)
        recording.addSeparator()
        recording.addAction("Input Settings...", self.choose_record_input)
        radio = audio_menu.addMenu("Venue Radio")
        radio.addAction("Start", self.start_radio)
        radio.addAction("Stop", self.stop_radio)
        radio.addSeparator()
        radio.addAction("Settings...", self.radio_settings)
        radio.addAction("Routing...", self.radio_routing)
        audio_menu.addSeparator()
        settings_action = audio_menu.addAction("Settings...", self.open_settings)
        settings_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Preferences))
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)

        control_menu = self.menuBar().addMenu("Control")
        remote = control_menu.addMenu("Phone Remote")
        remote.addAction("Start...", self.start_remote)
        remote.addAction("Stop", self.stop_remote)
        midi = control_menu.addMenu("MIDI")
        midi.addAction("Start Input...", self.start_midi)
        midi.addAction("Stop Input", self.stop_midi)
        midi.addSeparator()
        midi.addAction("Mapping...", self.edit_midi_mapping)
        control_menu.addSeparator()
        control_menu.addAction("Allen & Heath Console...", self.open_console)
        control_menu.addAction("Spotify...", self.open_spotify)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("Check for Updates...", self.check_for_updates)
        help_menu.addAction(f"About CueRunner {__version__}", self.about)

    def _status_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("statusTitle")
        return label

    def _status_value(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("statusValue")
        return label

    def _build_central(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)

        status = QHBoxLayout()
        self.trt_label = self._status_value("0:00:00")
        self.track_label = self._status_value(format_clock(0))
        self.remaining_label = self._status_value(format_clock(0))
        self.wall_label = self._status_value("--:--:--")
        status.addWidget(self._status_title("TRT"))
        status.addWidget(self.trt_label)
        status.addSpacing(28)
        status.addWidget(self._status_title("TRACK"))
        status.addWidget(self.track_label)
        status.addSpacing(28)
        status.addWidget(self._status_title("REMAINING"))
        status.addWidget(self.remaining_label)
        status.addStretch(1)
        status.addWidget(self.wall_label)
        layout.addLayout(status)

        transport = QHBoxLayout()
        self.go_button = QPushButton("GO  (Space)")
        self.go_button.setObjectName("go")
        self.go_button.clicked.connect(self.go)
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop)
        self.pause_button = QPushButton("PAUSE")
        self.pause_button.setObjectName("pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.panic_button = QPushButton("PANIC  (Esc)")
        self.panic_button.setObjectName("panic")
        self.panic_button.clicked.connect(self.panic)
        transport.addWidget(self.go_button, 3)
        transport.addWidget(self.stop_button, 2)
        transport.addWidget(self.pause_button, 2)
        transport.addWidget(self.panic_button, 2)
        layout.addLayout(transport)

        self.now_label = QLabel("Standby")
        self.now_label.setObjectName("now")
        layout.addWidget(self.now_label)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(NAME_COL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(PROGRESS_COL, 140)
        self.table.itemDoubleClicked.connect(lambda item: self.edit_row(item.row()))
        self.table.itemSelectionChanged.connect(self._refresh_now_label)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.countdown = Countdown(self)
        self.soundboard = Soundboard(self)
        self.soundboard.padPressed.connect(self._on_pad_pressed)
        self.soundboard.padAssign.connect(self._assign_pad)
        self.soundboard.padClear.connect(self._clear_pad)
        self.soundboard.padRouting.connect(self._pad_routing)
        bottom.addWidget(self.countdown, 1)
        bottom.addWidget(self.soundboard, 3)
        layout.addLayout(bottom)

        self.setCentralWidget(container)

    def _refresh_title(self) -> None:
        name = self.show_path.name if self.show_path else "Untitled"
        self.setWindowTitle(f"CueRunner - {name}")

    def _refresh_table(self) -> None:
        cues = self.show_model.cues
        playhead = self._playhead_row()
        playing_row = self._row_of(self._playing_cue)

        self.table.setRowCount(len(cues))
        self._row_bars = []
        for row, cue in enumerate(cues):
            marker = PLAYHEAD_MARK if row == playhead else ""
            values = [
                f"{marker} {cue.number}".strip(),
                cue.name,
                format_duration(cue.duration_ms),
                f"{cue.fade_in:g} s",
                f"{cue.fade_out:g} s",
                "✓" if cue.loop else "",
                THEN_LABELS.get(cue.then, ""),
                "",
                "",
                cue.hotkey,
                cue.file,
            ]
            for col, value in enumerate(values):
                if col == PROGRESS_COL:
                    continue
                item = QTableWidgetItem(value)
                if row == playing_row:
                    item.setBackground(QBrush(PLAYING_COLOUR))
                if row == playhead:
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            self.table.setCellWidget(row, PROGRESS_COL, bar)
            self._row_bars.append(bar)

        total_ms = sum(c.duration_ms for c in cues)
        self.trt_label.setText(format_hms(total_ms))
        self.soundboard.set_pads(self.show_model.pads)
        self._rebuild_shortcuts()
        self._refresh_now_label()
        self._update_clock()

    def _refresh_now_label(self) -> None:
        cues = self.show_model.cues
        if self._playing_cue is not None:
            self.now_label.setText(f"NOW   {self._playing_cue.name}")
            return
        playhead = self._playhead_row()
        if playhead >= 0:
            self.now_label.setText(f"STANDBY   {cues[playhead].name}")
        else:
            self.now_label.setText("Standby")

    def _rebuild_shortcuts(self) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._shortcuts = []
        for row, cue in enumerate(self.show_model.cues):
            if not cue.hotkey:
                continue
            shortcut = QShortcut(QKeySequence(cue.hotkey), self)
            shortcut.activated.connect(lambda r=row: self.go_row(r))
            self._shortcuts.append(shortcut)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _playhead_row(self) -> int:
        """The row GO will play next: the selected row, or the first cue."""
        if not self.show_model.cues:
            return -1
        row = self._selected_row()
        return row if row >= 0 else 0

    def _row_of(self, cue: Cue | None) -> int:
        if cue is None:
            return -1
        return next((i for i, c in enumerate(self.show_model.cues) if c is cue), -1)

    # --- Show file actions --------------------------------------------

    def new_show(self) -> None:
        self.panic()
        self.show_model = Show()
        self.show_path = None
        self._refresh_table()
        self._refresh_title()

    def open_show(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open show", "", SHOW_FILTER)
        if filename:
            self.open_show_path(Path(filename))

    def open_show_path(self, path: Path) -> None:
        try:
            self.show_model = Show.load(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open show", str(exc))
            return
        self.show_path = path
        launcher.remember_show(path)
        self.panic()
        self._refresh_table()
        self._refresh_title()
        for cue in self.show_model.cues:
            if cue.duration_ms == 0:
                self._probe_duration(cue)

    def _auto_backup(self) -> None:
        """Every 15 minutes, keep a snapshot of the show next to the show file."""
        if self.show_path is not None:
            self.show_model.snapshot(self.show_path)

    def save_show(self) -> None:
        if self.show_path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Save show", "show.cuerunner.json", SHOW_FILTER)
            if not filename:
                return
            self.show_path = Path(filename)
        try:
            self.show_model.save(self.show_path)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save show", str(exc))
            return
        launcher.remember_show(self.show_path)
        self._refresh_title()

    # --- Cue actions --------------------------------------------------

    def add_console_cue(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Add console cue", "Command (scene 7, mute 1, unmute 1, level 1 -6, level 1 -inf):"
        )
        if not ok:
            return
        try:
            allenheath.parse_command(self.show_model.console_family, text)
        except ValueError as exc:
            QMessageBox.warning(self, "Add console cue", str(exc))
            return
        cue = Cue(number=self.show_model.next_number(), name=text.strip(), kind="console", file=text.strip())
        self.show_model.cues.append(cue)
        self._refresh_table()

    def open_console(self) -> None:
        dialog = console_ui.ConsoleDialog(
            self.show_model.console_host, self.show_model.console_family, self._console_for, self
        )
        dialog.exec()
        self.show_model.console_host = dialog.host.text().strip()
        self.show_model.console_family = dialog.current_family()

    def _console_for(self, host: str, family: str | None = None) -> allenheath.MidiTcpSender | None:
        if not host:
            return None
        family = family or self.show_model.console_family
        port = allenheath.port_for(family)
        if self._console is None or self._console._host != host or self._console._port != port:
            if self._console is not None:
                self._console.close()
            self._console = allenheath.MidiTcpSender(host, port)
        return self._console

    def _send_console_cue(self, cue: Cue) -> None:
        sender = self._console_for(self.show_model.console_host)
        if sender is None:
            QMessageBox.warning(self, "Console cue", "Set the console's IP address under Audio → Allen & Heath Console first.")
            return
        try:
            data = allenheath.parse_command(self.show_model.console_family, cue.file, channel=1)
        except ValueError as exc:
            QMessageBox.warning(self, "Console cue", f"{cue.name}\n\n{exc}")
            return
        spotify_ui.run_async(
            self,
            lambda: sender.send(data),
            lambda _r, error: QMessageBox.warning(self, "Console cue", f"{cue.name}\n\n{error}") if error is not None else None,
        )

    def add_spotify_cue(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Spotify cue", "Paste a Spotify link or spotify: URI:")
        if not ok:
            return
        uri = spotify.uri_from_text(text)
        if uri is None:
            QMessageBox.warning(self, "Add Spotify cue", "That doesn't look like a Spotify link or URI.")
            return
        cue = Cue(number=self.show_model.next_number(), name=uri.split(":")[-1], kind="spotify", file=uri)
        self.show_model.cues.append(cue)
        self._refresh_table()

    def open_spotify(self) -> None:
        dialog = spotify_ui.SpotifyDialog(self.show_model.spotify_client_id, self._set_spotify_client, self)
        dialog.exec()
        self.show_model.spotify_client_id = dialog.client_id.text().strip()

    def _set_spotify_client(self, client: spotify.SpotifyClient) -> None:
        self._spotify = client

    def _spotify_client(self) -> spotify.SpotifyClient | None:
        if self._spotify is None:
            self._spotify = spotify_ui.load_client(self.show_model.spotify_client_id)
        return self._spotify

    def _play_spotify_cue(self, cue: Cue) -> None:
        if not cue.file:
            return
        self._spotify_playing = True
        spotify_ui.send(self, self._spotify_client(), cue.name, lambda c: c.play(cue.file))

    def _pause_spotify(self) -> None:
        if self._spotify_playing:
            self._spotify_playing = False
            spotify_ui.send(self, self._spotify_client(), "Spotify pause", lambda c: c.pause())

    def add_audio_cue(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Add audio cues", "", AUDIO_FILTER)
        new_cues = []
        for filename in filenames:
            path = Path(filename)
            cue = Cue(number=self.show_model.next_number(), name=path.stem, kind="audio", file=str(path))
            self.show_model.cues.append(cue)
            new_cues.append(cue)
        if new_cues:
            for cue in new_cues:
                self._probe_duration(cue)
            self._refresh_table()

    def add_video_cue(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Add video cues", "", VIDEO_FILTER)
        if not filenames:
            return
        default_output = self.show_model.video_default_output if QGuiApplication.screens() else ""
        for filename in filenames:
            path = Path(filename)
            self.show_model.cues.append(Cue(
                number=self.show_model.next_number(), name=path.stem, kind="video",
                file=str(path), video_output=default_output,
            ))
        self._refresh_table()

    def add_camera_cue(self) -> None:
        patches = self.show_model.video_inputs
        if not patches:
            QMessageBox.information(
                self, "No video inputs",
                "Add a video input first in Settings > Video > Video Inputs.",
            )
            return
        names = [str(p.get("name", "")) for p in patches]
        name, ok = QInputDialog.getItem(self, "Add camera cue", "Video input:", names, 0, False)
        if not ok or not name:
            return
        default_output = self.show_model.video_default_output if QGuiApplication.screens() else ""
        self.show_model.cues.append(Cue(
            number=self.show_model.next_number(), name=name, kind="camera",
            video_input=name, video_output=default_output,
        ))
        self._refresh_table()

    def _probe_duration(self, cue: Cue) -> None:
        if cue.kind != "audio" or not cue.file:
            return
        try:
            cue.duration_ms = probe_duration_ms(cue.file)
        except Exception:
            cue.duration_ms = 0

    def edit_row(self, row: int) -> None:
        if not 0 <= row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[row]
        dialog = CueDialog(
            cue, self, self.mixer.out_channels if self.mixer is not None else MAX_CHANNELS,
            video_choices=video_out.output_choices(self.show_model.video_routes),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply_to(cue)
            self._refresh_table()
            self.table.selectRow(row)

    def edit_selected(self) -> None:
        row = self._selected_row()
        if row >= 0:
            self.edit_row(row)

    def remove_cue(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        cue = self.show_model.cues[row]
        for voice in self._cue_voices:
            if voice.tag is cue:
                voice.done = True
        del self.show_model.cues[row]
        self._refresh_table()

    def go(self) -> None:
        row = self._playhead_row()
        if row >= 0:
            self.go_row(row)

    def go_row(self, row: int, replace: bool = True) -> None:
        """Start the cue in this row with its fade-in and move the playhead down.

        replace=True fades out whatever cue is already playing. replace=False lets it run on.
        """
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        if cue.kind == "spotify":
            self._play_spotify_cue(cue)
            self.table.selectRow(min(row + 1, len(cues) - 1))
            return
        if cue.kind == "console":
            self._send_console_cue(cue)
            self.table.selectRow(min(row + 1, len(cues) - 1))
            return
        if cue.kind == "video":
            if not cue.file:
                QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\nThis video cue has no file.")
            else:
                try:
                    self._video.play(cue.video_output, cue.file, self.show_model.video_routes)
                except Exception as exc:
                    QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\n{exc}")
            self.table.selectRow(min(row + 1, len(cues) - 1))
            return
        if cue.kind == "camera":
            try:
                self._video.show_camera(cue.video_output, self._camera_for(cue), self.show_model.video_routes)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\n{exc}")
            self.table.selectRow(min(row + 1, len(cues) - 1))
            return
        if self.mixer is None:
            return
        if cue.kind == "audio" and cue.file:
            try:
                clip = self._clip(cue.file)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\n{exc}")
                return
            if replace:
                for voice in self._cue_voices:
                    if not voice.done and not voice.stop_when_done:
                        self.mixer.fade_out(voice, voice.tag.fade_out)
            voice = Voice(
                clip=clip,
                matrix=route_matrix(cue.route, clip.channels, self.mixer.out_channels),
                loop=cue.loop,
                bed=True,
                tag=cue,
            )
            if cue.fade_in > 0:
                voice.env = 0.0
                voice.fade(1.0, cue.fade_in, self.mixer.rate)
            self.mixer.add(voice)
            self._cue_voices.append(voice)
            self._playing_cue = cue
            if cue.then == "continue" and row + 1 < len(cues):
                self.go_row(row + 1, replace=False)
        self.table.selectRow(min(row + 1, len(cues) - 1))

    def stop(self) -> None:
        self._pause_spotify()
        self._video.stop_all()
        if self.mixer is None:
            return
        for voice in self._cue_voices:
            if not voice.done and not voice.stop_when_done:
                self.mixer.fade_out(voice, voice.tag.fade_out)

    def toggle_pause(self) -> None:
        if self.mixer is None:
            return
        self.mixer.paused = not self.mixer.paused
        self.pause_button.setText("RESUME" if self.mixer.paused else "PAUSE")

    def panic(self) -> None:
        """Stop everything immediately, with no fade, including pads."""
        self._pause_spotify()
        self._video.stop_all()
        if self.mixer is not None:
            self.mixer.clear()
            self.mixer.paused = False
        self._cue_voices = []
        self._playing_cue = None
        self._radio = None
        self._radio_voice = None
        self.pause_button.setText("PAUSE")
        self._refresh_table()

    # --- Soundboard ---------------------------------------------------

    def _on_pad_pressed(self, index: int) -> None:
        pad = self.show_model.pads[index]
        if not pad.file or self.mixer is None:
            return
        try:
            clip = self._clip(pad.file)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot play pad", f"{pad.name}\n\n{exc}")
            return
        self.mixer.add(Voice(clip=clip, matrix=route_matrix(pad.route, clip.channels, self.mixer.out_channels)))

    def _pad_routing(self, index: int) -> None:
        pad = self.show_model.pads[index]
        out = self.mixer.out_channels if self.mixer is not None else 2
        dialog = RoutingDialog(pad.route, out, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pad.route = dialog.result_route()

    def _assign_pad(self, index: int) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Assign pad", "", AUDIO_FILTER)
        if not filename:
            return
        self.show_model.pads[index] = Pad(name=Path(filename).stem, file=filename)
        self.soundboard.set_pads(self.show_model.pads)

    def _clear_pad(self, index: int) -> None:
        self.show_model.pads[index] = Pad()
        self.soundboard.set_pads(self.show_model.pads)

    # --- Clock, follow cues and the playing row -----------------------

    def _tick(self) -> None:
        self.wall_label.setText(datetime.now().strftime("%H:%M:%S"))
        cues = self.show_model.cues

        # Follow: when a cue reaches its natural end, start the next cue if it is set to follow.
        for voice in list(self._cue_voices):
            if voice.natural_end and not voice.followed:
                voice.followed = True
                row = self._row_of(voice.tag)
                if row >= 0 and voice.tag.then == "follow" and row + 1 < len(cues):
                    self.go_row(row + 1, replace=False)

        self._cue_voices = [v for v in self._cue_voices if not v.done]
        active = self._cue_voices[-1].tag if self._cue_voices else None
        if active is not self._playing_cue:
            self._playing_cue = active
            self._refresh_table()
        self._radio_tick()
        if self._recorder is not None:
            self._recorder.drain()
        self._remote_tick()
        while not self._midi_commands.empty():
            self._run_command(self._midi_commands.get_nowait())
        if self._mic_chain is not None and self.mixer is not None:
            self.mixer.set_ducked(self._mic_chain.gate_open, self.show_model.duck_level)
        self._update_clock()

    def _update_clock(self) -> None:
        voice = next((v for v in reversed(self._cue_voices) if v.tag is self._playing_cue and not v.done), None)
        row = self._row_of(self._playing_cue)
        if voice is None or self.mixer is None:
            self.track_label.setText(format_clock(0))
            self.remaining_label.setText(format_clock(0))
            return

        rate = self.mixer.rate
        position_ms = voice.position * 1000 // rate
        duration_ms = voice.clip.frames * 1000 // rate
        remaining_ms = max(0, duration_ms - position_ms)
        self.track_label.setText(format_clock(position_ms))
        self.remaining_label.setText(format_clock(remaining_ms))
        if 0 <= row < len(self._row_bars) and duration_ms > 0:
            self._row_bars[row].setValue(min(100, position_ms * 100 // duration_ms))
        item = self.table.item(row, REMAINING_COL)
        if item is not None:
            item.setText(format_clock(remaining_ms))

    # --- Phone remote -------------------------------------------------

    def start_remote(self) -> None:
        if self._remote is not None:
            QMessageBox.information(self, "Phone remote", f"Already running at http://{lan_ip()}:{self._remote.port}")
            return
        port = self.show_model.remote_port
        pin, ok = QInputDialog.getText(self, "Phone remote", "Choose a PIN for the remote (4 or more digits):")
        if not ok or len(pin.strip()) < 4:
            if ok:
                QMessageBox.warning(self, "Phone remote", "The PIN needs at least 4 characters.")
            return
        try:
            state = RemoteState(pin.strip())
            server = RemoteServer(state, port=port)
            server.start()
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Phone remote",
                f"Could not start the remote on port {port}:\n{exc}\n\nChange the port under Audio → Phone Remote Port.",
            )
            return
        self._remote = server
        self._remote_state = state
        QMessageBox.information(
            self,
            "Phone remote",
            f"Open this on your phone or tablet, on the same Wi-Fi:\n\nhttp://{lan_ip()}:{server.port}\n\n"
            "Your computer's firewall may ask to allow access the first time.",
        )

    def stop_remote(self) -> None:
        if self._remote is not None:
            self._remote.stop()
        self._remote = None
        self._remote_state = None

    def _remote_tick(self) -> None:
        if self._remote_state is None:
            return
        state = self._remote_state
        while not state.commands.empty():
            self._run_command(state.commands.get_nowait())
        state.status = {
            "now": self._playing_cue.name if self._playing_cue is not None else "Standby",
            "track": self.track_label.text(),
            "remaining": self.remaining_label.text(),
            "paused": bool(self.mixer and self.mixer.paused),
        }

    # --- MIDI ---------------------------------------------------------

    def start_midi(self) -> None:
        if self._midi is not None:
            QMessageBox.information(self, "MIDI", "MIDI input is already running.")
            return
        try:
            from cuerunner import midi

            ports = midi.input_port_names()
        except Exception as exc:
            QMessageBox.critical(self, "MIDI", f"Could not list MIDI inputs:\n{exc}")
            return
        if not ports:
            QMessageBox.information(self, "MIDI", "No MIDI input ports were found.")
            return
        name, ok = QInputDialog.getItem(self, "MIDI input", "Choose the MIDI port:", ports, 0, False)
        if not ok:
            return
        try:
            self._midi = MidiInput(name, self._midi_commands.put, mapping=self.show_model.midi_map)
        except Exception as exc:
            QMessageBox.critical(self, "MIDI", f"Could not open that MIDI port:\n{exc}")

    def edit_midi_mapping(self) -> None:
        dialog = MidiMappingDialog(self.show_model.midi_map, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.show_model.midi_map = dialog.result_map()
        if self._midi is not None:
            self._midi.set_mapping(self.show_model.midi_map)

    def open_settings(self) -> None:
        actions = {
            "output": self.choose_output,
            "mic": self.choose_mic,
            "record_input": self.choose_record_input,
            "radio_settings": self.radio_settings,
            "radio_routing": self.radio_routing,
            "midi_mapping": self.edit_midi_mapping,
        }
        dialog = settings_ui.SettingsDialog(self.show_model, self, actions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings_ui.apply_settings(self.show_model, dialog.values())

    def stop_midi(self) -> None:
        if self._midi is not None:
            self._midi.close()
        self._midi = None

    def _run_command(self, command: str) -> None:
        """Run a command from the remote, MIDI, or anywhere else. Always called on the UI thread."""
        if command == "go":
            self.go()
        elif command == "stop":
            self.stop()
        elif command == "pause":
            self.toggle_pause()
        elif command == "panic":
            self.panic()
        elif command.startswith("cue:"):
            self.go_row(int(command.split(":", 1)[1]))
        elif command.startswith("pad:"):
            self._on_pad_pressed(int(command.split(":", 1)[1]))

    # --- Multitrack recording -----------------------------------------

    def choose_record_input(self) -> None:
        try:
            from cuerunner import audio_io

            devices = audio_io.input_devices()
        except Exception as exc:
            QMessageBox.critical(self, "Recorder", f"Could not list input devices:\n{exc}")
            return
        if not devices:
            QMessageBox.information(self, "Recorder", "No input devices were found.")
            return
        names = [devices_label(d) for d in devices]
        name, ok = QInputDialog.getItem(self, "Record input", "Choose the input device:", names, 0, False)
        if not ok:
            return
        chosen = devices[names.index(name)]
        self._record_device = chosen.index
        tracks, ok = QInputDialog.getInt(
            self, "Tracks", "How many tracks to record:", min(chosen.channels, MAX_TRACKS), 1, min(chosen.channels, MAX_TRACKS)
        )
        if ok:
            self._record_tracks = tracks

    def start_recording(self) -> None:
        if self._recorder is not None:
            return
        if self.mixer is None:
            QMessageBox.warning(self, "Recorder", "No audio output is open.")
            return
        default_name = f"recording-{datetime.now():%Y%m%d-%H%M%S}.wav"
        filename, _ = QFileDialog.getSaveFileName(self, "Save recording", default_name, "WAV files (*.wav)")
        if not filename:
            return
        try:
            from cuerunner import audio_io

            recorder = Recorder(filename, rate=self.mixer.rate, tracks=self._record_tracks)
            mic = audio_io.MicInput(
                self._record_device, self.mixer.rate, recorder.push, channels=self._record_tracks
            )
            mic.start()
        except Exception as exc:
            QMessageBox.critical(self, "Recorder", f"Could not start recording:\n{exc}")
            return
        self._recorder = recorder
        self._recorder_input = mic

    def stop_recording(self) -> None:
        if self._recorder is None:
            return
        try:
            if self._recorder_input is not None:
                self._recorder_input.close()
            self._recorder.close()
        except Exception as exc:
            QMessageBox.warning(self, "Recorder", f"Problem closing the recording:\n{exc}")
        self._recorder = None
        self._recorder_input = None

    # --- Talkback mic -------------------------------------------------

    def choose_mic(self) -> None:
        try:
            from cuerunner import audio_io

            devices = audio_io.input_devices()
        except Exception as exc:
            QMessageBox.critical(self, "Talkback mic", f"Could not list input devices:\n{exc}")
            return
        if not devices:
            QMessageBox.information(self, "Talkback mic", "No input devices were found.")
            return
        names = ["System default"] + [devices_label(d) for d in devices]
        name, ok = QInputDialog.getItem(self, "Mic input", "Choose the talkback input:", names, 0, False)
        if not ok:
            return
        self._mic_device = None if name == names[0] else devices[names.index(name) - 1].index

    def start_talkback(self) -> None:
        if self.mixer is None:
            QMessageBox.warning(self, "Talkback mic", "No audio output is open.")
            return
        if self._mic_input is not None:
            return
        try:
            from cuerunner import audio_io

            chain = MicChain()
            suppressor = FeedbackSuppressor(rate=self.mixer.rate)
            voice = LiveVoice(channels=1, matrix=default_matrix(1, self.mixer.out_channels), rate=self.mixer.rate, tag="mic")
            mic = audio_io.MicInput(
                self._mic_device,
                self.mixer.rate,
                lambda block: voice.push(chain.process(suppressor.process(block))),
            )
            mic.start()
        except Exception as exc:
            QMessageBox.critical(self, "Talkback mic", f"Could not open the mic:\n{exc}")
            return
        self.mixer.add(voice)
        self._mic_chain = chain
        self._mic_voice = voice
        self._mic_input = mic

    def stop_talkback(self) -> None:
        if self._mic_input is not None:
            try:
                self._mic_input.close()
            except Exception:
                pass
        if self._mic_voice is not None:
            self._mic_voice.done = True
        if self.mixer is not None:
            self.mixer.set_ducked(False)
        self._mic_input = None
        self._mic_chain = None
        self._mic_voice = None

    # --- Venue radio --------------------------------------------------

    def radio_routing(self) -> None:
        out = self.mixer.out_channels if self.mixer is not None else 2
        dialog = RoutingDialog(self.show_model.radio.route, out, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.show_model.radio.route = dialog.result_route()

    def radio_settings(self) -> None:
        settings = self.show_model.radio
        music = QFileDialog.getExistingDirectory(self, "Music folder", settings.music_folder)
        if music:
            settings.music_folder = music
        idents = QFileDialog.getExistingDirectory(self, "Ident and promo folder (optional)", settings.ident_folder)
        if idents:
            settings.ident_folder = idents
        every, ok = QInputDialog.getInt(
            self, "Ident interval", "Play an ident after this many tracks:", settings.ident_every, 1, 999
        )
        if ok:
            settings.ident_every = every

    def start_radio(self) -> None:
        if self.mixer is None:
            QMessageBox.warning(self, "Venue Radio", "No audio output is open.")
            return
        settings: RadioSettings = self.show_model.radio
        tracks = scan_audio(settings.music_folder)
        try:
            self._radio = Playlist(tracks, scan_audio(settings.ident_folder), every=settings.ident_every)
        except ValueError as exc:
            QMessageBox.warning(self, "Venue Radio", f"{exc}\n\nChoose a music folder in Venue Radio Settings.")
            return
        self._radio_voice = None

    def stop_radio(self) -> None:
        self._radio = None
        if self._radio_voice is not None and self.mixer is not None:
            self.mixer.fade_out(self._radio_voice, 1.0)
        self._radio_voice = None

    def _radio_tick(self) -> None:
        """Keep the radio going: when a track or ident ends, start the next one straight away."""
        if self._radio is None or self.mixer is None:
            return
        if self._radio_voice is not None and not self._radio_voice.done:
            return
        kind, path = self._radio.next()
        try:
            clip = self._clip(path)
        except Exception as exc:
            QMessageBox.warning(self, "Venue Radio", f"Could not play {Path(path).name}:\n{exc}")
            self.stop_radio()
            return
        voice = Voice(clip=clip, matrix=route_matrix(self.show_model.radio.route, clip.channels, self.mixer.out_channels), bed=True, tag=kind)
        self.mixer.add(voice)
        self._radio_voice = voice

    # --- Help and updates ---------------------------------------------

    def about(self) -> None:
        self.about_from(self)

    def about_from(self, owner: QWidget) -> None:
        """Show About with `owner` as its parent. Dialogs need a visible parent, so the launch screen passes itself."""
        QMessageBox.information(owner, "About CueRunner", f"CueRunner {__version__}")

    def check_for_updates(self) -> None:
        self.check_for_updates_from(self)

    def check_for_updates_from(self, owner: QWidget) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(owner, "Updates", "Updates are only available in the packaged app.")
            return
        try:
            release = updater.fetch_latest_release()
        except Exception as exc:  # network, JSON, or GitHub errors
            QMessageBox.warning(owner, "Update check failed", f"Could not check for updates:\n{exc}")
            return
        if release is None:
            QMessageBox.information(owner, "Updates", "No build for this platform is published yet.")
            return
        if not updater.is_newer(release.tag):
            QMessageBox.information(owner, "Updates", f"You have the latest version ({__version__}).")
            return
        answer = QMessageBox.question(
            owner,
            "Update available",
            f"CueRunner {release.tag} is available (you have {__version__}).\n\n"
            "Download it now? CueRunner will restart to apply the update.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._download_update(release, owner)

    def _download_update(self, release: updater.Release, owner: QWidget) -> None:
        dest = Path(tempfile.mkdtemp(prefix="cuerunner-dl-")) / release.asset_name
        dialog = QProgressDialog(f"Downloading CueRunner {release.tag}...", "Cancel", 0, 100, owner)
        dialog.setWindowTitle("Update CueRunner")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(420)
        bar = dialog.findChild(QProgressBar)
        if bar is not None:  # the app theme makes bars 8px tall, which hides the percentage
            bar.setStyleSheet(
                "QProgressBar { background: #1a1a1c; border: 0; border-radius: 4px; min-height: 22px;"
                " color: #f0f0f0; text-align: center; }"
                " QProgressBar::chunk { background: #3b82d6; border-radius: 4px; }"
            )
            bar.setTextVisible(True)
        dialog.setValue(0)

        def show_progress(value: int) -> None:
            if value < 0:  # size unknown: show a busy bar instead of a stuck one
                dialog.setRange(0, 0)
            else:
                if dialog.maximum() == 0:
                    dialog.setRange(0, 100)
                dialog.setValue(value)

        worker = _DownloadWorker(release.asset_url, dest, owner)
        worker.progress.connect(show_progress)
        worker.failed.connect(lambda msg: (dialog.close(), QMessageBox.critical(owner, "Update failed", msg)))

        def finished(path: str) -> None:
            dialog.close()
            try:
                updater.apply_update(Path(path))
            except Exception as exc:
                QMessageBox.critical(owner, "Update failed", str(exc))
                return
            QMessageBox.information(
                owner, "Update ready", "CueRunner will close and reopen to finish the update."
            )
            # The launch screen runs its own loop, so quit() alone would leave the app open
            # and the swap would never start. Close it first so the process exits.
            if isinstance(owner, QDialog):
                owner.reject()
            QApplication.quit()

        worker.done.connect(finished)
        dialog.canceled.connect(worker.requestInterruption)
        worker.start()
        self._update_worker = worker  # keep a reference while it runs


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    start = launcher.LaunchDialog(
        lambda: window.check_for_updates_from(start),
        lambda: window.about_from(start),
    )
    if start.exec() != QDialog.DialogCode.Accepted:
        window.close()
        return 0
    if start.choice == "open":
        window.open_show()
    elif start.choice == "recent" and start.path is not None:
        window.open_show_path(start.path)
    window.show()
    return app.exec()
