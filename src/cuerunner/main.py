"""CueRunner main window: transport, cue list, soundboard, countdown, follow cues, fades, hotkeys, updates."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
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
from cuerunner.fades import FADE_TICK_MS, fade_volumes
from cuerunner.model import Cue, Pad, Show
from cuerunner.soundboard import Soundboard
from cuerunner.theme import apply_dark_theme
from cuerunner.timecode import format_clock, format_duration, format_hms
from cuerunner.version import __version__

COLUMNS = ["#", "Name", "Length", "Fade In", "Fade Out", "Loop", "Then", "Progress", "Remaining", "Hotkey", "File"]
PROGRESS_COL = 7
REMAINING_COL = 8
NAME_COL = 1

SHOW_FILTER = "CueRunner show (*.cuerunner.json);;All files (*)"
AUDIO_FILTER = "Audio files (*.wav *.aiff *.aif *.mp3 *.m4a *.flac *.ogg);;All files (*)"

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
    """Edit a cue's name, fades, loop, follow and hotkey."""

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
        self._probes: list[QMediaPlayer] = []
        self._pad_players: list[QMediaPlayer] = []
        self._playing_cue: Cue | None = None
        self._current_row: int | None = None
        self._user_stop = False
        self._row_bars: list[QProgressBar] = []

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        self._playing_fade_out = 0.0
        self._fade_queue: list[float] = []
        self._fade_done = None
        self.fade_timer = QTimer(self)
        self.fade_timer.setInterval(FADE_TICK_MS)
        self.fade_timer.timeout.connect(self._fade_tick)

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(100)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start()

        self._build_toolbar()
        self._build_help_menu()
        self._build_central()

        go_key = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        go_key.activated.connect(self.go)
        panic_key = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        panic_key.activated.connect(self.panic)

        self._refresh_title()
        self.resize(1200, 820)

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

    def _build_help_menu(self) -> None:
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
        for text, name, handler, stretch in [
            ("GO  (Space)", "go", self.go, 3),
            ("STOP", "stop", self.stop, 2),
            ("PAUSE", "pause", self.toggle_pause, 2),
            ("PANIC  (Esc)", "panic", self.panic, 2),
        ]:
            button = QPushButton(text)
            button.setObjectName(name)
            button.clicked.connect(handler)
            transport.addWidget(button, stretch)
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
        playing_row = self._playing_row()

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
                "Follow" if cue.then == "follow" else "",
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

    def _playing_row(self) -> int:
        return next((i for i, c in enumerate(self.show_model.cues) if c is self._playing_cue), -1)

    # --- Show file actions --------------------------------------------

    def new_show(self) -> None:
        self._hard_stop()
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
        self._hard_stop()
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
            self._refresh_table()
            for cue in new_cues:
                self._probe_duration(cue)

    def _probe_duration(self, cue: Cue) -> None:
        """Load the file in a throwaway player to learn its length."""
        if cue.kind != "audio" or not cue.file:
            return
        probe = QMediaPlayer(self)
        self._probes.append(probe)
        probe.durationChanged.connect(lambda ms, c=cue, p=probe: self._on_duration(c, ms, p))
        probe.setSource(QUrl.fromLocalFile(cue.file))

    def _on_duration(self, cue: Cue, ms: int, probe: QMediaPlayer) -> None:
        if ms <= 0:
            return
        cue.duration_ms = ms
        probe.stop()
        if probe in self._probes:
            self._probes.remove(probe)
        probe.deleteLater()
        self._refresh_table()

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
        if self.show_model.cues[row] is self._playing_cue:
            self._hard_stop()
        del self.show_model.cues[row]
        self._refresh_table()

    def go(self) -> None:
        row = self._playhead_row()
        if row >= 0:
            self.go_row(row)

    def go_row(self, row: int) -> None:
        """Play the cue in this row with its fade-in, then move the playhead down."""
        if not 0 <= row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[row]
        if cue.kind == "audio" and cue.file:
            self._stop_fade_timer()
            self._user_stop = False
            self._current_row = row
            self.player.setSource(QUrl.fromLocalFile(cue.file))
            self.player.setLoops(-1 if cue.loop else 1)
            self._playing_cue = cue
            self._playing_fade_out = cue.fade_out
            if cue.fade_in > 0:
                self._start_fade(0.0, 1.0, cue.fade_in)
            else:
                self.audio_output.setVolume(1.0)
            self.player.play()
        self.table.selectRow(min(row + 1, len(self.show_model.cues) - 1))

    def stop(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.StoppedState:
            return
        self._user_stop = True
        if self._playing_fade_out > 0 and state == QMediaPlayer.PlaybackState.PlayingState:
            self._start_fade(self.audio_output.volume(), 0.0, self._playing_fade_out, on_done=self._hard_stop)
        else:
            self._hard_stop()

    def toggle_pause(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()

    def panic(self) -> None:
        """Stop everything immediately, with no fade."""
        self._user_stop = True
        self._current_row = None
        self._hard_stop()
        self._playing_cue = None
        self._refresh_table()

    def _hard_stop(self) -> None:
        self._stop_fade_timer()
        self.player.stop()
        self.audio_output.setVolume(1.0)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        """Follow cues: when a cue finishes naturally, start the next one."""
        if status != QMediaPlayer.MediaStatus.EndOfMedia or self._user_stop:
            return
        if self._current_row is None or not 0 <= self._current_row < len(self.show_model.cues):
            return
        cue = self.show_model.cues[self._current_row]
        if cue.then == "follow" and self._current_row + 1 < len(self.show_model.cues):
            self.go_row(self._current_row + 1)

    # --- Soundboard ---------------------------------------------------

    def _on_pad_pressed(self, index: int) -> None:
        pad = self.show_model.pads[index]
        if pad.file:
            self._play_pad(pad.file)

    def _play_pad(self, filename: str) -> None:
        """Pads use their own player, so they play over the show without stopping it."""
        player = QMediaPlayer(self)
        output = QAudioOutput(player)
        player.setAudioOutput(output)
        output.setVolume(1.0)
        player.playbackStateChanged.connect(lambda state, p=player: self._cleanup_pad(p, state))
        player.setSource(QUrl.fromLocalFile(filename))
        self._pad_players.append(player)
        player.play()

    def _cleanup_pad(self, player: QMediaPlayer, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState and player in self._pad_players:
            self._pad_players.remove(player)
            player.deleteLater()

    def _assign_pad(self, index: int) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Assign pad", "", AUDIO_FILTER)
        if not filename:
            return
        self.show_model.pads[index] = Pad(name=Path(filename).stem, file=filename)
        self.soundboard.set_pads(self.show_model.pads)

    def _clear_pad(self, index: int) -> None:
        self.show_model.pads[index] = Pad()
        self.soundboard.set_pads(self.show_model.pads)

    # --- Playback state and clock -------------------------------------

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._playing_cue = None
        self._refresh_table()

    def _update_clock(self) -> None:
        self.wall_label.setText(datetime.now().strftime("%H:%M:%S"))
        row = self._playing_row()
        if row < 0:
            self.track_label.setText(format_clock(0))
            self.remaining_label.setText(format_clock(0))
            return

        position = self.player.position()
        duration = self.player.duration() or self.show_model.cues[row].duration_ms
        remaining = max(0, duration - position) if duration > 0 else 0
        self.track_label.setText(format_clock(position))
        self.remaining_label.setText(format_clock(remaining))
        if row < len(self._row_bars) and duration > 0:
            self._row_bars[row].setValue(min(100, position * 100 // duration))
        item = self.table.item(row, REMAINING_COL)
        if item is not None:
            item.setText(format_clock(remaining))

    # --- Fade engine --------------------------------------------------

    def _start_fade(self, start: float, end: float, seconds: float, on_done=None) -> None:
        self.fade_timer.stop()
        self._fade_queue = fade_volumes(start, end, seconds, FADE_TICK_MS)
        self._fade_done = on_done
        self.audio_output.setVolume(start)
        self.fade_timer.start()

    def _stop_fade_timer(self) -> None:
        self.fade_timer.stop()
        self._fade_queue = []
        self._fade_done = None

    def _fade_tick(self) -> None:
        if not self._fade_queue:
            self.fade_timer.stop()
            done, self._fade_done = self._fade_done, None
            if done is not None:
                done()
            return
        self.audio_output.setVolume(self._fade_queue.pop(0))

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
