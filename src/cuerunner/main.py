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
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cuerunner import updater
from cuerunner.countdown import Countdown
from cuerunner.dsp import MicChain
from cuerunner.engine import Clip, LiveVoice, Mixer, Voice, default_matrix, load_clip, probe_duration_ms
from cuerunner.model import Cue, Pad, RadioSettings, Show
from cuerunner.playlist import Playlist, scan_audio
from cuerunner.soundboard import Soundboard
from cuerunner.theme import apply_dark_theme
from cuerunner.timecode import format_clock, format_duration, format_hms
from cuerunner.version import __version__

COLUMNS = ["#", "Name", "Length", "Fade In", "Fade Out", "Loop", "Then", "Progress", "Remaining", "Hotkey", "File"]
PROGRESS_COL = 7
REMAINING_COL = 8
NAME_COL = 1
MAX_CHANNELS = 64

SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.flac *.ogg);;All files (*)"
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
            path = updater.download(self._url, self._dest, progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if self.isInterruptionRequested():
            self.failed.emit("Download cancelled.")
            return
        self.done.emit(str(path))


class CueDialog(QDialog):
    """Edit a cue's name, fades, loop, follow/continue and hotkey."""

    def __init__(self, cue: Cue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cue {cue.number}")

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
        form.addRow(buttons)

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.show_model = Show()
        self.show_path: Path | None = None
        self._shortcuts: list[QShortcut] = []
        self._clips: dict[str, Clip] = {}
        self._cue_voices: list[Voice] = []
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
        self.duck_level = 0.3

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

        self._open_default_output()
        self._refresh_title()
        self.resize(1200, 820)

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
        names = [f"{d.name} ({d.channels} ch)" for d in devices]
        name, ok = QInputDialog.getItem(self, "Output device", "Choose the output device:", names, 0, False)
        if not ok:
            return
        try:
            self._open_output(devices[names.index(name)])
        except Exception as exc:
            QMessageBox.critical(self, "Audio output", f"Could not open that device:\n{exc}")

    def _clip(self, path: str) -> Clip:
        if self.mixer is None:
            raise RuntimeError("No audio output is open.")
        if path not in self._clips:
            self._clips[path] = load_clip(path, self.mixer.rate)
        return self._clips[path]

    # --- UI setup -----------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("File", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for label, handler in [
            ("New", self.new_show),
            ("Open...", self.open_show),
            ("Save", self.save_show),
        ]:
            bar.addAction(label, handler)
        bar.addSeparator()
        for label, handler in [
            ("Add Audio Cue...", self.add_audio_cue),
            ("Edit Cue...", self.edit_selected),
            ("Remove Cue", self.remove_cue),
        ]:
            bar.addAction(label, handler)

    def _build_menus(self) -> None:
        audio_menu = self.menuBar().addMenu("Audio")
        audio_menu.addAction("Output Device...", self.choose_output)
        audio_menu.addSeparator()
        audio_menu.addAction("Talkback Mic On", self.start_talkback)
        audio_menu.addAction("Talkback Mic Off", self.stop_talkback)
        audio_menu.addAction("Choose Mic Input...", self.choose_mic)
        audio_menu.addSeparator()
        audio_menu.addAction("Start Venue Radio", self.start_radio)
        audio_menu.addAction("Stop Venue Radio", self.stop_radio)
        audio_menu.addAction("Venue Radio Settings...", self.radio_settings)
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
        if not filename:
            return
        try:
            self.show_model = Show.load(Path(filename))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open show", str(exc))
            return
        self.show_path = Path(filename)
        self.panic()
        self._refresh_table()
        self._refresh_title()
        for cue in self.show_model.cues:
            if cue.duration_ms == 0:
                self._probe_duration(cue)

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
        self._refresh_title()

    # --- Cue actions --------------------------------------------------

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
        dialog = CueDialog(cue, self)
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
        if self.mixer is None or not 0 <= row < len(cues):
            return
        cue = cues[row]
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
                matrix=default_matrix(clip.channels, self.mixer.out_channels),
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
        self.mixer.add(Voice(clip=clip, matrix=default_matrix(clip.channels, self.mixer.out_channels)))

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
        if self._mic_chain is not None and self.mixer is not None:
            self.mixer.set_ducked(self._mic_chain.gate_open, self.duck_level)
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
        names = ["System default"] + [f"{d.name} ({d.channels} ch)" for d in devices]
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
            voice = LiveVoice(channels=1, matrix=default_matrix(1, self.mixer.out_channels), rate=self.mixer.rate, tag="mic")
            mic = audio_io.MicInput(self._mic_device, self.mixer.rate, lambda block: voice.push(chain.process(block)))
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
        voice = Voice(clip=clip, matrix=default_matrix(clip.channels, self.mixer.out_channels), bed=True, tag=kind)
        self.mixer.add(voice)
        self._radio_voice = voice

    # --- Help and updates ---------------------------------------------

    def about(self) -> None:
        QMessageBox.information(self, "About CueRunner", f"CueRunner {__version__}")

    def check_for_updates(self) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "Updates", "Updates are only available in the packaged app.")
            return
        try:
            release = updater.fetch_latest_release()
        except Exception as exc:  # network, JSON, or GitHub errors
            QMessageBox.warning(self, "Update check failed", f"Could not check for updates:\n{exc}")
            return
        if release is None:
            QMessageBox.information(self, "Updates", "No build for this platform is published yet.")
            return
        if not updater.is_newer(release.tag):
            QMessageBox.information(self, "Updates", f"You have the latest version ({__version__}).")
            return
        answer = QMessageBox.question(
            self,
            "Update available",
            f"CueRunner {release.tag} is available (you have {__version__}).\n\n"
            "Download it now? CueRunner will restart to apply the update.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._download_update(release)

    def _download_update(self, release: updater.Release) -> None:
        dest = Path(tempfile.mkdtemp(prefix="cuerunner-dl-")) / release.asset_name
        dialog = QProgressDialog(f"Downloading CueRunner {release.tag}...", "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)

        worker = _DownloadWorker(release.asset_url, dest, self)
        worker.progress.connect(dialog.setValue)
        worker.failed.connect(lambda msg: (dialog.close(), QMessageBox.critical(self, "Update failed", msg)))

        def finished(path: str) -> None:
            dialog.close()
            try:
                updater.apply_update(Path(path))
            except Exception as exc:
                QMessageBox.critical(self, "Update failed", str(exc))
                return
            QApplication.quit()

        worker.done.connect(finished)
        dialog.canceled.connect(worker.requestInterruption)
        worker.start()
        self._update_worker = worker  # keep a reference while it runs


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()
