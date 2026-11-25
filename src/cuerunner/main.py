"""CueRunner main window: transport, cue list, soundboard, countdown, follow and continue cues, updates.

Playback runs on cuerunner.engine. Cues and soundboard pads are voices in one mixer, so
they can overlap, and the mixer is fed to the sound card by audio_io.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
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
    QMenu,
    QPlainTextEdit,
    QSpinBox,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cuerunner import allenheath, artnet, console_ui, launcher, midi, mtc, netsend, osc, sacn, settings_ui, spotify, spotify_ui, updater, video_out
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
DMX_STEP_MS = 40  # light ramps send a new frame this often
MAIN_QSS = """
QWidget#mainWindow { background: #3b3b3f; }
QPushButton#bigGo {
    background: #c9c3ef; color: #ffffff; border: 1px solid #a9a2db; border-radius: 4px;
    font-size: 40px; font-weight: 600;
}
QPushButton#bigGo:hover { background: #d7d2f5; }
QPushButton#bigGo:pressed { background: #b4adde; }
QLabel#standby {
    background: #56565b; color: #d8d8dc; border: 1px solid #47474c; border-radius: 3px;
    padding: 10px 12px; font-size: 16px;
}
QFrame#clockBar { background: #4c4c51; border: 1px solid #47474c; border-radius: 3px; }
QToolButton#toolButton {
    background: #4a4a4f; color: #f0f0f2; border: 1px solid #5a5a60; border-radius: 3px;
    padding: 6px 12px; font-size: 13px;
}
QToolButton#toolButton:hover { background: #56565c; }
QToolButton#toolButton:checked { background: #3b82d6; border-color: #3b82d6; color: #ffffff; }
QToolButton#toolButton[kind="stop"] { background: #5a4a4a; }
QToolButton#toolButton[kind="pause"] { background: #5e5340; }
QToolButton#toolButton[kind="panic"] { background: #6e1c1c; border-color: #7e2a2a; }
QToolButton#toolButton[kind="panic"]:hover { background: #8a2626; }
QTableWidget {
    background: #424246; alternate-background-color: #3a3a3e; color: #ececf0;
    border: 1px solid #2e2e32; gridline-color: #2e2e32; selection-background-color: #3b82d6;
    selection-color: #ffffff; font-size: 13px;
}
QHeaderView::section {
    background: #6a6a70; color: #f5f5f7; border: 0; border-right: 1px solid #4f4f55;
    padding: 5px 8px; font-size: 13px; font-weight: 600;
}
QPushButton#modeButton {
    background: #56565b; color: #d8d8dc; border: 1px solid #47474c; padding: 4px 16px; font-size: 13px;
}
QPushButton#modeButton:checked { background: #6a6a70; color: #ffffff; }
QLabel#cueCount { color: #c8c8cc; font-size: 13px; }
"""

COLUMNS = ["#", "Name", "Pre-Wait", "Length", "Post-Wait", "Fade In", "Fade Out", "Loop", "Then", "Progress", "Remaining", "Hotkey", "File"]
PROGRESS_COL = 9
REMAINING_COL = 10
NAME_COL = 1
MAX_CHANNELS = 64

SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.flac *.ogg);;All files (*)"
VIDEO_FILTER = "Video files (*.mp4 *.mov *.m4v *.avi *.mkv);;All files (*)"
COLOURS = [
    ("None", ""),
    ("Red", "#c0392b"),
    ("Orange", "#d35400"),
    ("Yellow", "#b7950b"),
    ("Green", "#1e8449"),
    ("Blue", "#2471a3"),
    ("Purple", "#7d3c98"),
]
SPECIAL_KINDS = {
    "mic": "Mic",
    "network": "Network",
    "midi": "MIDI",
    "timecode": "Timecode",
    "light": "Light",
    "osc": "OSC",
    "text": "Text",
    "fade": "Fade",
    "devamp": "Devamp",
    "target": "Target",
    "program": "Run Program",
    "midifile": "MIDI File",
    "control": "Control",
    "wait": "Wait",
    "group": "Group",
    "memo": "Memo",
}
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
                 video_choices: list[tuple[str, str]] | None = None,
                 groups: list[tuple[str, str]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cue {cue.number}")
        self._out_channels = out_channels

        self.name_edit = QLineEdit(cue.name)
        self.fade_in = self._fade_spin(cue.fade_in)
        self.fade_out = self._fade_spin(cue.fade_out)
        self.loop_box = QCheckBox("Loop until stopped")
        self.loop_box.setChecked(cue.loop)
        self.loop_in_spin = QDoubleSpinBox()
        self.loop_in_spin.setRange(0, 86400)
        self.loop_in_spin.setDecimals(2)
        self.loop_in_spin.setSuffix(" s")
        self.loop_in_spin.setValue(cue.loop_in)
        self.loop_in_spin.setToolTip("Where the loop starts. 0 means the start of the file.")
        self.loop_out_spin = QDoubleSpinBox()
        self.loop_out_spin.setRange(0, 86400)
        self.loop_out_spin.setDecimals(2)
        self.loop_out_spin.setSuffix(" s")
        self.loop_out_spin.setValue(cue.loop_out)
        self.loop_out_spin.setToolTip("Where the loop wraps back to the loop start. 0 means the end of the file.")
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
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Fade in", self.fade_in)
        form.addRow("Fade out", self.fade_out)
        form.addRow("", self.loop_box)
        form.addRow("Loop in", self.loop_in_spin)
        form.addRow("Loop out", self.loop_out_spin)
        form.addRow("Then", self.then_box)
        form.addRow("Hotkey", self.hotkey_edit)
        outputs_row = QHBoxLayout()
        outputs_row.addWidget(self.route_label, 1)
        outputs_row.addWidget(self.route_button)
        form.addRow("Outputs", outputs_row)
        if self._cue_kind in ("video", "camera", "text"):
            form.addRow("Display", self.display_box)
        self._build_kind_fields(cue, form, groups or [])
        form.addRow(buttons)

    def _build_kind_fields(self, cue: Cue, form: QFormLayout, groups: list[tuple[str, str]]) -> None:
        self.parent_box = QComboBox()
        self.parent_box.addItem("None", "")
        for label, number in groups:
            self.parent_box.addItem(label, number)
        self.parent_box.setCurrentIndex(max(0, self.parent_box.findData(cue.parent)))
        form.addRow("Group", self.parent_box)

        self.pre_wait_spin = QDoubleSpinBox()
        self.pre_wait_spin.setRange(0, 3600)
        self.pre_wait_spin.setDecimals(1)
        self.pre_wait_spin.setSuffix(" s")
        self.pre_wait_spin.setValue(cue.pre_wait_ms / 1000)
        form.addRow("Pre-wait", self.pre_wait_spin)
        self.post_wait_spin = QDoubleSpinBox()
        self.post_wait_spin.setRange(0, 3600)
        self.post_wait_spin.setDecimals(1)
        self.post_wait_spin.setSuffix(" s")
        self.post_wait_spin.setValue(cue.post_wait_ms / 1000)
        form.addRow("Post-wait", self.post_wait_spin)
        self.trigger_tc_edit = QLineEdit(cue.trigger_tc)
        self.trigger_tc_edit.setPlaceholderText("Blank for none, e.g. 00:01:30:00")
        form.addRow("Timecode trigger", self.trigger_tc_edit)
        self.armed_box = QCheckBox("Armed (GO skips disarmed cues)")
        self.armed_box.setChecked(cue.armed)
        form.addRow("", self.armed_box)
        self.target_edit = QLineEdit(cue.target)
        self.target_edit.setPlaceholderText("Cue number, e.g. 3")
        self.action_box = QComboBox()
        self.action_box.addItem("Start", "start")
        self.action_box.addItem("Stop", "stop")
        self.action_box.addItem("Pause", "pause")
        self.action_box.addItem("Resume", "resume")
        self.action_box.addItem("Goto", "goto")
        self.action_box.addItem("Load", "load")
        self.action_box.addItem("Reset", "reset")
        self.action_box.addItem("Arm", "arm")
        self.action_box.addItem("Disarm", "disarm")
        self.light_universe = QSpinBox()
        self.light_universe.setRange(0, 32767)
        self.light_universe.setValue(cue.universe)
        self.osc_target_edit = QLineEdit(cue.target)
        self.osc_target_edit.setPlaceholderText("192.168.1.10:8000")
        self.osc_message_edit = QLineEdit(cue.text)
        self.osc_message_edit.setPlaceholderText("/cue/1/start 0.5")
        self.colour_box = QComboBox()
        for label, value in COLOURS:
            self.colour_box.addItem(label, value)
        self.colour_box.setCurrentIndex(max(0, self.colour_box.findData(cue.colour)))
        form.addRow("Colour", self.colour_box)
        self.action_box.setCurrentIndex(max(0, self.action_box.findData(cue.action)))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(0, 100)
        self.level_spin.setSuffix(" %")
        self.level_spin.setValue(round(cue.level * 100))
        self.seconds_spin = QDoubleSpinBox()
        self.seconds_spin.setRange(0, 3600)
        self.seconds_spin.setDecimals(1)
        self.seconds_spin.setSuffix(" s")
        self.seconds_spin.setValue(cue.seconds)
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(0, 86400)
        self.wait_spin.setDecimals(1)
        self.wait_spin.setSuffix(" s")
        self.wait_spin.setValue(cue.duration_ms / 1000)
        self.text_edit = QPlainTextEdit(cue.text)
        self.text_edit.setFixedHeight(90)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 400)
        self.font_spin.setValue(cue.font_size)

        kind = cue.kind
        if kind == "control":
            form.addRow("Cue number", self.target_edit)
            form.addRow("Action", self.action_box)
        if kind == "devamp":
            form.addRow("Loop cue", self.target_edit)
        if kind == "program":
            self.program_edit = QLineEdit(cue.file)
            self.program_edit.setPlaceholderText("Path to the program, e.g. /usr/bin/say")
            browse = QPushButton("Browse…")
            browse.clicked.connect(self._browse_program)
            row_box = QHBoxLayout()
            row_box.addWidget(self.program_edit, 1)
            row_box.addWidget(browse)
            self.program_args_edit = QLineEdit(cue.text)
            self.program_args_edit.setPlaceholderText("Arguments, separated by spaces")
            form.addRow("Program", row_box)
            form.addRow("Arguments", self.program_args_edit)
        if kind == "target":
            self.new_target_edit = QLineEdit(cue.new_target)
            self.new_target_edit.setPlaceholderText("Cue number, or a file path for audio and video")
            form.addRow("Cue to change", self.target_edit)
            form.addRow("New target", self.new_target_edit)
        if kind == "fade":
            form.addRow("Cue number", self.target_edit)
            form.addRow("Fade to", self.level_spin)
            form.addRow("Over", self.seconds_spin)
        if kind == "osc":
            form.addRow("Send to", self.osc_target_edit)
            form.addRow("Message", self.osc_message_edit)
        if kind in ("midi", "timecode", "midifile"):
            self.midi_port_box = QComboBox()
            try:
                ports = midi.output_port_names()
            except Exception:
                ports = []
            for port in ports:
                self.midi_port_box.addItem(port, port)
            if cue.target and self.midi_port_box.findData(cue.target) < 0:
                self.midi_port_box.addItem(f"{cue.target} (not connected)", cue.target)
            self.midi_port_box.setCurrentIndex(max(0, self.midi_port_box.findData(cue.target)))
            form.addRow("MIDI port", self.midi_port_box)
            if kind == "midi":
                form.addRow("Message", self.osc_message_edit)
            elif kind == "midifile":
                self.midifile_edit = QLineEdit(cue.file)
                self.midifile_edit.setPlaceholderText("Path to a .mid file")
                browse = QPushButton("Browse…")
                browse.clicked.connect(self._browse_midifile)
                file_row = QHBoxLayout()
                file_row.addWidget(self.midifile_edit, 1)
                file_row.addWidget(browse)
                form.addRow("MIDI file", file_row)
            else:
                self.timecode_start_edit = QLineEdit(cue.text or "00:00:00:00")
                self.fps_box = QComboBox()
                for rate in mtc.FRAME_RATES:
                    self.fps_box.addItem(f"{rate} fps", rate)
                self.fps_box.setCurrentIndex(max(0, self.fps_box.findData(cue.fps)))
                form.addRow("Start at", self.timecode_start_edit)
                form.addRow("Frame rate", self.fps_box)
        if kind in ("light", "network"):
            self.protocol_box = QComboBox()
            options = [("Art-Net", "artnet"), ("sACN", "sacn")] if kind == "light" else [("UDP", "udp"), ("TCP", "tcp")]
            for label, value in options:
                self.protocol_box.addItem(label, value)
            self.protocol_box.setCurrentIndex(max(0, self.protocol_box.findData(cue.protocol)))
            form.addRow("Protocol", self.protocol_box)
        if kind == "network":
            form.addRow("Send to", self.osc_target_edit)
            form.addRow("Message", self.osc_message_edit)
        if kind == "light":
            form.addRow("Node address", self.osc_target_edit)
            form.addRow("Universe", self.light_universe)
            form.addRow("Channels", self.osc_message_edit)
            form.addRow("Ramp over", self.seconds_spin)
        if kind == "wait":
            form.addRow("Wait", self.wait_spin)
        if kind in ("text", "memo"):
            form.addRow("Text" if kind == "text" else "Notes", self.text_edit)
        if kind == "text":
            form.addRow("Font size", self.font_spin)
        if kind == "group":
            self.group_mode_box = QComboBox()
            self.group_mode_box.addItem("Fire all children at once", "all")
            self.group_mode_box.addItem("Playlist: one child after another", "playlist")
            self.group_mode_box.setCurrentIndex(max(0, self.group_mode_box.findData(cue.group_mode)))
            form.addRow("Mode", self.group_mode_box)

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

    def _browse_midifile(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a MIDI file", "", "MIDI files (*.mid *.midi)")
        if filename:
            self.midifile_edit.setText(filename)

    def _browse_program(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a program", "")
        if filename:
            self.program_edit.setText(filename)

    def _accept_checked(self) -> None:
        """OK, but only when the timecode trigger is blank or a valid HH:MM:SS:FF."""
        text = self.trigger_tc_edit.text().strip() if hasattr(self, "trigger_tc_edit") else ""
        if text:
            try:
                mtc.parse_timecode(text, 24)  # 24 is the strictest rate, so a value that passes here is valid at 25 and 30 too
            except ValueError as exc:
                QMessageBox.warning(self, "Timecode trigger", f"{exc}\n\nExample: 00:01:30:00")
                return
        self.accept()

    def apply_to(self, cue: Cue) -> None:
        cue.name = self.name_edit.text().strip() or cue.name
        cue.fade_in = self.fade_in.value()
        cue.fade_out = self.fade_out.value()
        cue.loop = self.loop_box.isChecked()
        cue.loop_in = self.loop_in_spin.value()
        cue.loop_out = self.loop_out_spin.value()
        cue.then = self.then_box.currentData()
        cue.hotkey = self.hotkey_edit.keySequence().toString()
        cue.route = [list(group) for group in self._route]
        if cue.kind in ("video", "camera", "text") and self.display_box.count():
            cue.video_output = self.display_box.currentData()
        cue.parent = self.parent_box.currentData() or ""
        cue.pre_wait_ms = int(self.pre_wait_spin.value() * 1000)
        cue.post_wait_ms = int(self.post_wait_spin.value() * 1000)
        cue.trigger_tc = self.trigger_tc_edit.text().strip()
        cue.armed = self.armed_box.isChecked()
        cue.colour = self.colour_box.currentData() or ""
        if cue.kind in ("control", "fade", "devamp", "target"):
            cue.target = self.target_edit.text().strip()
        if cue.kind == "target":
            cue.new_target = self.new_target_edit.text().strip()
        if cue.kind == "program":
            cue.file = self.program_edit.text().strip()
            cue.text = self.program_args_edit.text().strip()
        if cue.kind == "midi":
            cue.target = self.midi_port_box.currentData() or ""
            cue.text = self.osc_message_edit.text().strip()
        if cue.kind == "midifile":
            cue.target = self.midi_port_box.currentData() or ""
            cue.file = self.midifile_edit.text().strip()
        if cue.kind == "timecode":
            cue.target = self.midi_port_box.currentData() or ""
            cue.text = self.timecode_start_edit.text().strip() or "00:00:00:00"
            cue.fps = self.fps_box.currentData() or 30
        if cue.kind in ("light", "network"):
            cue.protocol = self.protocol_box.currentData()
        if cue.kind in ("osc", "light", "network"):
            cue.target = self.osc_target_edit.text().strip()
            cue.text = self.osc_message_edit.text().strip()
        if cue.kind == "light":
            cue.universe = self.light_universe.value()
            cue.seconds = self.seconds_spin.value()
        if cue.kind == "control":
            cue.action = self.action_box.currentData()
        if cue.kind == "fade":
            cue.level = self.level_spin.value() / 100
            cue.seconds = self.seconds_spin.value()
        if cue.kind == "wait":
            cue.duration_ms = int(self.wait_spin.value() * 1000)
        if cue.kind in ("text", "memo"):
            cue.text = self.text_edit.toPlainText()
        if cue.kind == "text":
            cue.font_size = self.font_spin.value()
        if cue.kind == "group":
            cue.group_mode = self.group_mode_box.currentData()


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
        self._timers: list[QTimer] = []
        self._midifile_runs: dict[int, tuple] = {}  # id(cue) -> (cue, player)
        self._timecode_runs: dict[int, tuple] = {}  # id(cue) -> (cue, generator, timer, port)
        self._dmx_last: dict[tuple[str, int], dict[int, int]] = {}
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
        self._tc_listen: mtc.TimecodeInput | None = None  # incoming MIDI Timecode
        self._tc_queue: queue.Queue[tuple[int, int]] = queue.Queue()  # (ms, fps) from the MIDI thread
        self._tc_triggers = mtc.TimecodeTriggers()
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
        self._stop_timecode()
        self._stop_midifile()
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
        """No toolbar: the cue buttons sit above the cue list, as in QLab. File actions are in the File menu."""

    def _refresh_inspector(self) -> None:
        if not hasattr(self, "inspector_label"):
            return
        row = self._selected_row()
        cues = self.show_model.cues
        has_cue = 0 <= row < len(cues)
        editable = has_cue and self._editing
        self._insp_busy = True
        try:
            for widget in (self.insp_name, self.insp_pre, self.insp_post,
                           self.insp_colour, self.insp_hotkey):
                widget.setEnabled(editable)
            self.insp_full_edit.setEnabled(editable)
            if not has_cue:
                self.insp_name.clear()
            else:
                cue = cues[row]
                self.insp_name.setText(cue.name)
                self.insp_pre.setValue(cue.pre_wait_ms / 1000)
                self.insp_post.setValue(cue.post_wait_ms / 1000)
                index = self.insp_colour.findData(cue.colour or "")
                self.insp_colour.setCurrentIndex(max(index, 0))
                self.insp_hotkey.setKeySequence(QKeySequence(cue.hotkey))
        finally:
            self._insp_busy = False
        if not has_cue:
            self.inspector_label.setText("No cue selected")
            return
        cue = cues[row]
        parts = [
            f"Cue {cue.number}: {cue.name}",
            cue.kind.title(),
            self._target_text(cue),
            f"Pre-wait {cue.pre_wait_ms / 1000:g} s",
            f"Post-wait {cue.post_wait_ms / 1000:g} s",
        ]
        if cue.kind == "audio" or cue.kind == "video":
            parts.append(f"Fade in {cue.fade_in:g} s, out {cue.fade_out:g} s")
        if cue.hotkey:
            parts.append(f"Hotkey {cue.hotkey}")
        if cue.parent:
            parts.append(f"In group {cue.parent}")
        self.inspector_label.setText("  •  ".join(part for part in parts if part))

    def _inspector_apply(self) -> None:
        """Write the inspector fields back to the selected cue (Edit mode only)."""
        if self._insp_busy or not self._editing:
            return
        row = self._selected_row()
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        name = self.insp_name.text().strip()
        if name:
            cue.name = name
        cue.pre_wait_ms = int(self.insp_pre.value() * 1000)
        cue.post_wait_ms = int(self.insp_post.value() * 1000)
        cue.colour = self.insp_colour.currentData() or ""
        cue.hotkey = self.insp_hotkey.keySequence().toString()
        self._refresh_table()
        self._rebuild_shortcuts()
        self.table.selectRow(row)

    def _target_text(self, cue: Cue) -> str:
        """What the File column shows for each cue type."""
        if cue.kind == "control":
            return f"{cue.action.title()} cue {cue.target}"
        if cue.kind == "devamp":
            return f"Ends the loop on cue {cue.target}"
        if cue.kind == "midifile":
            return f"{Path(cue.file).name or 'no file'} to {cue.target or 'no port'}"
        if cue.kind == "program":
            return f"Runs {cue.file or 'no program'} {cue.text}".rstrip()
        if cue.kind == "target":
            return f"Cue {cue.target} now targets {cue.new_target or 'nothing'}"
        if cue.kind == "fade":
            return f"Fade cue {cue.target} to {round(cue.level * 100)}% over {cue.seconds:g} s"
        if cue.kind == "wait":
            return f"Wait {cue.duration_ms / 1000:g} s"
        if cue.kind == "osc":
            return f"{cue.text} to {cue.target}"
        if cue.kind == "midi":
            return f"{cue.text} to {cue.target or 'no port'}"
        if cue.kind == "timecode":
            return f"Start {cue.text or '00:00:00:00'} at {cue.fps} fps to {cue.target or 'no port'}"
        if cue.kind == "network":
            return f"{cue.protocol.upper()} {cue.text} to {cue.target}"
        if cue.kind == "mic":
            return "Mic input on"
        if cue.kind == "light":
            return f"Universe {cue.universe}: {cue.text} to {cue.target}"
        if cue.kind == "group":
            children = sum(1 for c in self.show_model.cues if c.parent == cue.number)
            return f"Group of {children} cue{'' if children == 1 else 's'}"
        if cue.kind in ("text", "memo"):
            return cue.text.replace("\n", " ")[:80]
        return cue.file

    def _camera_for(self, cue: Cue) -> str:
        """The camera name for a camera cue, looked up from its video input patch."""
        for patch in self.show_model.video_inputs:
            if patch.get("name") == cue.video_input:
                return str(patch.get("device", ""))
        raise RuntimeError(f"The video input \"{cue.video_input}\" no longer exists.")

    def _toggle_pads(self, shown: bool) -> None:
        self.soundboard.setVisible(shown)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction("New Show", self.new_show, QKeySequence(QKeySequence.StandardKey.New))
        file_menu.addAction("Open Show...", self.open_show, QKeySequence(QKeySequence.StandardKey.Open))
        file_menu.addAction("Save Show", self.save_show, QKeySequence(QKeySequence.StandardKey.Save))
        cues_menu = self.menuBar().addMenu("Cues")
        cues_menu.addAction("Add Audio Cue...", self.add_audio_cue)
        cues_menu.addAction("Add Video Cue...", self.add_video_cue)
        cues_menu.addAction("Add Camera Cue...", self.add_camera_cue)
        cues_menu.addAction("Add Spotify Cue...", self.add_spotify_cue)
        cues_menu.addAction("Add Console Cue...", self.add_console_cue)
        for kind, label in SPECIAL_KINDS.items():
            cues_menu.addAction(f"Add {label} Cue...", lambda _=False, k=kind: self.add_special_cue(k))
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
        timecode = control_menu.addMenu("Timecode")
        timecode.addAction("Listen for Timecode...", self.start_timecode_listen)
        timecode.addAction("Stop Listening", self.stop_timecode_listen)
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
        container.setObjectName("mainWindow")
        container.setStyleSheet(MAIN_QSS)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Top: big GO, then the standby cue name and the clock line beside it.
        top = QHBoxLayout()
        top.setSpacing(8)
        self.go_button = QPushButton("GO")
        self.go_button.setObjectName("bigGo")
        self.go_button.setToolTip("Start the selected cue (Space)")
        self.go_button.clicked.connect(self.go)
        self.go_button.setFixedSize(150, 96)
        top.addWidget(self.go_button)

        screens = QVBoxLayout()
        screens.setSpacing(6)
        self.now_label = QLabel("[no cue on standby]")
        self.now_label.setObjectName("standby")
        screens.addWidget(self.now_label)
        clock = QFrame()
        clock.setObjectName("clockBar")
        clock_row = QHBoxLayout(clock)
        clock_row.setContentsMargins(10, 4, 10, 4)
        self.trt_label = self._status_value("0:00:00")
        self.track_label = self._status_value(format_clock(0))
        self.remaining_label = self._status_value(format_clock(0))
        self.wall_label = self._status_value("--:--:--")
        clock_row.addWidget(self._status_title("TRT"))
        clock_row.addWidget(self.trt_label)
        clock_row.addSpacing(22)
        clock_row.addWidget(self._status_title("TRACK"))
        clock_row.addWidget(self.track_label)
        clock_row.addSpacing(22)
        clock_row.addWidget(self._status_title("REMAINING"))
        clock_row.addWidget(self.remaining_label)
        clock_row.addStretch(1)
        clock_row.addWidget(self.wall_label)
        screens.addWidget(clock)
        top.addLayout(screens, 1)
        layout.addLayout(top)

        # Button row: cue types on the left, transport in the middle, pads on the right.
        tools = QHBoxLayout()
        tools.setSpacing(4)
        for text, tip, slot in [
            ("Audio", "Add an audio cue", self.add_audio_cue),
            ("Video", "Add a video cue", self.add_video_cue),
            ("Camera", "Add a camera cue", self.add_camera_cue),
            ("Spotify", "Add a Spotify cue", self.add_spotify_cue),
            ("Console", "Add an Allen & Heath console cue", self.add_console_cue),
        ]:
            tools.addWidget(self._tool_button(text, tip, slot))
        more = QToolButton()
        more.setObjectName("toolButton")
        more.setText("More Cues")
        more.setToolTip("Text, fade, control, wait, group and memo cues")
        more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more_menu = QMenu(more)
        for kind, label in SPECIAL_KINDS.items():
            more_menu.addAction(f"{label} Cue", lambda _=False, k=kind: self.add_special_cue(k))
        more.setMenu(more_menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools.addWidget(more)
        tools.addSpacing(16)
        self.stop_button = self._tool_button("Stop", "Fade out playing cues over their fade out time", self.stop)
        self.stop_button.setProperty("kind", "stop")
        self.pause_button = self._tool_button("Pause", "Pause or resume all sound", self.toggle_pause)
        self.pause_button.setProperty("kind", "pause")
        self.panic_button = self._tool_button("Panic", "Stop everything now, with no fade (Esc)", self.panic)
        self.panic_button.setProperty("kind", "panic")
        tools.addWidget(self.stop_button)
        tools.addWidget(self.pause_button)
        tools.addWidget(self.panic_button)
        tools.addStretch(1)
        self.pads_button = self._tool_button("Pads", "Show or hide the soundboard pads", None)
        self.pads_button.setCheckable(True)
        self.pads_button.setChecked(True)
        self.pads_button.toggled.connect(self._toggle_pads)
        tools.addWidget(self.pads_button)
        layout.addLayout(tools)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(NAME_COL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        for col, width in [(0, 50), (2, 80), (3, 80), (4, 80), (5, 70), (6, 70), (7, 50), (8, 80),
                           (PROGRESS_COL, 140), (REMAINING_COL, 100), (11, 80)]:
            self.table.setColumnWidth(col, width)
        self.table.itemDoubleClicked.connect(lambda item: self.edit_row(item.row()))
        self.table.itemSelectionChanged.connect(self._refresh_now_label)
        self.table.itemSelectionChanged.connect(self._refresh_inspector)
        layout.addWidget(self.table, 1)

        self.inspector_label = QLabel("No cue selected")
        self.inspector_label.setObjectName("inspector")
        self.inspector_label.setWordWrap(True)
        layout.addWidget(self.inspector_label)

        # Editable fields for the selected cue. They apply straight away in Edit mode;
        # Full Edit opens the complete cue dialog for everything else.
        self._insp_busy = False
        self.inspector_bar = QFrame()
        self.inspector_bar.setObjectName("inspectorBar")
        bar = QHBoxLayout(self.inspector_bar)
        bar.setContentsMargins(8, 4, 8, 4)
        self.insp_name = QLineEdit()
        self.insp_name.setMinimumWidth(160)
        self.insp_pre = QDoubleSpinBox()
        self.insp_post = QDoubleSpinBox()
        for spin in (self.insp_pre, self.insp_post):
            spin.setRange(0, 3600)
            spin.setDecimals(1)
            spin.setSuffix(" s")
            spin.setMaximumWidth(100)
        self.insp_colour = QComboBox()
        for label, value in COLOURS:
            self.insp_colour.addItem(label, value)
        self.insp_hotkey = QKeySequenceEdit()
        self.insp_hotkey.setMaximumWidth(160)
        self.insp_full_edit = QPushButton("Full Edit…")
        for caption, widget in [("Name", self.insp_name), ("Pre-wait", self.insp_pre),
                                ("Post-wait", self.insp_post), ("Colour", self.insp_colour),
                                ("Hotkey", self.insp_hotkey)]:
            bar.addWidget(QLabel(caption))
            bar.addWidget(widget, 1 if widget is self.insp_name else 0)
        bar.addWidget(self.insp_full_edit)
        self.insp_name.editingFinished.connect(self._inspector_apply)
        self.insp_pre.valueChanged.connect(self._inspector_apply)
        self.insp_post.valueChanged.connect(self._inspector_apply)
        self.insp_colour.currentIndexChanged.connect(self._inspector_apply)
        self.insp_hotkey.keySequenceChanged.connect(self._inspector_apply)
        self.insp_full_edit.clicked.connect(self.edit_selected)
        layout.addWidget(self.inspector_bar)

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

        # Footer: Edit or Show mode, the cue count, and settings.
        footer = QHBoxLayout()
        footer.setSpacing(0)
        self.edit_button = QPushButton("Edit")
        self.show_button = QPushButton("Show")
        for button in (self.edit_button, self.show_button):
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.setChecked(True)
        self.edit_button.clicked.connect(lambda: self._set_mode(editing=True))
        self.show_button.clicked.connect(lambda: self._set_mode(editing=False))
        footer.addWidget(self.edit_button)
        footer.addWidget(self.show_button)
        footer.addStretch(1)
        self.cue_count_label = QLabel("0 cues in 1 list")
        self.cue_count_label.setObjectName("cueCount")
        footer.addWidget(self.cue_count_label)
        footer.addStretch(1)
        settings_button = self._tool_button("Settings", "Audio, video, console and other settings", None)
        settings_button.clicked.connect(lambda: self.open_settings())
        settings_button.setShortcut(QKeySequence(QKeySequence.StandardKey.Preferences))
        footer.addWidget(settings_button)
        layout.addLayout(footer)

        self._editing = True
        self.setCentralWidget(container)

    def _tool_button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setObjectName("toolButton")
        button.setText(text)
        button.setToolTip(tip)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot is not None:
            button.clicked.connect(lambda _checked=False, f=slot: f())
        return button

    def _set_mode(self, editing: bool) -> None:
        """Edit lets you change cues; Show locks the list so nothing changes by accident during a show."""
        self._editing = editing
        self.edit_button.setChecked(editing)
        self.show_button.setChecked(not editing)
        self._refresh_inspector()

    def _refresh_title(self) -> None:
        name = self.show_path.name if self.show_path else "Untitled"
        self.setWindowTitle(f"CueRunner - {name}")

    def _refresh_table(self) -> None:
        cues = self.show_model.cues
        if hasattr(self, "cue_count_label"):
            self.cue_count_label.setText(f"{len(cues)} cue{'' if len(cues) == 1 else 's'} in 1 list")
        playhead = self._playhead_row()
        playing_row = self._row_of(self._playing_cue)

        self.table.setRowCount(len(cues))
        self._row_bars = []
        for row, cue in enumerate(cues):
            marker = PLAYHEAD_MARK if row == playhead else ""
            values = [
                f"{marker} {cue.number}".strip(),
                ("    " if cue.parent else "") + cue.name,
                f"{cue.pre_wait_ms / 1000:g} s" if cue.pre_wait_ms else "",
                format_duration(cue.duration_ms),
                f"{cue.post_wait_ms / 1000:g} s" if cue.post_wait_ms else "",
                f"{cue.fade_in:g} s",
                f"{cue.fade_out:g} s",
                "✓" if cue.loop else "",
                THEN_LABELS.get(cue.then, ""),
                "",
                "",
                cue.hotkey,
                self._target_text(cue),
            ]
            for col, value in enumerate(values):
                if col == PROGRESS_COL:
                    continue
                item = QTableWidgetItem(value)
                if row == playing_row:
                    item.setBackground(QBrush(PLAYING_COLOUR))
                elif cue.colour:
                    item.setBackground(QBrush(QColor(cue.colour)))
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
        self._refresh_inspector()
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

    def add_special_cue(self, kind: str) -> None:
        cue = Cue(number=self.show_model.next_number(), name=SPECIAL_KINDS[kind], kind=kind)
        self.show_model.cues.append(cue)
        self._refresh_table()
        self.edit_row(len(self.show_model.cues) - 1)

    def _after(self, ms: int, callback) -> None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(max(0, ms))
        self._timers = [t for t in self._timers if t.isActive()] + [timer]

    def _finish(self, row: int) -> None:
        """An instant cue is done: in a playlist group start the next child, otherwise move the playhead."""
        next_child = self._playlist_next_row(row)
        if next_child is not None:
            self.go_row(next_child, replace=False)
        self._advance(row)

    def _send_dmx(self, cue: Cue, levels: dict[int, int]) -> None:
        if cue.protocol == "sacn":
            sacn.send(cue.universe, levels, host=cue.target)
        else:
            artnet.send(cue.target, cue.universe, levels)

    def _run_light(self, cue: Cue) -> None:
        """Send a light cue's levels, ramping from the last levels sent to the same node and universe."""
        levels = artnet.parse_levels(cue.text)
        key = (cue.protocol, cue.target, cue.universe)
        start = self._dmx_last.get(key, {})
        if cue.seconds <= 0:
            self._send_dmx(cue, levels)
        else:
            steps = max(1, round(cue.seconds * 1000 / DMX_STEP_MS))
            for step in range(1, steps + 1):
                frame = artnet.ramp_levels(start, levels, step / steps)
                self._after(step * DMX_STEP_MS, lambda f=frame: self._send_dmx(cue, f))
        self._dmx_last[key] = dict(levels)

    def _advance(self, row: int) -> None:
        cues = self.show_model.cues
        self.table.selectRow(min(row + 1, len(cues) - 1))

    def _reset_cue(self, target: Cue) -> None:
        """Stop the cue at once and return it to its unplayed state."""
        if self.mixer is not None:
            self.mixer.kill(target)
        self._stop_timecode(target)
        if target.kind in ("video", "camera", "text"):
            self._video.stop_all()
        self._refresh_table()

    def _control(self, cue: Cue) -> None:
        number = cue.target.strip()
        cues = self.show_model.cues
        target_row = next((i for i, c in enumerate(cues) if c.number == number), None)
        if target_row is None:
            QMessageBox.warning(self, "Cannot run cue", f"{cue.name}\n\nThere is no cue numbered \"{number}\".")
            return
        target = cues[target_row]
        if cue.action == "stop":
            self._stop_cue(target)
        elif cue.action in ("pause", "resume"):
            self._pause_cue(target, paused=cue.action == "pause")
        elif cue.action == "goto":
            self.table.selectRow(target_row)
        elif cue.action == "load":
            self._preload(target)
        elif cue.action == "reset":
            self._reset_cue(target)
        elif cue.action in ("arm", "disarm"):
            target.armed = cue.action == "arm"
            self._refresh_table()
        else:
            self.go_row(target_row, replace=True)

    def _start_timecode(self, cue: Cue) -> None:
        """Start sending MIDI Timecode for this cue. It keeps running until a Stop cue or Panic."""
        import mido

        if not cue.target:
            raise ValueError("Choose a MIDI output port for this cue.")
        start_ms = mtc.parse_timecode(cue.text or "00:00:00:00", cue.fps)
        self._stop_timecode(cue)
        port = mido.open_output(cue.target)
        gen = mtc.MtcGenerator(start_ms, cue.fps, lambda data: port.send(mido.Message.from_bytes(data)))
        timer = QTimer(self)
        timer.setInterval(max(1, round(gen.interval_ms)))
        timer.timeout.connect(gen.tick)
        self._timecode_runs[id(cue)] = (cue, gen, timer, port)
        gen.start()
        timer.start()

    def _stop_timecode(self, cue: Cue | None = None) -> None:
        """Stop the timecode generator for one cue, or for every cue when cue is None."""
        for key, (owner, gen, timer, port) in list(self._timecode_runs.items()):
            if cue is not None and owner is not cue:
                continue
            gen.stop()
            timer.stop()
            timer.deleteLater()
            port.close()
            del self._timecode_runs[key]

    def _stop_cue(self, target: Cue) -> None:
        if target.kind == "timecode":
            self._stop_timecode(target)
            return
        if target.kind == "midifile":
            self._stop_midifile(target)
            return
        if target.kind == "mic":
            self.stop_talkback()
            return
        if target.kind in ("video", "camera", "text"):
            self._video.stop_all()
        if self.mixer is None:
            return
        for voice in self._cue_voices:
            if not voice.done and not voice.stop_when_done and voice.tag is target:
                self.mixer.fade_out(voice, voice.tag.fade_out)

    def _playlist_next_row(self, row: int) -> int | None:
        """In a playlist group, the row of the child after this one, or None."""
        cues = self.show_model.cues
        if not 0 <= row < len(cues) or not cues[row].parent:
            return None
        group = next((c for c in cues if c.kind == "group" and c.number == cues[row].parent), None)
        if group is None or group.group_mode != "playlist":
            return None
        children = [i for i, c in enumerate(cues) if c.parent == group.number]
        if row not in children or children.index(row) + 1 >= len(children):
            return None
        return children[children.index(row) + 1]

    def _pause_cue(self, target: Cue, paused: bool) -> None:
        for voice in self._cue_voices:
            if not voice.done and voice.tag is target:
                voice.paused = paused

    def _loop_frames(self, cue: Cue) -> tuple[int, int]:
        """A cue's loop in and out points as frames at the mixer's rate (0 means the file's start or end)."""
        rate = self.mixer.rate if self.mixer is not None else 48000
        return round(cue.loop_in * rate), round(cue.loop_out * rate)

    def _start_midifile(self, cue: Cue) -> None:
        """Play the cue's MIDI file out to its port. It plays on in the background until it ends or Stop or Panic."""
        self._stop_midifile(cue)
        player = midi.MidiFilePlayer(cue.file, cue.target)
        self._midifile_runs[id(cue)] = (cue, player)
        player.start()

    def _stop_midifile(self, cue: Cue | None = None) -> None:
        for key, (owner, player) in list(self._midifile_runs.items()):
            if cue is not None and owner is not cue:
                continue
            player.stop()
            del self._midifile_runs[key]

    def _run_program(self, cue: Cue) -> None:
        """Start a program with the arguments typed on the cue. No shell is used, so nothing is interpreted."""
        program = cue.file.strip()
        if not program:
            raise ValueError("Choose a program for this cue.")
        arguments = shlex.split(cue.text)
        subprocess.Popen([program, *arguments], stdin=subprocess.DEVNULL)

    def _retarget_cue(self, cue: Cue) -> None:
        """Point the named cue at a new target: a cue number for control, fade and devamp cues, a file for audio and video."""
        number = cue.target.strip()
        new = cue.new_target.strip()
        target = next((c for c in self.show_model.cues if c.number == number), None)
        if target is None:
            raise ValueError(f"There is no cue {number or '(blank)'} to change.")
        if target.kind in ("control", "fade", "devamp", "target"):
            target.target = new
        elif target.kind in ("audio", "video"):
            target.file = new
        else:
            raise ValueError(f"Cue {number} ({target.kind}) has no target to change.")

    def _devamp_cue(self, cue: Cue) -> None:
        """Stop the loop on the target cue's playing voices so they play out to the end."""
        if self.mixer is None:
            return
        number = cue.target.strip()
        for voice in self._cue_voices:
            if not voice.done and voice.tag is not None and getattr(voice.tag, "number", None) == number:
                self.mixer.devamp(voice)

    def _fade_cue(self, cue: Cue) -> None:
        number = cue.target.strip()
        target = next((c for c in self.show_model.cues if c.number == number), None)
        if target is not None and target.kind == "video":
            self._ramp_video(number, cue.level, cue.seconds)
            return
        if self.mixer is None:
            return
        for voice in self._cue_voices:
            if not voice.done and voice.tag.number == number:
                voice.fade(cue.level, cue.seconds, self.mixer.rate)

    def _ramp_video(self, number: str, level: float, seconds: float) -> None:
        """Move a playing video's audio level to the target over the given seconds."""
        start = self._video.volume(number)
        if start is None:
            return
        if seconds <= 0:
            self._video.set_volume(number, level)
            return
        steps = max(1, round(seconds * 1000 / DMX_STEP_MS))
        for step in range(1, steps + 1):
            value = start + (level - start) * step / steps
            self._after(step * DMX_STEP_MS, lambda v=value: self._video.set_volume(number, v))

    def _preload(self, cue: Cue) -> None:
        """Load an audio cue into the mixer paused, so running it later starts at once."""
        if self.mixer is None or cue.kind != "audio" or not cue.file:
            return
        if any(v.tag is cue and not v.done for v in self._cue_voices):
            return
        clip = self._clip(cue.file)
        voice = Voice(
            clip=clip,
            matrix=route_matrix(cue.route, clip.channels, self.mixer.out_channels),
            loop=cue.loop,
            loop_start=self._loop_frames(cue)[0],
            loop_end=self._loop_frames(cue)[1],
            bed=True,
            tag=cue,
            paused=True,
            preloaded=True,
        )
        self.mixer.add(voice)
        self._cue_voices.append(voice)

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
        if not self._editing or not 0 <= row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[row]
        groups = [
            (f"{c.number}  {c.name}", c.number)
            for c in self.show_model.cues if c.kind == "group" and c.number != cue.number
        ]
        dialog = CueDialog(
            cue, self, self.mixer.out_channels if self.mixer is not None else MAX_CHANNELS,
            video_choices=video_out.output_choices(self.show_model.video_routes),
            groups=groups,
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

    def go_row(self, row: int, replace: bool = True, _pre_done: bool = False) -> None:
        """Start the cue in this row with its fade-in and move the playhead down.

        replace=True fades out whatever cue is already playing. replace=False lets it run on.
        """
        if not self.show_model.cues[row].armed:
            self._advance(row)  # disarmed cues are skipped, as QLab does on GO
            return
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        if cue.pre_wait_ms > 0 and not _pre_done:
            self._after(cue.pre_wait_ms, lambda: self.go_row(row, replace=replace, _pre_done=True))
            return
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
                    self._video.play(cue.video_output, cue.file, self.show_model.video_routes, cue.number)
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
        if cue.kind == "memo":
            self._finish(row)
            return
        if cue.kind == "mic":
            self.start_talkback()
            self._finish(row)
            return
        if cue.kind == "network":
            try:
                netsend.send(cue.protocol, cue.target, cue.text)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot send network cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "midi":
            try:
                midi.send_out(cue.target, cue.text)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot send MIDI cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "timecode":
            try:
                self._start_timecode(cue)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot start timecode", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "light":
            try:
                self._run_light(cue)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot send light cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "osc":
            try:
                osc.send(cue.target, cue.text)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot send OSC", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "wait":
            next_row = self._playlist_next_row(row)
            if next_row is None:
                next_row = row + 1 if row + 1 < len(cues) else None
            if next_row is not None:
                self._after(cue.duration_ms, lambda r=next_row: self.go_row(r, replace=False))
            self._advance(row)
            return
        if cue.kind == "group":
            children = [i for i, child in enumerate(cues) if child.parent == cue.number]
            if cue.group_mode == "playlist":
                if children:
                    self.go_row(children[0], replace=False)
            else:
                for i in children:
                    self.go_row(i, replace=False)
            self._advance(row)
            return
        if cue.kind == "control":
            self._control(cue)
            if cue.action != "goto":  # Goto leaves the playhead on its target
                self._finish(row)
            return
        if cue.kind == "text":
            try:
                self._video.show_text(cue.video_output, cue.text, cue.font_size, self.show_model.video_routes)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "fade":
            self._fade_cue(cue)
            self._finish(row)
            return
        if cue.kind == "devamp":
            self._devamp_cue(cue)
            self._finish(row)
            return
        if cue.kind == "midifile":
            try:
                self._start_midifile(cue)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot play MIDI file", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "program":
            try:
                self._run_program(cue)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot run program", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "target":
            try:
                self._retarget_cue(cue)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot change target", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if self.mixer is None:
            return
        if cue.kind == "audio" and cue.file:
            try:
                clip = self._clip(cue.file)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot play cue", f"{cue.name}\n\n{exc}")
                return
            ready = next((v for v in self._cue_voices if v.tag is cue and v.preloaded and not v.done), None)
            if ready is not None:
                ready.paused = False
                ready.preloaded = False
                self._playing_cue = cue
                if cue.then == "continue" and row + 1 < len(cues):
                    self.go_row(row + 1, replace=False)
                self.table.selectRow(min(row + 1, len(cues) - 1))
                return
            if replace:
                for voice in self._cue_voices:
                    if not voice.done and not voice.stop_when_done and not voice.preloaded:
                        self.mixer.fade_out(voice, voice.tag.fade_out)
            voice = Voice(
                clip=clip,
                matrix=route_matrix(cue.route, clip.channels, self.mixer.out_channels),
                loop=cue.loop,
                loop_start=self._loop_frames(cue)[0],
                loop_end=self._loop_frames(cue)[1],
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
        self._stop_timecode()
        self._stop_midifile()
        for timer in self._timers:
            timer.stop()
        self._timers = []
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
                next_child = self._playlist_next_row(row) if row >= 0 else None
                if next_child is not None:
                    self.go_row(next_child, replace=False)
                elif row >= 0 and voice.tag.then == "follow" and row + 1 < len(cues):
                    if voice.tag.post_wait_ms > 0:
                        self._after(voice.tag.post_wait_ms, lambda r=row + 1: self.go_row(r, replace=False))
                    else:
                        self.go_row(row + 1, replace=False)

        self._cue_voices = [v for v in self._cue_voices if not v.done]
        sounding = [v for v in self._cue_voices if not v.preloaded]
        active = sounding[-1].tag if sounding else None
        if active is not self._playing_cue:
            self._playing_cue = active
            self._refresh_table()
        self._radio_tick()
        if self._recorder is not None:
            self._recorder.drain()
        self._remote_tick()
        while not self._midi_commands.empty():
            self._run_command(self._midi_commands.get_nowait())
        self._poll_timecode()
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

    def start_timecode_listen(self) -> None:
        """Pick the MIDI input that carries timecode. Cues with a timecode trigger then fire as it passes them."""
        try:
            import mido

            ports = mido.get_input_names()
        except Exception as exc:
            QMessageBox.critical(self, "Timecode", f"Could not list MIDI inputs:\n{exc}")
            return
        if not ports:
            QMessageBox.information(self, "Timecode", "No MIDI input ports were found.")
            return
        name, ok = QInputDialog.getItem(self, "Timecode input", "Choose the MIDI port that sends timecode:", ports, 0, False)
        if not ok:
            return
        self.stop_timecode_listen()
        try:
            self._tc_listen = mtc.TimecodeInput(name, lambda ms, fps: self._tc_queue.put((ms, fps)))
        except Exception as exc:
            QMessageBox.critical(self, "Timecode", f"Could not open that MIDI port:\n{exc}")

    def stop_timecode_listen(self) -> None:
        if self._tc_listen is not None:
            self._tc_listen.close()
            self._tc_listen = None
        self._tc_triggers.set_items([])
        while not self._tc_queue.empty():
            self._tc_queue.get_nowait()

    def _poll_timecode(self) -> None:
        """Runs on the window's timer: fires cues whose timecode trigger the clock has just passed."""
        while not self._tc_queue.empty():
            ms, fps = self._tc_queue.get_nowait()
            self._tc_triggers.set_items(self._timecode_trigger_items(fps))
            for number in self._tc_triggers.advance(ms):
                row = next((i for i, c in enumerate(self.show_model.cues) if c.number == number), None)
                if row is not None:
                    self.go_row(row)

    def _timecode_trigger_items(self, fps: int) -> list[tuple[int, str]]:
        items = []
        for cue in self.show_model.cues:
            if not cue.trigger_tc:
                continue
            try:
                items.append((mtc.parse_timecode(cue.trigger_tc, fps), cue.number))
            except ValueError:
                continue  # a bad trigger is ignored; the cue dialog shows the format
        return items

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
