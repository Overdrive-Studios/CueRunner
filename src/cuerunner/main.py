"""CueRunner main window: a dark cue list with playhead, GO/Stop, fades and hotkeys."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cuerunner import updater
from cuerunner.fades import FADE_TICK_MS, fade_volumes
from cuerunner.model import Cue, Show
from cuerunner.theme import apply_dark_theme
from cuerunner.timecode import format_clock, format_duration
from cuerunner.version import __version__

COLUMNS = ["Q", "Cue", "Type", "Duration", "Fade In", "Fade Out", "Hotkey", "File"]
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
    """Edit a cue's name, fades and hotkey."""

    def __init__(self, cue: Cue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cue {cue.number}")

        self.name_edit = QLineEdit(cue.name)
        self.fade_in = self._fade_spin(cue.fade_in)
        self.fade_out = self._fade_spin(cue.fade_out)
        self.hotkey_edit = QKeySequenceEdit(QKeySequence(cue.hotkey))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Name", self.name_edit)
        form.addRow("Fade in", self.fade_in)
        form.addRow("Fade out", self.fade_out)
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
        cue.hotkey = self.hotkey_edit.keySequence().toString()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.show_model = Show()
        self.show_path: Path | None = None
        self._shortcuts: list[QShortcut] = []
        self._probes: list[QMediaPlayer] = []
        self._playing_cue: Cue | None = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_playback_state)

        self._playing_fade_out = 0.0
        self._fade_queue: list[float] = []
        self._fade_done = None
        self.fade_timer = QTimer(self)
        self.fade_timer.setInterval(FADE_TICK_MS)
        self.fade_timer.timeout.connect(self._fade_tick)

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(100)
        self.clock_timer.timeout.connect(self._update_clock)

        self._build_toolbar()
        self._build_help_menu()
        self._build_central()
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.go)
        self._refresh_title()
        self.resize(1100, 620)

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

    def _build_central(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)

        transport = QHBoxLayout()
        self.go_button = QPushButton("GO")
        self.go_button.setObjectName("go")
        self.go_button.clicked.connect(self.go)
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop)
        self.now_label = QLabel("Standby")
        self.now_label.setObjectName("now")
        self.clock_label = QLabel(format_clock(0))
        self.clock_label.setObjectName("clock")
        transport.addWidget(self.go_button)
        transport.addWidget(self.stop_button)
        transport.addSpacing(16)
        transport.addWidget(self.now_label, 1)
        transport.addWidget(self.clock_label)
        layout.addLayout(transport)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.itemDoubleClicked.connect(lambda item: self.edit_row(item.row()))
        self.table.itemSelectionChanged.connect(self._refresh_now_label)
        layout.addWidget(self.table, 1)

        self.setCentralWidget(container)

    def _refresh_title(self) -> None:
        name = self.show_path.name if self.show_path else "Untitled"
        self.setWindowTitle(f"CueRunner - {name}")

    def _refresh_table(self) -> None:
        cues = self.show_model.cues
        playhead = self._playhead_row()
        playing_row = next((i for i, c in enumerate(cues) if c is self._playing_cue), -1)

        self.table.setRowCount(len(cues))
        for row, cue in enumerate(cues):
            marker = PLAYHEAD_MARK if row == playhead else ""
            values = [
                f"{marker} {cue.number}".strip(),
                cue.name,
                cue.kind,
                format_duration(cue.duration_ms),
                f"{cue.fade_in:g} s",
                f"{cue.fade_out:g} s",
                cue.hotkey,
                cue.file,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row == playing_row:
                    item.setBackground(QBrush(PLAYING_COLOUR))
                if row == playhead:
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)
        self._rebuild_shortcuts()
        self._refresh_now_label()

    def _refresh_now_label(self) -> None:
        cues = self.show_model.cues
        if self._playing_cue is not None:
            self.now_label.setText(f"NOW  {self._playing_cue.name}")
            return
        playhead = self._playhead_row()
        if playhead >= 0:
            self.now_label.setText(f"STANDBY  {cues[playhead].name}")
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
            self.player.setSource(QUrl.fromLocalFile(cue.file))
            self._playing_cue = cue
            self._playing_fade_out = cue.fade_out
            if cue.fade_in > 0:
                self._start_fade(0.0, 1.0, cue.fade_in)
            else:
                self.audio_output.setVolume(1.0)
            self.player.play()
        self.table.selectRow(min(row + 1, len(self.show_model.cues) - 1))

    def stop(self) -> None:
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._playing_fade_out > 0:
            self._start_fade(self.audio_output.volume(), 0.0, self._playing_fade_out, on_done=self._hard_stop)
        else:
            self._hard_stop()

    def _hard_stop(self) -> None:
        self._stop_fade_timer()
        self.player.stop()
        self.audio_output.setVolume(1.0)

    # --- Playback state and clock -------------------------------------

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.clock_timer.start()
        else:
            self.clock_timer.stop()
            self._playing_cue = None
            self._update_clock()
            self._refresh_table()

    def _update_clock(self) -> None:
        self.clock_label.setText(format_clock(self.player.position()))

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


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()
