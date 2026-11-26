"""CueRunner main window: transport, cue list, soundboard, countdown, follow and continue cues, updates.

Playback runs on cuerunner.engine. Cues and soundboard pads are voices in one mixer, so
they can overlap, and the mixer is fed to the sound card by audio_io.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QSettings, QThread, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QScrollArea,
    QStackedWidget,
    QTabBar,
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
    QListWidget,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QSpinBox,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cuerunner import allenheath, artnet, console_ui, fx, vst, launcher, midi, mtc, netsend, osc, sacn, settings_ui, spotify, spotify_ui, updater, video_out, waveform
from cuerunner.devices import DeviceInfo, label as devices_label
from cuerunner.countdown import Countdown
from cuerunner.dsp import MicChain
from cuerunner.feedback import FeedbackSuppressor
from cuerunner.fx_ui import EffectParams
from cuerunner.engine import (
    output_gains,
    Clip,
    LiveVoice,
    Mixer,
    Voice,
    default_matrix,
    load_clip,
    probe_duration_ms,
    route_matrix,
)
from cuerunner.cart_view import CartView
from cuerunner.model import (
    PAD_COUNT,
    AudioPatch,
    Cue,
    CueList,
    Pad,
    RadioSettings,
    Show,
    group_mode_of,
)
from cuerunner.playlist import Playlist, scan_audio
import math
import queue
import random

from cuerunner import levels_ui
from cuerunner.levels_ui import OutputLevels
from cuerunner.midi import MidiInput
from cuerunner.recorder import MAX_TRACKS, Recorder
from cuerunner.remote import RemoteServer, RemoteState, lan_ip
from cuerunner.soundboard import Soundboard
from cuerunner.theme import apply_dark_theme
from cuerunner.timecode import format_clock, format_duration, format_hms
from cuerunner.timeline_view import TimelineView
from cuerunner.version import __version__

BACKUP_INTERVAL_MS = 15 * 60 * 1000  # automatic show snapshot every 15 minutes
DMX_STEP_MS = 40  # light ramps send a new frame this often
INSPECTOR_MAX_HEIGHT = 250  # the inspector scrolls past this, so the cue list keeps its room
INSPECTOR_TABS = ["Basics", "Triggers", "Timeline", "I/O", "Time & Loops", "Levels",
                  "Objects", "Trim", "Audio FX"]
# Qt reads a single & as a keyboard shortcut marker, so it is doubled for display only.
TAB_LABELS = [t.replace("&", "&&") for t in INSPECTOR_TABS]
MIDI_TRIGGER_KINDS = [("Note On", "note_on"), ("Note Off", "note_off"), ("Control Change", "control_change")]
SECOND_TRIGGER_ACTIONS = [("does nothing", "nothing"), ("starts it again", "restart"),
                          ("stops it", "stop"), ("pauses it", "pause")]
FADE_CURVES = [("Linear curve", "linear"), ("Smooth curve", "smooth")]
GROUP_MODE_LABELS = [
    ("Timeline: start every cue at once", "timeline"),
    ("Playlist: one cue after another", "playlist"),
    ("Start first and enter: start the first cue, playhead steps in", "start_first_enter"),
    ("Start first: start the first cue, playhead skips the group", "start_first"),
    ("Start random: start one cue at random", "start_random"),
]
CONTINUE_MODES = [("Do not continue", "none"), ("Auto-follow", "follow"), ("Auto-continue", "continue")]


def _file_format(path: str) -> str:
    """What the I/O tab shows about the file itself, read from its header."""
    try:
        import soundfile as sf

        info = sf.info(path)
        return f"{info.format} \u2014 {info.channels} channels @ {int(info.samplerate)} Hz"
    except Exception:
        return "unknown format"


def _route_summary(route: list) -> str:
    """Plain words for a cue's output routing, as the I/O tab and the cue dialog show it."""
    if not route:
        return "Default"
    return "; ".join(
        f"In {i + 1} → " + (", ".join(str(o) for o in group) if group else "none")
        for i, group in enumerate(route)
    )


def _format_duration(ms: int) -> str:
    """HH:MM:SS.mmm, as QLab shows durations."""
    if ms <= 0:
        return "00:00:00.000"
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


MAIN_QSS = """
/* The whole window is dark, so nothing falls back to the system's light palette and
   looks like a different app bolted on. Specific widgets override these below. */
QWidget { background: #3b3b3f; color: #ececf0; font-size: 13px; }
QLabel { background: transparent; color: #ececf0; }
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled { color: #83838c; }
QMainWindow, QDialog { background: #343438; }
QMenuBar { background: #2e2e32; color: #ececf0; }
QMenuBar::item:selected { background: #3b82d6; }
QMenu { background: #35353a; color: #ececf0; border: 1px solid #2a2a2e; }
QMenu::item:selected { background: #3b82d6; }
QMenu::separator { height: 1px; background: #2a2a2e; margin: 4px 8px; }
QPushButton {
    background: #4a4a50; color: #ececf0; border: 1px solid #27272b;
    border-radius: 4px; padding: 5px 12px;
}
QPushButton:hover { background: #55555c; }
QPushButton:pressed { background: #3f3f45; }
QPushButton:disabled { background: #3e3e43; color: #83838c; border-color: #333338; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QKeySequenceEdit {
    background: #2a2a2e; color: #ececf0; border: 1px solid #27272b;
    border-radius: 3px; padding: 3px 6px; selection-background-color: #3b82d6;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QKeySequenceEdit:disabled { background: #343438; color: #83838c; }
QLineEdit:read-only { background: #343438; color: #c8c8d0; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #35353a; color: #ececf0; selection-background-color: #3b82d6;
    border: 1px solid #27272b;
}
QCheckBox, QRadioButton { background: transparent; color: #ececf0; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator { border: 1px solid #6a6a74; border-radius: 3px; background: #2a2a2e; }
QCheckBox::indicator:checked { background: #3b82d6; border-color: #3b82d6; }
QCheckBox::indicator:disabled { border-color: #4a4a50; background: #343438; }
QRadioButton::indicator { border: 1px solid #6a6a74; border-radius: 7px; background: #2a2a2e; }
QRadioButton::indicator:checked { background: #3b82d6; border-color: #3b82d6; }
QTabBar::tab {
    background: #3a3a3f; color: #c8c8d0; border: 1px solid #2a2a2e;
    padding: 5px 14px; margin-right: 1px;
}
QTabBar::tab:selected { background: #4e4e56; color: #ffffff; }
QTabBar::tab:hover { background: #46464d; }
QListWidget {
    background: #2a2a2e; color: #ececf0; border: 1px solid #27272b;
    selection-background-color: #3b82d6;
}
QScrollArea, QScrollArea > QWidget > QWidget { background: #3b3b3f; }
QFrame#inspectorPage, QWidget#inspectorBar { background: #3b3b3f; }
QScrollBar:vertical { background: #2e2e32; width: 11px; margin: 0; }
QScrollBar:horizontal { background: #2e2e32; height: 11px; margin: 0; }
QScrollBar::handle { background: #55555c; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #64646c; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSlider::groove:vertical { background: #2a2a2e; width: 6px; border-radius: 3px; }
QSlider::handle:vertical {
    background: #d9d9e0; height: 12px; margin: 0 -5px; border-radius: 3px;
}
QProgressBar { background: #2a2a2e; border: 1px solid #27272b; border-radius: 3px; }
QProgressBar::chunk { background: #3b82d6; }
QToolTip { background: #26262a; color: #ececf0; border: 1px solid #4a4a50; }
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
QLabel#alert {
    background: #7a2520; color: #ffe9e7; border: 1px solid #a2342c;
    border-radius: 4px; padding: 6px 10px; font-size: 13px;
}
QPushButton#cartCell {
    background: #35353a; color: #d6d6de; border: 1px solid #2a2a2e; border-radius: 5px;
    font-size: 12px; padding: 4px;
}
QPushButton#cartCell:disabled { background: #303034; border-color: #2a2a2e; color: #55555c; }
QPushButton#cartCell[filled="true"] { background: #44444c; color: #ffffff; }
QPushButton#cartCell:checked { background: #3b82d6; color: #ffffff; border-color: #5a9ae6; }
QListWidget#listSidebar { background: #2e2e32; border: 1px solid #27272b; }
QTableWidget::item:selected, QTableWidget::item:selected:!active {
    background: #3b82d6; color: #ffffff;
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
# QLab's list is number, name, target and the three timings. Everything else is detail
# that now lives in the inspector, so it starts hidden and can be switched back on.
EXTRA_COLUMNS = ["Fade In", "Fade Out", "Loop", "Progress", "Remaining", "Hotkey"]
MAX_CHANNELS = 128  # what QLab allows, and what a Dante Virtual Soundcard can present

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


class PatchDialog(QDialog):
    """Name the audio outputs the show plays through, as QLab's audio patches do.

    A patch is a name, a device and a channel count. Cues choose a patch on their I/O tab.
    """

    COLUMNS = ["Name", "Device", "Channels"]

    def __init__(self, patches: list[AudioPatch], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio Patches")
        self.resize(620, 300)
        self._devices = _output_device_names()

        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        for patch in patches:
            self._add_row(patch)

        add = QPushButton("+")
        remove = QPushButton("\u2212")
        add.clicked.connect(lambda: self._add_row(AudioPatch(name=self._next_name())))
        remove.clicked.connect(self._remove_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cues choose one of these on their I/O tab."))
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)

    def _next_name(self) -> str:
        return f"Patch {self.table.rowCount() + 1}"

    def _add_row(self, patch: AudioPatch) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(patch.name))
        device = QComboBox()
        device.addItem("System default output", "")
        for name in self._devices:
            device.addItem(name, name)
        device.setCurrentIndex(max(0, device.findData(patch.device)))
        self.table.setCellWidget(row, 1, device)
        channels = QSpinBox()
        channels.setRange(1, MAX_CHANNELS)
        channels.setValue(max(1, patch.channels))
        self.table.setCellWidget(row, 2, channels)

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0 and self.table.rowCount() > 1:  # a show always keeps at least one patch
            self.table.removeRow(row)

    def result_patches(self) -> list[AudioPatch]:
        patches = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            name = (item.text().strip() if item else "") or f"Patch {row + 1}"
            patches.append(AudioPatch(
                name=name,
                device=self.table.cellWidget(row, 1).currentData() or "",
                channels=self.table.cellWidget(row, 2).value(),
            ))
        return patches or [AudioPatch()]


def _output_device_names() -> list[str]:
    try:
        from cuerunner.audio_io import output_devices

        return [devices_label(d) for d in output_devices()]
    except Exception:
        return []


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
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.setValue(round(cue.opacity * 100))
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
        if self._cue_kind == "video":
            form.addRow("Opacity", self.opacity_spin)
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
            for label, value in GROUP_MODE_LABELS:
                self.group_mode_box.addItem(label, value)
            self.group_mode_box.setCurrentIndex(
                max(0, self.group_mode_box.findData(group_mode_of(cue))))
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
        if cue.kind == "video":
            cue.opacity = self.opacity_spin.value() / 100
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
        self._undo: list[tuple[str, str]] = []  # (what changed, the cue list before it)
        self._redo: list[tuple[str, str]] = []
        self._undo_at = 0.0
        self._saved_json = self.show_model.to_json()  # the show as it was last saved or opened
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
        self._midi_triggers: queue.Queue[tuple[str, int, int]] = queue.Queue()
        self._wall_clock_fired: dict[str, str] = {}  # cue number -> the day it last fired
        self._ducking: Cue | None = None  # the cue currently ducking the others
        self._tc_listen: mtc.TimecodeInput | None = None  # incoming MIDI Timecode
        self._tc_queue: queue.Queue[tuple[int, int]] = queue.Queue()  # (ms, fps) from the MIDI thread
        self._tc_triggers = mtc.TimecodeTriggers()
        self._spotify: spotify.SpotifyClient | None = None
        self._spotify_playing = False
        self._console: allenheath.MidiTcpSender | None = None

        self._build_toolbar()
        self._build_menus()
        self._last_row = -1  # the last row that was highlighted; GO continues from it
        self._refreshing = False  # True while the table is being rebuilt, not edited
        self._problems: list[str] = []  # recent cue failures, newest last
        self._panicking = False  # True while the panic fade is running
        self._last_panic = 0.0  # when Escape was last pressed, for spotting a double tap
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.timeout.connect(lambda: self.alert_label.setVisible(False))
        self._quitting = False  # guards against the quit being handled twice
        self._build_central()
        self._load_view_settings()
        self.setAcceptDrops(True)  # drop audio, video or MIDI files onto the window to add cues
        QApplication.instance().installEventFilter(self)  # clicks elsewhere clear the highlight

        go_key = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        go_key.activated.connect(self.go)
        panic_key = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        panic_key.activated.connect(self._panic_pressed)

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
        # A window that was never shown has nobody to ask, so it just closes.
        if self.isVisible() and not self.confirm_discard("closing"):
            event.ignore()
            return
        QApplication.instance().removeEventFilter(self)
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
        if not hasattr(self, "insp_name"):
            return
        row = self._selected_row()
        cues = self.show_model.cues
        has_cue = 0 <= row < len(cues)
        editable = has_cue and self._editing
        self._insp_busy = True
        try:
            for widget in self._insp_editable:
                widget.setEnabled(editable)
            self.insp_full_edit.setEnabled(has_cue)
            if not has_cue:
                for widget in (self.insp_number, self.insp_duration, self.insp_target,
                               self.insp_name):
                    widget.clear()
                self.insp_notes.clear()
                self.insp_armed.setChecked(False)
                self.insp_loop.setChecked(False)
                self.insp_trigger_tc.clear()
                self.insp_route_label.setText("Default")
                self.insp_audio_map.setText("Default")
                self.insp_file_target.setText("\u2014")
                self.insp_file_format.setText("\u2014")
                self.insp_objects_note.setText("")
                return
            cue = cues[row]
            self.insp_number.setText(cue.number)
            self.insp_duration.setText(_format_duration(cue.duration_ms))
            self.insp_target.setText(self._target_text(cue))
            self.insp_name.setText(cue.name)
            self.insp_pre.setValue(cue.pre_wait_ms / 1000)
            self.insp_post.setValue(cue.post_wait_ms / 1000)
            index = self.insp_continue.findData(cue.then)
            self.insp_continue.setCurrentIndex(max(index, 0))
            index = self.insp_colour.findData(cue.colour or "")
            self.insp_colour.setCurrentIndex(max(index, 0))
            self.insp_hotkey.setKeySequence(QKeySequence(cue.hotkey))
            self.insp_notes.setPlainText(cue.notes)
            self.insp_armed.setChecked(cue.armed)
            self.insp_flagged.setChecked(cue.flagged)
            self.insp_auto_load.setChecked(cue.auto_load)
            self.insp_skip.setChecked(cue.skip_if_disarmed)
            self.insp_loop.setChecked(cue.loop)
            self.insp_loop_in.setValue(cue.loop_in)
            self.insp_loop_out.setValue(cue.loop_out)
            self.insp_trigger_tc.setText(cue.trigger_tc)
            self.insp_wall_clock.setText(cue.wall_clock)
            self.insp_midi_on.setChecked(cue.midi_trigger_on)
            self.insp_midi_kind.setCurrentIndex(max(0, self.insp_midi_kind.findData(cue.midi_trigger_kind)))
            self.insp_midi_number.setValue(cue.midi_trigger_number)
            self.insp_midi_channel.setCurrentIndex(max(0, self.insp_midi_channel.findData(cue.midi_trigger_channel)))
            self.insp_fade_stop.setChecked(cue.fade_stop_others)
            self.insp_fade_stop_time.setValue(cue.fade_stop_time)
            self.insp_duck_others.setChecked(cue.duck_others)
            self.insp_duck_by.setValue(cue.duck_by_db)
            self.insp_duck_time.setValue(cue.duck_time)
            self.insp_second_trigger.setCurrentIndex(
                max(0, self.insp_second_trigger.findData(cue.second_trigger)))
            self.insp_route_label.setText(_route_summary(cue.route))
            self.insp_audio_map.setText(_route_summary(cue.route))
            self._refresh_io_tab(cue)
            self._refresh_levels_tab(cue, editable)
            self.insp_fade_in.setValue(cue.fade_in)
            self.insp_fade_out.setValue(cue.fade_out)
            self.insp_start_time.setValue(cue.trim_in)
            self.insp_end_time.setValue(cue.trim_out)
            self.insp_trim.setValue(cue.trim_db)
            self.insp_play_count.setValue(max(1, cue.play_count))
            self.insp_infinite.setChecked(cue.loop)
            self.insp_play_once.setChecked(not cue.loop)
            self.insp_rate.setValue(cue.rate or 1.0)
            self.insp_preserve_pitch.setChecked(cue.preserve_pitch)
            self.insp_integrated.setChecked(cue.integrated_fade)
            self.insp_curve.setCurrentIndex(max(0, self.insp_curve.findData(cue.fade_curve)))
            self._refresh_effects(cue)
            self._refresh_objects_tab(cue, editable)
            self._refresh_timeline_tab(cue, editable)
            self._refresh_waveform(cue)
        finally:
            self._insp_busy = False

    def _refresh_timeline_tab(self, cue: Cue, editable: bool) -> None:
        """Fill the Timeline tab, and show the tab only for a group cue in timeline mode.

        QLab puts a Timeline tab on Timeline Group cues and nowhere else, because a
        timeline is only meaningful where the children all start together.
        """
        if not hasattr(self, "insp_timeline"):
            return
        index = INSPECTOR_TABS.index("Timeline")
        wanted = cue.kind == "group" and group_mode_of(cue) == "timeline"
        # Qt moves off a tab it is told to hide, but to whichever one is next along. Land
        # on Basics instead, which is where QLab leaves you.
        showing = self.insp_tabs.currentIndex() == index
        self.insp_tabs.setTabVisible(index, wanted)
        if not wanted:
            if showing:
                self.insp_tabs.setCurrentIndex(0)
            self.insp_timeline.set_cues([])
            return
        if self.insp_timeline.is_dragging():
            return
        cues = self.show_model.cues
        children = [cues[i] for i in self._group_children(cue.number)]
        self.insp_timeline.set_cues(children)
        self.insp_timeline.set_editable(editable)
        self.insp_timeline.set_playhead(self._timeline_playhead(children))

    def _timeline_playhead(self, children: list[Cue]) -> float:
        """How far into the group playback has got, taken from whichever child is furthest.

        This is the yellow line, and dragged cues snap to it, so pausing at a moment and
        dragging a cue onto it lines the cue up exactly.
        """
        furthest = 0.0
        if self.mixer is None:  # no output open, so nothing is playing to follow
            return 0.0
        rate = max(self.mixer.rate, 1)
        for voice in self._cue_voices:
            cue = next((c for c in children if c is voice.tag), None)
            if cue is None or voice.done or voice.preloaded:
                continue
            played = max(voice.position / rate - cue.trim_in, 0.0)
            furthest = max(furthest, cue.pre_wait_ms / 1000 + played)
        return furthest

    def _timeline_changed(self) -> None:
        """A drag or nudge in the timeline changed a child cue's timing."""
        self._refresh_table()
        self._refresh_inspector()

    def _refresh_waveform(self, cue: Cue | None = None) -> None:
        """Load the selected cue's waveform, but only while the Time & Loops tab is showing.

        Reading peaks means reading the whole file, so it is not done for cues the user is
        only clicking past. waveform.peaks caches, so coming back to a cue is instant.
        """
        if not hasattr(self, "insp_wave") or self.insp_wave.is_dragging():
            return
        if self.insp_tabs.currentIndex() != INSPECTOR_TABS.index("Time & Loops"):
            return
        if cue is None:
            row = self._selected_row()
            cues = self.show_model.cues
            cue = cues[row] if 0 <= row < len(cues) else None
        if cue is None:
            self.insp_wave.set_clip("", 0.0)
            return
        seconds = cue.duration_ms / 1000 if cue.duration_ms else self._file_seconds(cue)
        self.insp_wave.set_clip(cue.file if cue.kind == "audio" else "", seconds)
        self.insp_wave.set_markers(cue.loop_in, cue.loop_out)
        self.insp_wave.set_slices(cue.slices)
        self.insp_wave.set_playhead(cue.trim_in)

    def _file_seconds(self, cue: Cue) -> float:
        """A cue's length from the file header, for cues that have not been probed yet."""
        if cue.kind != "audio" or not cue.file:
            return 0.0
        try:
            return probe_duration_ms(cue.file) / 1000
        except Exception:
            return 0.0

    def _add_slice(self) -> None:
        """Mark a point in the file, at the loop in marker."""
        cue = self._selected_cue()
        if cue is None or not self._editing:
            return
        point = round(self.insp_wave.markers()[0], 3)
        if point not in cue.slices:
            cue.slices = sorted(cue.slices + [point])
        self.insp_wave.set_slices(cue.slices)

    def _clear_slices(self) -> None:
        cue = self._selected_cue()
        if cue is None or not self._editing:
            return
        cue.slices = []
        self.insp_wave.set_slices(cue.slices)

    def _seek_selected_cue(self, seconds: float) -> None:
        """Clicking the waveform jumps the cue to that point, if it is playing."""
        cue = self._selected_cue()
        if cue is None or self.mixer is None:
            return
        frame = int(max(0.0, seconds) * self.mixer.rate)
        for voice in self._cue_voices:
            if voice.tag is cue and not voice.done:
                voice.position = min(frame, max(voice.clip.frames - 1, 0))
                voice.natural_end = False

    def _follow_playhead(self) -> None:
        """While a cue plays, the waveform shows where it has got to."""
        if not hasattr(self, "insp_wave") or self.insp_wave.is_dragging():
            return
        if self.insp_tabs.currentIndex() != INSPECTOR_TABS.index("Time & Loops"):
            return
        cue = self._selected_cue()
        if cue is None or self.mixer is None:
            return
        for voice in self._cue_voices:
            if voice.tag is cue and not voice.done and not voice.preloaded:
                self.insp_wave.set_playhead(voice.position / self.mixer.rate)
                return

    def _wave_markers_changed(self, loop_in: float, loop_out: float) -> None:
        """A marker was dragged: update the spin boxes, which write back to the cue."""
        self.insp_loop_in.setValue(loop_in)
        self.insp_loop_out.setValue(loop_out)

    def _patches(self) -> list:
        """The show's audio patches, never empty."""
        if not self.show_model.audio_patches:
            self.show_model.audio_patches = [AudioPatch()]
        return self.show_model.audio_patches

    def _patch_for(self, cue: Cue):
        """The patch a cue plays out of: the one it names, else the first."""
        patches = self._patches()
        return next((p for p in patches if p.name == cue.patch), patches[0])

    def _refresh_io_tab(self, cue: Cue) -> None:
        self.insp_file_target.setText(cue.file or "\u2014")
        self.insp_file_format.setText(_file_format(cue.file) if cue.file else "\u2014")
        self.insp_patch.clear()
        for patch in self._patches():
            self.insp_patch.addItem(patch.name, patch.name)
        chosen = self._patch_for(cue)
        self.insp_patch.setCurrentIndex(max(0, self.insp_patch.findData(chosen.name)))
        device = chosen.device or "system default output"
        self.insp_patch_format.setText(f"{chosen.channels} out \u2014 {device}")

    def _refresh_levels_tab(self, cue: Cue, editable: bool) -> None:
        self.insp_levels.set_outputs(self._patch_for(cue).channels)
        self.insp_levels.set_values(fx.gain_to_db(cue.volume), cue.output_levels,
                                    cue.output_mutes, cue.output_solos)
        self.insp_levels.set_editable(editable)

    def edit_audio_patches(self) -> None:
        """Name the outputs the show plays through, as QLab's audio patches do."""
        dialog = PatchDialog(self._patches(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.show_model.audio_patches = dialog.result_patches()
            self._refresh_inspector()

    def _refresh_effects(self, cue: Cue) -> None:
        """Fill the effect list, keeping whichever effect was being edited selected."""
        chosen = self.insp_fx_list.currentRow()
        self.insp_fx_list.clear()
        for effect in cue.effects:
            self.insp_fx_list.addItem(fx.describe(effect))
        if cue.effects:
            self.insp_fx_list.setCurrentRow(min(max(chosen, 0), len(cue.effects) - 1))
        self._refresh_effect_fields()

    def _refresh_effect_fields(self) -> None:
        """Build and fill the controls for whichever effect is selected."""
        cue = self._selected_cue()
        row = self.insp_fx_list.currentRow()
        has = cue is not None and 0 <= row < len(cue.effects)
        was_busy, self._insp_busy = self._insp_busy, True
        try:
            self.insp_fx_params.show_effect(cue.effects[row] if has else None)
            self.insp_fx_params.set_editable(has and self._editing)
        finally:
            self._insp_busy = was_busy

    def _selected_cue(self) -> Cue | None:
        row = self._selected_row()
        cues = self.show_model.cues
        return cues[row] if 0 <= row < len(cues) else None

    def _add_effect(self, kind: str) -> None:
        cue = self._selected_cue()
        if cue is None or not self._editing:
            return
        self._remember("Add Effect")
        cue.effects.append(fx.new_effect(kind))
        self._refresh_effects(cue)
        self.insp_fx_list.setCurrentRow(len(cue.effects) - 1)

    def _add_plugin(self) -> None:
        """Pick a VST3 or Audio Unit plugin and add it to the cue's chain."""
        cue = self._selected_cue()
        if cue is None or not self._editing:
            return
        if not vst.available():
            QMessageBox.information(
                self, "Plugins",
                "Plugin hosting is not available in this build of CueRunner.")
            return
        found = vst.installed()
        names = [vst.plugin_name(path) for path in found] + ["Choose a file\u2026"]
        choice, ok = QInputDialog.getItem(self, "Add plugin", "Plugin:", names, 0, False)
        if not ok:
            return
        if choice == names[-1]:
            start = str(vst.plugin_folders()[0]) if vst.plugin_folders() else ""
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Choose a plugin", start, "Plugins (*.vst3 *.component);;All files (*)")
            if not chosen:
                return
            path = chosen
        else:
            path = str(found[names.index(choice)])

        if vst.load(path) is None:
            QMessageBox.warning(self, "Plugins",
                                f"{vst.plugin_name(path)} could not be opened.\n\n"
                                "It may be an instrument rather than an effect, or built "
                                "for a different kind of Mac or PC.")
            return
        cue.effects.append({"kind": fx.VST_KIND, "path": path,
                            "name": vst.plugin_name(path), "state": "", "bypass": False})
        self._refresh_effects(cue)
        self.insp_fx_list.setCurrentRow(len(cue.effects) - 1)

    def _open_plugin_editor(self) -> None:
        """Open the plugin's own window, and keep whatever was set up in it."""
        cue = self._selected_cue()
        row = self.insp_fx_list.currentRow()
        if cue is None or not 0 <= row < len(cue.effects):
            return
        effect = cue.effects[row]
        plugin = vst.load(str(effect.get("path", "")), str(effect.get("state", "")))
        if plugin is None:
            QMessageBox.warning(self, "Plugins",
                                f"{effect.get('name', 'The plugin')} could not be opened.")
            return
        try:
            plugin.show_editor()
        except Exception as exc:
            QMessageBox.warning(self, "Plugins", f"That plugin has no window to show:\n{exc}")
            return
        effect["state"] = vst.save_state(plugin)

    def _remove_effect(self) -> None:
        cue = self._selected_cue()
        row = self.insp_fx_list.currentRow()
        if cue is None or not self._editing or not 0 <= row < len(cue.effects):
            return
        self._remember("Remove Effect")
        cue.effects.pop(row)
        self._refresh_effects(cue)

    def _refresh_objects_tab(self, cue: Cue, editable: bool) -> None:
        """The Objects tab only applies to cues that put something on a screen."""
        visual = cue.kind in ("video", "camera", "text")
        self.insp_display.clear()
        for label, value in video_out.output_choices(self.show_model.video_routes):
            self.insp_display.addItem(label, value)
        self.insp_display.setCurrentIndex(max(0, self.insp_display.findData(cue.video_output)))
        self.insp_opacity.setValue(round(cue.opacity * 100))
        self.insp_display.setEnabled(editable and visual)
        self.insp_opacity.setEnabled(editable and cue.kind == "video")
        if visual:
            self.insp_objects_note.setText("Opacity applies to video cues.")
        else:
            self.insp_objects_note.setText(f"A {cue.kind} cue does not appear on a screen.")

    def _inspector_routing(self) -> None:
        """The Routing… button on the I/O tab, for the selected cue."""
        row = self._selected_row()
        cues = self.show_model.cues
        if not 0 <= row < len(cues) or not self._editing:
            return
        cue = cues[row]
        out_channels = self.mixer.out_channels if self.mixer is not None else MAX_CHANNELS
        dialog = RoutingDialog(cue.route, out_channels, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            cue.route = dialog.result_route()
            self.insp_route_label.setText(_route_summary(cue.route))

    def _inspector_apply(self) -> None:
        """Write the inspector fields back to the selected cue (Edit mode only)."""
        if self._insp_busy or not self._editing:
            return
        row = self._selected_row()
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        self._remember("Edit Cue", coalesce=True)
        trigger = self.insp_trigger_tc.text().strip()
        if trigger:
            try:
                mtc.parse_timecode(trigger, 24)
            except ValueError:
                self.insp_trigger_tc.setStyleSheet("border: 1px solid #c0392b;")
                return  # keep the last valid trigger until the text is fixed
        self.insp_trigger_tc.setStyleSheet("")
        cue.trigger_tc = trigger
        cue.loop = self.insp_loop.isChecked() or self.insp_infinite.isChecked()
        cue.loop_in = self.insp_loop_in.value()
        cue.loop_out = self.insp_loop_out.value()
        name = self.insp_name.text().strip()
        if name:
            cue.name = name
        cue.pre_wait_ms = int(self.insp_pre.value() * 1000)
        cue.post_wait_ms = int(self.insp_post.value() * 1000)
        cue.then = self.insp_continue.currentData() or "none"
        cue.colour = self.insp_colour.currentData() or ""
        cue.hotkey = self.insp_hotkey.keySequence().toString()
        cue.wall_clock = self.insp_wall_clock.text().strip()
        cue.midi_trigger_on = self.insp_midi_on.isChecked()
        cue.midi_trigger_kind = self.insp_midi_kind.currentData() or "note_on"
        cue.midi_trigger_number = self.insp_midi_number.value()
        cue.midi_trigger_channel = self.insp_midi_channel.currentData() or 0
        cue.fade_stop_others = self.insp_fade_stop.isChecked()
        cue.fade_stop_time = self.insp_fade_stop_time.value()
        cue.duck_others = self.insp_duck_others.isChecked()
        cue.duck_by_db = self.insp_duck_by.value()
        cue.duck_time = self.insp_duck_time.value()
        cue.second_trigger = self.insp_second_trigger.currentData() or "nothing"
        cue.notes = self.insp_notes.toPlainText()
        cue.armed = self.insp_armed.isChecked()
        cue.flagged = self.insp_flagged.isChecked()
        cue.auto_load = self.insp_auto_load.isChecked()
        cue.skip_if_disarmed = self.insp_skip.isChecked()
        main_db, levels, mutes, solos = self.insp_levels.values()
        cue.volume = fx.db_to_gain(main_db)
        cue.output_levels = levels
        cue.output_mutes = mutes
        cue.output_solos = solos
        cue.patch = self.insp_patch.currentData() or ""
        cue.fade_in = self.insp_fade_in.value()
        cue.fade_out = self.insp_fade_out.value()
        cue.trim_in = self.insp_start_time.value()
        cue.trim_out = self.insp_end_time.value()
        cue.trim_db = self.insp_trim.value()
        cue.play_count = self.insp_play_count.value()
        chosen = self.insp_fx_list.currentRow()
        if 0 <= chosen < len(cue.effects):
            cue.effects[chosen].update(self.insp_fx_params.values())
            item = self.insp_fx_list.item(chosen)
            if item is not None:
                item.setText(fx.describe(cue.effects[chosen]))
        cue.rate = self.insp_rate.value()
        cue.preserve_pitch = self.insp_preserve_pitch.isChecked()
        cue.integrated_fade = self.insp_integrated.isChecked()
        cue.fade_curve = self.insp_curve.currentData() or "linear"
        if self.insp_display.isEnabled():
            cue.video_output = self.insp_display.currentData() or ""
        if self.insp_opacity.isEnabled():
            cue.opacity = self.insp_opacity.value() / 100
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
            mode = next((lbl.split(":")[0] for lbl, value in GROUP_MODE_LABELS
                         if value == group_mode_of(cue)), "Timeline")
            return f"{mode} group of {children} cue{'' if children == 1 else 's'}"
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
        # Quit goes through the window so unsaved work is noticed. On a Mac this lands in
        # the application menu and replaces the one Qt would add for itself.
        quit_action = file_menu.addAction("Quit", self.close, QKeySequence(QKeySequence.StandardKey.Quit))
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        edit_menu = self.menuBar().addMenu("Edit")
        self.undo_action = edit_menu.addAction("Undo", self.undo,
                                               QKeySequence(QKeySequence.StandardKey.Undo))
        self.redo_action = edit_menu.addAction("Redo", self.redo,
                                               QKeySequence(QKeySequence.StandardKey.Redo))
        self._refresh_undo_actions()

        view_menu = self.menuBar().addMenu("View")
        self._column_actions = {}
        for name in EXTRA_COLUMNS:
            action = view_menu.addAction(name)
            action.setCheckable(True)
            action.toggled.connect(lambda shown, n=name: self._show_column(n, shown))
            self._column_actions[name] = action
        view_menu.addSeparator()
        self.pads_action = view_menu.addAction("Pads and Countdown")
        self.pads_action.setCheckable(True)
        self.pads_action.toggled.connect(self._show_bottom_panel)

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
        self.setStyleSheet(MAIN_QSS)
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
        # Problems during a show appear here rather than in a dialog, so nothing blocks.
        self.alert_label = QLabel("")
        self.alert_label.setObjectName("alert")
        self.alert_label.setWordWrap(True)
        self.alert_label.setVisible(False)
        screens.addWidget(self.alert_label)
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
        self.panic_button = self._tool_button("Panic", "Fade everything out and stop (Esc; press twice to cut)", self._panic_pressed)
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
        # Double clicking a cue's name renames it in place; nothing else is typed into.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.itemChanged.connect(self._name_edited)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        # A plain count down the left, so cues can be referred to by position as well as
        # by their own number, which the user is free to change.
        rows = self.table.verticalHeader()
        rows.setVisible(True)
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(26)
        rows.setFixedWidth(40)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(NAME_COL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        for col, width in [(0, 50), (2, 80), (3, 80), (4, 80), (5, 70), (6, 70), (7, 50), (8, 80),
                           (PROGRESS_COL, 140), (REMAINING_COL, 100), (11, 80)]:
            self.table.setColumnWidth(col, width)
        for name in EXTRA_COLUMNS:
            self.table.setColumnHidden(COLUMNS.index(name), True)
        self.table.itemDoubleClicked.connect(lambda item: self.edit_row(item.row()))
        self.table.itemSelectionChanged.connect(self._remember_selected_row)
        self.table.itemSelectionChanged.connect(self._refresh_now_label)
        self.table.itemSelectionChanged.connect(self._refresh_inspector)
        self.table.setMinimumHeight(140)  # the cue list keeps its room; the inspector scrolls instead

        self.list_sidebar = QListWidget()
        self.list_sidebar.setObjectName("listSidebar")
        self.list_sidebar.setMaximumWidth(170)
        self.list_sidebar.currentRowChanged.connect(self._choose_list)
        new_list = QPushButton("New List")
        new_cart = QPushButton("New Cart")
        new_list.clicked.connect(lambda: self.add_cue_list("list"))
        new_cart.clicked.connect(lambda: self.add_cue_list("cart"))
        delete_list = QPushButton("Delete")
        delete_list.clicked.connect(self.remove_cue_list)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(4)
        side.addWidget(self.list_sidebar, 1)
        side.addWidget(new_list)
        side.addWidget(new_cart)
        side.addWidget(delete_list)
        self.sidebar_panel = QWidget()
        self.sidebar_panel.setLayout(side)
        self.sidebar_panel.setMaximumWidth(170)

        self.cart = CartView()
        self.cart.selected.connect(lambda _: self._refresh_inspector())
        self.cart.triggered.connect(self._go_cart_cue)
        self.cue_area = QStackedWidget()
        self.cue_area.addWidget(self.table)
        self.cue_area.addWidget(self.cart)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(6)
        middle.addWidget(self.sidebar_panel)
        middle.addWidget(self.cue_area, 1)
        layout.addLayout(middle, 1)

        # QLab-style inspector: a tab row, and the Basics form for the selected cue.
        # Fields apply straight away in Edit mode; Full Edit opens the complete cue dialog.
        self._insp_busy = False
        self.inspector_bar = QFrame()
        self.inspector_bar.setObjectName("inspectorBar")
        outer = QVBoxLayout(self.inspector_bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.insp_tabs = QTabBar()
        self.insp_tabs.setObjectName("inspectorTabs")
        self.insp_tabs.setExpanding(False)
        for title in TAB_LABELS:
            self.insp_tabs.addTab(title)
        self.insp_tabs.setCurrentIndex(0)
        outer.addWidget(self.insp_tabs)

        page = QFrame()
        page.setObjectName("inspectorPage")
        grid = QGridLayout(page)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        self.insp_number = QLineEdit()
        self.insp_number.setReadOnly(True)
        self.insp_duration = QLineEdit()
        self.insp_duration.setReadOnly(True)
        self.insp_pre = QDoubleSpinBox()
        self.insp_post = QDoubleSpinBox()
        for spin in (self.insp_pre, self.insp_post):
            spin.setRange(0, 3600)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setMaximumWidth(140)
        self.insp_continue = QComboBox()
        for label, value in CONTINUE_MODES:
            self.insp_continue.addItem(label, value)

        self.insp_name = QLineEdit()
        self.insp_name.setMinimumWidth(180)
        self.insp_target = QLineEdit()
        self.insp_target.setReadOnly(True)
        self.insp_notes = QPlainTextEdit()
        self.insp_notes.setMinimumHeight(44)
        self.insp_notes.setMaximumHeight(64)  # it asks for far more room than it needs
        self.insp_colour = QComboBox()
        for label, value in COLOURS:
            self.insp_colour.addItem(label, value)
        self.insp_armed = QCheckBox("Armed")
        self.insp_flagged = QCheckBox("Flagged")
        self.insp_auto_load = QCheckBox("Auto-load")
        self.insp_skip = QCheckBox("Skip if disarmed")
        self.insp_full_edit = QPushButton("Full Edit…")

        left = [("Number:", self.insp_number), ("Duration:", self.insp_duration),
                ("Pre-Wait:", self.insp_pre), ("Post-Wait:", self.insp_post),
                ("Continue:", self.insp_continue)]
        for row, (caption, widget) in enumerate(left):
            grid.addWidget(QLabel(caption), row, 0)
            grid.addWidget(widget, row, 1)
        right = [("Name:", self.insp_name), ("Target:", self.insp_target),
                 ("Colour:", self.insp_colour)]
        for row, (caption, widget) in enumerate(right):
            grid.addWidget(QLabel(caption), row, 2)
            grid.addWidget(widget, row, 3)
        grid.addWidget(QLabel("Notes:"), len(right), 2, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.insp_notes, len(right), 3)
        flags = QHBoxLayout()
        flags.setSpacing(18)  # room between the tick boxes so they do not run together
        for box in (self.insp_armed, self.insp_flagged, self.insp_auto_load, self.insp_skip):
            flags.addWidget(box)
        flags.addStretch(1)
        grid.addLayout(flags, len(left), 0, 1, 3)
        grid.setRowStretch(len(left) + 1, 1)
        grid.addWidget(self.insp_full_edit, len(left), 3, Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(3, 1)
        # One page per tab, in tab order, so the stack index matches the tab index.
        self.insp_stack = QStackedWidget()
        built_pages = {"Basics": page}

        # Triggers: the timecode that fires this cue.
        triggers_page = QFrame()
        triggers_page.setObjectName("inspectorPage")
        tgrid = QGridLayout(triggers_page)
        tgrid.setContentsMargins(12, 10, 12, 10)
        self.insp_hotkey = QKeySequenceEdit()
        self.insp_hotkey.setMaximumWidth(190)
        self.insp_midi_on = QCheckBox()
        self.insp_midi_kind = QComboBox()
        for label, value in MIDI_TRIGGER_KINDS:
            self.insp_midi_kind.addItem(label, value)
        self.insp_midi_number = QSpinBox()
        self.insp_midi_number.setRange(0, 127)
        self.insp_midi_number.setMaximumWidth(70)
        self.insp_midi_channel = QComboBox()
        self.insp_midi_channel.addItem("Any channel", 0)
        for channel in range(1, 17):
            self.insp_midi_channel.addItem(f"Channel {channel}", channel)
        self.insp_wall_clock = QLineEdit()
        self.insp_wall_clock.setPlaceholderText("HH:MM:SS, blank for none")
        self.insp_wall_clock.setMaximumWidth(190)
        self.insp_trigger_tc = QLineEdit()
        self.insp_trigger_tc.setPlaceholderText("HH:MM:SS:FF, blank for none")
        self.insp_trigger_tc.setMaximumWidth(190)

        tgrid.addWidget(QLabel("Hotkey Trigger:"), 0, 0)
        tgrid.addWidget(self.insp_hotkey, 0, 1, 1, 3)
        tgrid.addWidget(QLabel("MIDI Trigger:"), 1, 0)
        tgrid.addWidget(self.insp_midi_on, 1, 1)
        tgrid.addWidget(self.insp_midi_kind, 1, 2)
        tgrid.addWidget(self.insp_midi_number, 1, 3)
        tgrid.addWidget(self.insp_midi_channel, 2, 2, 1, 2)
        tgrid.addWidget(QLabel("Wall Clock Trigger:"), 3, 0)
        tgrid.addWidget(self.insp_wall_clock, 3, 1, 1, 3)
        tgrid.addWidget(QLabel("Timecode Trigger:"), 4, 0)
        tgrid.addWidget(self.insp_trigger_tc, 4, 1, 1, 3)

        # Right hand column: what happens to everything else when this cue starts.
        self.insp_fade_stop = QCheckBox("Fade && stop other cues over")
        self.insp_fade_stop_time = QDoubleSpinBox()
        self.insp_duck_others = QCheckBox("Duck other cues in this list while running")
        self.insp_duck_by = QDoubleSpinBox()
        self.insp_duck_by.setRange(-60.0, 0.0)
        self.insp_duck_by.setSuffix(" dB")
        self.insp_duck_time = QDoubleSpinBox()
        for spin in (self.insp_fade_stop_time, self.insp_duck_time):
            spin.setRange(0.0, 600.0)
            spin.setDecimals(1)
            spin.setSuffix(" s")
        for spin in (self.insp_fade_stop_time, self.insp_duck_time, self.insp_duck_by):
            spin.setMaximumWidth(110)
        self.insp_second_trigger = QComboBox()
        for label, value in SECOND_TRIGGER_ACTIONS:
            self.insp_second_trigger.addItem(label, value)
        right_box = QGridLayout()
        right_box.addWidget(QLabel("When starting this cue:"), 0, 0, 1, 2)
        right_box.addWidget(self.insp_fade_stop, 1, 0)
        right_box.addWidget(self.insp_fade_stop_time, 1, 1)
        right_box.addWidget(self.insp_duck_others, 2, 0, 1, 2)
        duck_row = QHBoxLayout()
        duck_row.addWidget(QLabel("Duck by:"))
        duck_row.addWidget(self.insp_duck_by)
        duck_row.addWidget(QLabel("over:"))
        duck_row.addWidget(self.insp_duck_time)
        duck_row.addStretch(1)
        right_box.addLayout(duck_row, 3, 0, 1, 2)
        second_row = QHBoxLayout()
        second_row.addWidget(QLabel("If running, a second trigger:"))
        second_row.addWidget(self.insp_second_trigger)
        second_row.addStretch(1)
        right_box.addLayout(second_row, 4, 0, 1, 2)
        right_box.setRowStretch(5, 1)
        tgrid.addLayout(right_box, 0, 5, 6, 1)
        tgrid.setColumnStretch(5, 1)
        tgrid.setRowStretch(6, 1)
        built_pages["Triggers"] = triggers_page

        # Timeline: a Timeline Group's children on a shared time ruler. Only group cues in
        # timeline mode have one, so the tab hides itself for everything else.
        timeline_page = QFrame()
        timeline_page.setObjectName("inspectorPage")
        tlgrid = QGridLayout(timeline_page)
        tlgrid.setContentsMargins(12, 10, 12, 10)
        tlgrid.setVerticalSpacing(6)
        self.insp_timeline = TimelineView()
        # The hint lives on the widget rather than under it: the inspector is short, and a
        # paragraph of instructions under the timeline is exactly the clutter to avoid.
        self.insp_timeline.setToolTip(
            "Drag a cue to set its pre-wait, either end to trim it, or the bottom edge to "
            "resize the row.\n"
            "Alt+\u2190 and Alt+\u2192 nudge by 0.1 s; add Shift for 0.01 s.\n"
            "Hold Ctrl while dragging to ignore the snap lines, Alt to slip the file."
        )
        tlgrid.addWidget(self.insp_timeline, 0, 0)
        tlgrid.setRowStretch(0, 1)
        tlgrid.setColumnStretch(0, 1)
        built_pages["Timeline"] = timeline_page

        # Time & Loops: looping and the loop points.
        time_page = QFrame()
        time_page.setObjectName("inspectorPage")
        ggrid = QGridLayout(time_page)
        ggrid.setContentsMargins(12, 10, 12, 10)
        self.insp_loop = QCheckBox("Loop until stopped")
        self.insp_loop_in = QDoubleSpinBox()
        self.insp_loop_out = QDoubleSpinBox()
        for spin in (self.insp_loop_in, self.insp_loop_out):
            spin.setRange(0, 86400)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setMaximumWidth(140)
        self.insp_wave = waveform.WaveformView()
        self.insp_start_time = QDoubleSpinBox()
        self.insp_end_time = QDoubleSpinBox()
        for spin in (self.insp_start_time, self.insp_end_time):
            spin.setRange(0, 86400)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setMaximumWidth(130)
        self.insp_play_once = QRadioButton("Play")
        self.insp_play_count = QSpinBox()
        self.insp_play_count.setRange(1, 999)
        self.insp_play_count.setMaximumWidth(70)
        self.insp_infinite = QRadioButton("Infinite loop  \u221e")
        plays = QButtonGroup(time_page)
        plays.addButton(self.insp_play_once)
        plays.addButton(self.insp_infinite)
        self._insp_plays_group = plays
        ggrid.addWidget(QLabel("Start time:"), 0, 0)
        ggrid.addWidget(self.insp_start_time, 0, 1)
        ggrid.addWidget(QLabel("End time:"), 1, 0)
        ggrid.addWidget(self.insp_end_time, 1, 1)
        ggrid.addWidget(self.insp_play_once, 2, 0)
        ggrid.addWidget(self.insp_play_count, 2, 1)
        ggrid.addWidget(self.insp_infinite, 3, 0, 1, 2)
        ggrid.addWidget(self.insp_wave, 0, 2, 4, 1)
        ggrid.addWidget(QLabel("Loop in:"), 4, 0)
        ggrid.addWidget(self.insp_loop_in, 4, 1)
        ggrid.addWidget(QLabel("Loop out:"), 5, 0)
        ggrid.addWidget(self.insp_loop_out, 5, 1)
        ggrid.addWidget(self.insp_loop, 4, 2)
        self.insp_rate = QDoubleSpinBox()
        self.insp_rate.setRange(0.25, 4.0)
        self.insp_rate.setSingleStep(0.05)
        self.insp_rate.setDecimals(2)
        self.insp_rate.setMaximumWidth(90)
        self.insp_preserve_pitch = QCheckBox("Preserve pitch")
        self.insp_preserve_pitch.setToolTip("Hold the pitch where it is while the rate changes.")
        self.insp_integrated = QCheckBox("Integrated fade")
        self.insp_curve = QComboBox()
        for label, value in FADE_CURVES:
            self.insp_curve.addItem(label, value)
        self.insp_curve.setMaximumWidth(140)
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Rate:"))
        rate_row.addWidget(self.insp_rate)
        rate_row.addWidget(self.insp_preserve_pitch)
        rate_row.addWidget(self.insp_integrated)
        rate_row.addWidget(self.insp_curve)
        self.insp_add_slice = QPushButton("Add Slice")
        self.insp_clear_slices = QPushButton("Delete All")
        rate_row.addWidget(self.insp_add_slice)
        rate_row.addWidget(self.insp_clear_slices)
        rate_row.addStretch(1)
        ggrid.addLayout(rate_row, 5, 2)
        ggrid.setColumnStretch(2, 1)
        ggrid.setRowStretch(6, 1)
        built_pages["Time & Loops"] = time_page

        # I/O: which outputs this cue's audio goes to.
        io_page = QFrame()
        io_page.setObjectName("inspectorPage")
        iogrid = QGridLayout(io_page)
        iogrid.setContentsMargins(12, 10, 12, 10)
        self.insp_file_target = QLabel("—")
        self.insp_file_target.setWordWrap(True)
        self.insp_file_format = QLabel("—")
        self.insp_patch = QComboBox()
        self.insp_patch_format = QLabel("—")
        self.insp_edit_patches = QPushButton("Edit Audio Patches…")
        self.insp_route_label = QLabel("Default")
        self.insp_route_label.setWordWrap(True)
        self.insp_route_button = QPushButton("Routing…")
        iogrid.addWidget(QLabel("File Target:"), 0, 0)
        iogrid.addWidget(self.insp_file_target, 0, 1, 1, 2)
        iogrid.addWidget(QLabel("Format:"), 1, 0)
        iogrid.addWidget(self.insp_file_format, 1, 1, 1, 2)
        iogrid.addWidget(QLabel("Audio Output:"), 2, 0)
        iogrid.addWidget(self.insp_patch, 2, 1)
        iogrid.addWidget(self.insp_edit_patches, 2, 2)
        iogrid.addWidget(QLabel("Format:"), 3, 0)
        iogrid.addWidget(self.insp_patch_format, 3, 1, 1, 2)
        iogrid.addWidget(QLabel("Outputs:"), 4, 0)
        iogrid.addWidget(self.insp_route_label, 4, 1)
        iogrid.addWidget(self.insp_route_button, 4, 2)
        iogrid.setColumnStretch(1, 1)
        iogrid.setRowStretch(5, 1)
        built_pages["I/O"] = io_page

        # Levels: this cue's own level, and its fades.
        levels_page = QFrame()
        levels_page.setObjectName("inspectorPage")
        lgrid = QGridLayout(levels_page)
        lgrid.setContentsMargins(12, 10, 12, 10)
        self.insp_levels = OutputLevels()
        self.insp_set_default = QPushButton("Set Default Levels")
        self.insp_set_silent = QPushButton("Set All Silent Levels")
        self.insp_clear_mutes = QPushButton("Clear Mutes")
        self.insp_clear_solos = QPushButton("Clear Solos")
        self.insp_fade_in = QDoubleSpinBox()
        self.insp_fade_out = QDoubleSpinBox()
        for spin in (self.insp_fade_in, self.insp_fade_out):
            spin.setRange(0.0, 600.0)
            spin.setDecimals(1)
            spin.setSuffix(" s")
            spin.setMaximumWidth(140)
        lgrid.addWidget(self.insp_set_default, 0, 0, 1, 2)
        lgrid.addWidget(self.insp_set_silent, 1, 0, 1, 2)
        lgrid.addWidget(self.insp_clear_mutes, 2, 0)
        lgrid.addWidget(self.insp_clear_solos, 2, 1)
        lgrid.addWidget(QLabel("Fade in:"), 3, 0)
        lgrid.addWidget(self.insp_fade_in, 3, 1)
        lgrid.addWidget(QLabel("Fade out:"), 4, 0)
        lgrid.addWidget(self.insp_fade_out, 4, 1)
        self.insp_levels_scroll = QScrollArea()
        self.insp_levels_scroll.setWidget(self.insp_levels)
        self.insp_levels_scroll.setWidgetResizable(True)
        self.insp_levels_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.insp_levels_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.insp_levels_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lgrid.addWidget(self.insp_levels_scroll, 0, 2, 5, 1)
        lgrid.addWidget(QLabel("main is the cue's own level; the numbered faders are its outputs."),
                        5, 0, 1, 3)
        lgrid.setColumnStretch(2, 1)
        lgrid.setRowStretch(6, 1)
        built_pages["Levels"] = levels_page

        # Trim: play only part of the file.
        trim_page = QFrame()
        trim_page.setObjectName("inspectorPage")
        trgrid = QGridLayout(trim_page)
        trgrid.setContentsMargins(12, 10, 12, 10)
        self.insp_trim = QDoubleSpinBox()
        self.insp_trim.setRange(-60.0, 12.0)
        self.insp_trim.setDecimals(1)
        self.insp_trim.setSuffix(" dB")
        self.insp_trim.setMaximumWidth(140)
        note = QLabel("Trim is an overall adjustment to the volume of the cue.\n"
                      "Trim cannot be changed by Fade cues.")
        note.setWordWrap(True)
        trgrid.addWidget(note, 0, 0, 1, 2)
        trgrid.addWidget(QLabel("Trim:"), 1, 0)
        trgrid.addWidget(self.insp_trim, 1, 1)
        trgrid.setRowStretch(2, 1)
        built_pages["Trim"] = trim_page

        # Objects: where a video, camera or text cue appears.
        objects_page = QFrame()
        objects_page.setObjectName("inspectorPage")
        ogrid = QGridLayout(objects_page)
        ogrid.setContentsMargins(12, 10, 12, 10)
        self.insp_display = QComboBox()
        self.insp_opacity = QSpinBox()
        self.insp_opacity.setRange(0, 100)
        self.insp_opacity.setSuffix(" %")
        self.insp_opacity.setMaximumWidth(140)
        self.insp_objects_note = QLabel("")
        self.insp_objects_note.setWordWrap(True)
        self.insp_audio_map = QLabel("Default")
        self.insp_audio_map.setWordWrap(True)
        self.insp_view_map = QPushButton("View Audio Map…")
        ogrid.addWidget(QLabel("Audio map:"), 0, 0)
        ogrid.addWidget(self.insp_audio_map, 0, 1)
        ogrid.addWidget(self.insp_view_map, 0, 2)
        ogrid.addWidget(QLabel("Display:"), 1, 0)
        ogrid.addWidget(self.insp_display, 1, 1)
        ogrid.addWidget(QLabel("Opacity:"), 2, 0)
        ogrid.addWidget(self.insp_opacity, 2, 1)
        ogrid.addWidget(self.insp_objects_note, 3, 0, 1, 3)
        ogrid.setColumnStretch(1, 1)
        ogrid.setRowStretch(4, 1)
        built_pages["Objects"] = objects_page

        # Audio FX: a three band EQ applied while the cue plays.
        fx_page = QFrame()
        fx_page.setObjectName("inspectorPage")
        fgrid = QGridLayout(fx_page)
        fgrid.setContentsMargins(12, 10, 12, 10)
        self.insp_fx_list = QListWidget()
        self.insp_fx_list.setMaximumWidth(200)
        self.insp_fx_list.setMaximumHeight(130)  # keeps the tab inside the inspector
        self.insp_fx_add = QPushButton("Add audio effect…")
        self.insp_fx_remove = QPushButton("Remove")
        self.insp_fx_params = EffectParams()
        fx_menu = QMenu(self.insp_fx_add)
        for spec in fx.EFFECT_LIST:
            if spec.kind == fx.VST_KIND:
                fx_menu.addSeparator()
                fx_menu.addAction(spec.label + "\u2026", self._add_plugin)
            else:
                fx_menu.addAction(spec.label, lambda k=spec.kind: self._add_effect(k))
        self.insp_fx_add.setMenu(fx_menu)

        buttons = QHBoxLayout()
        buttons.addWidget(self.insp_fx_add)
        buttons.addWidget(self.insp_fx_remove)
        buttons.addStretch(1)
        fgrid.addLayout(buttons, 0, 0, 1, 2)
        fgrid.addWidget(self.insp_fx_list, 1, 0, 3, 1)
        fgrid.addWidget(self.insp_fx_params, 1, 1, 3, 2)
        fgrid.addWidget(QLabel("Effects run top to bottom, before the cue's level."),
                        4, 0, 1, 3)
        fgrid.setColumnStretch(2, 1)
        fgrid.setRowStretch(3, 1)
        built_pages["Audio FX"] = fx_page

        # Placeholders for the tabs not built yet, so the stack lines up with the tab row.
        for title in INSPECTOR_TABS:
            self.insp_stack.addWidget(built_pages[title])
        self.insp_tabs.currentChanged.connect(self.insp_stack.setCurrentIndex)
        self.insp_tabs.currentChanged.connect(lambda _: self._refresh_waveform())
        self.insp_timeline.about_to_edit.connect(
            lambda: self._remember("Timeline Edit", coalesce=True))
        self.insp_timeline.changed.connect(self._timeline_changed)
        self.insp_wave.markersChanged.connect(self._wave_markers_changed)
        self.insp_wave.seeked.connect(self._seek_selected_cue)
        # The stack is as tall as its tallest page, so it is capped and scrolls instead of
        # pushing the cue list off a laptop screen.
        self.insp_scroll = QScrollArea()
        self.insp_scroll.setWidget(self.insp_stack)
        self.insp_scroll.setWidgetResizable(True)
        self.insp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.insp_scroll.setMaximumHeight(INSPECTOR_MAX_HEIGHT)
        self.insp_scroll.setMinimumHeight(90)
        self.insp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.insp_scroll)

        # Every field that edits the selected cue, enabled together in Edit mode.
        self._insp_editable = [
            self.insp_name, self.insp_pre, self.insp_post, self.insp_continue, self.insp_colour,
            self.insp_hotkey, self.insp_notes, self.insp_armed, self.insp_flagged,
            self.insp_auto_load, self.insp_skip, self.insp_loop, self.insp_loop_in,
            self.insp_midi_on, self.insp_midi_kind, self.insp_midi_number, self.insp_midi_channel,
            self.insp_wall_clock, self.insp_fade_stop, self.insp_fade_stop_time,
            self.insp_duck_others, self.insp_duck_by, self.insp_duck_time, self.insp_second_trigger,
            self.insp_loop_out, self.insp_trigger_tc, self.insp_route_button, self.insp_patch,
            self.insp_set_default, self.insp_set_silent, self.insp_clear_mutes,
            self.insp_clear_solos, self.insp_view_map, self.insp_edit_patches,
            self.insp_wave, self.insp_timeline,
            self.insp_fade_in, self.insp_fade_out, self.insp_start_time, self.insp_end_time,
            self.insp_trim, self.insp_play_once, self.insp_play_count, self.insp_infinite,
            self.insp_rate, self.insp_preserve_pitch, self.insp_integrated, self.insp_curve,
            self.insp_add_slice,
            self.insp_clear_slices,
            self.insp_display, self.insp_opacity, self.insp_fx_list, self.insp_fx_add,
            self.insp_fx_remove,
        ]
        self.insp_route_button.clicked.connect(self._inspector_routing)
        self.insp_view_map.clicked.connect(self._inspector_routing)
        self.insp_edit_patches.clicked.connect(self.edit_audio_patches)
        self.insp_levels.changed.connect(self._inspector_apply)
        self.insp_fx_list.currentRowChanged.connect(lambda _: self._refresh_effect_fields())
        self.insp_fx_params.changed.connect(self._inspector_apply)
        self.insp_fx_params.editor_requested.connect(self._open_plugin_editor)
        self.insp_fx_remove.clicked.connect(self._remove_effect)
        self.insp_patch.currentIndexChanged.connect(self._inspector_apply)
        self.insp_set_default.clicked.connect(lambda: self.insp_levels.set_all(0))
        self.insp_set_silent.clicked.connect(lambda: self.insp_levels.set_all(levels_ui.MIN_DB))
        self.insp_clear_mutes.clicked.connect(self.insp_levels.clear_mutes)
        self.insp_clear_solos.clicked.connect(self.insp_levels.clear_solos)
        for spin in (self.insp_fade_in, self.insp_fade_out, self.insp_start_time,
                     self.insp_end_time, self.insp_trim, self.insp_play_count,
                     self.insp_rate):
            spin.valueChanged.connect(self._inspector_apply)
        self.insp_play_once.toggled.connect(self._inspector_apply)
        self.insp_add_slice.clicked.connect(self._add_slice)
        self.insp_clear_slices.clicked.connect(self._clear_slices)
        self.insp_preserve_pitch.toggled.connect(self._inspector_apply)
        self.insp_integrated.toggled.connect(self._inspector_apply)
        self.insp_curve.currentIndexChanged.connect(self._inspector_apply)
        self.insp_opacity.valueChanged.connect(self._inspector_apply)
        self.insp_display.currentIndexChanged.connect(self._inspector_apply)
        self.insp_wall_clock.editingFinished.connect(self._inspector_apply)
        for box in (self.insp_midi_on, self.insp_fade_stop, self.insp_duck_others):
            box.toggled.connect(self._inspector_apply)
        for combo in (self.insp_midi_kind, self.insp_midi_channel, self.insp_second_trigger):
            combo.currentIndexChanged.connect(self._inspector_apply)
        for spin in (self.insp_midi_number, self.insp_fade_stop_time, self.insp_duck_by,
                     self.insp_duck_time):
            spin.valueChanged.connect(self._inspector_apply)
        self.insp_trigger_tc.editingFinished.connect(self._inspector_apply)
        self.insp_loop.toggled.connect(self._inspector_apply)
        self.insp_loop_in.valueChanged.connect(self._inspector_apply)
        self.insp_loop_out.valueChanged.connect(self._inspector_apply)
        self.insp_name.editingFinished.connect(self._inspector_apply)
        self.insp_pre.valueChanged.connect(self._inspector_apply)
        self.insp_post.valueChanged.connect(self._inspector_apply)
        self.insp_continue.currentIndexChanged.connect(self._inspector_apply)
        self.insp_colour.currentIndexChanged.connect(self._inspector_apply)
        self.insp_hotkey.keySequenceChanged.connect(self._inspector_apply)
        self.insp_notes.textChanged.connect(self._inspector_apply)
        for box in (self.insp_armed, self.insp_flagged, self.insp_auto_load, self.insp_skip):
            box.toggled.connect(self._inspector_apply)
        self.insp_full_edit.clicked.connect(self.edit_selected)
        layout.addWidget(self.inspector_bar)

        self.bottom_panel = QWidget()
        bottom = QHBoxLayout(self.bottom_panel)
        bottom.setContentsMargins(0, 0, 0, 0)
        self.countdown = Countdown(self)
        self.soundboard = Soundboard(self)
        self.soundboard.padPressed.connect(self._on_pad_pressed)
        self.soundboard.padAssign.connect(self._assign_pad)
        self.soundboard.padClear.connect(self._clear_pad)
        self.soundboard.padRouting.connect(self._pad_routing)
        bottom.addWidget(self.countdown, 1)
        bottom.addWidget(self.soundboard, 3)
        layout.addWidget(self.bottom_panel)

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
        unsaved = self.has_unsaved_changes()
        self.setWindowTitle(f"CueRunner - {name}{' •' if unsaved else ''}")

    def _refresh_table(self) -> None:
        self._refreshing = True
        try:
            self._fill_table()
        finally:
            self._refreshing = False

    def _fill_table(self) -> None:
        cues = self.show_model.cues
        if hasattr(self, "_saved_json"):
            self._refresh_title()  # the title carries a dot while there is unsaved work
        if hasattr(self, "cue_count_label"):
            # The whole show, as QLab counts it, not just the list on screen.
            total = len(self.show_model.all_cues())
            places = len(self.show_model.lists)
            self.cue_count_label.setText(
                f"{total} cue{'' if total == 1 else 's'} in "
                f"{places} list{'' if places == 1 else 's'}")
        playhead = self._playhead_row()
        playing_row = self._row_of(self._playing_cue)
        if 0 <= playhead < len(cues) and cues[playhead].auto_load:
            self._preload(cues[playhead])  # ready to start the moment it is run

        self.table.setRowCount(len(cues))
        self._row_bars = []
        for row, cue in enumerate(cues):
            marker = PLAYHEAD_MARK if row == playhead else ""
            flag = " \u2691" if cue.flagged else ""  # a flagged cue carries a mark on its row
            values = [
                f"{marker} {cue.number}{flag}".strip(),
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
                if col == NAME_COL:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
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

        self.table.setVerticalHeaderLabels([str(i + 1) for i in range(len(cues))])
        self._refresh_lists()
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
        if self.showing_cart():
            return self.cart.selected_index()
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def showing_cart(self) -> bool:
        return hasattr(self, "cart") and self.show_model.current.is_cart()

    def _go_cart_cue(self, index: int) -> None:
        """A cart has no playhead, so starting a cue does not move anything on."""
        self.go_row(index)

    def _refresh_lists(self) -> None:
        """Rebuild the sidebar and show whichever list or cart is active."""
        if not hasattr(self, "list_sidebar"):
            return
        blocked = self.list_sidebar.blockSignals(True)
        self.list_sidebar.clear()
        for entry in self.show_model.lists:
            mark = "\u25a6" if entry.is_cart() else "\u2630"
            self.list_sidebar.addItem(f"{mark}  {entry.name}")
        self.list_sidebar.setCurrentRow(self.show_model.active_list)
        self.list_sidebar.blockSignals(blocked)
        cart = self.show_model.current.is_cart()
        self.cue_area.setCurrentWidget(self.cart if cart else self.table)
        if cart:
            entry = self.show_model.current
            self.cart.set_shape(entry.columns, entry.rows)
            self.cart.set_cues(entry.cues)

    def _choose_list(self, row: int) -> None:
        if not 0 <= row < len(self.show_model.lists) or row == self.show_model.active_list:
            return
        self.show_model.active_list = row
        self._last_row = -1
        self._refresh_table()

    def add_cue_list(self, kind: str = "list") -> None:
        """Add a cue list or a cue cart and switch to it."""
        self._remember("Add Cart" if kind == "cart" else "Add List")
        existing = len(self.show_model.lists)
        name = f"Cue Cart {existing + 1}" if kind == "cart" else f"Cue List {existing + 1}"
        self.show_model.cue_lists.append(CueList(name=name, kind=kind))
        self.show_model.active_list = len(self.show_model.cue_lists) - 1
        self._refresh_table()

    def remove_cue_list(self) -> None:
        """Delete the active list or cart, and everything in it."""
        lists = self.show_model.lists
        if len(lists) < 2:
            QMessageBox.information(self, "Cue lists", "A show keeps at least one cue list.")
            return
        entry = self.show_model.current
        answer = QMessageBox.question(
            self, "Delete cue list",
            f"Delete \u201c{entry.name}\u201d and the {len(entry.cues)} cues in it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._remember("Delete List")
        del self.show_model.cue_lists[self.show_model.active_list]
        self.show_model.active_list = max(0, self.show_model.active_list - 1)
        self._refresh_table()

    def rename_cue_list(self, name: str) -> None:
        self.show_model.current.name = name.strip() or self.show_model.current.name
        self._refresh_lists()

    def _playhead_row(self) -> int:
        """The row GO will play next: the selected row, else the last row selected, else the first cue.

        A cart has no playhead, so GO there simply starts whatever is selected.

        Clicking away clears the highlight but keeps this position, so GO carries on from where you were.
        """
        if not self.show_model.cues:
            return -1
        row = self._selected_row()
        if row >= 0:
            return row
        if self.showing_cart():
            return -1  # nothing picked in a cart means nothing to start
        if 0 <= self._last_row < len(self.show_model.cues):
            return self._last_row
        return 0

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt naming
        # Quitting (Cmd+Q, or the dock) does not go through closeEvent on its own, so it is
        # routed through the window here; a cancelled close calls the quit off too.
        if event.type() == QEvent.Type.Quit and not self._quitting:
            self._quitting = True
            try:
                if not self.close():
                    return True
            finally:
                self._quitting = False
            return False
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget) and obj.window() is self:
            self._on_outside_click(obj)
        return False

    UNDO_DEPTH = 60
    COALESCE_SECONDS = 1.0  # a run of small changes to the same thing counts as one step

    def _cue_snapshot(self) -> str:
        return json.dumps([asdict(c) for c in self.show_model.cues])

    def _restore_cues(self, snapshot: str) -> None:
        allowed = set(Cue.__dataclass_fields__)
        self.show_model.cues = [Cue(**{k: v for k, v in raw.items() if k in allowed})
                                for raw in json.loads(snapshot)]
        self._refresh_table()
        self._refresh_inspector()

    def _remember(self, label: str, coalesce: bool = False) -> None:
        """Record the cue list as it is now, before something changes it.

        coalesce is for edits that arrive in a stream, such as a slider being dragged:
        a run of them counts as one step. Separate actions are always their own step.
        """
        snapshot = self._cue_snapshot()
        now = time.monotonic()
        if self._undo:
            last_label, last_snapshot = self._undo[-1]
            if last_snapshot == snapshot:
                return  # nothing actually changed since last time
            if coalesce and last_label == label and now - self._undo_at < self.COALESCE_SECONDS:
                self._undo_at = now
                return
        self._undo.append((label, snapshot))
        del self._undo[:-self.UNDO_DEPTH]
        self._undo_at = now
        self._redo.clear()
        self._refresh_undo_actions()

    def undo(self) -> None:
        if not self._undo:
            return
        label, snapshot = self._undo.pop()
        self._redo.append((label, self._cue_snapshot()))
        self._restore_cues(snapshot)
        self._undo_at = 0.0
        self._refresh_undo_actions()

    def redo(self) -> None:
        if not self._redo:
            return
        label, snapshot = self._redo.pop()
        self._undo.append((label, self._cue_snapshot()))
        self._restore_cues(snapshot)
        self._undo_at = 0.0
        self._refresh_undo_actions()

    def _refresh_undo_actions(self) -> None:
        """Keep the menu entries naming what they would actually undo."""
        if not hasattr(self, "undo_action"):
            return
        self.undo_action.setEnabled(bool(self._undo))
        self.redo_action.setEnabled(bool(self._redo))
        self.undo_action.setText(f"Undo {self._undo[-1][0]}" if self._undo else "Undo")
        self.redo_action.setText(f"Redo {self._redo[-1][0]}" if self._redo else "Redo")

    def _show_column(self, name: str, shown: bool) -> None:
        self.table.setColumnHidden(COLUMNS.index(name), not shown)
        self._save_view_settings()

    def _show_bottom_panel(self, shown: bool) -> None:
        self.bottom_panel.setVisible(shown)
        self._save_view_settings()

    def _save_view_settings(self) -> None:
        settings = QSettings("Overdrive Studios", "CueRunner")
        settings.setValue("view/columns",
                          [n for n, a in self._column_actions.items() if a.isChecked()])
        settings.setValue("view/pads", self.pads_action.isChecked())

    def _load_view_settings(self) -> None:
        """Bring back whichever columns and panels were last switched on."""
        settings = QSettings("Overdrive Studios", "CueRunner")
        wanted = settings.value("view/columns", []) or []
        if isinstance(wanted, str):
            wanted = [wanted]
        for name, action in self._column_actions.items():
            action.setChecked(name in wanted)
        pads = settings.value("view/pads", False)
        self.pads_action.setChecked(pads in (True, "true", "True", 1, "1"))
        self.bottom_panel.setVisible(self.pads_action.isChecked())

    def _cue_problem(self, title: str, detail: str = "", modal_when_editing: bool = True) -> None:
        """A cue could not run.

        In Show mode a dialog would freeze the show until somebody clicked it, so the
        message goes to the banner and the show carries on. In Edit mode, where cues are
        being set up and nothing is running, it is shown as a dialog so it is not missed.
        """
        line = " ".join(str(detail).split())
        self._problems.append(f"{title}: {line}")
        del self._problems[:-50]
        if self._editing and modal_when_editing:
            QMessageBox.warning(self, title, str(detail))
            return
        self.alert_label.setText(f"\u26a0  {title} \u2014 {line}")
        self.alert_label.setVisible(True)
        self._alert_timer.start(8000)

    def recent_problems(self) -> list[str]:
        """What has gone wrong lately, newest last."""
        return list(self._problems)

    def _name_edited(self, item) -> None:
        """A name typed into the cue list goes straight onto the cue."""
        if self._refreshing or item.column() != NAME_COL:
            return
        row = item.row()
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        typed = item.text().strip()
        if not typed or typed == cue.name:
            item.setText(("    " if cue.parent else "") + cue.name)  # put back what it was
            return
        self._remember("Rename")
        cue.name = typed
        self._refresh_inspector()
        self._refresh_now_label()
        self._refresh_title()

    def _remember_selected_row(self) -> None:
        row = self._selected_row()
        if row >= 0:
            self._last_row = row

    def _on_outside_click(self, widget: QWidget) -> None:
        """A click anywhere except the cue table and the inspector clears the track highlight."""
        if widget is self.table or self.table.isAncestorOf(widget):
            return
        if widget is self.inspector_bar or self.inspector_bar.isAncestorOf(widget):
            return
        if self.table.selectedIndexes():
            self.table.clearSelection()

    def _row_of(self, cue: Cue | None) -> int:
        if cue is None:
            return -1
        return next((i for i, c in enumerate(self.show_model.cues) if c is cue), -1)

    # --- Show file actions --------------------------------------------

    def new_show(self) -> None:
        if not self.confirm_discard("starting a new show"):
            return
        self.hard_stop()
        self.show_model = Show()
        self.show_path = None
        self._refresh_table()
        self._mark_saved()

    def open_show(self) -> None:
        if not self.confirm_discard("opening another show"):
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Open show", "", SHOW_FILTER)
        if filename:
            self.open_show_path(Path(filename))

    def open_show_path(self, path: Path) -> None:
        try:
            self.show_model = Show.load(path)
        except Exception as exc:  # a show file can be anything the user points us at
            QMessageBox.critical(self, "Could not open show", str(exc))
            return
        self.show_path = path
        launcher.remember_show(path)
        self.hard_stop()
        self._refresh_table()
        for cue in self.show_model.cues:
            if cue.duration_ms == 0:
                self._probe_duration(cue)
        # Probing fills in durations, so the baseline is taken once that has settled.
        self._mark_saved()

    def _auto_backup(self) -> None:
        """Every 15 minutes, keep a snapshot of the show next to the show file."""
        if self.show_path is not None:
            self.show_model.snapshot(self.show_path)

    def _mark_saved(self) -> None:
        """Remember the show as it stands now, so later edits can be spotted."""
        self._saved_json = self.show_model.to_json()
        self._refresh_title()

    def has_unsaved_changes(self) -> bool:
        return self.show_model.to_json() != self._saved_json

    def confirm_discard(self, action: str) -> bool:
        """Offer to save before something that would lose the show. True means carry on."""
        if not self.has_unsaved_changes():
            return True
        name = self.show_path.name if self.show_path else "this show"
        answer = QMessageBox.question(
            self, "Unsaved changes", f"Save your changes to {name} before {action}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.save_show()
            return not self.has_unsaved_changes()  # the Save As dialog may have been cancelled
        return True

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
        self._mark_saved()

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
            self._cue_problem("Console cue", "Set the console's IP address under Audio → Allen & Heath Console first.")
            return
        try:
            data = allenheath.parse_command(self.show_model.console_family, cue.file, channel=1)
        except ValueError as exc:
            self._cue_problem("Console cue", f"{cue.name}\n\n{exc}")
            return
        spotify_ui.run_async(
            self,
            lambda: sender.send(data),
            lambda _r, error: self._cue_problem("Console cue", f"{cue.name}\n\n{error}") if error is not None else None,
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

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Dropped files become cues after the selected row (or at the end), by file type."""
        from cuerunner.dropped import split_drop

        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        accepted, skipped = split_drop(paths)
        if accepted:
            self._add_dropped_files(accepted)
        if skipped:
            QMessageBox.information(
                self, "Not added",
                "These files are not audio, video or MIDI files, so they were not added:\n\n" + "\n".join(skipped),
            )
        event.acceptProposedAction()

    def _add_dropped_files(self, accepted: list[tuple[str, str]]) -> None:
        self._remember("Add Cue")
        """Insert one cue per file. Audio is probed for its length afterwards, as the Add Audio Cues command does."""
        insert_at = self.table.currentRow() + 1 if self.table.currentRow() >= 0 else len(self.show_model.cues)
        default_output = self.show_model.video_default_output if QGuiApplication.screens() else ""
        new_cues: list[Cue] = []
        for path_text, kind in accepted:
            path = Path(path_text)
            cue = Cue(number=self.show_model.next_number(), name=path.stem, kind=kind, file=str(path))
            if kind == "video":
                cue.video_output = default_output
            self.show_model.cues.insert(insert_at, cue)
            new_cues.append(cue)
            insert_at += 1
        for cue in new_cues:
            if cue.kind == "audio":
                self._probe_duration(cue)
        self._refresh_table()

    def add_audio_cue(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "Add audio cues", "", AUDIO_FILTER)
        if filenames:
            self._remember("Add Cue")
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
        self._remember("Add Cue")
        default_output = self.show_model.video_default_output if QGuiApplication.screens() else ""
        for filename in filenames:
            path = Path(filename)
            self.show_model.cues.append(Cue(
                number=self.show_model.next_number(), name=path.stem, kind="video",
                file=str(path), video_output=default_output,
            ))
        self._refresh_table()

    def add_special_cue(self, kind: str) -> None:
        if kind == "group" and self.showing_cart():
            QMessageBox.information(self, "Cue carts", "A cart cannot hold group cues.")
            return
        self._remember("Add Cue")
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
            self.go_row(next_child)
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
            self._cue_problem("Cannot run cue", f"{cue.name}\n\nThere is no cue numbered \"{number}\".")
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
            self.go_row(target_row)

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

    def _group_children(self, number: str) -> list[int]:
        return [i for i, c in enumerate(self.show_model.cues) if c.parent == number]

    def _row_after_group(self, row: int, children: list[int]) -> int:
        """The first row past the group and everything inside it."""
        return (max(children) if children else row) + 1

    def _start_group(self, row: int, cue: Cue) -> None:
        """Start a group cue the way its mode says, and put the playhead where QLab puts it."""
        children = self._group_children(cue.number)
        mode = group_mode_of(cue)
        after = self._row_after_group(row, children)

        if mode == "timeline":
            for index in children:  # every child at once
                self.go_row(index)
        elif mode == "start_random":
            choices = [i for i in children
                       if self.show_model.cues[i].armed
                       and not self._cue_is_running(self.show_model.cues[i])]
            if choices:
                self.go_row(random.choice(choices))
        elif children:
            self.go_row(children[0])  # playlist, start first, start first and enter

        if mode == "start_first_enter":
            self._advance(row)  # the playhead steps into the group
        else:
            self.table.selectRow(min(after, len(self.show_model.cues) - 1))

    def _playlist_next_row(self, row: int) -> int | None:
        """In a playlist group, the row of the child after this one, or None."""
        cues = self.show_model.cues
        if not 0 <= row < len(cues) or not cues[row].parent:
            return None
        group = next((c for c in cues if c.kind == "group" and c.number == cues[row].parent), None)
        if group is None or group_mode_of(group) != "playlist":
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

    def _trim_frames(self, cue: Cue) -> tuple[int, int]:
        """A cue's trim in and out points as frames (0 out means the end of the file)."""
        rate = self.mixer.rate if self.mixer is not None else 48000
        return round(cue.trim_in * rate), round(cue.trim_out * rate)

    def _cue_eq(self, cue: Cue, channels: int):
        """The effect chain for a cue, or None when nothing in it changes the sound."""
        if not cue.effects and any((cue.eq_low, cue.eq_mid, cue.eq_high)):
            # Carried over from the fixed three band EQ of earlier versions.
            cue.effects = [dict(fx.new_effect(kind), gain_db=gain) for kind, gain in
                           (("lowshelf", cue.eq_low), ("peaking", cue.eq_mid),
                            ("highshelf", cue.eq_high)) if gain]
        rate = self.mixer.rate if self.mixer is not None else 48000
        effects = list(cue.effects)
        speed = cue.rate or 1.0
        if cue.preserve_pitch and abs(speed - 1.0) > 1e-6:
            # Playing faster raises the pitch, so shift it back down by the same amount.
            semitones = -12 * math.log2(speed)
            effects.append(dict(fx.new_effect("pitch"), semitones=semitones))
        chain = fx.Chain(effects=effects, rate=rate, channels=channels)
        return None if chain.flat() else chain

    def _apply_levels(self, voice: Voice, cue: Cue, clip) -> None:
        """Level, trim points and EQ from the inspector's Levels, Trim and Audio FX tabs."""
        voice.gain = max(0.0, cue.volume) * fx.db_to_gain(cue.trim_db)
        start, end = self._trim_frames(cue)
        voice.start = min(max(start, 0), max(clip.frames - 1, 0))
        voice.position = voice.start
        voice.end = end
        voice.plays = max(1, cue.play_count)
        voice.rate = cue.rate or 1.0
        voice.curve = cue.fade_curve if cue.integrated_fade else "linear"
        voice.fx = self._cue_eq(cue, clip.channels)
        outs = voice.matrix.shape[1]
        voice.matrix = voice.matrix * output_gains(cue.output_levels, cue.output_mutes,
                                                   cue.output_solos, outs)[None, :]

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
        self._apply_levels(voice, cue, clip)
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
        self._remember("Remove Cue")
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

    def go_row(self, row: int, _pre_done: bool = False) -> None:
        """Start the cue in this row with its fade-in and move the playhead down.

        Cues layer: starting one leaves whatever is already playing alone, as QLab does.
        A cue that should clear the others sets "Fade & stop other cues" on its Triggers tab.
        """
        if not self.show_model.cues[row].armed:
            # A disarmed cue never fires. "Skip if disarmed" sends GO on to the next cue;
            # without it the GO is used up here and the playhead simply moves on.
            self._advance(row)
            if self.show_model.cues[row].skip_if_disarmed and row + 1 < len(self.show_model.cues):
                self.go_row(row + 1)
            return
        cues = self.show_model.cues
        if not 0 <= row < len(cues):
            return
        cue = cues[row]
        if not _pre_done and self._second_trigger(cue):
            return
        if cue.pre_wait_ms > 0 and not _pre_done:
            self._after(cue.pre_wait_ms, lambda: self.go_row(row, _pre_done=True))
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
                self._cue_problem("Cannot play cue", f"{cue.name}\n\nThis video cue has no file.")
            else:
                try:
                    self._video.play(cue.video_output, cue.file, self.show_model.video_routes, cue.number)
                    self._video.set_opacity(cue.video_output, cue.opacity, self.show_model.video_routes)
                except Exception as exc:
                    self._cue_problem("Cannot play cue", f"{cue.name}\n\n{exc}")
            self.table.selectRow(min(row + 1, len(cues) - 1))
            return
        if cue.kind == "camera":
            try:
                self._video.show_camera(cue.video_output, self._camera_for(cue), self.show_model.video_routes)
            except Exception as exc:
                self._cue_problem("Cannot play cue", f"{cue.name}\n\n{exc}")
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
                self._cue_problem("Cannot send network cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "midi":
            try:
                midi.send_out(cue.target, cue.text)
            except Exception as exc:
                self._cue_problem("Cannot send MIDI cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "timecode":
            try:
                self._start_timecode(cue)
            except Exception as exc:
                self._cue_problem("Cannot start timecode", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "light":
            try:
                self._run_light(cue)
            except Exception as exc:
                self._cue_problem("Cannot send light cue", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "osc":
            try:
                osc.send(cue.target, cue.text)
            except Exception as exc:
                self._cue_problem("Cannot send OSC", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "wait":
            next_row = self._playlist_next_row(row)
            if next_row is None:
                next_row = row + 1 if row + 1 < len(cues) else None
            if next_row is not None:
                self._after(cue.duration_ms, lambda r=next_row: self.go_row(r))
            self._advance(row)
            return
        if cue.kind == "group":
            self._start_group(row, cue)
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
                self._cue_problem("Cannot play cue", f"{cue.name}\n\n{exc}")
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
                self._cue_problem("Cannot play MIDI file", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "program":
            try:
                self._run_program(cue)
            except Exception as exc:
                self._cue_problem("Cannot run program", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if cue.kind == "target":
            try:
                self._retarget_cue(cue)
            except Exception as exc:
                self._cue_problem("Cannot change target", f"{cue.name}\n\n{exc}")
            self._finish(row)
            return
        if self.mixer is None:
            return
        if cue.kind == "audio" and cue.file:
            try:
                clip = self._clip(cue.file)
            except Exception as exc:
                self._cue_problem("Cannot play cue", f"{cue.name}\n\n{exc}")
                return
            ready = next((v for v in self._cue_voices if v.tag is cue and v.preloaded and not v.done), None)
            if ready is not None:
                ready.paused = False
                ready.preloaded = False
                self._playing_cue = cue
                if cue.then == "continue" and row + 1 < len(cues):
                    self.go_row(row + 1)
                self.table.selectRow(min(row + 1, len(cues) - 1))
                return
            voice = Voice(
                clip=clip,
                matrix=route_matrix(cue.route, clip.channels, self.mixer.out_channels),
                loop=cue.loop,
                loop_start=self._loop_frames(cue)[0],
                loop_end=self._loop_frames(cue)[1],
                bed=True,
                tag=cue,
            )
            self._apply_levels(voice, cue, clip)
            self._start_options(cue)
            if cue.fade_in > 0:
                voice.env = 0.0
                voice.fade(1.0, cue.fade_in, self.mixer.rate)
            self.mixer.add(voice)
            self._cue_voices.append(voice)
            self._playing_cue = cue
            if cue.then == "continue" and row + 1 < len(cues):
                self.go_row(row + 1)
        if not self.showing_cart():
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
        """Fade everything out and stop, over the show's panic duration.

        This is what Escape does, as QLab does it. Pressing it again while the fade is
        running stops everything on the spot, and so does a double tap.
        """
        seconds = max(0.0, float(self.show_model.panic_duration))
        if self._panicking or seconds <= 0 or self.mixer is None:
            self.hard_stop()
            return
        self._panicking = True
        for voice in self.mixer.voices():
            self.mixer.fade_out(voice, seconds)
        QTimer.singleShot(int(seconds * 1000) + 50, self._finish_panic)

    def _panic_pressed(self) -> None:
        """Escape fades everything out; two presses in quick succession cut it dead."""
        now = time.monotonic()
        double_tap = now - self._last_panic < 0.4
        self._last_panic = now
        if double_tap:
            self.hard_stop()
        else:
            self.panic()

    def _finish_panic(self) -> None:
        if self._panicking:  # a double tap may already have stopped everything
            self.hard_stop()

    def hard_stop(self) -> None:
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
        self._panicking = False
        self._radio = None
        self._radio_voice = None
        self.pause_button.setText("PAUSE")
        self._refresh_table()

    # --- Soundboard ---------------------------------------------------

    def _grow_pads(self, index: int) -> bool:
        """Make room up to this slot, so a show with few pads can still have one assigned."""
        if index < 0 or index >= PAD_COUNT:
            return False
        pads = self.show_model.pads
        while len(pads) <= index:
            pads.append(Pad())
        return True

    def _pad_at(self, index: int):
        """The pad at this slot, or None when the show has fewer pads than the board shows."""
        pads = self.show_model.pads
        return pads[index] if 0 <= index < len(pads) else None

    def _on_pad_pressed(self, index: int) -> None:
        pad = self._pad_at(index)
        if pad is None:
            return
        if not pad.file or self.mixer is None:
            return
        try:
            clip = self._clip(pad.file)
        except Exception as exc:
            self._cue_problem("Cannot play pad", f"{pad.name}\n\n{exc}")
            return
        self.mixer.add(Voice(clip=clip, matrix=route_matrix(pad.route, clip.channels, self.mixer.out_channels)))

    def _pad_routing(self, index: int) -> None:
        pad = self._pad_at(index)
        if pad is None:
            return
        out = self.mixer.out_channels if self.mixer is not None else 2
        dialog = RoutingDialog(pad.route, out, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pad.route = dialog.result_route()

    def _assign_pad(self, index: int) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Assign pad", "", AUDIO_FILTER)
        if not filename:
            return
        if not self._grow_pads(index):
            return
        self.show_model.pads[index] = Pad(name=Path(filename).stem, file=filename)
        self.soundboard.set_pads(self.show_model.pads)

    def _clear_pad(self, index: int) -> None:
        if not self._grow_pads(index):
            return
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
                    self.go_row(next_child)
                elif row >= 0 and voice.tag.then == "follow" and row + 1 < len(cues):
                    if voice.tag.post_wait_ms > 0:
                        self._after(voice.tag.post_wait_ms, lambda r=row + 1: self.go_row(r))
                    else:
                        self.go_row(row + 1)

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
        while not self._midi_triggers.empty():
            self._fire_midi_trigger(self._midi_triggers.get_nowait())
        self._check_wall_clock()
        self._check_ducking()
        self._follow_playhead()
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
            self._midi = MidiInput(name, self._midi_commands.put, mapping=self.show_model.midi_map,
                                   on_trigger=self._midi_triggers.put)
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

    def _fire_midi_trigger(self, trigger: tuple[str, int, int]) -> None:
        """An incoming MIDI message: run any cue set to listen for it."""
        kind, number, channel = trigger
        for row, cue in enumerate(self.show_model.cues):
            if not cue.midi_trigger_on or cue.midi_trigger_kind != kind:
                continue
            if cue.midi_trigger_number != number:
                continue
            if cue.midi_trigger_channel and cue.midi_trigger_channel != channel:
                continue  # 0 means any channel
            self.go_row(row)

    def _check_wall_clock(self) -> None:
        """Cues with a wall clock trigger fire once a day as the clock passes their time."""
        now = QTime.currentTime()
        today = str(QDate.currentDate().toString(Qt.DateFormat.ISODate))
        for row, cue in enumerate(self.show_model.cues):
            if not cue.wall_clock:
                continue
            due = QTime.fromString(cue.wall_clock, "HH:mm:ss")
            if not due.isValid() or now < due:
                continue
            if self._wall_clock_fired.get(cue.number) == today:
                continue
            self._wall_clock_fired[cue.number] = today
            self.go_row(row)

    def _start_options(self, cue: Cue) -> None:
        """Fade & stop, and ducking, applied to the other cues when this one starts."""
        if self.mixer is None:
            return
        if cue.fade_stop_others:
            for voice in self._cue_voices:
                if voice.tag is not cue and not voice.done and not voice.preloaded:
                    self.mixer.fade_out(voice, cue.fade_stop_time)
        if cue.duck_others:
            level = fx.db_to_gain(cue.duck_by_db)
            for voice in self._cue_voices:
                if voice.tag is not cue and not voice.done and not voice.preloaded:
                    voice.fade(level, cue.duck_time, self.mixer.rate)
            self._ducking = cue

    def _check_ducking(self) -> None:
        """Bring the other cues back up once the cue that ducked them has finished."""
        if self._ducking is None or self.mixer is None:
            return
        still_running = any(v.tag is self._ducking and not v.done for v in self._cue_voices)
        if still_running:
            return
        cue, self._ducking = self._ducking, None
        for voice in self._cue_voices:
            if voice.tag is not cue and not voice.done and not voice.stop_when_done:
                voice.fade(1.0, cue.duck_time, self.mixer.rate)

    def _second_trigger(self, cue: Cue) -> bool:
        """A cue triggered while it is already running. True means the GO was dealt with here."""
        if not self._cue_is_running(cue):
            return False
        if cue.second_trigger == "nothing":
            return True  # the cue carries on undisturbed and the GO is used up
        if cue.second_trigger == "stop":
            self._reset_cue(cue)
            return True
        if cue.second_trigger == "pause":
            for voice in self._cue_voices:
                if voice.tag is cue and not voice.done:
                    voice.paused = not voice.paused
            return True
        self._reset_cue(cue)  # "restart": stop it, then let the GO start it again
        return False

    def _cue_is_running(self, cue: Cue) -> bool:
        return any(v.tag is cue and not v.done and not v.preloaded for v in self._cue_voices)

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
            self._cue_problem("Venue Radio", modal_when_editing=False, detail=f"Could not play {Path(path).name}:\n{exc}")
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
        dialog = _DownloadDialog(f"Downloading CueRunner {release.tag}...", owner)
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
        worker.failed.connect(lambda msg: (dialog.finish(), QMessageBox.critical(owner, "Update failed", msg)))

        def finished(path: str) -> None:
            dialog.finish()
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
        # A QDialog starts hidden, so without this the download runs with nothing on screen.
        dialog.show()
        dialog.raise_()
        worker.start()
        self._update_worker = worker  # keep a reference while it runs


class _DownloadDialog(QDialog):
    """Update download progress. Cancel sits under the bar, on the right, and emits canceled once."""

    canceled = Signal()

    def __init__(self, text: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update CueRunner")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(420)
        self._finished = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(text))
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        # the app theme makes bars 8px tall, which hides the percentage
        self._bar.setStyleSheet(
            "QProgressBar { background: #1a1a1c; border: 0; border-radius: 4px; min-height: 22px;"
            " color: #f0f0f0; text-align: center; }"
            " QProgressBar::chunk { background: #3b82d6; border-radius: 4px; }"
        )
        self._bar.setTextVisible(True)
        layout.addWidget(self._bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def setValue(self, value: int) -> None:  # noqa: N802 - same name as QProgressDialog, so callers stay the same
        self._bar.setValue(value)

    def setRange(self, low: int, high: int) -> None:  # noqa: N802
        self._bar.setRange(low, high)

    def maximum(self) -> int:
        return self._bar.maximum()

    def finish(self) -> None:
        """Close because the download ended, not because the user cancelled."""
        self._finished = True
        self.close()

    def reject(self) -> None:
        if not self._finished:
            self.canceled.emit()
        super().reject()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(MAIN_QSS)  # dialogs and menus match the window
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
